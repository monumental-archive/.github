#!/usr/bin/env bash
# The Rust leg of `audit:mutants` (#1355): would the suite fail if the
# code were wrong? Org canon. cargo-mutants changes the code one site at
# a time and runs the tests; a mutant the tests still pass is a
# SURVIVOR, and a survivor is a named line to fix.
#
# THE MEASURE IS "NO UNEXPLAINED SURVIVOR", NEVER A SCORE (#1336). A
# percentage can be bought by deleting hard code, and it lets the one
# survivor in the code that matters hide in the average. So this is red
# while any survivor stands. Its three outcomes, in order (#1981):
# delete code it proves redundant, kill it with an assertion, or — the
# code must stay and no input can tell — record it, at its site, as
# equivalent, with a reason.
#
# ADOPTION IS THE TOOL'S OWN CONFIG FILE. A repository adopts by tracking
# `.cargo/mutants.toml`; without one this skips clean, by name. No list of
# adopters lives in the belt, and no exclusion does either: an equivalent
# mutant is a fact about one site in one repository.
#
# THE RECORD OF AN EQUIVALENT MUTANT IS A COMMENT AT ITS SITE (#1987):
#
#   // mutants:equivalent <mutation>: <reason>
#
# trailing the mutant's line, or alone on the line directly above it.
# <mutation> is cargo-mutants' own text for the mutant without its
# ` in <function>` (`replace > with >=`, `delete -`). It moves with the
# code: the leg derives the exclusion from where the comment stands,
# `^<path>:<line>:[0-9]+: <mutation>( in .*)?$`, and passes it as
# `--exclude-re` to the configured listings and the run, beside the
# config's own. mutants_rust.py holds each record to a mutant at its
# site in the `--no-config` listing; its header has the rules.
#
# EVERY CONFIG EXCLUSION CARRIES ITS REASON, AND NONE IS A SITE.
# `exclude_re` is for rules that are not a site (the pgrx `tests::`
# pattern). Each `exclude_re` (and `exclude_globs`) entry sits on its
# own line under a `#` line that says why; one without is red by name,
# and so is an `exclude_re` entry that pins a line and column — it goes
# stale as soon as the code above it moves. `#[mutants::skip]` is
# refused: it puts a dependency in shipped code. A comment is not one.
#
# THE POPULATION IS EVERY WORKSPACE MEMBER, named one by one. Measured on
# release-lab: a bare `cargo mutants` follows `default-members`, which
# there leaves out the pgrx crate — an exclusion the repository never
# wrote for this purpose. A pgrx extension is mutated in a run of its
# own, under ONE major, the crate's default (`default = ["pgNN"]`), with
# `pg_test` so its `#[pg_test]` suite runs; which members are pgrx comes
# from gate_surface.py, as for every other pgrx task.
#
# THIS FILE IS THE SEQUENCE; mutants_rust.py READS AND JUDGES (#1486).
# Every step that parses or decides — the reason rule, the default
# major, the listings' counts, which exit statuses mean judged, the
# tally — is a call to the helper, so it comes under the canon's
# coverage figure and its own mutation run.
#
# Prints each survivor (file:line:col: the mutation) and one summary
# line: mutants, killed, survived, excluded. Run as one shard of
# repo-mutants.yml's matrix (MUTANTS_SHARD=k/n), it judges that shard
# alone and, given MUTANTS_SUMMARY_DIR, writes its counts to rust.tsv
# there for the job that states the totals across shards.
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here
# The task this script reports for, where mise named none (#1934).
: "${MISE_TASK_NAME:=audit:mutants}"
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"

label="audit:mutants: rust"
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"
config=".cargo/mutants.toml"

# One shard of the population, `k/n`, when repo-mutants.yml's matrix runs
# the leg; unset, the whole population (a local run). Anything else is
# red rather than read as "whole": a typo would quietly run every mutant
# n times over.
#
# A shard takes every n-th mutant (round-robin), as the Go and Python
# legs do, never cargo-mutants' default contiguous slice. A slice is a
# run of whole files, so one shard gets the slowest crate entire: on
# edtf (#107, run 44840394536) one slice was all edtf-core and took 16
# minutes of mutation, while another, mostly edtf-normalize, took six.
# Listing and run are both cut by these flags, or the plan would name
# mutants the run never tests.
shard="${MUTANTS_SHARD:-}"
where=""
sharding=()
if [[ -n ${shard} ]]; then
  if [[ ! ${shard} =~ ^([0-9]+)/([1-9][0-9]*)$ ]] || ((BASH_REMATCH[1] >= BASH_REMATCH[2])); then
    org_finding "rust" "MUTANTS_SHARD='${shard}' is not k/n with k < n"
    exit 1
  fi
  where=", shard ${shard}"
  sharding=(--shard "${shard}" --sharding round-robin)
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
# would have found.
"${ORG_PYTHON}" "${belt}/mutants_rust.py" reasons "${config}"

skips=$(git grep -n -e 'mutants::skip' -- ':!vendor/**' '*.rs' || true)
if [[ -n ${skips} ]]; then
  IFS=$'\n' read -r -d '' -a detail <<< "${skips}" || true
  org_finding "rust" "#[mutants::skip] in the tree:" "${detail[@]}" \
    "record an equivalent mutant at its site instead: // mutants:equivalent <mutation>: <reason>"
  exit 1
