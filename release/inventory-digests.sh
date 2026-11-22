#!/usr/bin/env bash
# Inventory each pushed digest from the registry: continuous.yml's
# step, moved out of the workflow when routing its refusal through the
# canon's writer grew the block past its ceiling (#1660, #1352).
#
# trivy exports each per-arch inventory; stele judges both by the one
# admissibility rule (stele#396, #1371). trivy stamps the document name
# with the reference it was given, so the product id IS the digest a
# consumer pulls: derived, never composed.
#
# Run from the job's workspace. Inputs: GITHUB_REPOSITORY, GITHUB_ACTOR,
# GH_TOKEN, GITHUB_OUTPUT, and digests/<arch> from the build legs.
# Writes dist/<arch>.spdx.json and the `subjects` output.
set -euo pipefail
: "${GITHUB_REPOSITORY:?}"
: "${GITHUB_ACTOR:?}"
: "${GH_TOKEN:?}"
: "${GITHUB_OUTPUT:?}"
# The canon's writer of findings (#1934), which brings its one writer
# of workflow commands (#1660), in this tree.
# shellcheck source=mise/findings.sh
. "$(dirname "${BASH_SOURCE[0]}")/../mise/findings.sh"
image="ghcr.io/$(printf '%s' "${GITHUB_REPOSITORY}" | tr '[:upper:]' '[:lower:]')"
printf '%s' "${GH_TOKEN}" | docker login ghcr.io -u "${GITHUB_ACTOR}" --password-stdin
mkdir -p dist
subjects=""
for arch in amd64 arm64; do
  digest=$(cat "digests/${arch}" 2> /dev/null || echo "")
  if [[ ! ${digest} =~ ^sha256:[0-9a-f]{64}$ ]]; then
    org_finding_annotated "" "no ${arch} digest to inventory; refusing to decide about a partial image"
    exit 1
  fi
  trivy image --config /dev/null --format spdx-json --output "dist/${arch}.spdx.json" "${image}@${digest}"
  subjects="${subjects}${subjects:+,}$(jq -r '.name' "dist/${arch}.spdx.json")=dist/${arch}.spdx.json"
done
stele assert inventory dist/amd64.spdx.json dist/arm64.spdx.json
echo "subjects=${subjects}" >> "${GITHUB_OUTPUT}"
