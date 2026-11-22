#!/usr/bin/env bash
# Resolve and validate a versioned release's facts: publish.yml's
# `facts` step, moved out of the workflow when routing its refusal
# through the canon's finding writer grew the block past its ceiling
# (#1934, #1352).
#
# stele derives every fact from the released checkout and validates it
# at construction (publish.yml has why). Run from the caller's checkout.
# Inputs: VERSION, IMAGE_TITLE, IMAGE_DESCRIPTION (either may be empty),
# GITHUB_REPOSITORY, GITHUB_SERVER_URL, GITHUB_OUTPUT. Writes the
# `facts` and `epoch` outputs and prints stele's other lines.
set -euo pipefail
: "${VERSION:?}"
: "${IMAGE_TITLE?}"
: "${IMAGE_DESCRIPTION?}"
: "${GITHUB_REPOSITORY:?}"
: "${GITHUB_SERVER_URL:?}"
: "${GITHUB_OUTPUT:?}"
# The canon's finding writer (#1934), in this tree.
# shellcheck source=mise/findings.sh
. "$(dirname "${BASH_SOURCE[0]}")/../mise/findings.sh"
command -v stele > /dev/null || {
  org_finding_annotated "" "stele is not on the runner — it is a belt tool"
  exit 1
}
out=$(stele derive facts \
  --archetype versioned \
  --version "${VERSION}" \
  --repo "${GITHUB_REPOSITORY}" \
  --server-url "${GITHUB_SERVER_URL}" \
  --git-dir . \
  --title "${IMAGE_TITLE}" \
  --description "${IMAGE_DESCRIPTION}")
grep -E '^(facts|epoch)=' <<< "${out}" >> "${GITHUB_OUTPUT}"
grep -Ev '^(facts|epoch)=' <<< "${out}" || true
