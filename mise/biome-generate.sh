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
# - every tsconfig.json, the list `lint:types` reads
#   (mise/tsconfig-files.sh, the one selection), each resolved by
#   mise/tsconfig-resolve.sh, the one resolver `lint:tsconfig-dials`
#   calls too (#2133), so `extends` is followed and the text is read by
#   TypeScript's own parser. A project whose resolved
#   `compilerOptions.types` holds `node` gets `noNodejsModules` off
#   under its own directory (#2116).
#
# A tsconfig tsc refuses ends the run by name, with the compiler's
# message: one deleted from the tree, and one tsc cannot parse, which
# `--showConfig` alone would have resolved to a project with no `types`.
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

# shellcheck source=mise/tsconfig-files.sh
. "${ORG_BELT_DIR}/tsconfig-files.sh"
cfgs=() && tsconfig_files cfgs
if [[ -n ${cfgs[0]+set} ]] && ! command -v tsc > /dev/null; then
  org_finding "" "tsc missing from the belt"
  exit 1
fi
resolved=$(mktemp -d)
rc=0
trap 'rc=$?; rm -rf "${resolved}"; [[ -n ${org_done:-} || ${rc} -ne 0 ]] || rc=1; exit "${rc}"' EXIT
projects=()
n=0
for cfg in ${cfgs[@]+"${cfgs[@]}"}; do
  n=$((n + 1))
  bash "${ORG_BELT_DIR}/tsconfig-resolve.sh" "${cfg}" "${resolved}/${n}.json" || exit 1
  projects+=(--tsconfig "${cfg}" "${resolved}/${n}.json")
done
org_tracked ':!vendor/**' "package.json" "*/package.json" \
  | "${ORG_PYTHON}" -I "${ORG_BELT_ROOT}/belt" biome-config \
    --org "${ORG_BELT_DIR}/biome-org.json" \
    --domains "${ORG_BELT_DIR}/biome-domains.tsv" \
    --nursery "${ORG_BELT_DIR}/biome-nursery-domains.tsv" \
    --out "${generated}" ${projects[@]+"${projects[@]}"}
org_done=1
