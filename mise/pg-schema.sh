#!/usr/bin/env bash
# A pgrx extension's SQL surface against its snapshot (#896). Org canon —
# the body of `lint:pg-schema` (`check`) and `fix:pg-schema` (`write`),
# extracted so the lint and its fixer generate the surface the same way
# and cannot drift into judging one surface and writing another.
#
# THE EXTENSION IS DERIVED, never declared: gate-surface.py calls a
# member a pgrx extension when it depends on pgrx AND carries pgNN
# features. The snapshot's path is derived too — `schema.snapshot.sql`
# beside the extension's Cargo.toml — so a repository configures nothing.
#
# SKIPS ONLY WHEN THERE IS NO EXTENSION. An extension that tracks no
# snapshot is red (ruled on #896): a skip would let any extension leave
# its surface unguarded by committing nothing. That judgment, the diff
# and the write are pg-schema.py's, where they have table tests.
#
# ONE MAJOR, THE HIGHEST DECLARED, `lint:pg-clippy`'s choice and reason:
# the surface is one set of `#[pg_extern]`s, not one per major, and
# `test:pgrx` compiles every major anyway.
set -euo pipefail

mode="${1:-}"
case "${mode}" in
  check) label="lint:pg-schema" ;;
  write) label="fix:pg-schema" ;;
  *)
    echo "pg-schema.sh: usage: pg-schema.sh check|write" >&2
    exit 1
    ;;
esac
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: conf.d symlink)}"

manifests=$(git ls-files ':!vendor/**' "Cargo.toml" "*/Cargo.toml")
[[ -n ${manifests} ]] || {
  echo "${label}: no Cargo.toml tracked, skipped"
  exit 0
}
command -v cargo > /dev/null || {
  echo "${label}: Cargo.toml present but cargo missing — pin rust in mise.toml" >&2
  exit 1
}

# Assigned first, one step each: a `<<< "$(...)"` discards the
# derivation's exit status, and a red derivation would read as a
# workspace with no extension — the skip this task must not take.
metadata=$(cargo metadata --no-deps --format-version 1 --locked)
surface=$(python3 "${belt}/gate-surface.py" <<< "${metadata}")
targets=()
majors=""
while IFS=$'\t' read -r record spec kind crate_majors dir; do
  [[ ${record} == crate && ${kind} == pgrx ]] || continue
  [[ ${dir} != "-" ]] || {
    echo "${label}: ${spec} is a pgrx extension cargo reports no directory for" >&2
    exit 1
  }
  targets+=("${spec%@*}:${crate_majors##*,}:${dir}")
  majors="${majors}${majors:+,}${crate_majors##*,}"
done <<< "${surface}"
[[ -n ${majors} ]] || {
  echo "${label}: no pgrx extension tracked, skipped"
  exit 0
}

# An untracked snapshot is red before anything is built: the remedy is
# the fixer, and making the reader wait for a compile to learn that
# would be a cost with no information in it. pg-schema.py asks again
# after generating, so this is a shortcut, never the only guard.
if [[ ${mode} == check ]]; then
  missing=0
  for target in "${targets[@]}"; do
    snapshot="${target#*:*:}/schema.snapshot.sql"
    git ls-files --error-unmatch -- "${snapshot}" > /dev/null 2>&1 || {
      python3 "${belt}/pg-schema.py" check /dev/null "${snapshot}" || missing=1
    }
  done
  [[ ${missing} -eq 0 ]] || exit 1
fi

command -v cargo-pgrx > /dev/null || {
  echo "${label}: cargo-pgrx missing — a pgrx crate pins it as a build input" >&2
  exit 1
}
# shellcheck source=mise/pgrx-env.sh
. "${belt}/pgrx-env.sh"
org_pgrx_darwin_flags
installed=$(mise ls "aqua:theseus-rs/postgresql-binaries" --json)
plan=$(python3 "${belt}/pgrx-postgres.py" --majors "${majors}" <<< "${installed}")
org_pgrx_init <<< "${plan}" > /dev/null

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
  dir="${rest#*:}"
  snapshot="${dir}/schema.snapshot.sql"
  raw="${out}/${name}.raw.sql"
  cargo pgrx schema "pg${major}" --package "${name}" \
    --manifest-path "${dir}/Cargo.toml" \
    --no-default-features --features "pg${major}" --out "${raw}"
  if python3 "${belt}/pg-schema.py" "${mode}" "${raw}" "${snapshot}"; then
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
