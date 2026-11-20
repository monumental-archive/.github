#!/usr/bin/env bash
# Emit the wasm-npm inventory plan: which package this artifact ships.
# Moved out of build-wasm-npm.yml when routing its notices through the
# canon's writer grew the block past its ceiling (#1660, #1352).
#
# The document is sbom-npm-<package>: the prefix names what the
# artifact IS (the wasm-npm class's owed assetPrefixes), the closure
# inside stays cargo, what it is MADE OF (#544).
#
# Run from the caller's checkout. Inputs: UNTRUSTED_CRATE_DIR, and
# PLAN_DOC_PREFIX, which the step states where the producer join
# (mise/plan_producers.py) reads it. Writes plan/plan.json and one
# notice per planned inventory.
set -euo pipefail
: "${UNTRUSTED_CRATE_DIR:?}"
: "${PLAN_DOC_PREFIX:?PLAN_DOC_PREFIX is unset — the step states the planned prefix of the class}"
# The canon's one writer of workflow commands (#1660), in this tree.
# shellcheck source=mise/workflow-command.sh
. "$(dirname "${BASH_SOURCE[0]}")/../mise/workflow-command.sh"
meta=$(cargo metadata --no-deps --format-version 1 \
  --manifest-path "${UNTRUSTED_CRATE_DIR}/Cargo.toml")
mp=$(realpath "${UNTRUSTED_CRATE_DIR}/Cargo.toml")
mkdir -p plan
jq --arg mp "${mp}" --arg prefix "${PLAN_DOC_PREFIX}" \
  '[.packages[] | select(.manifest_path == $mp) | .name]
   | if length != 1 then error("crate-dir resolves to \(length) packages") else . end
   | map({class: "wasm-npm", doc: ($prefix + .),
          params: {cargoPackage: .}})' \
  <<< "${meta}" > plan/plan.json
# NUL-delimited, so a field's own newline stays inside its notice; a
# file between, so jq's own status stops the script.
notices=$(mktemp)
jq -j '.[] | "plans inventory \(.doc) for package \(.params.cargoPackage)\u0000"' \
  plan/plan.json > "${notices}"
while IFS= read -r -d '' planned; do
  org_notice "${planned}"
done < "${notices}"
rm -f "${notices}"
