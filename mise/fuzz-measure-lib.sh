#!/usr/bin/env bash
# A fuzz target's leg name and its figure (#1896, #1897). Sourced, never
# run: the coverage floor's fuzz leg (coverage-measure.sh) and the
# scheduled search (fuzz-search-rust.sh, fuzz-search-go.sh) measure
# through the one function here, so the search keeps a find by exactly
# the figure the floor holds. Kept out of fuzz-corpus-lib.sh, which
# lint:fuzz-build's replay sources and which needs none of these tools.

# org_fuzz_leg_name <name>: a fuzz target's leg (#1896), `fuzz-<name>`
# lower-cased, every character but [a-z0-9] turned into `-`, so it fits
# coverage_floor.py's leg grammar whatever the target is called.
org_fuzz_leg_name() {
  local name
  name=$(LC_ALL=C tr '[:upper:]' '[:lower:]' <<< "$1")
  printf 'fuzz-%s\n' "${name//[^a-z0-9]/-}"
}

# org_fuzz_measure <label> <leg> rust <target> <corpus>
# org_fuzz_measure <label> <leg> go <package dir> <Fuzz name>
#
# A fuzz target's figure (#1896): the line coverage of replaying its
# corpus once, printed as a bare percent. The ONE measurement: the
# coverage floor's fuzz leg (coverage-measure.sh) hands it the tracked
# copy org_fuzz_corpus_copy made, and the scheduled search (#1897,
# fuzz-search-lib.sh) hands it that copy plus a candidate, so the
# search's figure and the floor's cannot differ. It reads no
# environment variable of its own.
#
# Rust: <corpus> replayed by `cargo fuzz coverage -s none` into a build
# directory of its compiler (#1877); the figure is llvm-cov's over the
# tracked sources outside fuzz/fuzz_targets/. A fuzz/coverage it made is
# removed before it returns. Go: <package dir> is from the repository
# root; the module is the nearest directory above it holding a go.mod,
# the root's or a nested one's (#1899), and `go test -run '^<name>$'`,
# which replays exactly the package's seed corpus (f.Add and its
# testdata), runs inside that module on the package's path relative to
# it, with -coverpkg naming the module's packages the package imports.
#
# Needs ORG_PYTHON, and for Rust cargo-fuzz and llvm-tools, which the
# caller checks. Returns 1, naming <leg> and the step, on any failure.
org_fuzz_measure() {
  local label=$1 leg=$2 kind=$3
  case "${kind}" in
    rust) org__fuzz_measure_rust "${label}" "${leg}" "$4" "$5" ;;
    go) org__fuzz_measure_go "${label}" "${leg}" "$4" "$5" ;;
    *)
      echo "${label}: ${leg}: unknown fuzz target kind '${kind}'" >&2
      return 1
      ;;
  esac
}

