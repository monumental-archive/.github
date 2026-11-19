#!/usr/bin/env bash
# The Go leg of `audit:mutants` (#1356): would the suite fail if the code
# were wrong? Org canon. gomu changes the code one site at a time and
# runs the package's tests against each change; a mutant the tests still
# pass is a SURVIVOR, and a survivor is a named line to fix.
#
# THE MEASURE IS "NO UNEXPLAINED SURVIVOR", NEVER A SCORE (#1336). Red
# while any mutant survives that is neither killed by an assertion nor
# recorded, at its site, as equivalent — with a reason.
#
# ADOPTION IS THE TOOL'S OWN FILE. gomu has no config file; the one file
# it reads is `.gomuignore`. A repository adopts by tracking one at its
# root, and without it this skips clean, by name. It must hold NO
# pattern: a pattern drops whole files from the run and from the count,
# a denominator bought (#1336, "no number can be bought"). gomu finds the
# nearest `.gomuignore` walking up from each file, so a pattern in ANY
# tracked one is red, by name.
#
# THE RECORD OF AN EQUIVALENT MUTANT IS A COMMENT AT ITS SITE:
#
#   //mutants:equivalent <type> <reason>
#
# trailing the mutant's line, or alone on the line directly above it.
# <type> is gomu's mutator name (`gomu run --list`). mutants_go.py reads
# them and holds each one to a surviving mutant of its type at its site;
# its header has the rules. The belt delivers the reader, never a record.
#
# NOT COVERED IS ITS OWN VERDICT. gomu takes no coverage, so it runs a
# mutant on a line no test executes and calls it SURVIVED. This leg takes
# the package's own coverage profile first (gomu runs only the package's
# own tests against its mutants, so the package's own tests are the
# coverage that counts), and a survivor on a line no test executes is
# reported NOT COVERED: untested, not equivalent, and only a test fixes
# it. NOT_VIABLE (gomu's compile check rejected the mutant) is counted on
# its own, never as killed. TIMED_OUT is killed: the suite noticed, by
# hanging; the timeout is ten times the package's own measured test time,
# never less than gomu's 30 s default, so a slow package is not read as a
# hang.
#
# GOMU EXITS 0 WHATEVER HAPPENED (v0.3.0, outside --ci-mode), and it
# SKIPS A FILE SILENTLY when generating or executing its mutants fails:
# it prints "(error: ...)" or "(execution error: ...)" and carries on. So
# the verdict is read from its JSON, never its exit status, and either
# message is red.
#
# NOTHING IS WRITTEN INTO THE TREE. gomu writes `.gomu_history.json` and
# `mutation-report.json` to its working directory, so it runs from a
# scratch directory with the file's absolute path; `--incremental=false`,
# always, because an incremental run judges only what changed.
#
# THE REAL TOOLCHAIN, NEVER A SHIM: `$(go env GOROOT)/bin` goes first on
# PATH, and the leg refuses to run if `go` resolves anywhere else. A shim
# that fails `go test` for a reason of its own exits 1, and a mutation
# tool reads exit 1 as killed (measured on gremlins and on gomu, #1356).
#
# ONE WORKER. Four parallel `-overlay` workers were running when a Mac
# ran out of memory (93 GB, 2026-11-17); the runner's memory is measured
# by the first audit runs before this is raised.
#
# Run as one shard of repo-mutants.yml's `mutants-go` matrix
# (MUTANTS_SHARD=k/n), the leg takes the k-th of every n packages in
# `go list` order and, given MUTANTS_SUMMARY_DIR, writes its counts to
# go.tsv there (creating it) for the job that states the totals.
set -euo pipefail
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"

label="audit:mutants: go"
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: conf.d symlink)}"
adoption=".gomuignore"

shard="${MUTANTS_SHARD:-}"
where=""
k=0
n=1
if [[ -n ${shard} ]]; then
  if [[ ! ${shard} =~ ^([0-9]+)/([1-9][0-9]*)$ ]] || ((BASH_REMATCH[1] >= BASH_REMATCH[2])); then
    echo "${label}: MUTANTS_SHARD='${shard}' is not k/n with k < n" >&2
    exit 1
  fi
  k="${BASH_REMATCH[1]}"
  n="${BASH_REMATCH[2]}"
  where=", shard ${shard}"
