#!/usr/bin/env bash
# Measure statement coverage, one line per language leg. Org canon — the
# shared half of #652.
#
# `coverage:check` enforces this number and the release path derives the
# floor from it, so there is ONE measurement with one set of flags rather
# than two that agree until they do not. That is not a hypothetical
# shape: a second hardcoded `--workspace` beside coverage:check's drifted
# from it the instant a repository excluded a member, and reddened main
# (#316).
#
# Prints `<leg> <percent>` on stdout, one line per language the tree
# tracks and nothing else; every tool's own output goes to stderr, so a
# caller may read stdout as data. A tree with neither manifest prints
# nothing and exits 0 — measuring is not adoption.
#
# Invoked directly, never through a nested `mise run`: a nested mise
# inside a task races the parallel lint fan-out and fails to resolve its
# own lockfile, which made `mise run ci` unrunnable locally (#82).
set -euo pipefail

# The caller's name, so a remedy printed here names the task the session
# actually ran.
label="${1:-coverage:measure}"

# EVERY tracked language measures its own leg, because the floor is a
# minimum and applying it per language is the only reading that means
# anything: a mixed repository that measured Rust alone once reported
# "floor held" over a language nothing had measured (#392).
if [[ -f go.mod ]]; then
  if ! command -v go > /dev/null; then
    echo "${label}: go.mod present but go missing — pin go in mise.toml" >&2
    exit 1
  fi
  # -coverpkg=./... (#652). Without it a statement exercised only by a
  # NEIGHBOURING package's tests scores uncovered, so the gate enforces a
  # number below what the tests prove — measured at 0.5 points and 51
  # statements on stele, which is a floor's worth of understatement in a
  # repository whose floor is the point.
  go test ./... -coverpkg=./... -coverprofile=coverage.txt -covermode=atomic >&2
  go_pct=$(go tool cover -func=coverage.txt | awk '/^total:/ { sub(/%/, "", $3); print $3 }')
  if [[ -z ${go_pct} ]]; then
    echo "${label}: could not read a total from the Go profile — a" >&2
    echo "  measurement that reads nothing must not become a floor" >&2
    exit 1
  fi
  echo "go ${go_pct}"
fi

if [[ -f Cargo.toml ]]; then
  # No task-time rustup, by rule: a component installed mid-run races
  # every other task driving the toolchain (measured twice on the lab's
  # v0.18.0 release PR, #117). llvm-tools is a declared build input —
  # pinned in the repository's mise.toml, installed before any task runs
  # — and this asserts it with a remedy, never installs it.
  if ! rustup component list --installed 2> /dev/null | grep -q "^llvm-tools"; then
    echo "${label}: llvm-tools component missing — declare it with the" >&2
    echo '  rust pin in mise.toml: rust = { version = "1.xx.x", components = "llvm-tools" }' >&2
    echo "  (installed with the toolchain, before tasks run — never mid-run)" >&2
    exit 1
  fi
  # COVERAGE_EXCLUDE ([env] in the repository's mise.toml): workspace
  # members whose tests cannot run on the gate runner — the lab's lab-pg
  # needs a live postgres and is excluded from `test` for the same reason
  # (#316).
  excludes=""
  for member in ${COVERAGE_EXCLUDE:-}; do excludes="${excludes} --exclude ${member}"; done
  # `--json --summary-only` rather than `--fail-under-lines`: the release
  # path needs the NUMBER, and a leg that only ever answers pass/fail
  # cannot derive a floor. The comparison is unchanged — measured against
  # a fixture, --fail-under-lines 61.5 passes and 61.6 fails at
  # `.data[0].totals.lines.percent` 61.53846153846154, so comparing the
  # extracted percentage is the same test with the number visible.
  #
  # shellcheck disable=SC2086  # ${excludes} is a built '--exclude <member>' list,
  # which cargo must receive as separate arguments
  report=$(cargo llvm-cov --workspace --locked ${excludes} --json --summary-only)
  rust_pct=$(jq -r '.data[0].totals.lines.percent // empty' <<< "${report}")
  if [[ -z ${rust_pct} ]]; then
    echo "${label}: could not read a total from the Rust report — a" >&2
    echo "  measurement that reads nothing must not become a floor" >&2
    exit 1
  fi
  echo "rust ${rust_pct}"
fi
