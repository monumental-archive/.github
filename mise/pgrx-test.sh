#!/usr/bin/env bash
# The Postgres extension's own suite, one major per run. Org canon — the
# body of `test:pgrx`, extracted so the task and the gate's fan-out legs
# run the same code (#1148).
#
# THE UNIT IS ONE MAJOR. Run with `ORG_PGRX_MAJOR` set, it tests that
# major and nothing else: that is what one leg of the gate's fan-out
# does. Run with nothing set, it tests every major the crate declares,
# side by side — the local form of the same thing. The list is the
# crate's own `[features]` either way, read through gate_surface.py, so
# the workflow and the laptop cannot name different majors.
#
# `ORG_PGRX_FANNED` is the third form, for the gate's main job when the
# legs exist: it carries the list the workflow planned, this compares it
# with the list derived here from the pinned toolchain, and runs nothing.
# The plan is made early and cheaply, before any toolchain is installed;
# this is where it is held to the truth. A plan that missed a major is a
# red here, not a major quietly untested.
#
# THE SUITE IS ALSO THE MEASUREMENT (#1656). An extension's functions run
# inside the Postgres backend, which no `cargo llvm-cov --workspace` run
# collects from, so the rust leg scored every line of one as uncovered
# (#917, measured on #1652: 0 of 18 on release-lab, 0 of 143 on edtf).
# This suite is the only thing that executes that code. Where the
# repository holds a `.coverage-floor`, each major's run is built
# instrumented, renders the extension crate's own report as
# `lcov-pgrx-pgNN.info`, prints `pgrx-pgNN <percent>` and holds that
# major to the floor by itself: a major's figure is over the lines its
# own build compiled, so per-major code counts in exactly the majors
# that build it. No floor: the plain run, untouched. The suite's exit
# status gates the figure, and a run that passed and wrote no profile is
# refused by name, never read as covered.
#
# `measure` (the first argument) is the same run for coverage-measure.sh's
# `pgrx` selector, which `coverage:adopt` and the release path's floor
# derivation call: instrumented with or without a floor, every declared
# major, and no floor held, because those callers derive the floor.
set -euo pipefail
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"

label="test:pgrx"
mode="${1:-test}"
case "${mode}" in
  test | measure) ;;
  *)
    echo "${label}: unknown mode '${mode}' — expected test or measure" >&2
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

# Assigned first, one step each: an empty here-string from a failed
# derivation would skip this suite silently, which is the exact failure
# #813 exists to end.
metadata=$(cargo metadata --no-deps --format-version 1 --locked)
surface=$("${ORG_PYTHON}" "${belt}/gate_surface.py" --exclude "${CLIPPY_EXCLUDE:-}" <<< "${metadata}")
crates=""
declared=""
while IFS=$'\t' read -r record spec kind crate_majors dir; do
  [[ ${record} == crate && ${kind} == pgrx ]] || continue
  [[ ${dir} != "-" ]] || {
    echo "${label}: ${spec} is a pgrx extension cargo reports no directory for" >&2
    exit 1
  }
  # dir:name:majors — the name is what the report is restricted to.
  crates="${crates} ${dir}:${spec%@*}:${crate_majors}"
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

if [[ ${mode} == test && -n ${ORG_PGRX_FANNED+set} ]]; then
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

# The floor this run holds, or nothing. Read through coverage_floor.py,
# the one reader, so a floor that disagrees with its own record is the
# same refusal here as in coverage:check.
floor=""
if [[ ${mode} == test ]]; then
  floor=$("${ORG_PYTHON}" "${belt}/coverage_floor.py" read) || exit 1
fi
measured=""
[[ ${mode} == test && -z ${floor} ]] || measured=1
components=""
[[ -z ${measured} ]] || components=$(rustup component list --installed 2> /dev/null) || components=""
if [[ -n ${measured} ]] && ! grep -q "^llvm-tools" <<< "${components}"; then
  echo "${label}: the extension is measured here and llvm-tools is missing — declare it with the" >&2
  echo '  rust pin in mise.toml: rust = { version = "1.xx.x", components = "llvm-tools" }' >&2
  exit 1
fi

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
plan=$("${ORG_PYTHON}" "${belt}/pgrx_postgres.py" --majors "${only:-${declared}}" <<< "${installed}")
org_pgrx_init <<< "${plan}" > /dev/null

# The runs this invocation owes, as `dir:major`.
runs=""
for entry in ${crates}; do
  dir="${entry%%:*}"
  rest="${entry#*:}"
  for one in $(tr ',' ' ' <<< "${rest#*:}"); do
    [[ -z ${only} || ${one} == "${only}" ]] || continue
    runs="${runs} ${dir}:${rest%%:*}:${one}"
  done
done
runs="${runs# }"
root=$(python3 -c 'import json, sys; print(json.load(sys.stdin)["target_directory"])' <<< "${metadata}")

