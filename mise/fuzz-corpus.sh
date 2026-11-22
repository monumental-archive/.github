#!/usr/bin/env bash
# lint:fuzz-build's corpus half (#1889): every cargo-fuzz target has a
# committed corpus, and that corpus replays clean. Run from the repository
# root after the task's `cargo fuzz build -s none`, whose build the replay
# reuses, so nothing is rebuilt.
#
# The contract had been a sentence in audit:fuzz's comment and nothing held
# it: six of the org's seven Rust targets fuzzed from an empty corpus every
# Monday, green. So every [[bin]] in fuzz/Cargo.toml needs at least one
# TRACKED file under fuzz/corpus/<target>/, and each is run through the
# target once with no mutation (`-runs=0`): the corpus becomes the
# regression suite, the Rust half of lint:go-fuzz-seeds. What the seeds are
# is the repository's own business; the canon checks only that they exist
# and pass.
#
# A LINT MUST NOT WRITE (#374), and belt linters read tracked files only.
# The tracked files (links followed) are copied to a scratch directory and
# only that directory is handed to cargo fuzz run, so libFuzzer never sees
# the tree's corpus directory and an untracked local find is never run.
# cargo-fuzz passes -artifact_prefix=fuzz/artifacts/<target>/, where
# libFuzzer writes a crashing input even under -runs=0; libFuzzer takes the
# last value given, so the replay names the scratch directory after it.
#
# The copy is org_fuzz_corpus_copy (fuzz-corpus-lib.sh), the one the
# coverage floor's fuzz leg measures through (#1896), so the corpus
# replayed here and the corpus measured there cannot differ.
#
# Usage: fuzz-corpus.sh <count>, the target count the task measured, for
# the closing line.
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here
# The task this script reports for, where mise named none (#1934).
: "${MISE_TASK_NAME:=lint:fuzz-build}"

# shellcheck source=mise/fuzz-corpus-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/fuzz-corpus-lib.sh"

count="${1:?usage: fuzz-corpus.sh <target count>}"
if ! targets=$(python3 -c 'import sys, tomllib
for b in tomllib.load(open(sys.argv[1], "rb")).get("bin", []):
    print(b["name"])' fuzz/Cargo.toml); then
  org_finding "" "could not read the [[bin]] targets of fuzz/Cargo.toml"
  exit 1
fi

scratch=$(mktemp -d)
rc=0
trap 'rc=$?; rm -rf "${scratch}"; [[ -n ${org_done:-} || ${rc} -ne 0 ]] || rc=1; exit "${rc}"' EXIT

missing=()
replay=()
seeds=0
while IFS= read -r target; do
  [[ -n ${target} ]] || continue
  # A copy that fails says why itself and ends the run (errexit).
  n=$(org_fuzz_corpus_copy "${target}" "${scratch}/${target}")
  if ((n == 0)); then
    missing+=("${target}")
  else
    replay+=("${target}")
    seeds=$((seeds + n))
  fi
done <<< "${targets}"

if ((${#missing[@]} > 0)); then
  for target in "${missing[@]}"; do
    org_finding "" "fuzz target '${target}' has no committed corpus (no tracked file under fuzz/corpus/${target}/)" \
      "commit seed inputs under fuzz/corpus/<target>/ — the gate replays them on every pull request" \
      "and make sure fuzz/.gitignore does not ignore corpus/"
  done
  exit 1
fi

failed=0
for target in "${replay[@]}"; do
  if ! report=$(cargo fuzz run -s none "${target}" "${scratch}/${target}" -- -runs=0 -artifact_prefix="${scratch}/" 2>&1); then
    org_finding "" "fuzz target '${target}' fails on its committed corpus — libFuzzer says:" \
      "every tracked file under fuzz/corpus/<target>/ is replayed once; fix the target or the seed"
    printf '%s\n' "${report}" >&2
    failed=1
  fi
done
((failed == 0)) || exit 1

echo "lint:fuzz-build: ${count} target(s) build, lockfile current, ${seeds} committed seed(s) replay clean"
org_done=1
