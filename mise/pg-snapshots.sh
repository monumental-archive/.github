#!/usr/bin/env bash
# Which pg-schema snapshots the belt owns (#1240). Org canon — one
# derivation, three consumers: `lint:pg-schema` and `fix:pg-schema`
# (through pg-schema.sh) generate and write these files, and `lint:sql`
# and `fix:sql` leave exactly these files out of sqlfluff's file list.
# Two copies of the answer would let a task lint a file the other owns,
# which is the defect: the belt wrote a snapshot and then linted it red.
#
# THE EXTENSIONS ARE DERIVED, never declared: gate-surface.py calls a
# member a pgrx extension when it depends on pgrx AND carries pgNN
# features. The snapshot is `schema.snapshot.sql` beside that
# extension's Cargo.toml. Never a filename match: a hand-written
# `schema.snapshot.sql` anywhere else is the repository's own SQL and
# stays linted.
#
# One record per extension, tab-separated, no field ever empty:
#   <snapshot path> <package name> <highest declared major> <crate dir>
# Nothing at all when no Cargo.toml is tracked or no member is an
# extension; the caller says which of those it was.
set -euo pipefail
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"

belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: conf.d symlink)}"

manifests=$(git ls-files ':!vendor/**' "Cargo.toml" "*/Cargo.toml")
[[ -n ${manifests} ]] || exit 0
command -v cargo > /dev/null || {
  echo "pg-snapshots: Cargo.toml present but cargo missing — pin rust in mise.toml" >&2
  exit 1
}

# Assigned first, one step each: a `<<< "$(...)"` discards the
# derivation's exit status, and a red derivation would read as a
# workspace with no extension — an empty answer nobody could tell from
# the real one.
metadata=$(cargo metadata --no-deps --format-version 1 --locked)
surface=$("${ORG_PYTHON}" "${belt}/gate-surface.py" <<< "${metadata}")
while IFS=$'\t' read -r record spec kind majors dir; do
  [[ ${record} == crate && ${kind} == pgrx ]] || continue
  [[ ${dir} != "-" ]] || {
    echo "pg-snapshots: ${spec} is a pgrx extension cargo reports no directory for" >&2
    exit 1
  }
  # A crate at the workspace root is `.`; the path is spelled the way
  # `git ls-files` spells it, because lint:sql compares the two as text.
  snapshot="${dir}/schema.snapshot.sql"
  [[ ${dir} != "." ]] || snapshot="schema.snapshot.sql"
  printf '%s\t%s\t%s\t%s\n' "${snapshot}" "${spec%@*}" "${majors##*,}" "${dir}"
done <<< "${surface}"
