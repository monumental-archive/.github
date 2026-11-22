#!/usr/bin/env bash
# A pgrx extension's SQL surface against its snapshot (#896). Org canon —
# the body of `lint:pg-schema` (`check`) and `fix:pg-schema` (`write`),
# extracted so the lint and its fixer generate the surface the same way
# and cannot drift into judging one surface and writing another.
#
# THE EXTENSION IS DERIVED, never declared, and so is its snapshot's
# path — `schema.snapshot.sql` beside the extension's Cargo.toml — so a
# repository configures nothing. Both come from pg-snapshots.sh, the
# one answer lint:sql also takes to leave these files to this task
# (#1240): the two cannot disagree about which files the belt owns.
#
# SKIPS ONLY WHEN THERE IS NO EXTENSION. An extension that tracks no
# snapshot is red (ruled on #896): a skip would let any extension leave
# its surface unguarded by committing nothing. That judgment, the diff
# and the write are pg_schema.py's, where they have table tests.
#
# ONE MAJOR, THE HIGHEST DECLARED, `lint:pg-clippy`'s choice and reason:
# the surface is one set of `#[pg_extern]`s, not one per major, and
# `test:pgrx` compiles every major anyway.
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"

mode="${1:-}"
case "${mode}" in
  check) label="lint:pg-schema" ;;
  write) label="fix:pg-schema" ;;
  *)
    org_finding "" "usage: pg-schema.sh check|write"
    exit 1
    ;;
esac
# The task this script reports for, where mise named none (#1934).
org_task "${label}"
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"

manifests=$(git ls-files ':!vendor/**' "Cargo.toml" "*/Cargo.toml")
[[ -n ${manifests} ]] || {
  echo "${label}: no Cargo.toml tracked, skipped"
  exit 0
}
command -v cargo > /dev/null || {
  org_finding "" "Cargo.toml present but cargo missing — pin rust in mise.toml"
  exit 1
}

# Assigned first: a `<<< "$(...)"` discards the helper's exit status,
# and a red derivation would read as a workspace with no extension —
# the skip this task must not take.
records=$("${belt}/pg-snapshots.sh")
targets=()
majors=""
while IFS=$'\t' read -r snapshot name major dir; do
  [[ -n ${snapshot} ]] || continue
  targets+=("${name}:${major}:${dir}:${snapshot}")
  majors="${majors}${majors:+,}${major}"
done <<< "${records}"
[[ -n ${majors} ]] || {
  echo "${label}: no pgrx extension tracked, skipped"
  exit 0
}

# An untracked snapshot is red before anything is built: the remedy is
# the fixer, and making the reader wait for a compile to learn that
# would be a cost with no information in it. pg_schema.py asks again
# after generating, so this is a shortcut, never the only guard.
if [[ ${mode} == check ]]; then
  missing=0
  for target in "${targets[@]}"; do
    snapshot="${target#*:*:*:}"
    git ls-files --error-unmatch -- "${snapshot}" > /dev/null 2>&1 || {
      "${ORG_PYTHON}" "${belt}/pg_schema.py" check /dev/null "${snapshot}" || missing=1
    }
  done
  [[ ${missing} -eq 0 ]] || exit 1
fi

command -v cargo-pgrx > /dev/null || {
  org_finding "" "cargo-pgrx missing — a pgrx crate pins it as a build input"
  exit 1
}
# shellcheck source=mise/pgrx-env.sh
. "${belt}/pgrx-env.sh"
org_pgrx_darwin_prepare "${majors}"
# Every major's pin checked before anything compiles (#1940).
installed=$(mise ls "aqua:theseus-rs/postgresql-binaries" --json)
"${ORG_PYTHON}" "${belt}/pgrx_postgres.py" --majors "${majors}" <<< "${installed}" > /dev/null

# The raw output lands under the target directory cargo already ignores,
# where a reader can open it after a red.
out="${CARGO_TARGET_DIR:-target}/pg-schema"
mkdir -p "${out}"
failed=0
done_list=""
for target in "${targets[@]}"; do
  name="${target%%:*}"
  rest="${target#*:}"
  major="${rest%%:*}"
  rest="${rest#*:}"
  dir="${rest%%:*}"
  snapshot="${rest#*:}"
  raw="${out}/${name}.raw.sql"
  # With its own major's ICU headers and Postgres, in a subshell
  # (#1774, #1940).
  org_pgrx_major "${major}" cargo pgrx schema "pg${major}" --package "${name}" \
    --manifest-path "${dir}/Cargo.toml" \
    --no-default-features --features "pg${major}" --out "${raw}"
  if "${ORG_PYTHON}" "${belt}/pg_schema.py" "${mode}" "${raw}" "${snapshot}"; then
    done_list="${done_list} ${name}(pg${major})"
  else
    failed=1
  fi
done
[[ ${failed} -eq 0 ]] || exit 1
if [[ ${mode} == check ]]; then
  echo "${label}: SQL surface matches the snapshot:${done_list}"
else
  echo "${label}: snapshot written — read the diff before committing it:${done_list}"
fi
