#!/usr/bin/env bash
# The committed corpus of one cargo-fuzz target, as the belt reads it
# (#1889, #1896). Sourced, never run: lint:fuzz-build's replay
# (fuzz-corpus.sh) and the coverage floor's fuzz leg
# (coverage-measure.sh) both copy through it, so the corpus the gate
# replays and the corpus whose depth it measures cannot differ.
#
# org_fuzz_corpus_copy <target> <dest>
#
# Copies every TRACKED file under fuzz/corpus/<target>/ into <dest>,
# keeping its path below the corpus directory, links followed (`cp -L`):
# a seed that is a link to a fixture is the fixture's bytes, and neither
# cargo fuzz nor libFuzzer follows a link (iiif-server's `master_open`,
# measured: "The corpus does not contain program-input files"). Prints
# the number of files copied on stdout and copies nothing else: the
# tree's corpus directory is never handed to a tool, so an untracked
# local find is never read. Run from the repository root.
#
# Returns 1, naming the step, when git cannot list the corpus or a file
# cannot be copied. Every step is checked here rather than left to
# errexit, which a caller's `$(...)` or `if` switches off.
# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here

org_fuzz_corpus_copy() {
  local target="$1" dest="$2" list file to copied=0
  list=$(mktemp) || return 1
  if ! org_tracked "fuzz/corpus/${target}/" > "${list}"; then
    rm -f -- "${list}"
    org_finding "" "git could not list the tracked fuzz/corpus/${target}/"
    return 1
  fi
  while IFS= read -r -d '' file; do
    to="${dest}/${file#"fuzz/corpus/${target}/"}"
    if ! mkdir -p -- "${to%/*}" || ! cp -L -- "${file}" "${to}"; then
      org_finding "" "could not copy ${file} out of the tree"
      copied=-1
      break
    fi
    copied=$((copied + 1))
  done < "${list}"
  rm -f -- "${list}"
  ((copied >= 0)) || return 1
  echo "${copied}"
}
