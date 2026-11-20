#!/usr/bin/env bash
# The consumer proof's second recipe, run as postgres inside the image
# the `path` Dockerfile builds (release/pgrx-image-proof.sh, #1381), on
# 18 and above:
# the extension loads through PostgreSQL's own two path settings with
# nothing under the system directories — our half of CloudNativePG's
# extension ImageVolume contract, and all we claim.
#
# Quoted heredoc: `$system` and `$libdir` are PostgreSQL's tokens, and
# passed through `pg_ctl -o` the shell eats them (measured), so the
# settings go into postgresql.conf.
set -euo pipefail
# The canon's one writer of workflow commands (#1660): the copy the
# container run mounts and names, or this tree's own on the runner.
# shellcheck source=mise/workflow-command.sh
. "${ORG_WORKFLOW_COMMAND:-$(dirname "${BASH_SOURCE[0]}")/../mise/workflow-command.sh}"
# The first control file in glob order, as `ls | sed -n 1p` picked it.
controls=(/extensions/ext/share/extension/*.control)
if [[ ! -e ${controls[0]} ]]; then
  org_error "no extension control file under /extensions/ext/share/extension"
  exit 1
fi
ext=$(basename "${controls[0]}" .control)
found=$(find /usr/lib/postgresql /usr/share/postgresql -name "${ext}*")
if [[ -n ${found} ]]; then
  org_error "${ext} files under the system directories; the path proof would not prove the path:"
  org_say "${found}"
  exit 1
fi
org_say "no file named ${ext}* under the system directories"
bins=(/usr/lib/postgresql/*/bin)
bin=${bins[0]}
"${bin}/initdb" -D /tmp/pgdata -A trust > /dev/null
cat >> /tmp/pgdata/postgresql.conf << 'CONF'
extension_control_path = '/extensions/ext/share:$system'
dynamic_library_path = '/extensions/ext/lib:$libdir'
CONF
"${bin}/pg_ctl" -D /tmp/pgdata -o "-c listen_addresses=" -w start > /dev/null
psql -c "CREATE EXTENSION ${ext};"
org_say "created extension ${ext} through extension_control_path"
