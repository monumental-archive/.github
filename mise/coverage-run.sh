#!/usr/bin/env bash
# The run's measurement directory and its sweep (#1667). Sourced, never
# run: `measured` and coverage-measure.sh share it.
#
# The belt's `[env]` names one directory per `mise run`, as
# ORG_COVERAGE_RUN: `org-coverage-<pid>` under the temporary directory,
# the pid being that run's mise. Every task of the run and every child
# of those tasks sees the same one, with no exec at load, and no earlier
# run's data can mix into it.
#
# Nothing in mise runs "finally": a red `test` ends the run before
# coverage:check reads, so a run's directory can outlive it. The first
# `measured` call in a run, and every reader, removes the siblings of
# this run's directory whose pid no process holds. A live pid is another
# run in progress, whoever owns it, and is left. Only names of exactly
# that shape are touched, and only when this run's own directory has
# that shape too, so a directory named some other way is never swept.
# In CI the directory is under the runner's temp, which the runner
# discards.

# org_coverage_sweep <run directory>
org_coverage_sweep() {
  local run=$1 old pid
  [[ $(basename "${run}") =~ ^org-coverage-[0-9]+$ ]] || return 0
  for old in "$(dirname "${run}")"/org-coverage-*; do
    pid=${old##*/org-coverage-}
    [[ ${pid} =~ ^[0-9]+$ && ${old} != "${run}" ]] || continue
    ps -p "${pid}" > /dev/null 2>&1 || rm -rf -- "${old}"
  done
}
