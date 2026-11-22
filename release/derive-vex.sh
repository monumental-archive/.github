#!/usr/bin/env bash
# Derive the release's VEX and gate on undecided advisories:
# publish.yml's `sbom` step, moved out of the workflow when routing its
# refusal through the canon's finding writer grew the block past its
# ceiling (#1934, #1352).
#
# The ecosystems and the author are the assert policy's (blastRadius),
# the document id its vexId template, and the release view's product the
# verify policy's resourceUri (stele#397, #1372). Every other inventory
# in dist/ is covered under its own document name. A gate-class advisory
# with no decision fails here (publish.yml has why).
#
# Run from the job's workspace, with the inventories in dist/. Inputs:
# VERSION, SBOM_BASENAME (empty names the repository), SBOM_CREATED,
# GITHUB_REPOSITORY. Writes dist/<base>-<version>.vex.openvex.json.
set -euo pipefail
: "${VERSION:?}"
: "${SBOM_BASENAME?}"
: "${SBOM_CREATED:?}"
: "${GITHUB_REPOSITORY:?}"
canon="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The canon's finding writer (#1934), in this tree.
# shellcheck source=mise/findings.sh
. "${canon}/mise/findings.sh"
command -v stele > /dev/null || {
  org_finding_annotated "" "stele is not on the runner — it is a belt tool"
  exit 1
}
base="${SBOM_BASENAME:-${GITHUB_REPOSITORY##*/}}"
base="${base#.}"
stele derive vex \
  --policy "${canon}/slsa/assert-policy.json" \
  --verify-policy "${canon}/slsa/verify-policy.json" \
  --inventories dist --release-view "dist/${base}-${VERSION}.spdx.json" \
  --vex "${canon}/security/vex" \
  --repo "${GITHUB_REPOSITORY}" --version "${VERSION}" \
  --released "${SBOM_CREATED}" \
  --out "dist/${base}-${VERSION}.vex.openvex.json"
