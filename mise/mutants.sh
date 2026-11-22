#!/usr/bin/env bash
# The body of `audit:mutants` (#1336, property 1), delivered so the task
# body stays under lint:task-shell's ceiling (#1544). Org canon. One leg
# per language, each its own delivered file (mise/mutants-<language>.sh),
# so a language is ONE added `leg` line below; mise/test_task_contract.py
# holds every leg file to exactly one line here.
#
# EVERY LEG RUNS, THEN THE RED ONES ARE NAMED: one language's survivors
# must not hide another's. Unset, MUTANTS_LEG runs every leg; set, it
# selects one (repo-mutants.yml's per-language jobs). A MUTANTS_LEG
# naming no leg is red, never a run of nothing.
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here
# The task this script reports for, where mise named none (#1934).
org_task "audit:mutants"

belt="${ORG_BELT_DIR:?unset — no belt}"
red=""
ran=""
leg() {
  [[ -z ${MUTANTS_LEG:-} || ${MUTANTS_LEG} == "$1" ]] || return 0
  ran+=" $1"
  bash "${belt}/mutants-$1.sh" || red+=" $1"
}
leg go
leg python
leg rust
leg typescript
if [[ -n ${MUTANTS_LEG:-} && -z ${ran} ]]; then
  org_finding "" "MUTANTS_LEG='${MUTANTS_LEG}' names no leg (mise/mutants-<language>.sh)"
  exit 1
fi
if [[ -n ${red} ]]; then
  org_finding "" "red leg(s):${red}"
  exit 1
fi
