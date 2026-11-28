#!/usr/bin/env bash
# The TypeScript leg of `audit:mutants` (#1358): would the suite fail if
# the code were wrong? Org canon. StrykerJS changes the code one site at
# a time and runs the tests that reach it; a mutant the tests still pass
# is a SURVIVOR, and a survivor is a named line to fix.
#
# THE MEASURE IS "NO UNEXPLAINED SURVIVOR", NEVER A SCORE (#1336). Red
# while any survivor stands. Its three outcomes, in order (#1981):
# delete code it proves redundant, kill it with an assertion, or — the
# code must stay and no input can tell — exclude it, with a reason, at
# its site:
#
#   // Stryker disable next-line <Mutator>: <reason>
#
# StrykerJS's own mechanism; the belt delivers the reader, never a
# record. mutants_typescript.py has the classes and the config refusals.
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
# EVERY MUTANT, EVERY WEEK: incremental mode is deferred (ruling on
# #1358). A runner starts with no earlier results, so `incremental` in
# the config would read as on and do nothing, and a tracked incremental
# file would be rewritten by the run and red the clean-tree guard; the
# config check refuses `incremental` by name. Reopen when a full weekly
# run no longer fits the job: a restored and saved incremental file
# outside the tree, in its own issue. The weekly run is sharded instead
# (#2153), which answers the fit; incremental would answer re-running.
#
# SHARDED BY FILE (#2153). StrykerJS has no sharding of its own, but its
# `--mutate` names the files to mutate, so the leg shares them out:
# mutants_typescript.py expands the config's `mutate` list against the
# tracked files, sorts them, and shard k of MUTANTS_SHARD=k/n takes every
# file whose index i has i % n == k, the round-robin cut the other legs
# make. A `mutate` entry with a mutation range (`file:1-5`) is red by
# name: a range cannot be shared out by file. A shard whose share is
# empty runs nothing and counts zero.
#
# PROGRESS IN THE JOB LOG. StrykerJS's output, with its
# `progress-append-only` reporter, is copied to the log as it runs, so a
# shard stopped by its timeout shows how far it got; the scratch copy is
# tailed again on a non-zero exit. A lost runner keeps no log at all
# (#2153), so short shards are the protection there, not this.
#
# Prints each survivor (file:line:column, the mutator, the change) and
# one summary line; given MUTANTS_SUMMARY_DIR, writes typescript.tsv
# there for the job that states the totals. Its excluded count is the
# shard's own files', so the row says `shard` and the totals sum it.
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here
# The task this script reports for, where mise named none (#1934).
org_task "audit:mutants"

label="audit:mutants: typescript"
adoption="stryker.config.json"
report="reports/mutation/mutation.json"

shard="${MUTANTS_SHARD:-0/1}"

summary() { # summary <row>: the counts the totals job reads
  [[ -n ${MUTANTS_SUMMARY_DIR:-} ]] || return 0
  mkdir -p "${MUTANTS_SUMMARY_DIR}"
  printf '%s\n' "$1" > "${MUTANTS_SUMMARY_DIR}/typescript.tsv"
}

configs=() && org_tracked_read configs 'stryker.conf.*' 'stryker.config.*' '.stryker.conf.*'
others=()
for f in ${configs[@]+"${configs[@]}"}; do
  [[ ${f} == "${adoption}" ]] || others+=("${f}")
done
if [[ -n ${others[0]+set} ]]; then
  for item in "${others[@]}"; do
    org_finding "typescript" "a StrykerJS config the leg cannot read: ${item}" \
      "the leg reads ${adoption} only; move the config there"
  done
  exit 1
fi
if ! org_tracked_has "${adoption}"; then
  echo "${label}: no ${adoption} tracked, skipped"
  summary "${shard}"$'\t'"skipped"
  exit 0
fi

if [[ -z ${CI:-} ]]; then
  org_finding "typescript" "CI is unset — mutants run in place with the tree's own environment," \
    "so this leg runs on a runner only (repo-mutants.yml), never a workstation"
  exit 1
fi

belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"
python3 -I "${belt%/*}/belt" mutants-typescript --label "${label}" config "${adoption}"

stryker=node_modules/.bin/stryker
[[ -x ${stryker} ]] || {
  org_finding "typescript" "${adoption} is tracked but ${stryker} is missing — add" \
    "@stryker-mutator/core and its runner plugin to devDependencies, and install"
  exit 1
}
if org_tracked_has "${report}"; then
  org_finding "typescript" "${report} is tracked; it is StrykerJS's output, and the leg replaces it"
  exit 1
fi

dirty=$(git status --porcelain --untracked-files=no)
if [[ -n ${dirty} ]]; then
  while IFS= read -r item; do
    [[ -n ${item} ]] || continue
    org_finding "typescript" "uncommitted: ${item}" "the leg mutates tracked files in place; it runs on a clean tree only"
  done <<< "${dirty}"
  exit 1
fi

rc=0
scratch=$(mktemp -d)
trap 'rc=$?; rm -rf "${scratch}"; [ -n "${org_done:-}" ] || [ "${rc}" -ne 0 ] || rc=1; exit "${rc}"' EXIT

python3 -I "${belt%/*}/belt" mutants-typescript --label "${label}" share "${adoption}" \
  --shard "${shard}" --out "${scratch}/mutate" --summary-dir "${MUTANTS_SUMMARY_DIR:-}"
mutate=$(< "${scratch}/mutate")
if [[ -z ${mutate} ]]; then
  echo "${label}: shard ${shard}: no file to mutate, nothing run"
  org_done=1
  exit 0
fi

rm -f "${report}"
status=0
"${stryker}" run "${adoption}" --inPlace --mutate "${mutate}" \
  --reporters json,progress-append-only 2>&1 | tee "${scratch}/stryker.log" || status=$?

left=$(git status --porcelain --untracked-files=no)
if [[ -n ${left} ]]; then
  while IFS= read -r item; do
    [[ -n ${item} ]] || continue
    org_finding "typescript" "left changed after the run: ${item}" \
      "StrykerJS did not restore the tree it mutated in place; restore it with git"
  done <<< "${left}"
  exit 1
fi
if [[ ${status} -ne 0 ]]; then
  org_finding "typescript" "StrykerJS exited ${status} — nothing judged; its output:"
  tail -n 40 "${scratch}/stryker.log" | sed 's/^/    /' >&2
  exit 1
fi
if [[ ! -s ${report} ]]; then
  org_finding "typescript" "StrykerJS wrote no ${report}"
  exit 1
fi
mv "${report}" "${scratch}/mutation.json"

python3 -I "${belt%/*}/belt" mutants-typescript --label "${label}" \
  verdict "${scratch}/mutation.json" --shard "${shard}" \
  --summary-dir "${MUTANTS_SUMMARY_DIR:-}"
org_done=1
