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
# Usage: fuzz-corpus.sh <count>, the target count the task measured, for
# the closing line.
set -euo pipefail

count="${1:?usage: fuzz-corpus.sh <target count>}"
if ! targets=$(python3 -c 'import sys, tomllib
for b in tomllib.load(open(sys.argv[1], "rb")).get("bin", []):
    print(b["name"])' fuzz/Cargo.toml); then
  echo "lint:fuzz-build: could not read the [[bin]] targets of fuzz/Cargo.toml" >&2
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
  if ! git ls-files -z -- "fuzz/corpus/${target}/" > "${scratch}/.list"; then
    echo "lint:fuzz-build: git ls-files failed listing fuzz/corpus/${target}/" >&2
    exit 1
  fi
  n=0
  while IFS= read -r -d '' file; do
    dest="${scratch}/${target}/${file#"fuzz/corpus/${target}/"}"
    mkdir -p "${dest%/*}"
    cp -L -- "${file}" "${dest}"
    n=$((n + 1))
  done < "${scratch}/.list"
  if ((n == 0)); then
    missing+=("${target}")
  else
    replay+=("${target}")
    seeds=$((seeds + n))
  fi
done <<< "${targets}"

if ((${#missing[@]} > 0)); then
  for target in "${missing[@]}"; do
    echo "lint:fuzz-build: fuzz target '${target}' has no committed corpus (no tracked file under fuzz/corpus/${target}/)" >&2
  done
  echo "  commit seed inputs under fuzz/corpus/<target>/ — the gate replays them on every pull request" >&2
  echo "  and make sure fuzz/.gitignore does not ignore corpus/" >&2
  exit 1
fi

failed=0
for target in "${replay[@]}"; do
  if ! report=$(cargo fuzz run -s none "${target}" "${scratch}/${target}" -- -runs=0 -artifact_prefix="${scratch}/" 2>&1); then
    echo "lint:fuzz-build: fuzz target '${target}' fails on its committed corpus — libFuzzer says:" >&2
    printf '%s\n' "${report}" >&2
    failed=1
  fi
done
if ((failed)); then
  echo "  every tracked file under fuzz/corpus/<target>/ is replayed once; fix the target or the seed" >&2
  exit 1
fi

echo "lint:fuzz-build: ${count} target(s) build, lockfile current, ${seeds} committed seed(s) replay clean"
org_done=1
