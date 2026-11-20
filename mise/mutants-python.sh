#!/usr/bin/env bash
# The Python leg of `audit:mutants` (#1354, #1771): would the suite fail
# if the code were wrong? Org canon. cosmic-ray changes the TRACKED FILE
# IN PLACE, one mutant at a time, and runs the repository's own tests
# against it; a mutant the tests still pass is a SURVIVOR, and a
# survivor is a named line to fix. No copy of the tree, no rewritten
# imports, nothing a child process must be handed: a helper run as a
# script in a child is judged like one a test imports.
#
# THE MEASURE IS "NO UNEXPLAINED SURVIVOR", NEVER A SCORE (#1336). Red
# while any mutant is neither killed by an assertion nor excluded, with
# a reason, at its site in the repository.
#
# ADOPTION IS `cosmic-ray.toml`, TRACKED AT THE REPOSITORY'S ROOT:
# cosmic-ray's own `[cosmic-ray]` table (`module-path`,
# `excluded-modules`, `test-command`, which runs ONE test file: the leg
# fills `{test}`, `{dir}` and `{name}`) and the leg's `[audit-mutants]`
# (`tests`, git pathspecs for the test files). The roots and the suite
# stay the repository's. Without it this skips clean, by name.
#
# EVERY EXCLUSION CARRIES ITS REASON, at the site, on the mutant's first
# line:
#     x = 3  # pragma: no mutate -- the fixture's width, no caller
# Only the line form exists: `block`, `start` and `end` are red by name.
#
# THE PLAN, once per run: a dirty tracked tree under `module-path` is
# refused; every mutant is listed and sorted; each test file is run
# alone under coverage, for its time and the lines it executes (child
# processes included). A mutant's tests are the files that execute its
# first line, fastest first; none is "no tests", and nothing runs. Each
# is limited to three times its planned time, at least 10 s, and
# running over is a kill. The module's cached bytecode is removed around
# each mutant, and the file must equal git's afterwards.
#
# WHAT COUNTS: killed, incompetent and a time limit are killed;
# survived and "no tests" are survivors; any other worker outcome was
# never judged and is red by name. EXCLUDED counts the mutants on
# reasoned pragma lines, across the whole population.
#
# SHARDS: MUTANTS_SHARD=k/N judges every mutant whose position in the
# sorted list is k modulo N; 0/1 is all. `plan` writes the plan into
# MUTANTS_PLAN and stops (repo-mutants.yml's plan job); a shard with
# MUTANTS_PLAN set judges from the plan there. Unset, the plan is made
# in scratch first. One mutant at a time per checkout, always.
#
# AN EXIT TRAP restores any file under `module-path` that differs from
# git, whatever ended the run, once the clean-tree check has passed.
#
# THIS FILE IS THE SEQUENCE; mutants_python.py READS AND JUDGES (#1486).
set -euo pipefail
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"

label="audit:mutants: python"
shard="${MUTANTS_SHARD:-0/1}"

summary() { # summary <row>: the counts the totals job reads
  [[ -n ${MUTANTS_SUMMARY_DIR:-} ]] || return 0
  mkdir -p "${MUTANTS_SUMMARY_DIR}"
  printf '%s\n' "$1" > "${MUTANTS_SUMMARY_DIR}/python.tsv"
}

config=cosmic-ray.toml
if ! git ls-files --error-unmatch "${config}" > /dev/null 2>&1; then
  echo "${label}: no ${config} tracked at the repository's root, skipped"
  [[ ${shard} == plan ]] || summary "${shard}"$'\t'"skipped"
  exit 0
fi

belt="${ORG_BELT_DIR:?unset — no belt}"
helper=("${ORG_PYTHON}" "${belt}/mutants_python.py")
listed=$("${helper[@]}" paths "${config}")
paths=()
while IFS= read -r p; do paths+=("${p}"); done <<< "${listed}"

# Restores only after the clean-tree check passed: before it, a
# difference is the user's own edit, never the leg's mutant.
restore() {
  [[ -n ${mutating:-} ]] || return 0
  local f changed
  changed=$(git diff --name-only -- "${paths[@]}") || changed=""
  [[ -n ${changed} ]] || return 0
  while IFS= read -r f; do
    git restore -- "${f}"
    echo "${label}: ${f} differed from git at exit — restored" >&2
  done <<< "${changed}"
}
# The status survives the cleanup, and a run that died before its last
# statement is red even on bash 3.2, where an unset-variable death under
# an EXIT trap exits 0 (#1233).
rc=0
scratch=$(mktemp -d)
trap 'rc=$?; restore; rm -rf "${scratch}"; [ -n "${org_done:-}" ] || [ "${rc}" -ne 0 ] || rc=1; exit "${rc}"' EXIT

# Pragmas in every tracked Python file. `git grep` narrows the files;
# the tokenizer decides, since a string that spells one (a test fixture)
# is not a site. Every exclusion carries its reason, or this is red
# before anything runs.
candidates=$(git grep -l -E 'pragma:.*no mutate' -- ':!vendor/**' '*.py' || true)
: > "${scratch}/pragmas.tsv"
if [[ -n ${candidates} ]]; then
  files=()
  while IFS= read -r f; do files+=("${f}"); done <<< "${candidates}"
  "${ORG_PYTHON}" "${belt}/mutmut_pragmas.py" "${files[@]}" > "${scratch}/pragmas.tsv"
fi
"${helper[@]}" pragmas "${scratch}/pragmas.tsv"

command -v cosmic-ray > /dev/null || {
  echo "${label}: cosmic-ray missing — the belt pins it lazy; install it by name:" >&2
  echo "    mise install pipx:cosmic-ray" >&2
  exit 1
}

plan="${MUTANTS_PLAN:-${scratch}/plan}"
if [[ ${shard} == plan ]]; then
  : "${MUTANTS_PLAN:?unset — MUTANTS_SHARD=plan writes the plan there}"
  "${helper[@]}" plan "${config}" "${plan}"
  org_done=1
  exit 0
fi
if [[ -z ${MUTANTS_PLAN:-} ]]; then
  "${helper[@]}" plan "${config}" "${plan}"
else
  # The plan job checked its own checkout; a shard checks its own.
  changed=$(git status --porcelain --untracked-files=no -- "${paths[@]}")
  if [[ -n ${changed} ]]; then
    echo "${label}: the tracked tree differs from git under module-path — refused" >&2
    exit 1
  fi
fi

mutating=1
"${helper[@]}" judge "${plan}" "${scratch}/pragmas.tsv" "${shard}" "${scratch}/runs"
org_done=1
