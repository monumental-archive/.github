#!/usr/bin/env bash
# The scheduled fuzz search for Rust (#316, #445, #1897), run by
# audit:fuzz, whose comment in mise/config.toml says what the run is
# and is not; a script rather than a task body, which the belt holds to
# fifteen lines. What a run keeps is fuzz-search-lib.sh's rule, measured
# by fuzz-measure-lib.sh's org_fuzz_measure, the coverage floor's own
# measurement.
#
# Run from the repository root, under the belt (ORG_PYTHON).
# FUZZ_SECONDS is each target's whole budget, the belt's and no
# repository's (#1899): 1800 by default, and one run gets all of it.
# FUZZ_TARGET, where set, names the one target to search, as
# repo-deep-tests.yml's matrix runs it, one runner per target.
# Unset, every target is searched, as a local run does; a name the
# search cannot find is red, naming it. Every step is checked where it
# is taken, as the libraries' are: a failing target is named and the
# run goes on to the next, so one crash never hides another's finds.
set -uo pipefail

# The scratch directory is removed however the run ends
# (completion-guarded, #1233).
work=""
failed=""
cleanup() {
  [[ -z ${work} ]] || rm -rf -- "${work}"
}
# The budget of each target (#1899).
budget=1800

# unknown <task> <what>: FUZZ_TARGET names no target the task can find.
unknown() {
  local in=""
  [[ -z ${FUZZ_PACKAGE:-} ]] || in=" in '${FUZZ_PACKAGE}'"
  echo "$1: FUZZ_TARGET '${FUZZ_TARGET}'${in} is no fuzz target $2" >&2
  echo "  repo-deep-tests.yml's matrix lists the targets audit_legs.py reads from the tree;" >&2
  echo "  a local run names one of the targets, or leaves FUZZ_TARGET unset to run every one" >&2
  exit 1
}

rc=0
trap 'rc=$?; cleanup; [ -n "${org_done:-}" ] || [ "${rc}" -ne 0 ] || rc=1; exit "${rc}"' EXIT

[[ -d fuzz && -f fuzz/Cargo.toml ]] || {
  [[ -z ${FUZZ_TARGET:-} ]] || unknown audit:fuzz "of this tree (no fuzz/)"
  echo "audit:fuzz: no fuzz/, skipped"
  org_done=1
  exit 0
}

# The libraries, once the run is known to apply.
belt=$(dirname "${BASH_SOURCE[0]}")
# shellcheck source=mise/fuzz-corpus-lib.sh
. "${belt}/fuzz-corpus-lib.sh"
# shellcheck source=mise/fuzz-measure-lib.sh
. "${belt}/fuzz-measure-lib.sh"
# shellcheck source=mise/fuzz-search-lib.sh
. "${belt}/fuzz-search-lib.sh"

# list_dir <dir>: sets `listing` to the names in <dir>, none when absent.
list_dir() {
  listing=""
  [[ -d $1 ]] || return 0
  listing=$(ls -A -- "$1")
}

command -v cargo-fuzz > /dev/null || {
  echo "audit:fuzz: cargo-fuzz missing (pin cargo:cargo-fuzz in mise.toml)" >&2
  exit 1
}
if [[ -z ${FUZZ_TOOLCHAIN:-} ]]; then
  echo "audit:fuzz: FUZZ_TOOLCHAIN is not declared — sanitized fuzzing needs a nightly toolchain" >&2
  echo "  AddressSanitizer is cargo-fuzz's default and needs -Zsanitizer, so stable cannot run it" >&2
  echo "  declare BOTH toolchains in this repo's mise.toml (mise holds and locks both):" >&2
  echo "    [tools]" >&2
  echo '    rust = ["1.xx.x", "nightly-YYYY-MM-DD"]' >&2
  echo "    [env]" >&2
  echo '    FUZZ_TOOLCHAIN = "nightly-YYYY-MM-DD"' >&2
  echo "  a dated nightly, never bare 'nightly' — a moving channel is not a pinned input" >&2
  exit 1
fi
if ! toolchains=$(rustup toolchain list 2> /dev/null) \
  || ! grep -q "^${FUZZ_TOOLCHAIN}" <<< "${toolchains}"; then
  echo "audit:fuzz: FUZZ_TOOLCHAIN='${FUZZ_TOOLCHAIN}' is declared but not installed" >&2
  echo "  it rides the rust pin in mise.toml so it installs before tasks run, never mid-run" >&2
  exit 1
fi
# --careful implies -Zbuild-std, which rebuilds the standard library from
# source and fails without rust-src. Asserted with a remedy rather than
# discovered halfway through a build, the llvm-tools/clippy pattern.
if ! src=$(rustup component list --installed --toolchain "${FUZZ_TOOLCHAIN}" 2> /dev/null) \
  || ! grep -q "^rust-src" <<< "${src}"; then
  echo "audit:fuzz: the nightly lacks rust-src, which --careful needs (it implies -Zbuild-std)" >&2
  echo "  components ride the pin, so declare them there rather than adding them by hand:" >&2
  echo "    rust = [\"1.xx.x\", { version = \"${FUZZ_TOOLCHAIN}\", components = \"rust-src\" }]" >&2
  exit 1
