#!/usr/bin/env bash
# Refuse a tag whose version disagrees with the extension crate's
# manifest: the pgrx-extension build's guard, moved out of the
# workflow when routing its refusal through the canon's writer grew
# the block past its debt entry (#1660, #1352).
#
# Run from the caller's checkout, on the runner. Inputs:
# UNTRUSTED_VERSION (the tag's version) and UNTRUSTED_CRATE_DIR. A
# `version.workspace = true` crate reads the workspace's version.
set -euo pipefail
: "${UNTRUSTED_VERSION:?}"
: "${UNTRUSTED_CRATE_DIR:?}"
# The canon's writer of findings (#1934), in this tree, found with no
# command run.
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/../../mise/findings.sh"
unset org__here
manifest=$(
  python3 - "${UNTRUSTED_CRATE_DIR}/Cargo.toml" << 'PY'
import sys
import tomllib

with open(sys.argv[1], "rb") as f:
    t = tomllib.load(f)
v = t.get("package", {}).get("version")
if isinstance(v, dict):
    # version.workspace = true
    with open("Cargo.toml", "rb") as f:
        w = tomllib.load(f)
    v = w["workspace"]["package"]["version"]
print(v)
PY
)
if [[ ${manifest} != "${UNTRUSTED_VERSION}" ]]; then
  org_finding_annotated "" "tag says ${UNTRUSTED_VERSION}, manifest says ${manifest}"
  exit 1
fi
