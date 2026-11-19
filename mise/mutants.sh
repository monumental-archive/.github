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
  echo "audit:mutants: MUTANTS_LEG='${MUTANTS_LEG}' names no leg (mise/mutants-<language>.sh)" >&2
  exit 1
fi
if [[ -n ${red} ]]; then
  echo "audit:mutants: red leg(s):${red}" >&2
  exit 1
fi
