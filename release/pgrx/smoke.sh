#!/usr/bin/env bash
# The fresh-install smoke, as root inside a stock postgres container of
# one distro (#1380): the package lands in the server's own directories,
# a throwaway cluster runs CREATE EXTENSION as postgres
# (/pgrx/smoke-cluster.sh), then the caller's smoke-test script, if it
# names one, runs against the extracted package root.
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
    org_finding_annotated "" "smoke-test ${UNTRUSTED_SMOKE} is not executable"
    exit 1
  fi
  "/src/${UNTRUSTED_SMOKE}" /pkgroot "${PG}"
fi
