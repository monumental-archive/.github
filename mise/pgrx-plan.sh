#!/usr/bin/env bash
# Which Postgres majors the gate's fan-out owes a leg, as a JSON array on
# stdout (`[]` when the tree has no pgrx extension). Org canon — the
# planning half of #1148, run by ci.yml's `plan` job.
#
# CHEAP ON PURPOSE, AND THEREFORE NOT TRUSTED. A matrix has to exist
# before its jobs do, so this runs first and alone, and installing the
# pinned toolchain to answer one question would cost every pull request
# a minute before any leg could start. It uses the runner's own cargo and
# python3 instead — but only to PLAN. The answer is checked by the gate's
# main job, where `pgrx-test.sh` derives the same list from the pinned
# toolchain and reds on any difference, so an unpinned tool can make a
# gate fail and can never make one pass.
#
# The derivation itself is gate-surface.py's, the one every other
# consumer uses. No major is written here.
set -euo pipefail

canon="${1:?usage: pgrx-plan.sh <canon tree>}"

if [[ ! -f Cargo.toml ]]; then
  echo "[]"
  exit 0
fi
for tool in cargo python3 jq; do
  command -v "${tool}" > /dev/null || {
    echo "pgrx-plan: ${tool} is not on this runner, so the fan-out cannot be planned" >&2
    exit 1
  }
done

metadata=$(cargo metadata --no-deps --format-version 1 --locked)
surface=$(python3 "${canon}/mise/gate-surface.py" <<< "${metadata}")
majors=""
while IFS=$'\t' read -r record _spec kind crate_majors _dir; do
  [[ ${record} == crate && ${kind} == pgrx ]] || continue
  majors="${majors}${majors:+,}${crate_majors}"
done <<< "${surface}"

list=$(tr -c '0-9' '\n' <<< "${majors}" | sed '/^$/d' | sort -un)
jq -cnR '[inputs | select(length > 0)]' <<< "${list}"
