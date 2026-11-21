#!/usr/bin/env bash
# The scheduled fuzz search, both languages (#316, #445, #1897). Run by
# audit:fuzz (`rust`) and audit:go-fuzz (`go`), whose comments in
# mise/config.toml say what each run is and is not; a script rather than
# two task bodies, which the belt holds to fifteen lines. What a run
# keeps is fuzz-search-lib.sh's rule, measured by fuzz-corpus-lib.sh's
# org_fuzz_measure, the coverage floor's own measurement.
#
#   fuzz-search.sh rust|go
#
# Run from the repository root, under the belt (ORG_PYTHON);
# FUZZ_SECONDS bounds each run (default 60). Every step is checked where
# it is taken, as the two libraries' are: a failing target is named and
# the run goes on to the next, so one crash never hides another target's
# finds.
set -uo pipefail

belt=$(dirname "${BASH_SOURCE[0]}")
# shellcheck source=mise/fuzz-corpus-lib.sh
. "${belt}/fuzz-corpus-lib.sh"
# shellcheck source=mise/fuzz-search-lib.sh
. "${belt}/fuzz-search-lib.sh"

# list_dir <dir>: sets `listing` to the names in <dir>, none when absent.
list_dir() {
  listing=""
  [[ -d $1 ]] || return 0
  listing=$(ls -A -- "$1")
}

