#!/usr/bin/env bash
# The crates whose public API the belt can judge, from cargo's own
# account of a workspace. Org canon, sourced by the two scripts that
# judge a Rust API: `audit:semver` (mise/semver-audit.sh, against the
# registry) and `break:check` (mise/break-check.sh, a pull request
# against its base, #1906). One derivation, so the two can never judge
# different populations.
#
#   org_rust_api_population <cargo metadata JSON>
#
# prints one record per workspace member, tab-separated:
#
#   name  where  library  kind  majors
#
#   where    where it may be published, as cargo resolved it: `any`
#            (`null`, or a list naming crates.io as `crates-io`), `none`
#            (`[]`, which is `publish = false`), or the registries it
#            names, comma-joined;
#   library  `lib` when a target is of a kind cargo-semver-checks itself
#            checks (v0.51.0, `is_lib_like_checkable_target`): lib, rlib,
#            dylib, cdylib or staticlib. A bin-only crate is not one, and
#            neither is a proc-macro crate. Otherwise `nolib`;
#   kind     `pgrx` or `plain`, and
#   majors   the Postgres majors a pgrx crate declares, comma-joined —
#            both from gate_surface.py, the belt's one derivation of
#            which crates are pgrx (`-` for none).
#
# A field cargo did not report is `-`, never empty: the readers are bash
# `read` loops with IFS set to tab, a whitespace separator, so an empty
# field would vanish and shift the fields after it (gate_surface.py says
# how that was measured). A failed derivation returns 1 and prints
# nothing a caller could read as a workspace with no crates.
#
# Needs ORG_PYTHON, ORG_BELT_DIR and jq.

org_rust_api_population() {
  local metadata=$1 surface where record spec kind majors _dir crate target library
  surface=$("${ORG_PYTHON:?unset}" "${ORG_BELT_DIR:?unset}/gate_surface.py" <<< "${metadata}") || return 1
  where=$(jq -r '
    .workspace_members as $ids
    | .packages[]
    | select(.id as $id | $ids | index($id))
    | [.name, (if .publish == null then "any"
               elif (.publish | length) == 0 then "none"
               elif (.publish | index("crates-io")) then "any"
               else (.publish | join(",")) end),
       (if any(.targets[].kind[]; IN("lib", "rlib", "dylib", "cdylib", "staticlib"))
        then "lib" else "nolib" end)]
    | @tsv' <<< "${metadata}") || return 1
  while IFS=$'\t' read -r record spec kind majors _dir; do
    [[ ${record} == crate ]] || continue
    crate="${spec%@*}"
    target=$(awk -F '\t' -v n="${crate}" '$1 == n { print $2 }' <<< "${where}")
    library=$(awk -F '\t' -v n="${crate}" '$1 == n { print $3 }' <<< "${where}")
    printf '%s\t%s\t%s\t%s\t%s\n' "${crate}" "${target:--}" "${library:--}" \
      "${kind}" "${majors}"
  done <<< "${surface}"
}
