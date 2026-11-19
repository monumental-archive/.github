#!/usr/bin/env bash
# The Python leg of `audit:mutants` (#1354): would the suite fail if the
# code were wrong? Org canon. mutmut changes the code one site at a time
# and runs the tests that reach it; a mutant the tests still pass is a
# SURVIVOR, and a survivor is a named line to fix.
#
# THE MEASURE IS "NO UNEXPLAINED SURVIVOR", NEVER A SCORE (#1336). Red
# while any mutant is neither killed by an assertion nor excluded, with
# a reason, at its site in the repository.
#
# ADOPTION IS THE TOOL'S OWN CONFIG. A repository adopts by tracking
# `[tool.mutmut]` in pyproject.toml (or `[mutmut]` in setup.cfg), which
# also names the paths to mutate and where the tests are: the roots stay
# the repository's, never the belt's. Without it this skips clean, by
# name.
#
# EVERY EXCLUSION CARRIES ITS REASON, at the site:
#
#   - `# pragma: no mutate` (and its `block` and `start` forms) carries
#     the reason in the same comment, after ` -- `:
#         x = 3  # pragma: no mutate -- the fixture's width, no caller
#     mutmut 3.8.1 reads only the first word after `no mutate`, so the
#     reason never changes what is excluded. `end` closes a region and
#     needs none.
#   - every entry of the config's `do_not_mutate`,
#     `do_not_mutate_patterns` and `only_mutate` lists sits on its own
#     line with a `#` line above it saying why.
#   - `mutate_only_covered_lines = true` is refused: it drops every line
#     no test runs, which is exactly where the survivors are, with no
#     reason anywhere.
#
# WHAT COUNTS. From `mutmut results --all true`, one status per mutant:
# killed, timeout, caught by type check and segfault are killed (the
# suite noticed); survived and "no tests" are survivors — a mutant no
# test reaches is the least explained of all; anything else
# (suspicious, skipped, not checked, interrupted) was never judged and
# is red by name.
#
# EXCLUDED COUNTS SITES, not mutants: mutmut never generates an excluded
# mutant, so what there is to count is the reasoned pragmas and list
# entries. The summary line says so.
#
# mutmut has no sharding (3.8.1 has no option for it), so the leg runs
# whole: a shard other than 0/1 is red, never a silent re-run of
# everything.
#
# THIS FILE IS THE SEQUENCE; mutants_python.py READS AND JUDGES (#1486).
# The config's exclusion lists, the pragma reason rule, the mapping of
# mutmut's states and the line locator are calls to the helper, so they
# come under the canon's coverage figure and its own mutation run.
#
# Prints each survivor (file:line, the mutant, the changed line) and one
# summary line: mutants, killed, survived, excluded.
set -euo pipefail
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"

label="audit:mutants: python"

shard="${MUTANTS_SHARD:-0/1}"
if [[ ${shard} != 0/1 ]]; then
  echo "${label}: MUTANTS_SHARD='${shard}' — mutmut cannot shard; the python jobs run one shard, 0/1" >&2
  exit 1
fi

summary() { # summary <row>: the counts the totals job reads
  [[ -n ${MUTANTS_SUMMARY_DIR:-} ]] || return 0
  mkdir -p "${MUTANTS_SUMMARY_DIR}"
  printf '%s\n' "$1" > "${MUTANTS_SUMMARY_DIR}/python.tsv"
}

config=""
if git ls-files --error-unmatch pyproject.toml > /dev/null 2>&1 \
  && grep -q '^\[tool\.mutmut\]' pyproject.toml; then
  config=pyproject.toml
elif git ls-files --error-unmatch setup.cfg > /dev/null 2>&1 \
  && grep -q '^\[mutmut\]' setup.cfg; then
  config=setup.cfg
fi
if [[ -z ${config} ]]; then
  echo "${label}: no [tool.mutmut] in a tracked pyproject.toml (or [mutmut] in setup.cfg), skipped"
  summary "0/1"$'\t'"skipped"
  exit 0
fi

belt="${ORG_BELT_DIR:?unset — no belt}"
# The status survives the cleanup, and a run that died before its last
# statement is red even on bash 3.2, where an unset-variable death under
# an EXIT trap exits 0 (#1233).
rc=0
scratch=$(mktemp -d)
trap 'rc=$?; rm -rf "${scratch}"; [ -n "${org_done:-}" ] || [ "${rc}" -ne 0 ] || rc=1; exit "${rc}"' EXIT

# Pragmas in every tracked Python file. `git grep` narrows the files;
# the tokenizer decides, since mutmut reads pragmas from COMMENTS only
# and a string that spells one (a test fixture) is not a site.
candidates=$(git grep -l -E 'pragma:.*no mutate' -- ':!vendor/**' '*.py' || true)
: > "${scratch}/pragmas.tsv"
if [[ -n ${candidates} ]]; then
  files=()
  while IFS= read -r f; do files+=("${f}"); done <<< "${candidates}"
  "${ORG_PYTHON}" "${belt}/mutmut_pragmas.py" "${files[@]}" > "${scratch}/pragmas.tsv"
fi
# Every exclusion carries its reason, or this is red before mutmut runs.
excluded=$("${ORG_PYTHON}" "${belt}/mutants_python.py" exclusions "${config}" "${scratch}/pragmas.tsv")

command -v mutmut > /dev/null || {
  echo "${label}: mutmut missing — the belt pins it lazy; install it by name:" >&2
  echo "    mise install pipx:mutmut" >&2
  exit 1
}

# Children of the run can import mutmut (#1354 item 3): a test that
# runs a helper as `python3 helper.py` runs its mutated copy, which
# imports mutmut's trampoline. The hook APPENDS mutmut's site directory
# to sys.path in every Python the run starts; mutants_python.py derives
# the directory from the installed tool.
"${ORG_PYTHON}" "${belt}/mutants_python.py" hook "${scratch}/hook"
status=0
PYTHONPATH="${scratch}/hook${PYTHONPATH:+:${PYTHONPATH}}" mutmut run || status=$?
if [[ ${status} -ne 0 ]]; then
  echo "${label}: mutmut run exited ${status} — nothing judged (a failing unmutated suite exits 1)" >&2
  exit 1
fi

mutmut results --all true > "${scratch}/results.txt"
# Each survivor's diff, in the order the helper names them; the helper
# finds the line in the file, since the diff counts from the function.
"${ORG_PYTHON}" "${belt}/mutants_python.py" survivors "${scratch}/results.txt" > "${scratch}/survivors.txt"
mkdir "${scratch}/shows"
i=0
while IFS= read -r name; do
  i=$((i + 1))
  mutmut show "${name}" > "${scratch}/shows/${i}.diff"
done < "${scratch}/survivors.txt"
"${ORG_PYTHON}" "${belt}/mutants_python.py" verdict "${scratch}/results.txt" "${scratch}/shows" \
  --excluded "${excluded}"
org_done=1
