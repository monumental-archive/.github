#!/usr/bin/env bash
# The release SBOM, class-shaped (docs/dependency-track.md): derived from
# what the repository actually ships, by detection, never configuration.
#
#   Cargo.lock tracked  -> trivy over the tree at the tag: every PURL
#                          versioned, deterministic, the versionless-PURL
#                          defect unwritable (asserted below, not hoped).
#   go.mod tracked      -> the same trivy path: go.sum pins every module
#                          byte and a 1.17+ go.mod lists the full
#                          transitive closure, so the inventory is
#                          lock-derived and versioned exactly like
#                          Cargo's. (The shipped binaries additionally
#                          carry their own module list, readable with
#                          `go version -m` — asserted at build time by
#                          the go-binary class, stele#7.)
#   no manifest         -> GitHub's dependency-graph export (the canon and
#                          any docs/config-only repository: its
#                          dependencies are actions, which the graph
#                          covers and no lockfile describes).
#
# Inputs: VERSION (required), SBOM_BASENAME (optional; defaults to the
# repository name), GH_TOKEN (fallback path only). Writes into dist/.
set -euo pipefail

[[ -n ${VERSION:-} ]] || {
  echo "::error::VERSION is required"
  exit 1
}
base="${SBOM_BASENAME:-${GITHUB_REPOSITORY##*/}}"
# The canon's own name starts with a dot, and `dist/*` globs and checksum
# loops silently skip dotfiles — measured: every canon release before this
# script shipped without its SBOM, with everything green.
base="${base#.}"
mkdir -p dist
out="dist/${base}-${VERSION}.spdx.json"

cargo_locks=$(git ls-files 'Cargo.lock' '*/Cargo.lock')
go_mods=$(git ls-files 'go.mod' '*/go.mod')
if [[ -n ${cargo_locks} || -n ${go_mods} ]]; then
  trivy fs --config /dev/null --format spdx-json --output "${out}" .
  mode="lock-derived"
else
  gh api "repos/${GITHUB_REPOSITORY}/dependency-graph/sbom" \
    --jq '.sbom' > "${out}"
  mode="dependency-graph"
fi

[[ -s ${out} ]] || {
  echo "::error::SBOM export produced nothing"
  exit 1
}
packages=$(jq '.packages | length' "${out}")
purls=$(jq '[.packages[].externalRefs[]? | select(.referenceType=="purl") | .referenceLocator] | length' "${out}")
bare=$(jq '[.packages[].externalRefs[]? | select(.referenceType=="purl")
  | .referenceLocator | select(test("@") | not)] | length' "${out}")
if [[ ${packages} -eq 0 ]]; then
  echo "::error::SBOM has zero packages — an empty inventory asserts nothing"
  exit 1
fi
if [[ ${mode} == "lock-derived" ]]; then
  # The reason this path exists: a versionless PURL can never match an
  # advisory, so it is silently invisible to every scanner. Fail, never
  # ship an SBOM that under-claims.
  if [[ ${purls} -eq 0 || ${bare} -ne 0 ]]; then
    echo "::error::lock-derived SBOM has ${purls} PURLs, ${bare} versionless"
    exit 1
  fi
else
  # The platform export's PURL quality is GitHub's, not ours: report it
  # honestly rather than fail a release over data we do not derive.
  echo "::notice::dependency-graph SBOM: ${bare} of ${purls} PURLs versionless"
fi
echo "::notice::${mode} SBOM: ${packages} packages, ${purls} PURLs (${out##*/})"
