#!/usr/bin/env bash
# Write the generated biome config a repository is checked against. Org
# canon — the mechanism is mise/biome_config.py; this script hands it
# what the repository states.
#
# ONE derivation site, two callers: lint:biome and fix:biome have to see
# one project and one identity, or the fixer writes edits the check then
# judges against a different config (#758). Both used to carry the
# generator call inline; #2116 added a second input to it and the call
# moved here, so the two cannot drift apart.
#
# Two inputs, both read from the tracked tree:
#
# - every package.json, from which the generator refuses a domain the
#   repository has and did not claim (#695);
# - every tsconfig.json, the list `lint:types` reads, each resolved with
#   `tsc -p <config> --showConfig` exactly as `lint:tsconfig-dials`
#   does, so `extends` is followed and the text is read by TypeScript's
#   own parser. A project whose resolved `compilerOptions.types` holds
#   `node` gets `noNodejsModules` off under its own directory (#2116).
#
# A tsconfig tsc refuses ends the run by name, with the compiler's
# message. tsc 7 prints that message (TS5058, a tracked tsconfig deleted
# from the tree) on STDOUT, so both streams are read.
#
# Usage: biome-generate.sh <generated config path>
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here
# The task this script reports for, where mise named none (#1934).
org_task "lint:biome"
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"
: "${ORG_BELT_DIR:?unset — the belt directory (the belt [env] computes it)}"

generated=${1:?usage: biome-generate.sh <generated config path>}

cfgs=() && org_tracked_read cfgs ':!vendor/**' "tsconfig.json" "*/tsconfig.json" "*/*/tsconfig.json" \
  "*/*/*/tsconfig.json"
if [[ -n ${cfgs[0]+set} ]] && ! command -v tsc > /dev/null; then
  org_finding "" "tsc missing from the belt"
  exit 1
fi
resolved=$(mktemp -d)
trap 'rm -rf "${resolved}"' EXIT
projects=()
n=0
for cfg in ${cfgs[@]+"${cfgs[@]}"}; do
  n=$((n + 1))
  if ! tsc -p "${cfg}" --showConfig > "${resolved}/${n}.json" 2> "${resolved}/err"; then
    detail=()
    IFS=$'\n' read -r -d '' -a detail < <(cat "${resolved}/${n}.json" "${resolved}/err") || true
    org_finding "" "${cfg} does not resolve" ${detail[@]+"${detail[@]}"}
    exit 1
  fi
  projects+=(--tsconfig "${cfg}" "${resolved}/${n}.json")
done
org_tracked ':!vendor/**' "package.json" "*/package.json" \
  | "${ORG_PYTHON}" "${ORG_BELT_DIR}/biome_config.py" \
    --org "${ORG_BELT_DIR}/biome-org.json" \
    --domains "${ORG_BELT_DIR}/biome-domains.tsv" \
    --nursery "${ORG_BELT_DIR}/biome-nursery-domains.tsv" \
    --out "${generated}" ${projects[@]+"${projects[@]}"}
