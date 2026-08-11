#!/usr/bin/env bash
# Published releases are immutable, so a VEX decision made after a release
# cannot attach to it — the raw document rides the NEXT release of each
# affected repository instead (roll-forward, like everything else). This
# collects every statement in the canon's security/vex/ whose products
# name this repository into dist/, where the attach job publishes it as a
# release asset for consuming tools to eat. The signed claim in the
# attestation store (vex-attest.yml) is the query surface; this is the
# document surface.
#
# Inputs: CANON_DIR (default .org-canon; the canon repo passes "." when
# releasing itself). Writes into dist/.
set -euo pipefail

canon="${CANON_DIR:-.org-canon}"
vexdir="${canon}/security/vex"
repo="${GITHUB_REPOSITORY##*/}"
[[ -d ${vexdir} ]] || {
  echo "::notice::no ${vexdir}; nothing to collect"
  exit 0
}
mkdir -p dist
n=0
for f in "${vexdir}"/*.openvex.json; do
  [[ -f ${f} ]] || continue
  if jq -e --arg repo "${repo}" '
    [.statements[].products[]?["@id"] // empty]
    | any(startswith("pkg:github/monumental-archive/" + $repo + "@"))
  ' "${f}" > /dev/null; then
    cp "${f}" dist/
    n=$((n + 1))
  fi
done
echo "::notice::${n} VEX document(s) ride this release"