search_rust() {
  local secs targets target base finds log measured artifacts before name crashes
  local candidates leg kept from to n last timing failed=""
  [[ -d fuzz && -f fuzz/Cargo.toml ]] || {
    echo "audit:fuzz: no fuzz/, skipped"
    org_done=1
    exit 0
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
  secs="${FUZZ_SECONDS:-60}"
  targets=$(cargo fuzz list) || {
    echo "audit:fuzz: cargo fuzz list failed (above)" >&2
    exit 1
  }
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
}

search_go() {
  local mods secs root ran=0 m d pkgs pkg targets target pdir rel seeds log untracked
  local proposed name file crashes cache found candidates leg kept from to n last plain race
  local others failed=""
  mods=$(git ls-files ':!vendor/**' "go.mod" "*/go.mod" "*/*/go.mod") || exit 1
  [[ -n ${mods} ]] || {
    echo "audit:go-fuzz: no go.mod tracked, skipped"
    org_done=1
    exit 0
  }
  command -v go > /dev/null || {
    echo "audit:go-fuzz: go.mod present but go missing — pin go in mise.toml" >&2
    exit 1
  }
  secs="${FUZZ_SECONDS:-60}"
  # Go replays a target's seeds from its package's testdata alone, so a
  # proposed seed and each candidate are placed there while it is
  # measured and removed after: the tree is left as found, but for a
  # failing input, which is moved to the stage.
  org_fuzz_stage_dir || exit 1
  work=$(mktemp -d) || exit 1
  root=$(pwd -P) || exit 1
  while IFS= read -r m; do
    [[ -n ${m} ]] || continue
    d=$(dirname "${m}")
    pkgs=$(cd "${d}" && go list ./... 2> /dev/null) || pkgs=""
    while IFS= read -r pkg; do
      [[ -n ${pkg} ]] || continue
      targets=$(cd "${d}" && go test -list '^Fuzz' "${pkg}" 2> /dev/null) || targets=""
      while IFS= read -r target; do
        [[ ${target} == Fuzz* ]] || continue
        ran=$((ran + 1))
        if ! pdir=$(cd "${d}" && go list -f '{{.Dir}}' "${pkg}"); then
          failed="${failed} ${pkg}.${target}(list)"
          continue
        fi
        rel=${pdir#"${root}"/}
        [[ ${pdir} != "${root}" ]] || rel=.
        seeds="${rel}/testdata/fuzz/${target}"
        [[ ${rel} != . ]] || seeds="testdata/fuzz/${target}"
        log="${work}/${ran}"
        # What an unmerged proposal holds for this target, placed among
        # its seeds for the run, so a second run builds on the first.
        if ! untracked=$(git ls-files -o -- "${seeds}/") \
          || ! proposed=$(org_fuzz_proposed "${seeds}" "${seeds}"); then
          failed="${failed} ${pkg}.${target}(corpus)"
          continue
        fi
        while IFS= read -r name; do
          [[ -z ${name} ]] || placed+=("${seeds}/${name}")
        done <<< "${proposed}"
        # The cache is the finds, so it starts empty for each target.
        if ! (cd "${d}" && go clean -fuzzcache); then
          failed="${failed} ${pkg}.${target}(cache)"
          continue
        fi
        echo "audit:go-fuzz: ${target} in ${pkg} for ${secs}s, plain"
        (cd "${d}" && go test -fuzz "^${target}\$" -fuzztime "${secs}s" "${pkg}") 2>&1 \
          | tee "${log}.plain" || failed="${failed} ${pkg}.${target}(plain)"
        echo "audit:go-fuzz: ${target} in ${pkg} for ${secs}s, race detector on"
        (cd "${d}" && CGO_ENABLED=1 go test -race -fuzz "^${target}\$" -fuzztime "${secs}s" "${pkg}") 2>&1 \
          | tee "${log}.race" || failed="${failed} ${pkg}.${target}(race)"
        # A failing input Go wrote among the seeds: staged as crash-<name>
        # and moved out of the tree, where it would fail the measurement.
        crashes=0
        others=$(git ls-files -o -- "${seeds}/") || exit 1
        while IFS= read -r file; do
          [[ -n ${file} && $'\n'${untracked}$'\n' != *$'\n'${file}$'\n'* ]] || continue
          [[ " ${placed[*]-} " != *" ${file} "* ]] || continue
          if org_fuzz_stage "${file}" "${seeds}/crash-${file##*/}" && rm -f -- "${file}"; then
            crashes=$((crashes + 1))
          else
            failed="${failed} ${pkg}.${target}(stage)"
          fi
        done <<< "${others}"
        cache=$(cd "${d}" && go env GOCACHE) || exit 1
        cache="${cache}/fuzz/${pkg}/${target}"
        candidates=()
        found=0
        list_dir "${cache}" || exit 1
        while IFS= read -r name; do
          [[ -n ${name} ]] || continue
          candidates+=("${cache}/${name}")
          found=$((found + 1))
        done <<< "${listing}"
        # The coverage floor's fuzz leg measures the root module's targets
        # (#1896); a nested module's finds have no figure to raise.
        if [[ ${d} != . ]]; then
          org_fuzz_summary "- \`${target}\` in ${pkg}: ${found} find(s) not measured (nested module),\
 ${crashes} crash(es)"
          continue
        fi
        if [[ ${rel} == . ]]; then
          leg=$(org_fuzz_leg_name "${target#Fuzz}") || exit 1
        else
          leg=$(org_fuzz_leg_name "${rel}-${target#Fuzz}") || exit 1
        fi
        if ! kept=$(org_fuzz_select audit:go-fuzz "${leg}" "${seeds}" "${seeds}" \
          go "${rel}" "${target}" -- ${candidates[@]+"${candidates[@]}"}); then
          failed="${failed} ${pkg}.${target}(measure)"
          continue
        fi
        read -r from to n <<< "${kept}"
        last=$(org_fuzz_last_new go "${log}.plain") || exit 1
        plain=$(org_fuzz_timing "${last}" "${secs}") || exit 1
        last=$(org_fuzz_last_new go "${log}.race") || exit 1
        race=$(org_fuzz_timing "${last}" "${secs}") || exit 1
        org_fuzz_summary "- \`${target}\` in ${pkg} (${leg}): corpus ${from}% to ${to}%,\
 ${n} input(s) kept, ${crashes} crash(es); plain: ${plain}; race: ${race}"
      done <<< "${targets}"
    done <<< "${pkgs}"
  done <<< "${mods}"
  [[ ${ran} -gt 0 ]] || {
    echo "audit:go-fuzz: no Go fuzz targets, skipped"
    org_done=1
    exit 0
  }
  [[ -n ${ORG_FUZZ_CANDIDATES:-} ]] || echo "audit:go-fuzz: staged under ${org_fuzz_stage}"
  if [[ -n ${failed} ]]; then
    echo "audit:go-fuzz: failing target(s):${failed}" >&2
    echo "  a failing input is staged as testdata/fuzz/<Target>/crash-<name> for repo-deep-tests.yml" >&2
    echo "  to propose; its original name is in the output above" >&2
    exit 1
  fi
  echo "audit:go-fuzz: ${ran} target(s) ran ${secs}s clean"
}

# The scratch directory and the seeds placed in the tree are removed
# however the run ends (completion-guarded, #1233).
work=""
placed=()
cleanup() {
  rm -f -- ${placed[@]+"${placed[@]}"}
  [[ -z ${work} ]] || rm -rf -- "${work}"
}
rc=0
trap 'rc=$?; cleanup; [ -n "${org_done:-}" ] || [ "${rc}" -ne 0 ] || rc=1; exit "${rc}"' EXIT
case "${1:-}" in
  rust) search_rust ;;
  go) search_go ;;
  *)
    echo "usage: fuzz-search.sh rust|go" >&2
    exit 1
    ;;
esac
org_done=1