org__fuzz_measure_rust() {
  local label=$1 leg=$2 target=$3 corpus=$4
  local belt root file tracked sources=() verbose host sysroot llvm_cov metadata dir compiler
  local build made="" first exported totals count covered status=0
  belt=$(dirname "${BASH_SOURCE[0]}")
  first=$(find "${corpus}" -type f -print -quit 2> /dev/null) || first=""
  if [[ -z ${first} ]]; then
    echo "${label}: ${leg}: fuzz target '${target}' has no committed corpus — a" >&2
    echo "  measurement that reads nothing must not become a floor (lint:fuzz-build names the remedy)" >&2
    return 1
  fi
  # The tracked sources, the figure's surface: everything the repository
  # tracks as Rust outside the harnesses themselves, by absolute path as
  # cargo records it. A file the target does not link counts nothing,
  # and nothing outside the repository (registry, toolchain, generated
  # code) is named.
  root=$(pwd -P)
  tracked=$(git ls-files -- '*.rs' ':!fuzz/fuzz_targets/**') || return 1
  while IFS= read -r file; do
    [[ -z ${file} ]] || sources+=("${root}/${file}")
  done <<< "${tracked}"
  verbose=$(rustc -vV) || return 1
  host=$(sed -n 's/^host: //p' <<< "${verbose}")
  sysroot=$(rustc --print sysroot) || return 1
  llvm_cov="${sysroot}/lib/rustlib/${host}/bin/llvm-cov"
  if [[ -z ${host} || ! -x ${llvm_cov} ]]; then
    echo "${label}: no llvm-cov at ${llvm_cov} — the llvm-tools component is declared but" >&2
    echo "  its binaries are not where the toolchain keeps them" >&2
    return 1
  fi
  # A build directory of the compiler's own (#1877), beside the fuzz
  # crate's other builds: a toolchain bump is a new, empty directory,
  # never an old compiler's binary read as this one's.
  metadata=$(cargo metadata --no-deps --format-version 1 --locked --manifest-path fuzz/Cargo.toml) || return 1
  dir=$(jq -r '.target_directory // empty' <<< "${metadata}") || return 1
  if [[ -z ${dir} ]]; then
    echo "${label}: cargo metadata named no target_directory for fuzz/ — nowhere to build into" >&2
    return 1
  fi
  compiler=$("${ORG_PYTHON:?}" "${belt}/gate_cache_key.py" digest) || return 1
  build="${dir}/coverage-${compiler}"
  # cargo fuzz coverage writes its profiles under fuzz/coverage/<target>/
  # and offers no other place; a directory this call made is removed
  # before it returns, and a target's stale raw profiles are cleared
  # before its run, which would otherwise merge into this one's.
  [[ -e fuzz/coverage ]] || made=${root}/fuzz/coverage
  rm -rf -- "fuzz/coverage/${target}/raw"
  if ! cargo fuzz coverage -s none --target-dir "${build}" "${target}" "${corpus}" >&2; then
    echo "${label}: ${leg}: cargo fuzz coverage failed replaying the committed corpus (above)" >&2
    status=1
  fi
  if ((status == 0)); then
    exported=$("${llvm_cov}" export -summary-only \
      -instr-profile="fuzz/coverage/${target}/coverage.profdata" \
      "${build}/${host}/release/${target}" ${sources[@]+"${sources[@]}"}) || exported=""
    totals=$(jq -r '.data[0].totals.lines | "\(.count) \(.covered)"' <<< "${exported}") || totals=""
    read -r count covered <<< "${totals}"
  fi
  [[ -z ${made} ]] || rm -rf -- "${made}"
  ((status == 0)) || return 1
  if [[ ! ${count:-} =~ ^[0-9]+$ || ! ${covered:-} =~ ^[0-9]+$ || ${count} == 0 ]]; then
    echo "${label}: ${leg}: llvm-cov counted no tracked line (count '${count:-absent}') — a" >&2
    echo "  measurement that reads nothing must not become a floor" >&2
    return 1
  fi
  awk -v n="${count}" -v c="${covered}" 'BEGIN { printf "%.16g\n", c * 100 / n }'
}

org__fuzz_measure_go() {
  local label=$1 leg=$2 dir=$3 name=$4 module rel pkgs pkg coverpkg="" work cover pct="" status=0
  # The package's module, as Go resolves it: the nearest go.mod above.
  module=${dir}
  while [[ ! -f ${module}/go.mod ]]; do
    if [[ ${module} == . ]]; then
      echo "${label}: ${leg}: no go.mod above ${dir} — no module owns ${name}" >&2
      return 1
    fi
    module=$(dirname "${module}")
  done
  rel=${dir#"${module}"/}
  [[ ${module} != "${dir}" ]] || rel=.
  # The module's packages the target's package imports, itself included:
  # the code its seeds can reach.
  pkgs=$(cd "${module}" && go list -deps -f '{{if and .Module .Module.Main}}{{.ImportPath}}{{end}}' "./${rel}") \
    || return 1
  while IFS= read -r pkg; do
    [[ -z ${pkg} ]] || coverpkg+="${coverpkg:+,}${pkg}"
  done <<< "${pkgs}"
  if [[ -z ${coverpkg} ]]; then
    echo "${label}: ${leg}: go list named no package of the module for ./${dir}" >&2
    return 1
  fi
  work=$(mktemp -d) || return 1
  if ! (cd "${module}" && go test -count=1 -run "^${name}\$" -coverpkg="${coverpkg}" \
    -coverprofile="${work}/${leg}.out" "./${rel}") >&2; then
    echo "${label}: ${leg}: ${name}'s seed corpus failed under measurement (above)" >&2
    status=1
  fi
  if ((status == 0)); then
    cover=$(cd "${module}" && go tool cover -func="${work}/${leg}.out") || cover=""
    pct=$(awk '/^total:/ { sub(/%/, "", $3); print $3 }' <<< "${cover}")
  fi
  rm -rf -- "${work}"
  ((status == 0)) || return 1
  if [[ -z ${pct} ]]; then
    echo "${label}: ${leg}: could not read a total from the Go profile — a" >&2
    echo "  measurement that reads nothing must not become a floor" >&2
    return 1
  fi
  echo "${pct}"
}
