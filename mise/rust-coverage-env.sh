#!/usr/bin/env bash
# The Rust measurement's environment (#1653). Sourced, never run:
# `measured` and coverage-measure.sh's rust leg share it, so the
# directory the suite is instrumented into and the one the leg reports
# from are the same derivation, worked out once.
#
# org_rust_coverage_env <label>
#
# Exports CARGO_TARGET_DIR as `<target_directory>/llvm-cov-target-<digest>`
# (`target_directory` from `cargo metadata --no-deps`), and THEN what
# `cargo llvm-cov show-env
# --sh` prints for it. The order is the mechanism, probed on release-lab
# with cargo-llvm-cov 0.9.1: exported first, LLVM_PROFILE_FILE is under
# it and `cargo llvm-cov report` finds the run; exported after, the
# profiles go to the repository's own `target/` and `report` fails.
#
# A directory of its own, never the plain build's: the instrumentation
# rides RUSTC_WRAPPER, which cargo's fingerprint does not see, so in a
# directory a plain `cargo test` built the run reuses uninstrumented
# binaries and writes no profile (probed the same day; pgrx-test.sh's
# measured runs rest on the same fact, #1652).
#
# And a directory of its own compiler (#1877): `<digest>` is
# `gate_cache_key.py digest`, 16 hex of the `rustc -vV` cargo builds
# with. `cargo llvm-cov report` reads every test executable in the
# directory, and a toolchain bump that moves where they go (Rust 1.100:
# `deps/` to `build/*/out/`) leaves the old compiler's beside the new
# one's: edtf measured 95.77% over the mixed directory, 99.37% fresh. A
# compiler change is a new, empty directory instead. Nothing prunes the
# old one: the nightly leg may be measuring into its own beside this.
#
# One step per assignment, never a pipeline inside `$(...)`: a sourced
# file cannot rely on its caller's pipefail.
org_rust_coverage_env() {
  local label="$1" metadata target compiler envs
  # The belt Python names the compiler (#1877): refused first, as every
  # helper start is (test_canon_python).
  : "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"
  metadata=$(cargo metadata --no-deps --format-version 1 --locked) || return 1
  target=$(jq -r '.target_directory // empty' <<< "${metadata}") || return 1
  if [[ -z ${target} ]]; then
    echo "${label}: cargo metadata named no target_directory — nowhere to measure Rust into" >&2
    return 1
  fi
  compiler=$("${ORG_PYTHON}" "$(dirname "${BASH_SOURCE[0]}")/gate_cache_key.py" digest) || return 1
  export CARGO_TARGET_DIR="${target}/llvm-cov-target-${compiler}"
  envs=$(cargo llvm-cov show-env --sh) || return 1
  eval "${envs}"
}