fi
secs="${FUZZ_SECONDS:-${budget}}"
targets=$(cargo fuzz list) || {
  echo "audit:fuzz: cargo fuzz list failed (above)" >&2
  exit 1
}
if [[ -n ${FUZZ_TARGET:-} ]]; then
  names=${targets//$'\n'/ }
  [[ $'\n'${targets}$'\n' == *$'\n'"${FUZZ_TARGET}"$'\n'* ]] \
    || unknown audit:fuzz "of fuzz/Cargo.toml (cargo fuzz list: ${names})"
  targets=${FUZZ_TARGET}
fi
# The search reads a scratch copy of the corpus and writes its finds to
# a second scratch directory, so the tracked corpus is never written.
org_fuzz_stage_dir || exit 1
work=$(mktemp -d) || exit 1
while IFS= read -r target; do
  [[ -n ${target} ]] || continue
  base="${work}/${target}/base" finds="${work}/${target}/finds" log="${work}/${target}/log"
  measured="${work}/${target}/measured" artifacts="fuzz/artifacts/${target}"
  # The tracked corpus, and what an unmerged proposal adds to it, so a
  # second run builds on a first one nobody has merged yet.
  if ! mkdir -p "${finds}" \
    || ! org_fuzz_corpus_copy "${target}" "${base}" > /dev/null \
    || ! org_fuzz_proposed "fuzz/corpus/${target}" "${base}" > /dev/null; then
    failed="${failed} ${target}(corpus)"
    continue
  fi
  list_dir "${artifacts}" || exit 1
  before=${listing}
  echo "audit:fuzz: ${target} for ${secs}s under AddressSanitizer (${FUZZ_TOOLCHAIN})"
  # finds/ first: libFuzzer writes what it finds into the first
  # directory. Each line is stamped with its second, for the summary.
  RUSTUP_TOOLCHAIN="${FUZZ_TOOLCHAIN}" cargo fuzz run -s address --careful "${target}" \
    "${finds}" "${base}" -- -max_total_time="${secs}" 2>&1 | org_fuzz_stamp | tee "${log}" \
    || failed="${failed} ${target}"
  # Every crash libFuzzer wrote this run is staged into the corpus, so
  # the gate's replay is red on the pull request until it is fixed.
  crashes=0
  list_dir "${artifacts}" || exit 1
  while IFS= read -r name; do
    [[ ${name} =~ ^(crash|timeout|oom)- ]] || continue
    [[ $'\n'${before}$'\n' != *$'\n'${name}$'\n'* ]] || continue
    if org_fuzz_stage "${artifacts}/${name}" "fuzz/corpus/${target}/${name}"; then
      crashes=$((crashes + 1))
    else
      failed="${failed} ${target}(stage)"
    fi
  done <<< "${listing}"
  # The candidates: what libFuzzer's merge adds to the corpus from the
  # finds, with the same flags. `cargo fuzz cmin` is never used: it
  # deletes and renames tracked seeds.
  if ! cp -R "${base}" "${measured}"; then
    failed="${failed} ${target}(merge)"
    continue
  fi
  if ! RUSTUP_TOOLCHAIN="${FUZZ_TOOLCHAIN}" cargo fuzz run -s address --careful "${target}" \
    "${base}" "${finds}" -- -merge=1 >&2; then
    echo "audit:fuzz: ${target}: libFuzzer's merge of the finds failed (above)" >&2
    failed="${failed} ${target}(merge)"
    continue
  fi
  candidates=()
  list_dir "${base}" || exit 1
  while IFS= read -r name; do
    [[ -z ${name} || -e ${measured}/${name} ]] || candidates+=("${base}/${name}")
  done <<< "${listing}"
  leg=$(org_fuzz_leg_name "${target}") || exit 1
  if ! kept=$(org_fuzz_select audit:fuzz "${leg}" "${measured}" "fuzz/corpus/${target}" \
    rust "${target}" "${measured}" -- ${candidates[@]+"${candidates[@]}"}); then
    failed="${failed} ${target}(measure)"
    continue
  fi
  read -r from to n <<< "${kept}"
  last=$(org_fuzz_last_new rust "${log}") || exit 1
  timing=$(org_fuzz_timing "${last}" "${secs}") || exit 1
  org_fuzz_summary "- \`${target}\` (${leg}): corpus ${from}% to ${to}%, ${n} input(s) kept,\
 ${crashes} crash(es); ${timing}"
done <<< "${targets}"
[[ -n ${ORG_FUZZ_CANDIDATES:-} ]] || echo "audit:fuzz: staged under ${org_fuzz_stage}"
if [[ -n ${failed} ]]; then
  echo "audit:fuzz: failing target(s):${failed}" >&2
  echo "  a crash's input is staged at fuzz/corpus/<target>/crash-<hash> for repo-deep-tests.yml" >&2
  echo "  to propose; replay it with: cargo fuzz run <target> fuzz/artifacts/<target>/crash-<hash>" >&2
  exit 1
fi
echo "audit:fuzz: all targets ran ${secs}s clean under ASan"
org_done=1
