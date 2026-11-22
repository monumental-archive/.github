#!/usr/bin/env bash
# The glibc floor, asserted, not presumed: readelf proves no GLIBC_
# symbol the packaged library needs exceeds the floor the base-image
# mapping guarantees, so a dependency dragging in a newer symbol fails
# the release instead of failing at dlopen on Debian 12 (#1380).
#
#   glibc-floor.sh PKGROOT FLOOR
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
pkgroot=$1
floor=$2
# -print -quit, never `| head -1`: find's own first-match stop, so there
# is no pipe to break and no walk to finish (#682).
so=$(find "${pkgroot}" -name '*.so' -type f -print -quit)
[[ -n ${so} ]] || {
  org_finding_annotated "" "no shared library packaged"
  exit 1
}
worst=$(readelf --dyn-syms --wide "${so}" \
  | grep -oE 'GLIBC_[0-9]+\.[0-9]+' | sort -uV | tail -1 || true)
if [[ -z ${worst} ]]; then
  org_notice "no versioned glibc symbols (static or unusual link)"
  exit 0
fi
highest=$(printf '%s\n' "GLIBC_${floor}" "${worst}" | sort -V | tail -1)
if [[ ${highest} != "GLIBC_${floor}" ]]; then
  org_finding_annotated "" "${so##*/} needs ${worst}, above the ${floor} floor the class guarantees"
  exit 1
fi
org_notice "glibc ceiling ${worst} within floor ${floor}"
