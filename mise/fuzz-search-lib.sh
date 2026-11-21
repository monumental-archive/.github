#!/usr/bin/env bash
# What the scheduled fuzz search keeps (#1897). Sourced, never run:
# audit:fuzz and audit:go-fuzz both call it, so the two languages keep
# a find by one rule and report a run's length one way.
#
# The search never writes a find into the tracked corpus. A find is a
# candidate, and a candidate is kept only when it raises its target's
# line coverage as the coverage floor's fuzz leg measures it
# (org_fuzz_measure in fuzz-corpus-lib.sh, #1896), one at a time in
# name order. A crashing
# input is kept as well. What is kept is STAGED: copied under the stage
# directory at the path it would have in the repository, which
# repo-deep-tests.yml uploads and its `propose` job turns into a pull
# request. Nothing here pushes, and nothing here holds a token.
#
# Every step is checked here rather than left to errexit, which a
# caller's `$(...)`, `if` or `||` switches off.

# The names the search reads from, and stages as, a corpus file: one
# path segment, never `.` or `..`. The `propose` job holds every staged
# path to the same grammar.
org_fuzz_segment_re='^[A-Za-z0-9._-]+$'

# org_fuzz_stage_dir: sets `org_fuzz_stage` to ORG_FUZZ_CANDIDATES,
# made if absent, or to a new scratch directory when it is unset (a
# local run, which prints where it staged).
org_fuzz_stage_dir() {
  if [[ -n ${ORG_FUZZ_CANDIDATES:-} ]]; then
    org_fuzz_stage=${ORG_FUZZ_CANDIDATES}
    mkdir -p -- "${org_fuzz_stage}" || return 1
  else
    org_fuzz_stage=$(mktemp -d) || return 1
  fi
}

# org_fuzz_stage <file> <repository path>: copies <file> under the stage
# directory at <repository path>.
org_fuzz_stage() {
  local to="${org_fuzz_stage:?org_fuzz_stage_dir first}/$2"
  if ! mkdir -p -- "${to%/*}" || ! cp -- "$1" "${to}"; then
    echo "could not stage $1 at $2" >&2
    return 1
  fi
}

# org_fuzz_stamp: copies its input to its output, each line prefixed
# with the whole seconds since it started and a space. libFuzzer's lines
# carry no time of their own, and the run's length is read from these.
org_fuzz_stamp() {
  local start=${SECONDS} line
  while IFS= read -r line || [[ -n ${line} ]]; do
    printf '%s %s\n' "$((SECONDS - start))" "${line}"
  done
}

# org_fuzz_last_new <rust|go> <log>: the second of the run's last new
# coverage, or nothing when it found none. Rust: the stamp of the last
# libFuzzer `NEW` line (`<secs> #<n> NEW cov: …`). Go: the `elapsed:` of
# the last progress line whose `new interesting:` count rose, which Go
# prints as a duration (`9s`, `1m3s`).
org_fuzz_last_new() {
  case "$1" in
    rust)
      awk '$2 ~ /^#[0-9]+$/ && $3 == "NEW" { last = $1 } END { if (last != "") print last }' "$2"
      ;;
    go)
      awk '
        /fuzz: elapsed: / && /new interesting: / {
          d = $0; sub(/.*elapsed: /, "", d); sub(/,.*/, "", d)
          k = $0; sub(/.*new interesting: /, "", k); sub(/[^0-9].*/, "", k)
          if (k + 0 <= seen + 0) next
          seen = k; s = 0; n = ""
          for (i = 1; i <= length(d); i++) {
            c = substr(d, i, 1)
            if (c ~ /[0-9.]/) { n = n c; continue }
            if (c == "h") s += n * 3600
            if (c == "m") s += n * 60
            if (c == "s") s += n
            n = ""
          }
          last = int(s)
        }
        END { if (last != "") print last }' "$2"
      ;;
    *)
      echo "org_fuzz_last_new: unknown kind '$1'" >&2
      return 1
      ;;
  esac
}

# org_fuzz_timing <last> <budget>: how long the run went on finding new
# code, as the summary prints it. In the last quarter of the budget, the
# line says the run stopped while still finding: a longer run would
# likely have found more.
org_fuzz_timing() {
  local last=$1 budget=$2
  if [[ -z ${last} ]]; then
    echo "no new coverage in ${budget}s"
  elif ((4 * last >= 3 * budget)); then
    echo "last new coverage at ${last}s of ${budget}s: still finding new code when it stopped"
  else
    echo "last new coverage at ${last}s of ${budget}s"
  fi
}

