#!/usr/bin/env bash
# The scheduled fuzz search for Go (#316, #445, #1897), run by
# audit:go-fuzz, whose comment in mise/config.toml says what the run is
# and is not; a script rather than a task body, which the belt holds to
# fifteen lines. What a run keeps is fuzz-search-lib.sh's rule, measured
# by fuzz-measure-lib.sh's org_fuzz_measure, the coverage floor's own
# measurement.
#
# Run from the repository root, under the belt (ORG_PYTHON);
# FUZZ_SECONDS bounds each run (default 60). Every step is checked where
# it is taken, as the libraries' are: a failing target is named and the
# run goes on to the next, so one crash never hides another's finds.
set -uo pipefail

# The scratch directory and the seeds placed in the tree are removed
# however the run ends (completion-guarded, #1233).
work=""
failed=""
ran=0
placed=()
cleanup() {
  local seed
  for seed in ${placed[@]+"${placed[@]}"}; do
    rm -- "${seed}"
  done
  [[ -z ${work} ]] || rm -rf -- "${work}"
}
rc=0
trap 'rc=$?; cleanup; [ -n "${org_done:-}" ] || [ "${rc}" -ne 0 ] || rc=1; exit "${rc}"' EXIT

mods=$(git ls-files ':!vendor/**' "go.mod" "*/go.mod" "*/*/go.mod") || exit 1
[[ -n ${mods} ]] || {
  echo "audit:go-fuzz: no go.mod tracked, skipped"
  org_done=1
  exit 0
}

# The libraries, once the run is known to apply.
belt=$(dirname "${BASH_SOURCE[0]}")
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
org_done=1
