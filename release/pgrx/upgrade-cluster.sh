#!/usr/bin/env bash
# The upgrade's postgres half (#1380). `create` starts a throwaway
# cluster on a socket only and creates NAME at PREV; `update` moves the
# running installation to NEW and refuses any other version read back.
#
#   upgrade-cluster.sh create|update
set -euo pipefail
# The canon's writer of findings (#1934), which brings its one writer of
# workflow commands (#1660): the copy the container run mounts and
# names, beside its writer of workflow commands, or this tree's own on
# the runner, found with no command run.
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${ORG_FINDINGS_WRITER:-${org__here}/../../mise/findings.sh}"
unset org__here
: "${PG:?}"
: "${NAME:?}"
: "${PREV:?}"
: "${NEW:?}"
case $1 in
  create)
    "/usr/lib/postgresql/${PG}/bin/initdb" -D /tmp/pgdata -A trust > /dev/null
    "/usr/lib/postgresql/${PG}/bin/pg_ctl" -D /tmp/pgdata -o "-c listen_addresses=" -w start > /dev/null
    psql -c "CREATE EXTENSION ${NAME} VERSION '${PREV}';"
    ;;
  update)
    psql -c "ALTER EXTENSION ${NAME} UPDATE TO '${NEW}';"
    got=$(psql -tA -c "SELECT extversion FROM pg_extension WHERE extname = '${NAME}';")
    if [[ ${got} != "${NEW}" ]]; then
      org_finding_annotated "" "upgrade left extversion ${got}, wanted ${NEW}"
      exit 1
    fi
    echo "upgraded ${PREV} -> ${got}"
    ;;
  *)
    org_finding_annotated "" "unknown phase '$1' (create or update)"
    exit 2
    ;;
esac
