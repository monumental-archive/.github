#!/usr/bin/env bash
# The TypeScript leg of `audit:mutants` (#1358): would the suite fail if
# the code were wrong? Org canon. StrykerJS changes the code one site at
# a time and runs the tests that reach it; a mutant the tests still pass
# is a SURVIVOR, and a survivor is a named line to fix.
#
# THE MEASURE IS "NO UNEXPLAINED SURVIVOR", NEVER A SCORE (#1336). Red
# while any mutant is neither killed by an assertion nor excluded, with
# a reason, at its site:
#
#   // Stryker disable next-line <Mutator>: <reason>
#
# StrykerJS's own mechanism; the belt delivers the reader, never a
# record. mutants-typescript.py has the classes and the config refusals.
#
# ADOPTION IS THE TOOL'S OWN CONFIG: a tracked root `stryker.config.json`,
# which names the paths to mutate (`mutate`) and the test runner. Without
# it this skips clean, by name. JSON only, so the leg can read it: a
# tracked StrykerJS config in any other form is red, never a skip.
#
# STRYKERJS IS THE REPOSITORY'S, from its own lockfile, under its own
# npm provenance policy: `@stryker-mutator/core` and the runner plugin in
# devDependencies, run from `node_modules/.bin`. The belt pins no second
# copy. Under pnpm the config must name the runner in `plugins`: core,
# isolated in `.pnpm/`, cannot see its sibling (measured, 10.1.1, #1358).
#
# IN PLACE, NEVER IN THE SANDBOX (`--inPlace`). StrykerJS's sandbox
# copies the working folder, ignoring .gitignore, so a `.env` in the
# checkout is copied; and it links `node_modules` back to the ORIGINAL by
# absolute path, so a workspace package imported by name never sees its
# mutant: 144 of 144 "survived" on a measured file, against 106 killed in
# place (#1358). In place, nothing is copied and every import sees the
# mutant. StrykerJS restores each file after; the leg refuses a tree with
# uncommitted tracked changes before the run, and is red if one remains
# after it. The wrapper passes `--inPlace` itself; the repository cannot
# turn it off through the leg (ruling on #1358).
#
# A RUNNER ONLY: refused, by name, where `CI` is unset. In place, every
# mutant runs with whatever environment the tree holds, so mutated code
# never runs beside a workstation's live `.env` (ruling on #1358).
#
# STRYKERJS EXITS 0 WHATEVER SURVIVES (no `thresholds.break`, which the
# config may not set), so the verdict is read from its JSON report, never
# its exit status; a non-zero exit is a run that judged nothing, and red.
#
# Incremental mode is the repository's (`incremental` in its config, the
# incremental file tracked), switched on once its survivors reach zero:
# the report still names every mutant, reused or re-run.
#
# StrykerJS has no sharding, so the leg runs whole: a shard other than
# 0/1 is red, never a silent re-run of everything.
#
# Prints each survivor (file:line:column, the mutator, the change) and
# one summary line; given MUTANTS_SUMMARY_DIR, writes typescript.tsv
# there for the job that states the totals.
set -euo pipefail

label="audit:mutants: typescript"
adoption="stryker.config.json"
report="reports/mutation/mutation.json"

shard="${MUTANTS_SHARD:-0/1}"
if [[ ${shard} != 0/1 ]]; then
  echo "${label}: MUTANTS_SHARD='${shard}' — StrykerJS cannot shard; the typescript job runs one shard, 0/1" >&2
  exit 1
fi

summary() { # summary <row>: the counts the totals job reads
  [[ -n ${MUTANTS_SUMMARY_DIR:-} ]] || return 0
  mkdir -p "${MUTANTS_SUMMARY_DIR}"
  printf '%s\n' "$1" > "${MUTANTS_SUMMARY_DIR}/typescript.tsv"
}

others=$(git ls-files -- 'stryker.conf.*' 'stryker.config.*' '.stryker.conf.*' \
  | grep -v -x -F "${adoption}" || true)
if [[ -n ${others} ]]; then
  printf '%s\n' "${others}" | sed "s/^/${label}: a StrykerJS config the leg cannot read: /" >&2
  echo "  the leg reads ${adoption} only; move the config there" >&2
  exit 1
fi
if ! git ls-files --error-unmatch -- "${adoption}" > /dev/null 2>&1; then
  echo "${label}: no ${adoption} tracked, skipped"
  summary "0/1"$'\t'"skipped"
  exit 0
fi

if [[ -z ${CI:-} ]]; then
  echo "${label}: CI is unset — mutants run in place with the tree's own environment," >&2
  echo "  so this leg runs on a runner only (repo-mutants.yml), never a workstation" >&2
  exit 1
fi

belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: conf.d symlink)}"
python3 "${belt}/mutants-typescript.py" --label "${label}" config "${adoption}"

stryker=node_modules/.bin/stryker
[[ -x ${stryker} ]] || {
  echo "${label}: ${adoption} is tracked but ${stryker} is missing — add" >&2
  echo "  @stryker-mutator/core and its runner plugin to devDependencies, and install" >&2
  exit 1
}
if git ls-files --error-unmatch -- "${report}" > /dev/null 2>&1; then
  echo "${label}: ${report} is tracked; it is StrykerJS's output, and the leg replaces it" >&2
  exit 1
fi

dirty=$(git status --porcelain --untracked-files=no)
if [[ -n ${dirty} ]]; then
  printf '%s\n' "${dirty}" | sed "s/^/${label}: uncommitted: /" >&2
  echo "  the leg mutates tracked files in place; it runs on a clean tree only" >&2
  exit 1
fi

rc=0
scratch=$(mktemp -d)
trap 'rc=$?; rm -rf "${scratch}"; [ -n "${org_done:-}" ] || [ "${rc}" -ne 0 ] || rc=1; exit "${rc}"' EXIT

rm -f "${report}"
status=0
"${stryker}" run "${adoption}" --inPlace --reporters json > "${scratch}/stryker.log" 2>&1 || status=$?

left=$(git status --porcelain --untracked-files=no)
if [[ -n ${left} ]]; then
  printf '%s\n' "${left}" | sed "s/^/${label}: left changed after the run: /" >&2
  echo "  StrykerJS did not restore the tree it mutated in place; restore it with git" >&2
  exit 1
fi
if [[ ${status} -ne 0 ]]; then
  echo "${label}: StrykerJS exited ${status} — nothing judged; its output:" >&2
  tail -n 40 "${scratch}/stryker.log" | sed 's/^/    /' >&2
  exit 1
fi
if [[ ! -s ${report} ]]; then
  echo "${label}: StrykerJS wrote no ${report}" >&2
  exit 1
fi
mv "${report}" "${scratch}/mutation.json"

python3 "${belt}/mutants-typescript.py" --label "${label}" \
  verdict "${scratch}/mutation.json" --summary-dir "${MUTANTS_SUMMARY_DIR:-}"
org_done=1
