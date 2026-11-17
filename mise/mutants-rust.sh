#!/usr/bin/env bash
# The Rust leg of `audit:mutants` (#1355): would the suite fail if the
# code were wrong? Org canon. cargo-mutants changes the code one site at
# a time and runs the tests; a mutant the tests still pass is a
# SURVIVOR, and a survivor is a named line to fix.
#
# THE MEASURE IS "NO UNEXPLAINED SURVIVOR", NEVER A SCORE (#1336). A
# percentage can be bought by deleting hard code, and it lets the one
# survivor in the code that matters hide in the average. So this is red
# while any survivor is neither killed by an assertion nor recorded, with
# a reason, as an exclusion in the repository's own config.
#
# ADOPTION IS THE TOOL'S OWN CONFIG FILE. A repository adopts by tracking
# `.cargo/mutants.toml`; without one this skips clean, by name. No list of
# adopters lives in the belt, and no exclusion does either: an equivalent
# mutant is a fact about one site in one repository.
#
# EVERY EXCLUSION CARRIES ITS REASON. Each `exclude_re` (and
# `exclude_globs`) entry sits on its own line under a `#` line that says
# why; one without is red by name. `#[mutants::skip]` is refused: it puts
# a dependency in shipped code, and it is an exclusion with nowhere to
# write the reason.
#
# THE POPULATION IS EVERY WORKSPACE MEMBER, named one by one. Measured on
# release-lab: a bare `cargo mutants` follows `default-members`, which
# there leaves out the pgrx crate — an exclusion the repository never
# wrote for this purpose. A pgrx extension is mutated in a run of its
# own, under ONE major, the crate's default (`default = ["pgNN"]`), with
# `pg_test` so its `#[pg_test]` suite runs; which members are pgrx comes
# from gate-surface.py, as for every other pgrx task.
#
# Prints each survivor (file:line:col: the mutation) and one summary
# line: mutants, killed, survived, excluded. Run as one shard of
# repo-mutants.yml's matrix (MUTANTS_SHARD=k/n), it judges that shard
# alone and, given MUTANTS_SUMMARY_DIR, writes its counts to rust.tsv
# there for the job that states the totals across shards.
set -euo pipefail

label="audit:mutants: rust"
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: conf.d symlink)}"
config=".cargo/mutants.toml"

# One shard of the population, `k/n`, when repo-mutants.yml's matrix runs
# the leg; unset, the whole population (a local run). Anything else is
# red rather than read as "whole": a typo would quietly run every mutant
# n times over.
shard="${MUTANTS_SHARD:-}"
where=""
if [[ -n ${shard} ]]; then
  if [[ ! ${shard} =~ ^([0-9]+)/([1-9][0-9]*)$ ]] || ((BASH_REMATCH[1] >= BASH_REMATCH[2])); then
    echo "${label}: MUTANTS_SHARD='${shard}' is not k/n with k < n" >&2
    exit 1
  fi
  where=", shard ${shard}"
fi

if ! git ls-files --error-unmatch "${config}" > /dev/null 2>&1; then
  echo "${label}: no ${config} tracked, skipped"
  if [[ -n ${MUTANTS_SUMMARY_DIR:-} ]]; then
    mkdir -p "${MUTANTS_SUMMARY_DIR}"
    printf '%s\tskipped\n' "${shard:-whole}" > "${MUTANTS_SUMMARY_DIR}/rust.tsv"
  fi
  exit 0
fi

