#!/usr/bin/env bash
# The consumer proof's first recipe, run as root inside the image the
# `copy` Dockerfile builds (release/pgrx-image-proof.sh, #1381): CREATE
# EXTENSION, as postgres, from the files COPY --from put in the stock
# postgres's own directories.
set -euo pipefail
# The first control file in glob order, as `ls | sed -n 1p` picked it.
controls=(/usr/share/postgresql/*/extension/*.control)
if [[ ! -e ${controls[0]} ]]; then
  echo "::error::no extension control file under /usr/share/postgresql/*/extension"
  exit 1
fi
ext=$(basename "${controls[0]}" .control)
su postgres -c "
  set -euo pipefail
  /usr/lib/postgresql/*/bin/initdb -D /tmp/pgdata -A trust > /dev/null
  /usr/lib/postgresql/*/bin/pg_ctl -D /tmp/pgdata -o \"-c listen_addresses=\" -w start > /dev/null
  psql -c \"CREATE EXTENSION ${ext};\"
"