fi

# The site records, and the exclusion each one stands for: a malformed
# record is red here, before anything is built, as a reason is.
records=$(git grep -n -F -e '// mutants:equivalent' -- ':!vendor/**' '*.rs' || true)
derived=$("${ORG_PYTHON}" "${belt}/mutants_rust.py" records <<< "${records}")
excludes=()
while IFS= read -r pattern; do
  if [[ -n ${pattern} ]]; then excludes+=(--exclude-re "${pattern}"); fi
done <<< "${derived}"

command -v cargo > /dev/null || {
  org_finding "rust" "${config} present but cargo missing — pin rust in mise.toml"
  exit 1
}
command -v cargo-mutants > /dev/null || {
  org_finding "rust" "cargo-mutants missing — the belt pins it lazy; install it by name:" \
    "  mise install cargo:cargo-mutants"
  exit 1
}

# Assigned one step each: an empty here-string from a failed derivation
# would read as a workspace with nothing to mutate.
metadata=$(cargo metadata --no-deps --format-version 1 --locked)
surface=$("${ORG_PYTHON}" "${belt}/gate_surface.py" <<< "${metadata}")

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
  "${ORG_PYTHON}" "${belt}/mutants_rust.py" default-major "$1" <<< "${metadata}"
}

# The status survives the cleanup, and a run that died before its last
# statement is red even on bash 3.2, where an unset-variable death under
# an EXIT trap exits 0 (#1233).
rc=0
scratch=$(mktemp -d)
trap 'rc=$?; rm -rf "${scratch}"; [ -n "${org_done:-}" ] || [ "${rc}" -ne 0 ] || rc=1; exit "${rc}"' EXIT

# The run log the verdict replays, one line per event, in order.
runs="${scratch}/runs.tsv"
: > "${runs}"
# The records, which the verdict holds to the --no-config listings.
printf '%s\n' "${records}" > "${scratch}/records.txt"

# One cargo-mutants run: $1 names it, the rest are its selection flags.
# The listings go to the helper, which says what the run mutates and
# whether the shard holds anything; the run's exit status is recorded
# for the verdict, which decides what it means. The records' exclusions
# cut every listing but the --no-config one, which is the population
# they are counted and held against.
mutate() {
  local name="$1" out="${scratch}/$1" status=0
  shift
  mkdir -p "${out}"
  cargo mutants --list --no-config "$@" > "${out}/all.txt"
  cargo mutants --list ${excludes[@]+"${excludes[@]}"} "$@" > "${out}/listed.txt"
  local here=""
  if [[ -n ${shard} ]]; then
    cargo mutants --list "${sharding[@]}" ${excludes[@]+"${excludes[@]}"} "$@" > "${out}/here.txt"
    here="${out}/here.txt"
  fi
  "${ORG_PYTHON}" "${belt}/mutants_rust.py" plan --name "${name}" --where "${where}" --dir "${out}" \
    --all "${out}/all.txt" --listed "${out}/listed.txt" --here "${here}"
  printf 'run\t%s\t%s\n' "${name}" "${out}" >> "${runs}"
  [[ ! -e ${out}/empty ]] || return 0
  cargo mutants --output "${out}" ${sharding[@]+"${sharding[@]}"} ${excludes[@]+"${excludes[@]}"} "$@" || status=$?
  echo "${status}" > "${out}/status"
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
    printf 'red\t%s: %s\n' "${c}" \
      "a pgrx extension whose default features name no single pgNN — it is mutated under its default major" >> "${runs}"
    continue
  fi
  if [[ -z ${provisioned} ]]; then
    provisioned=ok
    # shellcheck source=mise/pgrx-env.sh
    . "${belt}/pgrx-env.sh"
    # shellcheck disable=SC2310  # deliberate: a failed provisioning reds
    # this crate and the run still reports the rest
    org_pgrx_provision || provisioned=failed
  fi
  if [[ ${provisioned} != ok ]]; then
    printf 'red\t%s\n' "${c} at ${major}: no Postgres could be provisioned" >> "${runs}"
    continue
  fi
  # shellcheck disable=SC2310  # deliberate: a missing pin reds this crate
  # and the run still reports the rest
  if ! org_pgrx_darwin_icu_check "${major#pg}"; then
    printf 'red\t%s\n' "${c} at ${major}: no ICU headers for it are pinned (above)" >> "${runs}"
    continue
  fi
  echo "${label}: ${c} is a pgrx extension, mutated under its default major, ${major}"
  # In a subshell holding that major's ICU headers (#1774).
  org_pgrx_with_icu "${major#pg}" mutate "${c}-${major}" --package "${c}" --features pg_test
done

if [[ ${#plain[@]} -eq 0 && ${#pgrx[@]} -eq 0 ]]; then
  org_finding "rust" "${config} tracked but no crate in the workspace"
  exit 1
fi

"${ORG_PYTHON}" "${belt}/mutants_rust.py" verdict "${scratch}" --where "${where}" --shard "${shard:-whole}"
org_done=1
