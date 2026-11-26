#!/usr/bin/env bash
# Resolve one tsconfig.json with the belt's tsc, or refuse it with the
# compiler's own message. Org canon (#2133): the ONE place a belt task
# asks tsc what a tsconfig says. `lint:tsconfig-dials` and
# mise/biome-generate.sh (`lint:biome`, `fix:biome`) both call it; each
# used to carry its own copy, and both copies trusted `--showConfig`'s
# exit status.
#
# That status says nothing. Measured on tsc 7.0.2: `tsc -p <config>
# --showConfig` exits 0 on a tsconfig it cannot parse (`{`, an unknown
# option, a bad enum value, a value of the wrong type), prints whatever
# keys it could read and no diagnostic. A config with `"strict": true`
# beside an unknown option resolves to `strict` alone, so a reader of
# the document sees a valid project. Two causes, measured side by side
# with typescript@6.0.3 on 2026-11-26:
#
# - An option error (an unknown option, a bad enum value, a wrong type,
#   a missing `extends` target) is new in 7: 6.0.3 prints the error and
#   exits 1, while tsgo returns success after printing without looking
#   at the parse's errors (microsoft/typescript-go
#   internal/execute/tsc.go, at 7.0.2 and at main).
# - A JSON syntax error (`{`) exits 0 in both, so the second run below
#   is needed whatever tsc a future pin brings.
#
# So the config is asked twice:
#
# 1. `--showConfig` writes the resolved document. Its one refusal,
#    TS5058 (the path does not exist), is printed on STDOUT, so both
#    streams go into the finding.
# 2. A second run asks whether the config PARSES, and nothing else. It
#    loads a scratch tsconfig that `extends` the real one over a program
#    of one empty declaration file, so the diagnostics tsc can raise are
#    the config's own, through every `extends` above it. What is left
#    out on purpose, because `lint:types` owns it and each is a fact
#    about the tree rather than the config:
#      - the project's sources (a syntax error in one is not a bad
#        config, and `include` matching nothing is TS18003);
#      - its type packages (`--types ''`: a project naming `node` with
#        no node_modules installed is TS2688, and a fixer run before an
#        install must still resolve);
#      - its imports (`--noResolve`).
#    `--listFilesOnly` stops before checking, so the run costs the
#    parse: 40ms a config measured, against 10ms for `--showConfig`.
#
# The verdict is that run's exit status, never a reading of its text.
# The text is only shown: its `error TS` lines, without the file list
# the flag prints beside them.
#
# Usage: tsconfig-resolve.sh <tsconfig> <resolved document path>
# Exit 0 with the document written; exit 1 with one finding,
# `<tsconfig> does not resolve`, reported under the caller's task.
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here
# The task this script reports for, where mise named none (#1979).
org_task "tsconfig-resolve"

cfg=${1:?usage: tsconfig-resolve.sh <tsconfig> <resolved document path>}
out=${2:?usage: tsconfig-resolve.sh <tsconfig> <resolved document path>}

work=$(mktemp -d)
rc=0
trap 'rc=$?; rm -rf "${work}"; [[ -n ${org_done:-} || ${rc} -ne 0 ]] || rc=1; exit "${rc}"' EXIT

# Print the finding from tsc's output in a file, and end.
refuse() {
  local said detail=()
  said=$(grep -E 'error TS[0-9]+:' "$1" || true)
  [[ -n ${said} ]] || said=$(< "$1")
  IFS=$'\n' read -r -d '' -a detail <<< "${said}" || true
  org_finding "" "${cfg} does not resolve" ${detail[@]+"${detail[@]}"}
  exit 1
}

if ! tsc -p "${cfg}" --showConfig > "${out}" 2> "${work}/err"; then
  cat "${out}" "${work}/err" > "${work}/said"
  refuse "${work}/said"
fi

base=${cfg}
[[ ${base} == /* ]] || base=${PWD}/${base}
: > "${work}/stub.d.ts"
jq -n --arg base "${base}" '{extends: $base, files: ["./stub.d.ts"], include: []}' > "${work}/tsconfig.json"
if ! tsc -p "${work}/tsconfig.json" --listFilesOnly --noResolve --types '' > "${work}/said" 2>&1; then
  refuse "${work}/said"
fi
org_done=1
