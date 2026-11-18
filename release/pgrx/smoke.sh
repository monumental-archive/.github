#!/usr/bin/env bash
# The fresh-install smoke, as root inside a stock postgres container of
# one distro (#1380): the package lands in the server's own directories,
# a throwaway cluster runs CREATE EXTENSION as postgres
# (/pgrx/smoke-cluster.sh), then the caller's smoke-test script, if it
# names one, runs against the extracted package root.
set -euo pipefail
: "${PG:?}"
: "${UNTRUSTED_SMOKE?}"
cp -a "/pkgroot/usr/lib/postgresql/${PG}/lib/." \
  "/usr/lib/postgresql/${PG}/lib/"
cp -a "/pkgroot/usr/share/postgresql/${PG}/extension/." \
  "/usr/share/postgresql/${PG}/extension/"
EXT=$(ls "/pkgroot/usr/share/postgresql/${PG}/extension/"*.control)
EXT=$(basename "${EXT}" .control)
export EXT
su postgres -s /bin/bash /pgrx/smoke-cluster.sh
if [[ -n ${UNTRUSTED_SMOKE} ]]; then
  if [[ ! -x "/src/${UNTRUSTED_SMOKE}" ]]; then
    echo "::error::smoke-test ${UNTRUSTED_SMOKE} is not executable"
    exit 1
  fi
  "/src/${UNTRUSTED_SMOKE}" /pkgroot "${PG}"
fi
