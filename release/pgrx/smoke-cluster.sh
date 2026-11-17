#!/usr/bin/env bash
# The smoke's postgres half (#1380): a throwaway cluster on a socket
# only, CREATE EXTENSION, and the version it installed, read back.
set -euo pipefail
: "${PG:?}"
: "${EXT:?}"
"/usr/lib/postgresql/${PG}/bin/initdb" -D /tmp/pgdata -A trust > /dev/null
"/usr/lib/postgresql/${PG}/bin/pg_ctl" -D /tmp/pgdata -o "-c listen_addresses=" -w start > /dev/null
psql -c "CREATE EXTENSION ${EXT};"
psql -c "SELECT extversion FROM pg_extension WHERE extname = '${EXT}';"
