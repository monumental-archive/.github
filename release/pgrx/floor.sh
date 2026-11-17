#!/usr/bin/env bash
# The PostgreSQL floor, stated where a person meets the failure (#1274).
# A library built against a newer minor can need a server symbol an
# older minor lacks (18.7 added heap_datum_check; edtf 1.3.4's pg18
# library fails on 18.6 with `undefined symbol`). Every script that
# installs or updates TO this release — <ext>--<version>.sql and each
# <ext>--*--<version>.sql — begins with floor.sql.in, filled, so the
# explanation prints in the same output as the failure. A WARNING,
# never an error: most minors do not move the floor. Its dollar-quote
# tag must be one no generated script uses (#1380).
#
#   floor.sh EXTDIR VERSION FLOOR_XY FLOOR_NUM
set -euo pipefail
extdir=$1
version=$2
xy=$3
num=$4
template=$(cat "${BASH_SOURCE[0]%/*}/floor.sql.in")
for control in "${extdir}"/*.control; do
  ext=$(basename "${control}" .control)
  block="${template//@NUM@/${num}}"
  block="${block//@XY@/${xy}}"
  block="${block//@EXT@/${ext}}"
  block="${block//@VERSION@/${version}}"
  shopt -s nullglob
  scripts=("${extdir}/${ext}--${version}.sql" "${extdir}/${ext}--"*"--${version}.sql")
  shopt -u nullglob
  for script in "${scripts[@]}"; do
    [[ -f ${script} ]] || continue
    if grep -qF "pgrx_floor" "${script}"; then
      echo "::error::${script##*/} already uses the pgrx_floor tag"
      exit 1
    fi
    { printf "%s\n\n" "${block}"; cat "${script}"; } > "${script}.floor"
    mv "${script}.floor" "${script}"
  done
  echo "::notice::${ext} ${version} was built against PostgreSQL ${xy}"
done
