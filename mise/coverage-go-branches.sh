#!/usr/bin/env bash
# Measure Go condition coverage with gobco: the `go-branches` leg (#1546).
#
# Go's own `go test -cover` counts statements, and an `if` with no
# `else` has no block for the path not taken, so the statement figure
# coverage-measure.sh's `go` leg prints cannot see it. gobco counts each
# condition, every operand of `&&` and `||` included, taken both ways.
#
# Prints `go-branches <percent>` on stdout and nothing else; every
# tool's own output goes to stderr, so a caller may read stdout as data.
# The figure is held as a floor of its own, the `go-branches` line of
# .coverage-floor, under the same law as `rust-branches`:
# audit:coverage-go-branches holds it on the Monday cron and
# coverage:adopt derives it. Never the gate: it re-runs the suite
# instrumented, one package at a time, at 6.15 times `go test`
# (measured on stele).
#
# A helper of its own, not a selector of coverage-measure.sh, so that
# the gate's tool derivation (#1410), which reads every call in the
# scripts a gate task runs, sees gobco called by the audit task alone
# (#1546's ruling).
#
# Invoked directly, never through a nested `mise run` (#82).
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here

# The caller's name, so a remedy printed here names the task the session
# actually ran.
label="${1:-coverage:go-branches}"
# The task this script reports for, where mise named none (#1934).
org_task "${label}"

if ! org_tracked_has go.mod; then
  org_finding "" "no go.mod — the go-branches leg measures a Go module"
  exit 1
fi
if ! command -v go > /dev/null; then
  org_finding "" "go.mod present but go missing — pin go in mise.toml"
  exit 1
fi
gobco_bin=$(command -v gobco) || {
  org_finding "" "gobco missing — the belt pins it lazy; install it by name:" \
    "  mise install go:github.com/rillig/gobco"
  exit 1
}
# The binary itself, never mise's shim: a shim runs gobco under mise's
# own environment, which puts the `go` shim back ahead of the toolchain
# set below (gomu's trap, #1356).
target=$(readlink "${gobco_bin}" || true)
if [[ ${target##*/} == mise ]]; then
  gobco_bin=$(mise which gobco) || {
    # shellcheck disable=SC2016  # the backticks quote a command in prose
    org_finding "" 'gobco resolves to a mise shim and `mise which gobco` names no binary'
    exit 1
  }
fi
# The real Go directory first on PATH. gobco copies the module, and its
# mise.toml with it, to a temporary directory and runs `go test` there;
# through the `go` shim that copy's config is untrusted and every run
# dies "go test .: exit status 1" (measured on stele, #1546).
goroot=$(go env GOROOT)
PATH="${goroot}/bin:${PATH}"
export PATH
resolved=$(command -v go)
if [[ ${resolved} != "${goroot}/bin/go" ]]; then
  org_finding "" "go resolves to ${resolved}, not ${goroot}/bin/go"
  exit 1
fi
# gobco parses the source with the go/parser it was built with, so one
# built with an older Go than the toolchain it runs under may not read
# this toolchain's syntax. The belt builds it with the repository's own
# go; a stale local binary or a cache restored across a Go bump is
# refused by name, as gomu's is (#1356). An unreadable version is older.
# awk reads to the end, never `exit`s at line 1: under pipefail an early
# exit can SIGPIPE `go version -m` mid-write and kill this script with
# nothing on stderr (it reddened this leg's own rows on the runner).
built=$(go version -m "${gobco_bin}" | awk 'NR == 1 { print $2 }')
[[ ${built} =~ ^go[0-9] ]] || built=""
running=$(go env GOVERSION)
if awk -v b="${built}" -v r="${running}" '
  function rel(v, out,    n) {
    if (v !~ /^go[0-9]+\.[0-9]+/) return 0
    sub(/^go/, "", v)
    n = split(v, out, /[^0-9]+/)
    return 1
  }
  BEGIN {
    if (!rel(b, have)) exit 0
    if (!rel(r, need)) exit 1
    for (i = 1; i <= 3; i++) if (have[i] + 0 != need[i] + 0) exit !(have[i] + 0 < need[i] + 0)
    exit 1
  }'; then
  org_finding "" "${gobco_bin} was built with ${built:-an unreadable Go}, older than the ${running} it runs under —" \
    "rebuild it with the repository's go:" \
    "  mise install go:github.com/rillig/gobco"
  exit 1
fi
# One package per call: gobco v1.3.4 panics on more than one ("checking
# multiple packages doesn't work yet"). Default mode, never `-branch`:
# condition coverage counts each operand of `&&` and `||` both ways,
# and `-branch` missed a planted half-taken `a && b` (#1546). Only a
# package's own tests count (there is no -coverpkg), and `select` is
# not covered (gobco's README). gobco works on a copy: the tree is
# never touched.
dirs=$(go list -f '{{.Dir}}' ./...)
covered=0
total=0
while IFS= read -r dir; do
  [[ -n ${dir} ]] || continue
  if ! out=$(cd "${dir}" && "${gobco_bin}" 2>&1); then
    printf '%s\n' "${out}" >&2
    org_finding "" "gobco failed in ${dir#"${PWD}/"} — a suite that does not pass" \
      "under measurement cannot measure a floor"
    exit 1
  fi
  printf '%s\n' "${out}" >&2
  count=$(awk '$1 == "Condition" && $2 == "coverage:" { print $3 }' <<< "${out}")
  if [[ ! ${count} =~ ^[0-9]+/[0-9]+$ ]]; then
    org_finding "" "gobco printed no condition count in ${dir#"${PWD}/"} — a" \
      "measurement that reads nothing must not become a floor"
    exit 1
  fi
  covered=$((covered + ${count%/*}))
  total=$((total + ${count#*/}))
done <<< "${dirs}"
# 0 of 0 is the shape of a run that measured no conditions, never 0%.
if [[ ${total} == 0 ]]; then
  org_finding "" "gobco counted no conditions in any package — a" \
    "measurement that reads nothing must not become a floor"
  exit 1
fi
go_br=$(awk -v c="${covered}" -v t="${total}" 'BEGIN { printf "%.16g\n", c * 100 / t }')
echo "go-branches ${go_br}"