# Reasons first: cheap, and a malformed exclusion is red whatever the run
# would have found. Every entry of an exclusion list must sit on its own
# line with a `#` line directly above it; an entry on the `key = [` line
# itself has nowhere to put one.
unexplained=$(awk '
  /^[[:space:]]*(exclude_re|exclude_globs)[[:space:]]*=/ {
    key = $0
    sub(/^[[:space:]]*/, "", key)
    sub(/[[:space:]]*=.*/, "", key)
    rest = $0
    sub(/^[^=]*=[[:space:]]*\[?/, "", rest)
    if (rest ~ /^[[:space:]]*["\047]/) {
      printf "%d\t%s\t%s\n", NR, key, "an entry on the opening line"
    }
    inside = ($0 !~ /\]/)
    prev = ""
    next
  }
  inside {
    if ($0 ~ /^[[:space:]]*\]/) { inside = 0; next }
    if ($0 ~ /^[[:space:]]*["\047]/) {
      if (prev !~ /^[[:space:]]*#/) {
        entry = $0
        sub(/^[[:space:]]*/, "", entry)
        sub(/,[[:space:]]*$/, "", entry)
        printf "%d\t%s\t%s\n", NR, key, entry
      }
      if ($0 ~ /\][[:space:]]*$/) { inside = 0 }
    }
    if ($0 !~ /^[[:space:]]*$/) { prev = $0 }
  }
' "${config}")
if [[ -n ${unexplained} ]]; then
  while IFS=$'\t' read -r line key entry; do
    echo "${label}: ${config}:${line}: ${key} entry ${entry} has no # reason line above it" >&2
  done <<< "${unexplained}"
  echo "  an exclusion records a mutant the suite cannot kill; say why, one # line above each entry" >&2
  exit 1
fi

skips=$(git grep -n -e 'mutants::skip' -- ':!vendor/**' '*.rs' || true)
if [[ -n ${skips} ]]; then
  echo "${label}: #[mutants::skip] in the tree:" >&2
  printf '  %s\n' "${skips}" >&2
  echo "  record the site as an exclude_re entry in ${config}, under a # reason line" >&2
  exit 1
fi

command -v cargo > /dev/null || {
  echo "${label}: ${config} present but cargo missing — pin rust in mise.toml" >&2
  exit 1
}
command -v cargo-mutants > /dev/null || {
  echo "${label}: cargo-mutants missing — the belt pins it lazy; install it by name:" >&2
  echo "    mise install cargo:cargo-mutants" >&2
  exit 1
}

# Assigned one step each: an empty here-string from a failed derivation
# would read as a workspace with nothing to mutate.
metadata=$(cargo metadata --no-deps --format-version 1 --locked)
surface=$(python3 "${belt}/gate-surface.py" <<< "${metadata}")

plain=()
pgrx=()
while IFS=$'\t' read -r record spec kind _majors _dir; do
  [[ ${record} == crate ]] || continue
  if [[ ${kind} == pgrx ]]; then
    pgrx+=("${spec%@*}")
  else
    plain+=("${spec%@*}")
  fi
done <<< "${surface}"

# The crate's default major: the one `pgNN` its `default` feature names.
default_major() {
  jq -r --arg n "$1" '
    .packages[] | select(.name == $n)
    | [(.features.default // [])[] | select(test("^pg[0-9]+$"))]
    | if length == 1 then .[0] else "" end' <<< "${metadata}"
}

# The status survives the cleanup, and a run that died before its last
# statement is red even on bash 3.2, where an unset-variable death under
# an EXIT trap exits 0 (#1233).
rc=0
scratch=$(mktemp -d)
trap 'rc=$?; rm -rf "${scratch}"; [ -n "${org_done:-}" ] || [ "${rc}" -ne 0 ] || rc=1; exit "${rc}"' EXIT

runs=()
red=0
total=0
killed=0
survived=0
excluded=0
survivors=()

# One cargo-mutants run: $1 names it, the rest are its selection flags.
# The verdict is read from outcomes.json, never the exit status alone:
# 2 (survivors) and 3 (timeouts) are results to report, anything else
# but 0 means nothing was judged. A timeout counts as killed: the suite
# noticed, by hanging, and cargo-mutants itself calls it no survivor.
#
# EXCLUDED IS THE WHOLE POPULATION'S, never a shard's: a shard is cut
# from the list AFTER exclusion, so the configured and unconfigured
# lists slice differently and a per-shard difference means nothing (it
# can go negative). Every shard reports the same whole-population
# figure and the totals take it once.
mutate() {
  local name="$1" out="${scratch}/$1" status=0 all listed here
  shift
  # Counted by awk, not `wc -l`: BSD wc pads its number with spaces.
  all=$(cargo mutants --list --no-config "$@" | awk 'NF { n++ } END { print n + 0 }')
  listed=$(cargo mutants --list "$@" | awk 'NF { n++ } END { print n + 0 }')
  excluded=$((excluded + all - listed))
  here="${listed}"
  if [[ -n ${shard} ]]; then
    here=$(cargo mutants --list --shard "${shard}" "$@" | awk 'NF { n++ } END { print n + 0 }')
  fi
  echo "${label}: mutating ${name}${where} (${here} mutants, $((all - listed)) excluded)"
  # Measured on cargo-mutants 27.1.0: a shard with nothing in it exits 0
  # and writes no outcomes.json. Nothing to run is said by name, green.
  if [[ ${here} -eq 0 ]]; then
    echo "${label}: ${name}${where}: no mutants in this shard, nothing to run"
    return 0
  fi
  local sharding=()
  [[ -z ${shard} ]] || sharding=(--shard "${shard}")
  cargo mutants --output "${out}" ${sharding[@]+"${sharding[@]}"} "$@" || status=$?
  if [[ ${status} -ne 0 && ${status} -ne 2 && ${status} -ne 3 ]]; then
    runs+=("${name}: cargo mutants exited ${status} — nothing judged (a red baseline is exit 4)")
    red=1
    return 0
  fi
  local outcomes="${out}/mutants.out/outcomes.json" counts
  [[ -r ${outcomes} ]] || {
    runs+=("${name}: cargo mutants wrote no outcomes.json")
    red=1
    return 0
  }
  counts=$(jq -r '[.total_mutants, .caught + .timeout, .missed] | @tsv' "${outcomes}")
  local t k m
  IFS=$'\t' read -r t k m <<< "${counts}"
  total=$((total + t))
  killed=$((killed + k))
  survived=$((survived + m))
  if [[ ${m} -gt 0 ]]; then
    red=1
    while IFS= read -r s; do
      [[ -n ${s} ]] && survivors+=("${s}")
    done < "${out}/mutants.out/missed.txt"
  fi
  return 0
}

if [[ ${#plain[@]} -gt 0 ]]; then
  flags=()
  for c in "${plain[@]}"; do flags+=(--package "${c}"); done
  mutate "workspace" "${flags[@]}"
fi

provisioned=""
for c in ${pgrx[@]+"${pgrx[@]}"}; do
  major=$(default_major "${c}")
  if [[ -z ${major} ]]; then
    runs+=("${c}: a pgrx extension whose default features name no single pgNN — it is mutated under its default major")
    red=1
    continue
  fi
  if [[ -z ${provisioned} ]]; then
    provisioned=ok
    # shellcheck source=mise/pgrx-env.sh
    . "${belt}/pgrx-env.sh"
    # shellcheck disable=SC2310  # deliberate: a failed provisioning reds
    # this crate and the run still reports the rest
    org_pgrx_provision "${label}" "" || provisioned=failed
  fi
  if [[ ${provisioned} != ok ]]; then
    runs+=("${c} at ${major}: no Postgres could be provisioned")
    red=1
    continue
  fi
  echo "${label}: ${c} is a pgrx extension, mutated under its default major, ${major}"
  mutate "${c}-${major}" --package "${c}" --features pg_test
done

if [[ ${#plain[@]} -eq 0 && ${#pgrx[@]} -eq 0 ]]; then
  echo "${label}: ${config} tracked but no crate in the workspace" >&2
  exit 1
fi

for s in ${survivors[@]+"${survivors[@]}"}; do
  echo "${label}: survived: ${s}"
done
for r in ${runs[@]+"${runs[@]}"}; do
  echo "${label}: ${r}" >&2
done
echo "${label}:${where:+${where#,}:} ${total} mutants, ${killed} killed, ${survived} survived, ${excluded} excluded"
# Only a shard that judged everything it was given reports: a red run
# (as opposed to red survivors) writes nothing, and the totals step
# names the shard that is missing.
if [[ -n ${MUTANTS_SUMMARY_DIR:-} && ${#runs[@]} -eq 0 ]]; then
  mkdir -p "${MUTANTS_SUMMARY_DIR}"
  printf '%s\t%s\t%s\t%s\t%s\n' "${shard:-whole}" "${total}" "${killed}" "${survived}" "${excluded}" \
    > "${MUTANTS_SUMMARY_DIR}/rust.tsv"
fi
if [[ ${red} -ne 0 ]]; then
  echo "${label}: red — kill each survivor with an assertion, or record it in ${config}" >&2
  echo "  as an exclude_re entry under a # line saying why the mutant is equivalent" >&2
  exit 1
fi
org_done=1
