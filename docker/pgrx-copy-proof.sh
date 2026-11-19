#!/usr/bin/env bash
# The consumer proof's first recipe, run as root inside the image the
# `copy` Dockerfile builds (release/pgrx-image-proof.sh, #1381): CREATE
# EXTENSION, as postgres, from the files COPY --from put in the stock
# postgres's own directories.
#
#   pgrx-copy-proof.sh <extension>
#
# The name is the artifact image's own (#1439), read by the caller from
# the staged tree the image was built from. It is never picked from the
# destination: that directory already holds the stock image's 45-47
# contrib control files, and the first in sort order (adminpack,
# amcheck) is what the proof used to create. PGRX_PROOF_ROOT prefixes
# the system paths so the tests run this file against a planted tree;
# in the image it is unset.
set -euo pipefail
# The canon's one writer of workflow commands (#1660): the copy the
# container run mounts and names, or this tree's own on the runner.
# shellcheck source=mise/workflow-command.sh
. "${ORG_WORKFLOW_COMMAND:-$(dirname "${BASH_SOURCE[0]}")/../mise/workflow-command.sh}"
ext="${1:?the extension the artifact image ships}"
root="${PGRX_PROOF_ROOT:-}"
controls=("${root}"/usr/share/postgresql/*/extension/"${ext}".control)
if [[ ! -e ${controls[0]} ]]; then
  org_error "${ext}.control is not under /usr/share/postgresql/*/extension; the COPY did not place the shipped extension"
  exit 1
fi
su postgres -c "
  set -euo pipefail
  ${root}/usr/lib/postgresql/*/bin/initdb -D /tmp/pgdata -A trust > /dev/null
  ${root}/usr/lib/postgresql/*/bin/pg_ctl -D /tmp/pgdata -o \"-c listen_addresses=\" -w start > /dev/null
  psql -c \"CREATE EXTENSION ${ext};\"
"
echo "created extension ${ext} from the COPY recipe"
