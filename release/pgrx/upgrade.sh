#!/usr/bin/env bash
# The upgrade from the previous release, executed (#1380): as root
# inside the newest-distro postgres container, the previous release's
# real package is installed and a cluster creates the extension at the
# old version; the new package lands over it — exactly what a package
# upgrade does — and the running installation must cross the gap. The
# cluster work runs as postgres (/pgrx/upgrade-cluster.sh).
#
# Environment: PG, NAME, PREV, NEW. The previous package root is
# mounted at /prev, the new one at /new.
set -euo pipefail
: "${PG:?}"
lib="/usr/lib/postgresql/${PG}/lib"
ext="/usr/share/postgresql/${PG}/extension"
cp -a "/prev${lib}/." "${lib}/"
cp -a "/prev${ext}/." "${ext}/"
su postgres -s /bin/bash /pgrx/upgrade-cluster.sh create
cp -a "/new${lib}/." "${lib}/"
cp -a "/new${ext}/." "${ext}/"
su postgres -s /bin/bash /pgrx/upgrade-cluster.sh update
