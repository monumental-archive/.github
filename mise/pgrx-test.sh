#!/usr/bin/env bash
# The Postgres extension's own suite, one major per run. Org canon — the
# body of `test:pgrx`, extracted so the task and the gate's fan-out legs
# run the same code (#1148).
#
# THE UNIT IS ONE MAJOR. Run with `ORG_PGRX_MAJOR` set, it tests that
# major and nothing else: that is what one leg of the gate's fan-out
# does. Run with nothing set, it tests every major the crate declares,
# side by side — the local form of the same thing. The list is the
# crate's own `[features]` either way, read through gate-surface.py, so
# the workflow and the laptop cannot name different majors.
#
# `ORG_PGRX_FANNED` is the third form, for the gate's main job when the
# legs exist: it carries the list the workflow planned, this compares it
# with the list derived here from the pinned toolchain, and runs nothing.
# The plan is made early and cheaply, before any toolchain is installed;
# this is where it is held to the truth. A plan that missed a major is a
# red here, not a major quietly untested.
set -euo pipefail

label="test:pgrx"
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

# Assigned first, one step each: an empty here-string from a failed
# derivation would skip this suite silently, which is the exact failure
# #813 exists to end.
metadata=$(cargo metadata --no-deps --format-version 1 --locked)
surface=$(python3 "${belt}/gate-surface.py" --exclude "${CLIPPY_EXCLUDE:-}" <<< "${metadata}")
crates=""
declared=""
while IFS=$'\t' read -r record spec kind crate_majors dir; do
  [[ ${record} == crate && ${kind} == pgrx ]] || continue
  [[ ${dir} != "-" ]] || {
    echo "${label}: ${spec} is a pgrx extension cargo reports no directory for" >&2
    exit 1
  }
  crates="${crates} ${dir}:${crate_majors}"
  declared="${declared}${declared:+,}${crate_majors}"
done <<< "${surface}"

# Normalised to one sorted, de-duplicated, comma-separated list, so two
# lists that name the same majors compare equal however they were built.
normalise() {
  local list
  list=$(tr -c '0-9' '\n' <<< "$1" | sed '/^$/d' | sort -un | tr '\n' ',')
  printf '%s' "${list%,}"
}
declared=$(normalise "${declared}")

if [[ -n ${ORG_PGRX_FANNED+set} ]]; then
  planned=$(normalise "${ORG_PGRX_FANNED}")
  if [[ ${planned} != "${declared}" ]]; then
    echo "${label}: the gate planned legs for majors [${planned}]; the crate declares [${declared}]" >&2
    echo "  the legs are planned before the toolchain exists and checked here, where it" >&2
    echo "  does — a major with no leg is a major nothing tested (#1148)" >&2
    exit 1
  fi
  if [[ -n ${declared} ]]; then
    echo "${label}: run by the gate's fan-out, one leg per major: ${declared}"
    exit 0
  fi
fi

[[ -n ${crates} ]] || {
  echo "${label}: no pgrx extension tracked, skipped"
  exit 0
}

only="${ORG_PGRX_MAJOR:-}"
if [[ -n ${only} && ",${declared}," != *",${only},"* ]]; then
  echo "${label}: asked for pg${only}; the crate declares [${declared}]" >&2
  exit 1
fi

cargo fetch --locked
# shellcheck source=mise/pgrx-env.sh
. "${belt}/pgrx-env.sh"
org_pgrx_darwin_flags
installed=$(mise ls "aqua:theseus-rs/postgresql-binaries" --json)
plan=$(python3 "${belt}/pgrx-postgres.py" --majors "${only:-${declared}}" <<< "${installed}")
org_pgrx_init <<< "${plan}" > /dev/null

# The runs this invocation owes, as `dir:major`.
runs=""
for entry in ${crates}; do
  dir="${entry%%:*}"
  for one in $(tr ',' ' ' <<< "${entry#*:}"); do
    [[ -z ${only} || ${one} == "${only}" ]] || continue
    runs="${runs} ${dir}:${one}"
  done
done
runs="${runs# }"

if [[ ${runs} != *" "* ]]; then
  # One run: in the foreground, in the workspace's own target directory,
  # so a fan-out leg's cargo cache is the ordinary one.
  dir="${runs%%:*}"
  one="${runs##*:}"
  echo "${label}: ${dir} at pg${one}"
  (cd "${dir}" && cargo pgrx test "pg${one}")
  echo "${label}: 1 suite run: ${dir}(pg${one})"
  exit 0
fi

# Several: side by side. Each gets its own target directory, because
# cargo locks a target directory for the length of a build and five
# builds sharing one would simply queue. Their Postgres is already
# separate — a data directory and a port per major.
root=$(python3 -c 'import json, sys; print(json.load(sys.stdin)["target_directory"])' <<< "${metadata}")
logs=$(mktemp -d)
trap 'rm -rf "${logs}"' EXIT
pids=""
for run in ${runs}; do
  dir="${run%%:*}"
  one="${run##*:}"
  echo "${label}: ${dir} at pg${one} (concurrent)"
  (
    cd "${dir}"
    CARGO_TARGET_DIR="${root}/pgrx-pg${one}" cargo pgrx test "pg${one}"
  ) > "${logs}/pg${one}.log" 2>&1 &
  pids="${pids} $!:${dir}:${one}"
done
failed=""
tested=""
count=0
for job in ${pids}; do
  pid="${job%%:*}"
  rest="${job#*:}"
  count=$((count + 1))
  tested="${tested} ${rest%%:*}(pg${rest##*:})"
  if ! wait "${pid}"; then
    failed="${failed} pg${rest##*:}"
  fi
done
for name in ${failed}; do
  echo "---- ${label}: ${name} failed; its output follows ----" >&2
  cat "${logs}/${name}.log" >&2
done
if [[ -n ${failed} ]]; then
  echo "${label}: failed at:${failed}" >&2
  exit 1
fi
echo "${label}: ${count} suite run(s):${tested}"
