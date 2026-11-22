#!/usr/bin/env bash
# Unpack the go-binary legs the release SBOM describes: publish.yml's
# `sbom` step, moved out of the workflow when routing its refusal
# through the canon's finding writer grew the block past its ceiling
# (#1934, #1352).
#
# One directory per archive: two legs both ship the same command
# basename, and a flat extraction would silently overwrite one leg with
# another. Run from the job's workspace, with the build artifact's
# archives in shipped-archives/. Inputs: GITHUB_ENV. Writes shipped/<leg>/
# and BINARIES_DIR.
set -euo pipefail
: "${GITHUB_ENV:?}"
# The canon's finding writer (#1934), in this tree.
# shellcheck source=mise/findings.sh
. "$(dirname "${BASH_SOURCE[0]}")/../mise/findings.sh"
found=0
for a in shipped-archives/*.tar.gz; do
  [[ -e ${a} ]] || break
  leg="${a##*/}"
  leg="${leg%.tar.gz}"
  mkdir -p "shipped/${leg}"
  tar -xzf "${a}" -C "shipped/${leg}"
  found=1
done
if [[ ${found} -ne 1 ]]; then
  org_finding_annotated "" "the go-binary build artifact holds no archives"
  exit 1
fi
echo "BINARIES_DIR=${PWD}/shipped" >> "${GITHUB_ENV}"
