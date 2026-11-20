#!/usr/bin/env bash
# The two steps of coverage:adopt either side of the repository's own
# `test` run (#652, #1667).
#
#   coverage-adopt.sh start   before `test`
#   coverage-adopt.sh write   after `test`: measure the run, write the floor
#
# Adoption is a MEASUREMENT, not a guess, and the measurement is the
# repository's own `test` run through `measured`, which measures only a
# tree whose `.coverage-floor` EXISTS. So `start` creates the floor file
# empty where the tree has none, the task runs `test` by reference, and
# `write` reads that run and writes the floor. Nobody creates a file by
# hand. A run that dies between the two leaves an empty floor, which
# coverage:check refuses by name and the next adopt overwrites.
#
# A run-list reference to a task that does not exist fails, and
# `optional` is not honoured there (measured on mise 2026.11.7), so
# `start` refuses a tree with no `test` task by name, before any floor
# is created.
#
# `write` is the measurement coverage:adopt always made: every leg the
# tree measures, the pgrx majors, the rust-branches line where the
# repository declares its nightly (#1359) and the go-branches line where
# go.mod is (#1546), then coverage_floor.py writes it.
set -euo pipefail
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"
label=coverage:adopt
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: conf.d symlink)}"

case "${1:-}" in
  start)
    if ! mise tasks info test --json > /dev/null 2>&1; then
      cat >&2 << 'REFUSAL'
coverage:adopt: no test task — adoption measures the repository's own test run;
  define one whose test command runs through "${ORG_BELT_DIR}/measured"
REFUSAL
      exit 1
    fi
    [[ -f .coverage-floor ]] || : > .coverage-floor
    ;;
  write)
    applies=$(ORG_COVERAGE_DISCOVER=1 "${belt}/coverage-measure.sh" "${label}" probe)
    if [[ -z ${applies} ]]; then
      echo "${label}: no measurable language (go.mod, Cargo.toml, tracked *.py with a test" \
        "task, package.json with vitest, or a floor recording its legs), nothing to measure"
      exit 0
    fi
    legs=$(ORG_COVERAGE_DISCOVER=1 "${belt}/coverage-measure.sh" "${label}")
    pgrx=$("${belt}/coverage-measure.sh" "${label}" pgrx)
    [[ -z ${pgrx} ]] || legs+=$'\n'${pgrx}
    if [[ -f Cargo.toml && -n ${FUZZ_TOOLCHAIN:-} ]]; then
      legs+=$'\n'$("${belt}/coverage-measure.sh" "${label}" rust-branches)
    elif [[ -f Cargo.toml ]]; then
      echo "${label}: no FUZZ_TOOLCHAIN declared, so no rust-branches line" \
        "(branch coverage needs the dated nightly)"
    fi
    [[ ! -f go.mod ]] || legs+=$'\n'$("${belt}/coverage-go-branches.sh" "${label}")
    "${ORG_PYTHON}" "${belt}/coverage_floor.py" write \
      --provenance "${label}" --adopt <<< "${legs}"
    ;;
  *)
    echo "usage: coverage-adopt.sh start|write" >&2
    exit 2
    ;;
esac
