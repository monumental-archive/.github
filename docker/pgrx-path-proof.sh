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
# The canon's finding writer (#1934): the copy the container run mounts
# and names, beside the workflow-command writer it sources, or this
# tree's own on the runner, found with no command run.
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${ORG_FINDINGS_WRITER:-${org__here}/../mise/findings.sh}"
unset org__here
# The first control file in glob order, as `ls | sed -n 1p` picked it.
controls=(/extensions/ext/share/extension/*.control)
if [[ ! -e ${controls[0]} ]]; then
  org_finding_annotated "" "no extension control file under /extensions/ext/share/extension"
  exit 1
fi
ext=$(basename "${controls[0]}" .control)
found=$(find /usr/lib/postgresql /usr/share/postgresql -name "${ext}*")
if [[ -n ${found} ]]; then
  files=()
  while IFS= read -r file; do
    files+=("${file}")
  done <<< "${found}"
  org_finding_annotated "${ext}" \
    "${ext} files under the system directories; the path proof would not prove the path:" "${files[@]}"
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
