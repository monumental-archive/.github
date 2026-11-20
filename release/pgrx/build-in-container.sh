#!/usr/bin/env bash
# Build, test and package inside the target Postgres: the
# pgrx-extension build step's docker run, moved out of the workflow
# when mounting the canon's writer of workflow commands grew the block
# past its ceiling (#1660, #1352).
#
# The canon's release/pgrx is mounted read-only at /pgrx and runs as
# plain scripts (#1380): build-root.sh installs the headers at the
# image's own server and sets the PostgreSQL floor (#1157, #1274), then
# becomes postgres for build.sh. The writer is mounted beside them and
# named in ORG_WORKFLOW_COMMAND. Source mounted read-only and copied
# inside: the container must not write into the checkout, and target/
# never enters it.
#
# Run from the caller's checkout. Inputs: PG, RUST_VERSION,
# PGRX_VERSION, AUDITABLE_VERSION, BUILD_IMAGE, UNTRUSTED_VERSION,
# UNTRUSTED_CRATE_DIR. Output: /tmp/out.
set -euo pipefail
: "${BUILD_IMAGE:?}"
: "${UNTRUSTED_VERSION:?}"
: "${UNTRUSTED_CRATE_DIR:?}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
writer="$(cd "${here}/../../mise" && pwd -P)/workflow-command.sh"
epoch=$(git log -1 --pretty=%ct)
mkdir -p /tmp/out
docker run --rm \
  --env PG --env RUST_VERSION --env PGRX_VERSION --env AUDITABLE_VERSION \
  --env BUILD_IMAGE \
  --env VERSION="${UNTRUSTED_VERSION}" \
  --env CRATE_DIR="${UNTRUSTED_CRATE_DIR}" \
  --env SOURCE_DATE_EPOCH="${epoch}" \
  --volume "${PWD}:/src:ro" \
  --volume "${here}:/pgrx:ro" \
  --volume "${writer}:/opt/org/workflow-command.sh:ro" \
  --env ORG_WORKFLOW_COMMAND=/opt/org/workflow-command.sh \
  --volume /tmp/out:/out \
  "${BUILD_IMAGE}" \
  bash /pgrx/build-root.sh
