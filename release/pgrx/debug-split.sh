#!/usr/bin/env bash
# Split, then strip (#1274): every shared library under PKGROOT leaves
# its debug info for a sibling archive under DEBUGROOT/usr/lib/debug,
# where gdb finds it through the debuglink — the Debian -dbgsym practice.
# GNU binutils from build-essential, never llvm-strip, which drops
# .dep-v0. Run by the user that owns the tree: the runner cannot create
# directories inside a bind mount the container user owns (#1380).
#
#   debug-split.sh PKGROOT DEBUGROOT
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
debugroot=$2
mkdir -p "${debugroot}"
libs=0
# shellcheck disable=SC2312  # process substitution: a find that fails
# finds nothing, and libs == 0 below refuses that
while IFS= read -r -d "" so; do
  libs=$((libs + 1))
  rel="${so#"${pkgroot}"/}"
  dbg="${debugroot}/usr/lib/debug/${rel}.debug"
  mkdir -p "${dbg%/*}"
  syms=$(readelf --dyn-syms --wide "${so}")
  before=$(awk '$4 == "FUNC"' <<< "${syms}" | wc -l)
  objcopy --only-keep-debug "${so}" "${dbg}"
  strip --strip-unneeded "${so}"
  objcopy --add-gnu-debuglink="${dbg}" "${so}"
  syms=$(readelf --dyn-syms --wide "${so}")
  after=$(awk '$4 == "FUNC"' <<< "${syms}" | wc -l)
  sections=$(readelf -S --wide "${so}")
  bad=()
  [[ ${sections} == *" .dep-v0 "* ]] || bad+=("no .dep-v0")
  [[ ${sections} == *" .gnu_debuglink "* ]] || bad+=("no .gnu_debuglink")
  [[ ${sections} != *" .debug_info "* ]] || bad+=("still has .debug_info")
  [[ ${sections} != *" .symtab "* ]] || bad+=("still has .symtab")
  ((before == after)) || bad+=("${after} dynamic functions, ${before} before the split")
  if ((${#bad[@]})); then
    org_finding_annotated "" "${rel} after the split: ${bad[*]}"
    exit 1
  fi
  org_notice "${rel}: split and stripped, ${after} dynamic functions, .dep-v0 kept"
done < <(find "${pkgroot}" -type f -name "*.so" -print0)
if ((libs == 0)); then
  org_finding_annotated "" "no shared library under ${pkgroot} to split"
  exit 1
fi
