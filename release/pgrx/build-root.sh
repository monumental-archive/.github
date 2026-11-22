#!/usr/bin/env bash
# The pgrx-extension build's root half, inside the digest-pinned
# consumer Postgres (build-pgrx-extension.yml, "Build, test and package
# inside the target Postgres"; #1380). The canon tree's release/pgrx is
# mounted read-only at /pgrx, the caller's checkout at /src, the output
# at /out. Root installs the headers, sets the floor, hands the tree to
# the unprivileged postgres user and becomes it (/pgrx/build.sh).
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
# The docker run line's environment.
: "${PG:?}"
: "${BUILD_IMAGE:?}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# Headers at exactly the server the pinned image already carries
# (#1157): unversioned, apt serves the newest minor, and installing it
# also upgrades the server, so the suite would pass against a server the
# image does not contain. Textually identical to the install in
# release/generate-pgrx-upgrade.sh, asserted by
# release/test_generate_pgrx_upgrade.py.
server=$(dpkg-query -W -f="\${Version}" "postgresql-${PG}")
if ! apt-cache show "postgresql-server-dev-${PG}=${server}" > /dev/null 2>&1; then
  msg="image ${BUILD_IMAGE} carries server ${server}; the apt repository"
  msg+=" no longer serves its headers — move the image pin in"
  msg+=" docker/pgrx-base-images.toml"
  org_finding_annotated "" "${msg}"
  exit 1
fi
apt-get install -y -qq --no-install-recommends \
  "postgresql-server-dev-${PG}=${server}" build-essential \
  ca-certificates curl pkg-config clang llvm libclang-dev git
after=$(dpkg-query -W -f="\${Version}" "postgresql-${PG}")
[[ ${after} == "${server}" ]] || {
  org_finding_annotated "" "the build moved the server from ${server} to ${after}"
  exit 1
}
# The floor this build sets: the server it compiles against, up to its
# first `-` (18.7-1.pgdg12+1 is 18.7), and that in server_version_num
# form (180007).
export FLOOR_XY="${server%%-*}"
if [[ ! ${FLOOR_XY} =~ ^[0-9]+\.[0-9]+$ ]]; then
  org_finding_annotated "" "server ${server} does not start with a major.minor version"
  exit 1
fi
export FLOOR_NUM=$((${FLOOR_XY%%.*} * 10000 + ${FLOOR_XY#*.}))
# Everything after the installs runs as the unprivileged postgres user:
# initdb refuses root, and the test suite spins up real clusters. Root
# hands over exactly what the build needs to write — the copied source,
# the out dir, and the extension install dirs pgrx test installs into.
mkdir -p /build
cp -a /src/. /build/
# shellcheck disable=SC2312  # pg_config of the image's own server
chown -R postgres:postgres /build /out \
  "$(/usr/bin/pg_config --pkglibdir)" \
  "$(/usr/bin/pg_config --sharedir)/extension"
keep=PG,VERSION,CRATE_DIR,RUST_VERSION,PGRX_VERSION,AUDITABLE_VERSION,SOURCE_DATE_EPOCH
keep+=,FLOOR_XY,FLOOR_NUM,ORG_FINDINGS_WRITER
exec su -w "${keep}" postgres -s /bin/bash /pgrx/build.sh
