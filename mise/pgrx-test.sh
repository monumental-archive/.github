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
# `lcov-pgrx-pgNN.info` (outside the tree, #1628), prints
# `pgrx-pgNN <percent>` and holds that major to the floor by itself,
# through coverage_floor.py as the job owning that leg (#1875): a
# major's figure is over the lines its
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

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"

label="test:pgrx"
# The task this script reports for, where mise named none (#1934).
org_task "${label}"
mode="${1:-test}"
case "${mode}" in
  test | measure) ;;
  *)
    org_finding "" "unknown mode '${mode}' — expected test or measure"
    exit 1
    ;;
esac
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"

manifests=() && org_tracked_read manifests ':!vendor/**' "Cargo.toml" "*/Cargo.toml"
[[ -n ${manifests[0]+set} ]] || {
  echo "${label}: no Cargo.toml tracked, skipped"
  exit 0
}
command -v cargo > /dev/null || {
  org_finding "" "Cargo.toml present but cargo missing — pin rust in mise.toml"
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
    org_finding "" "${spec} is a pgrx extension cargo reports no directory for"
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
  # The plan's output as pgrx-plan.sh wrote it, a JSON array of major
  # strings, decoded by jq, the tool that wrote it: normalise takes the
  # comma list, never the JSON (#1880). Slurped, so exactly one document.
  planned=$(jq -sr 'if length == 1 and (.[0] | type == "array" and all(.[];
      type == "string" and test("\\A[1-9][0-9]*\\z")))
    then .[0] | join(",") else error("refused") end' \
    <<< "${ORG_PGRX_FANNED}" 2> /dev/null) || {
    org_finding "" "ORG_PGRX_FANNED '${ORG_PGRX_FANNED}' is not a JSON array of canonical major strings"
    exit 1
  }
  planned=$(normalise "${planned}")
  if [[ ${planned} != "${declared}" ]]; then
    org_finding "" "the gate planned legs for majors [${planned}]; the crate declares [${declared}]" \
      "the legs are planned before the toolchain exists and checked here, where it" \
      "does — a major with no leg is a major nothing tested (#1148)"
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
  org_finding "" "the extension is measured here and llvm-tools is missing — declare it with the" \
    'rust pin in mise.toml: rust = { version = "1.xx.x", components = "llvm-tools" }'
  exit 1
fi
# The run each major's build directory is claimed for (#2062). The
# belt's `[env]` always names it, so empty means outside the belt.
if [[ -n ${measured} && -z ${ORG_COVERAGE_RUN:-} ]]; then
  org_finding "" "ORG_COVERAGE_RUN is empty — the run a measured build directory is" \
    "claimed for; the belt's [env] names it, so run this through mise"
  exit 1
fi

only="${ORG_PGRX_MAJOR:-}"
if [[ -n ${only} && ",${declared}," != *",${only},"* ]]; then
  org_finding "" "asked for pg${only}; the crate declares [${declared}]"
  exit 1
fi

cargo fetch --locked
# shellcheck source=mise/pgrx-env.sh
. "${belt}/pgrx-env.sh"
# The SDK, and every major's ICU headers checked before anything
# compiles (#1774); each run below adds its own major's.
org_pgrx_darwin_prepare "${only:-${declared}}"
# Every major's pin checked before anything compiles; each run below
# takes its own major's Postgres, a clone of that pin for its target
# directory, through org_pgrx_major (#1940).
installed=$(mise ls "aqua:theseus-rs/postgresql-binaries" --json)
"${ORG_PYTHON}" "${belt}/pgrx_postgres.py" --majors "${only:-${declared}}" <<< "${installed}" > /dev/null

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

# Where a measured run's report goes: never the working tree, where it
# is one `git add -A` from a commit (#1628). ORG_COVERAGE_REPORTS where
# the gate names it, whose artifact step keeps that directory; otherwise
# a scratch directory removed on exit, after the figure is read. One
# EXIT trap for this and the side-by-side logs below, completion-guarded
# (#1233).
reports=""
scratch=""
logs=""
if [[ -n ${measured} ]]; then
  reports=${ORG_COVERAGE_REPORTS:-}
  if [[ -z ${reports} ]]; then
    scratch=$(mktemp -d)
    reports=${scratch}
  fi
  mkdir -p "${reports}"