fi

if ! git ls-files --error-unmatch -- "${adoption}" > /dev/null 2>&1; then
  echo "${label}: no ${adoption} tracked, skipped"
  if [[ -n ${MUTANTS_SUMMARY_DIR:-} ]]; then
    mkdir -p "${MUTANTS_SUMMARY_DIR}"
    printf '%s\tskipped\n' "${shard:-whole}" > "${MUTANTS_SUMMARY_DIR}/go.tsv"
  fi
  exit 0
fi

ignores=$(git ls-files -- "${adoption}" "*/${adoption}")
patterns=""
while IFS= read -r f; do
  [[ -n ${f} ]] || continue
  hits=$(awk -v f="${f}" '!/^[[:space:]]*(#|$)/ { printf "%s:%d: %s\n", f, NR, $0 }' "${f}")
  patterns+="${hits:+${hits}$'\n'}"
done <<< "${ignores}"
if [[ -n ${patterns} ]]; then
  printf '%s' "${patterns}" | sed "s/^/${label}: pattern in /" >&2
  echo "  a ${adoption} pattern drops whole files from the run and the count;" >&2
  echo "  record an equivalent mutant at its site instead: //mutants:equivalent <type> <reason>" >&2
  exit 1
fi

if ! git ls-files --error-unmatch -- go.mod > /dev/null 2>&1; then
  echo "${label}: ${adoption} is tracked but no go.mod beside it" >&2
  exit 1
