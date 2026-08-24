#!/usr/bin/env bash
# Render CITATION.cff from REUSE.toml. Org canon — the enforcement half
# lives in lint:citation.
#
# ONE derivation site, two callers, which is why this is a script and not
# a task body (#82, #764). lint:citation used to obtain the derivation by
# running `mise run fix:citation` from inside a task; that nested mise
# raced the ~40 lint tasks `ci` runs in parallel and failed to resolve its
# own lockfile — the failure #82 recorded, which made `mise run ci`
# unrunnable locally and forced --no-verify on every push. lint:citation
# is universal, so every repository carrying a CITATION.cff inherited the
# race, and a red naming no finding is the least debuggable failure a gate
# can produce. A derivation both callers invoke directly has no such race,
# and as an ordinary .sh it also comes under shellcheck and shfmt.
#
# The derivation contract is unchanged and is not this file's to restate:
# REUSE.toml is the source, and lint:citation compares the render to the
# committed file byte for byte. What each field is and why the two human
# ones survive regeneration is written at fix:citation, the task that
# names this script.
#
# Output: CITATION_OUT names a file to write the render to (what
# lint:citation wants); unset, it renders CITATION.cff in place.
set -euo pipefail

out="${CITATION_OUT:-CITATION.cff}"
[[ -f REUSE.toml ]] || {
  echo "fix:citation: no REUSE.toml, nothing to derive from"
  exit 0
}
lic=$(grep -h "SPDX-License-Identifier" REUSE.toml | sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/' | sort -u)
if [[ -z ${lic} || ${lic} == "<SPDX expression>" ]]; then
  echo "fix:citation: REUSE.toml declares no licence expression — fill it first (lint:licence)" >&2
  exit 1
fi
count=$(wc -l <<< "${lic}" | tr -d " ")
if [[ ${count} != "1" ]]; then
  echo "fix:citation: REUSE.toml carries more than one licence expression — one citation cannot state two:" >&2
  echo "${lic}" >&2
  exit 1
fi
supplier=$(grep "^SPDX-PackageSupplier" REUSE.toml | sed -n 1p | sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/')
person="${supplier%% <*}"
if [[ -z ${person} ]]; then
  echo "fix:citation: REUSE.toml carries no SPDX-PackageSupplier — the citation needs an author" >&2
  exit 1
fi
family="${person##* }"
given="${person% "${family}"}"
repo_url=$(grep "^SPDX-PackageDownloadLocation" REUSE.toml | sed -n 1p | sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/')
[[ -n ${repo_url} ]] || {
  echo "fix:citation: REUSE.toml carries no SPDX-PackageDownloadLocation" >&2
  exit 1
}
title="${repo_url##*/}"
version="0.0.0"
released=$(git log -1 --format=%cs 2> /dev/null || date -u +%F)
concept=""
if [[ -f CITATION.cff ]]; then
  t=$(sed -n "s/^title: //p" CITATION.cff | sed -n 1p)
  [[ -n ${t} ]] && title="${t}"
  v=$(sed -n "s/^version: //p" CITATION.cff | sed -n 1p)
  [[ -n ${v} ]] && version="${v}"
  d=$(sed -n 's/^date-released: "\{0,1\}\([0-9-]*\)"\{0,1\}$/\1/p' CITATION.cff | sed -n 1p)
  [[ -n ${d} ]] && released="${d}"
  concept=$(awk '/^identifiers:/{f=1} f && /value:/{print $NF; exit}' CITATION.cff)
fi
{
  echo "# Derived file (#316): fix:citation renders licence, authors and"
  echo "# repository-code from REUSE.toml; the release pipeline stamps"
  echo "# version and date-released; title and the concept-DOI identifier"
  echo "# are the two human fields, preserved on regeneration."
  echo "# lint:citation reddens hand drift. Edit REUSE.toml, not this."
  echo "cff-version: 1.2.0"
  echo "message: If you use this software, please cite it using these metadata."
  echo "title: ${title}"
  echo "type: software"
  echo "authors:"
  echo "  - family-names: ${family}"
  echo "    given-names: ${given}"
  echo "repository-code: ${repo_url}"
  echo "license: ${lic}"
  echo "version: ${version}"
  # Unquoted deliberately: release/prepare-release.sh's sed rewrites the
  # whole line unquoted at every release, and the render must byte-match
  # what the stamper writes or lint:citation reddens every release PR
  # (measured on canon v1.20.0's).
  echo "date-released: ${released}"
  if [[ -n ${concept} ]]; then
    echo "identifiers:"
    echo "  - type: doi"
    echo "    value: ${concept}"
    echo "    description: The concept DOI of the software."
  fi
} > "${out}"
echo "fix:citation: rendered ${out} (licence ${lic}, author ${person})"