# org_fuzz_summary <line>: one line of the run's summary, printed, and
# appended to ORG_FUZZ_SUMMARY when it is set (the pull request's body).
org_fuzz_summary() {
  echo "$1"
  [[ -z ${ORG_FUZZ_SUMMARY:-} ]] || echo "$1" >> "${ORG_FUZZ_SUMMARY}"
}

# org_fuzz_proposed <repository dir> <dest>: copies the files an
# unmerged proposal holds under <repository dir> on origin/fuzz-corpus
# into <dest>, as data, so the search builds on it. Only regular files
# named by one segment directly under <repository dir>; never a name
# starting crash-, timeout- or oom-, which would stop the search on a
# crash already proposed; never over a file <dest> already holds.
# Prints each name it wrote. No branch, nothing written.
org_fuzz_proposed() {
  local dir=$1 dest=$2 listing entry mode type oid path name
  git rev-parse -q --verify 'refs/remotes/origin/fuzz-corpus^{commit}' > /dev/null || return 0
  listing=$(git ls-tree --full-tree origin/fuzz-corpus -- "${dir}/") || {
    echo "git ls-tree failed listing ${dir}/ on origin/fuzz-corpus" >&2
    return 1
  }
  while IFS=$'\t' read -r entry path; do
    [[ -n ${path:-} ]] || continue
    read -r mode type oid <<< "${entry}"
    name=${path#"${dir}/"}
    [[ ${type} == blob && ${mode} =~ ^1007?55$|^100644$ ]] || continue
    [[ ${name} =~ ${org_fuzz_segment_re} && ${name} != . && ${name} != .. ]] || continue
    [[ ${name} != crash-* && ${name} != timeout-* && ${name} != oom-* ]] || continue
    [[ ! -e ${dest}/${name} ]] || continue
    if ! mkdir -p -- "${dest}" || ! git cat-file blob "${oid}" > "${dest}/${name}"; then
      echo "could not read ${path} from origin/fuzz-corpus" >&2
      return 1
    fi
    echo "${name}"
  done <<< "${listing}"
}

# org_fuzz_select <label> <leg> <dir> <stage dir> <measure...> -- <candidate...>
#
# Keeps the candidates that raise the figure (#1897 item 2). <measure>
# is org_fuzz_measure's arguments after the leg (fuzz-corpus-lib.sh,
# the measurement the coverage floor's fuzz leg makes): `rust <target>
# <dir>`, or `go <package dir> <Fuzz name>` with <dir> that package's
# testdata/fuzz/<Fuzz name>. It measures <dir> as it is, then adds each
# candidate to it in name order and measures again: a candidate whose
# figure is strictly higher is kept, staged at <stage dir>/<its name>,
# and stays in <dir> for the next one's measurement; any other is taken
# out again. A candidate whose name <dir> already holds is the same
# input and is skipped. Every candidate added to <dir> is removed
# before it returns, so <dir> is left as found.
#
# Prints `<before> <after> <kept>`.
org_fuzz_select() {
  local label=$1 leg=$2 dir=$3 stage=$4 measure=() before current pct
  local file name list kept=0 added=() status=0
  shift 4
  while [[ $# -gt 0 && $1 != -- ]]; do
    measure+=("$1")
    shift
  done
  [[ $# -eq 0 ]] || shift
  mkdir -p -- "${dir}" || return 1
  before=$(org_fuzz_measure "${label}" "${leg}" "${measure[@]}") || return 1
  current=${before}
  # In name order: each candidate as `<name><tab><path>`, sorted.
  list=$(mktemp) || return 1
  for file in "$@"; do
    printf '%s\t%s\n' "${file##*/}" "${file}"
  done > "${list}"
  if ! LC_ALL=C sort -o "${list}" "${list}"; then
    rm -f -- "${list}"
    return 1
  fi
  while IFS=$'\t' read -r name file; do
    [[ -n ${file} ]] || continue
    [[ ! -e ${dir}/${name} ]] || continue
    if ! cp -- "${file}" "${dir}/${name}"; then
      echo "${label}: could not add ${file} to ${dir}" >&2
      status=1
      break
    fi
    added+=("${dir}/${name}")
    if ! pct=$(org_fuzz_measure "${label}" "${leg}" "${measure[@]}"); then
      status=1
      break
    fi
    if awk -v p="${pct}" -v c="${current}" 'BEGIN { exit !(p > c) }'; then
      if ! org_fuzz_stage "${file}" "${stage}/${name}"; then
        status=1
        break
      fi
      current=${pct}
      kept=$((kept + 1))
    else
      rm -f -- "${dir}/${name}"
    fi
  done < "${list}"
  rm -f -- "${list}" ${added[@]+"${added[@]}"}
  ((status == 0)) || return 1
  echo "${before} ${current} ${kept}"
}