fi
command -v go > /dev/null || {
  echo "${label}: ${adoption} is tracked but go is missing — pin go in mise.toml" >&2
  exit 1
}
gomu_bin=$(command -v gomu) || {
  echo "${label}: gomu missing — the belt pins it lazy; install it by name:" >&2
  echo "    mise install go:github.com/sivchari/gomu/cmd/gomu" >&2
  exit 1
}
# The binary itself, never mise's shim. A shim runs gomu under mise's own
# environment, which puts the shims back ahead of the real toolchain set
# below, so gomu's `go build` and `go test` reach the `go` shim instead:
# outside a directory that pins go it fails ("No version is set for shim:
# go"), and gomu reads every mutant as NOT_VIABLE (measured, #1356, on a
# one-function module; the binary itself killed 3 of 4 in the same run).
target=$(readlink "${gomu_bin}" || true)
if [[ ${target##*/} == mise ]]; then
  gomu_bin=$(mise which gomu) || {
    echo "${label}: gomu resolves to a mise shim and \`mise which gomu\` names no binary" >&2
    exit 1
  }
fi

goroot=$(go env GOROOT)
PATH="${goroot}/bin:${PATH}"
export PATH
resolved=$(command -v go)
if [[ ${resolved} != "${goroot}/bin/go" ]]; then
  echo "${label}: go resolves to ${resolved}, not ${goroot}/bin/go" >&2
  exit 1
fi

# gomu reads export data with the importer of the Go it was built with,
# so one built with an older Go than the toolchain it runs under cannot
# read that toolchain's export data: the v0.3.0 release binary, built
# with Go 1.24.13, panicked on every import against Go 1.27 (#1356). The
# belt builds it with the repository's own go; a stale local binary or a
# cache restored across a Go bump is refused here, by name, before it can
# panic or quietly lose its type information. The comparison and the
# refusal are mutants_go.py's (`build-go`); the go calls stay here.
built=$(go version -m "${gomu_bin}" | awk 'NR == 1 { print $2 }')
running=$(go env GOVERSION)
"${ORG_PYTHON}" "${belt}/mutants_go.py" build-go --label "${label}" --gomu "${gomu_bin}" \
  --built "${built}" --running "${running}"

root=$(pwd -P)
rc=0
scratch=$(mktemp -d)
trap 'rc=$?; rm -rf "${scratch}"; [ -n "${org_done:-}" ] || [ "${rc}" -ne 0 ] || rc=1; exit "${rc}"' EXIT

# The whole population: every package, its directory and its files, one
# tab-separated line each, sorted so every shard cuts the same list.
go list -f '{{.ImportPath}}{{"\t"}}{{.Dir}}{{range .GoFiles}}{{"\t"}}{{.}}{{end}}{{range .CgoFiles}}{{"\t"}}{{.}}{{end}}' ./... \
  | LC_ALL=C sort > "${scratch}/all.tsv"
awk -F '\t' -v k="${k}" -v n="${n}" '(NR - 1) % n == k' "${scratch}/all.tsv" > "${scratch}/shard.tsv"

# Generated files are not mutated (gomu's own default, and a survivor
# there is fixed by the generator, not the file): they leave the
# population here, so a record in one is in no mutated file. Go's marker
# must precede the package clause; awk reads no further than that.
: > "${scratch}/files.tsv"
while IFS=$'\t' read -r -a fields; do
  dir="${fields[1]}"
  for f in "${fields[@]:2}"; do
    marker=$(awk '/^package / { exit } /^\/\/ Code generated .* DO NOT EDIT\.$/ { print "yes"; exit }' "${dir}/${f}")
    [[ -n ${marker} ]] || printf '%s\t%s\n' "${fields[0]}" "${dir}/${f}" >> "${scratch}/files.tsv"
  done
done < "${scratch}/all.tsv"

pkgs=()
while IFS=$'\t' read -r path _; do
  pkgs+=("${path}")
done < "${scratch}/shard.tsv"

mkdir "${scratch}/reports"
: > "${scratch}/cover.out"
: > "${scratch}/gomu.log"
: > "${scratch}/runs.log"
if [[ ${#pkgs[@]} -gt 0 ]]; then
  echo "${label}: measuring coverage of ${#pkgs[@]} package(s)${where}"
  status=0
  go test -vet=off -covermode=set -coverprofile="${scratch}/cover.out" "${pkgs[@]}" \
    > "${scratch}/baseline.log" 2>&1 || status=$?
  if [[ ${status} -ne 0 ]]; then
    cat "${scratch}/baseline.log" >&2
    echo "${label}: the suite fails before any mutant (go test exited ${status})" >&2
    exit 1
  fi
fi

i=0
while IFS=$'\t' read -r path dir _; do
  # Ten times the package's own test time from the baseline, at least 30 s.
  secs=$(awk -v p="${path}" '$1 == "ok" && $2 == p { sub(/s$/, "", $3); print $3; exit }' "${scratch}/baseline.log")
  timeout=$(awk -v s="${secs:-0}" 'BEGIN { t = int(s * 10) + 1; print (t < 30 ? 30 : t) }')
  while IFS=$'\t' read -r owner file; do
    [[ ${owner} == "${path}" ]] || continue
    i=$((i + 1))
    run="${scratch}/run-${i}"
    mkdir "${run}"
    status=0
    (cd "${run}" && "${gomu_bin}" run "${file}" --workers 1 --timeout "${timeout}" \
      --incremental=false --output json --fail-on-gate=false) > "${run}/out" 2>&1 || status=$?
    cat "${run}/out" >> "${scratch}/gomu.log"
    if [[ ${status} -ne 0 ]]; then
      printf '%s: gomu exited %s\n' "${file#"${root}"/}" "${status}" >> "${scratch}/runs.log"
      # The first failure's own output, once: without it a run that
      # failed for every file says only "exited N" (#1356, stele's first
      # run: exit 2 on all 26 files of a shard, the cause discarded).
      if [[ -z ${shown:-} ]]; then
        shown=1
        echo "${label}: gomu (${gomu_bin}) exited ${status} on ${file#"${root}"/}; its output:" >&2
        tail -n 40 "${run}/out" | sed 's/^/    /' >&2
      fi
    elif [[ -s ${run}/mutation-report.json ]]; then
      mv "${run}/mutation-report.json" "${scratch}/reports/${i}.json"
    else
      printf '%s: gomu wrote no report\n' "${file#"${root}"/}" >> "${scratch}/runs.log"
    fi
  done < "${scratch}/files.tsv"
done < "${scratch}/shard.tsv"

git grep -n -e '//mutants:equivalent' -- ':!vendor/**' '*.go' > "${scratch}/records.txt" || true

"${ORG_PYTHON}" "${belt}/mutants_go.py" \
  --label "${label}" --where "${where}" --shard "${shard:-whole}" \
  --root "${root}" --scratch "${scratch}" --summary-dir "${MUTANTS_SUMMARY_DIR:-}"
org_done=1