# One major's suite, instrumented, then its report. Prints only the leg
# line on stdout; the suite's own output goes to stderr. In a subshell,
# so the instrumentation never leaks into the next run.
measure_one() { # usage: measure_one <dir> <crate> <major>
  local dir="$1" crate="$2" one="$3"
  local leg="pgrx-pg$3"
  (
    # A target directory of its own (#1652, measured): the instrumentation
    # rides RUSTC_WRAPPER, which cargo's fingerprint does not see, so in a
    # directory a plain build used it reuses uninstrumented binaries and
    # writes no profile at all.
    export CARGO_TARGET_DIR="${root}/llvm-cov-${leg}"
    envs=$(cargo llvm-cov show-env --sh) || exit 1
    eval "${envs}"
    # pgrx deletes the data directory on shutdown, and that is every
    # backend's working directory: a relative profile path is written
    # there and goes with it.
    if [[ ${LLVM_PROFILE_FILE:-} != /* ]]; then
      echo "${label}: pg${one}: the profile path '${LLVM_PROFILE_FILE:-}' is not absolute —" >&2
      echo "  the backends write it inside the data directory pgrx deletes on shutdown" >&2
      exit 1
    fi
    cargo llvm-cov clean --profraw-only >&2 || exit 1
    if ! (cd "${dir}" && cargo pgrx test "pg${one}") >&2; then
      echo "${label}: ${dir} at pg${one} failed — a suite that does not pass measures nothing" >&2
      exit 1
    fi
    if ! compgen -G "$(dirname "${LLVM_PROFILE_FILE}")/*.profraw" > /dev/null; then
      echo "${label}: ${leg}: the suite passed and wrote no coverage profile — an extension" >&2
      echo "  that measured nothing must not read as held (#1656)" >&2
      exit 1
    fi
    cargo llvm-cov report -p "${crate}" --lcov --output-path "${PWD}/lcov-${leg}.info" >&2 || exit 1
    pct=$(awk -F: '
      $1 == "LF" { found += $2 }
      $1 == "LH" { hit += $2 }
      END { if (found > 0) printf "%.16g\n", hit * 100 / found }
    ' "lcov-${leg}.info" 2> /dev/null) || pct=""
    if [[ -z ${pct} ]]; then
      echo "${label}: ${leg}: could not read a total from the report — a" >&2
      echo "  measurement that reads nothing must not become a floor" >&2
      exit 1
    fi
    echo "${leg} ${pct}"
  )
}

# Prints the leg lines, and in the gate holds each to the floor by name.
hold() { # usage: hold <leg lines>
  local leg pct low=""
  while read -r leg pct; do
    [[ -n ${leg} ]] || continue
    echo "${leg} ${pct}"
    [[ -n ${floor} ]] || continue
    if awk -v p="${pct}" -v f="${floor}" 'BEGIN { exit !(p < f) }'; then
      echo "${label}: ${leg}: ${pct}% is below the derived floor ${floor}%" >&2
      low=1
    else
      echo "${label}: ${leg} holds the floor ${floor} (${pct}%)" >&2
    fi
  done <<< "$1"
  [[ -z ${low} ]]
}

if [[ ${runs} != *" "* ]]; then
  # One run: in the foreground, in the workspace's own target directory,
  # so a fan-out leg's cargo cache is the ordinary one (a measured run
  # takes a directory of its own, in measure_one).
  dir="${runs%%:*}"
  crate="${runs#*:}"
  crate="${crate%%:*}"
  one="${runs##*:}"
  echo "${label}: ${dir} at pg${one}"
  if [[ -n ${measured} ]]; then
    line=$(measure_one "${dir}" "${crate}" "${one}")
    hold "${line}"
  else
    (cd "${dir}" && cargo pgrx test "pg${one}")
  fi
  echo "${label}: 1 suite run: ${dir}(pg${one})"
  exit 0
fi

# Several: side by side. Each gets its own target directory, because
# cargo locks a target directory for the length of a build and five
# builds sharing one would simply queue. Their Postgres is already
# separate — a data directory and a port per major.
logs=$(mktemp -d)
rc=0
trap 'rc=$?; rm -rf "${logs}"; [[ -n ${org_done:-} || ${rc} -ne 0 ]] || rc=1; exit "${rc}"' EXIT
pids=""
for run in ${runs}; do
  dir="${run%%:*}"
  crate="${run#*:}"
  crate="${crate%%:*}"
  one="${run##*:}"
  echo "${label}: ${dir} at pg${one} (concurrent)"
  if [[ -n ${measured} ]]; then
    measure_one "${dir}" "${crate}" "${one}" > "${logs}/pg${one}.leg" 2> "${logs}/pg${one}.log" &
  else
    (
      cd "${dir}"
      CARGO_TARGET_DIR="${root}/pgrx-pg${one}" cargo pgrx test "pg${one}"
    ) > "${logs}/pg${one}.log" 2>&1 &
  fi
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
if [[ -n ${measured} ]]; then
  lines=$(cat "${logs}"/*.leg)
  hold "${lines}"
fi
echo "${label}: ${count} suite run(s):${tested}"
org_done=1