fi
rc=0
trap 'rc=$?; [[ -z ${logs} ]] || rm -rf "${logs}"; [[ -z ${scratch} ]] || rm -rf "${scratch}"
[[ -n ${org_done:-} || ${rc} -ne 0 ]] || rc=1; exit "${rc}"' EXIT

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
    # And of its own compiler (#1877, rust-coverage-env.sh says why).
    compiler=$("${ORG_PYTHON}" "${belt}/gate_cache_key.py" digest) || exit 1
    export CARGO_TARGET_DIR="${root}/llvm-cov-${leg}-${compiler}"
    # Its clang driver (#2022), its major's ICU headers and Postgres,
    # after the target directory the Postgres clone is chosen by (#1940).
    org_pgrx_major_env "${one}" || exit 1
    envs=$(cargo llvm-cov show-env --sh) || exit 1
    eval "${envs}"
    # pgrx deletes the data directory on shutdown, and that is every
    # backend's working directory: a relative profile path is written
    # there and goes with it.
    if [[ ${LLVM_PROFILE_FILE:-} != /* ]]; then
      org_finding "" "pg${one}: the profile path '${LLVM_PROFILE_FILE:-}' is not absolute —" \
        "the backends write it inside the data directory pgrx deletes on shutdown"
      exit 1
    fi
    # Claimed for this run before it is cleaned (#2062): a second run
    # measuring in this checkout is refused by name, never mixed in.
    "${ORG_PYTHON}" "${belt}/coverage_owner.py" claim "${CARGO_TARGET_DIR}" \
      "${ORG_COVERAGE_RUN}" || exit 1
    # The workspace's own artifacts and old profiles go (#2061): `report`
    # reads every workspace executable in the directory, and one an earlier
    # build left (a version bump renames them all) joins the figure with
    # zero hits. Dependencies stay built. One caller per directory per run,
    # so no lock: the majors never share one.
    cargo llvm-cov clean --workspace >&2 || exit 1
    if ! (cd "${dir}" && cargo pgrx test "pg${one}") >&2; then
      org_finding "" "${dir} at pg${one} failed — a suite that does not pass measures nothing"
      exit 1
    fi
    if ! compgen -G "$(dirname "${LLVM_PROFILE_FILE}")/*.profraw" > /dev/null; then
      org_finding "" "${leg}: the suite passed and wrote no coverage profile — an extension" \
        "that measured nothing must not read as held (#1656)"
      exit 1
    fi
    cargo llvm-cov report -p "${crate}" --lcov --output-path "${reports}/lcov-${leg}.info" >&2 || exit 1
    pct=$(awk -F: '
      $1 == "LF" { found += $2 }
      $1 == "LH" { hit += $2 }
      END { if (found > 0) printf "%.16g\n", hit * 100 / found }
    ' "${reports}/lcov-${leg}.info" 2> /dev/null) || pct=""
    if [[ -z ${pct} ]]; then
      org_finding "" "${leg}: could not read a total from the report — a" \
        "measurement that reads nothing must not become a floor"
      exit 1
    fi
    echo "${leg} ${pct}"
  )
}

# Prints the leg lines, and in the gate holds them to the floor through
# coverage_floor.py, the one holder (#1875), as the job that owns each
# major this run measured: a leg below the floor is red, and so is a
# major the floor records and this run did not report. Its hold lines go
# to stderr, so stdout stays the leg lines the `pgrx` selector reads.
hold() { # usage: hold <leg lines> <major>...
  local lines="$1" one jobs=()
  shift
  printf '%s\n' "${lines}"
  [[ -n ${floor} ]] || return 0
  for one in "$@"; do jobs+=(--job "pgrx:${one}"); done
  "${ORG_PYTHON}" "${belt}/coverage_floor.py" hold --label "${label}" ${jobs[@]+"${jobs[@]}"} \
    <<< "${lines}" >&2
}

# The leg lines this run held, beside its reports (#1692): the gate's
# pgrx job keeps them for the release job, which derives the floor from
# them instead of measuring the extension again. Written after hold, so
# a leg below the floor keeps nothing.
keep() { # usage: keep <leg lines>
  printf '%s\n' "$1" > "${reports}/coverage-figures.txt"
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
    hold "${line}" "${one}"
    keep "${line}"
  else
    (cd "${dir}" && org_pgrx_major "${one}" cargo pgrx test "pg${one}")
  fi
  echo "${label}: 1 suite run: ${dir}(pg${one})"
  org_done=1
  exit 0
fi

# Several: side by side. Each gets its own target directory, because
# cargo locks a target directory for the length of a build and five
# builds sharing one would simply queue. Their Postgres is already
# separate — a data directory and a port per major.
logs=$(mktemp -d)
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
      CARGO_TARGET_DIR="${root}/pgrx-pg${one}" org_pgrx_major "${one}" cargo pgrx test "pg${one}"
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
  org_finding "" "failed at:${failed}"
  exit 1
fi
if [[ -n ${measured} ]]; then
  lines=$(cat "${logs}"/*.leg)
  majors=()
  for run in ${runs}; do majors+=("${run##*:}"); done
  hold "${lines}" "${majors[@]}"
  keep "${lines}"
fi
echo "${label}: ${count} suite run(s):${tested}"
org_done=1
