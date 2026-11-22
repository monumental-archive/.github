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
# The canon's finding writer (#1934): the copy the container run mounts
# and names, beside the workflow-command writer it sources, or this
# tree's own on the runner, found with no command run.
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${ORG_FINDINGS_WRITER:-${org__here}/../mise/findings.sh}"
unset org__here
ext="${1:?the extension the artifact image ships}"
root="${PGRX_PROOF_ROOT:-}"
controls=("${root}"/usr/share/postgresql/*/extension/"${ext}".control)
if [[ ! -e ${controls[0]} ]]; then
  org_finding_annotated "${ext}" \
    "${ext}.control is not under /usr/share/postgresql/*/extension; the COPY did not place the shipped extension"
  exit 1
fi
su postgres -c "
  set -euo pipefail
  ${root}/usr/lib/postgresql/*/bin/initdb -D /tmp/pgdata -A trust > /dev/null
  ${root}/usr/lib/postgresql/*/bin/pg_ctl -D /tmp/pgdata -o \"-c listen_addresses=\" -w start > /dev/null
  psql -c \"CREATE EXTENSION ${ext};\"
"
org_say "created extension ${ext} from the COPY recipe"
