#!/usr/bin/env bash
# Execute the upgrade from the previous release: the pgrx-extension
# build step, moved out of the workflow when mounting the canon's
# writer of workflow commands grew the block past its ceiling (#1660,
# #1352).
#
# Behavioural, like the smoke: install the previous release's real
# tarball, CREATE EXTENSION at the old version, install the new package
# over it, ALTER EXTENSION UPDATE, and read the version back
# (release/pgrx/upgrade.sh). Skips cleanly when no previous release
# shipped this cell (previous_release.py fetch prints no version).
#
# Run from the caller's checkout. Inputs: ORG_PYTHON, GITHUB_REPOSITORY,
# UNTRUSTED_VERSION, UNTRUSTED_CRATE_DIR, PG, ARCH, SMOKE_IMAGE, and the
# GH_TOKEN previous_release.py's gh reads.
set -euo pipefail
: "${ORG_PYTHON:?}"
: "${GITHUB_REPOSITORY:?}"
: "${UNTRUSTED_VERSION:?}"
: "${UNTRUSTED_CRATE_DIR:?}"
: "${PG:?}"
: "${ARCH:?}"
: "${SMOKE_IMAGE:?}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
writer="$(cd "${here}/../../mise" && pwd -P)/workflow-command.sh"
# The canon's one writer of workflow commands (#1660), in this tree.
# shellcheck source=mise/workflow-command.sh
. "${writer}"
prev=$("${ORG_PYTHON}" "${here}/previous_release.py" fetch \
  --repo "${GITHUB_REPOSITORY}" --version "${UNTRUSTED_VERSION}" \
  --crate-dir "${UNTRUSTED_CRATE_DIR}" --pg "${PG}" --arch "${ARCH}" \
  --tarball /tmp/prev.tar.gz --dest /tmp/prevroot)
[[ -n ${prev} ]] || exit 0
name=$(basename "${UNTRUSTED_CRATE_DIR}" | tr '-' '_')
docker run --rm \
  --env PG --env NAME="${name}" \
  --env PREV="${prev}" --env NEW="${UNTRUSTED_VERSION}" \
  --volume /tmp/prevroot/pkgroot:/prev:ro \
  --volume /tmp/out/pkgroot:/new:ro \
  --volume "${here}:/pgrx:ro" \
  --volume "${writer}:/opt/org/workflow-command.sh:ro" \
  --env ORG_WORKFLOW_COMMAND=/opt/org/workflow-command.sh \
  "${SMOKE_IMAGE}" bash /pgrx/upgrade.sh
org_notice "pg${PG}/${ARCH}: ${prev} -> ${UNTRUSTED_VERSION} upgrade executed"
