#!/usr/bin/env bash
# A repository's proptest properties, under the belt's seed.
# Org canon — the body of `lint:rust-properties` (the gate, `lint`) and
# `audit:rust-properties` (the nightly search, `audit`), one script so
# the two tasks, and audit_legs.py's `rust-properties` output, read the
# lock files the same way (#1930, the Rust half of #1332's shape).
#
# The gate's seed is not set here: proptest reads PROPTEST_RNG_SEED
# from the environment, and the belt's `[env]` sets it from
# ORG_PROPERTY_SEED, `1` when that is unset, so the repository's own
# `test` task draws the same inputs on every gate run. What the gate
# checks is that the seed reaches proptest: a locked proptest below
# 1.7.0 ignores the variable without a word (`lint`).
#
# The audit draws a fresh seed, prints it before anything runs, and
# runs the repository's own `test` task under it, with the
# repository's own case counts: a larger PROPTEST_CASES aborts a
# correct test on proptest's fixed reject limit (measured on edtf,
# docs/tooling-verdicts.md). A failure leaves proptest's minimal
# failing input in the log; `ORG_PROPERTY_SEED=<n> mise run test`
# reproduces it and writes proptest's line into the
# *.proptest-regressions file beside the test, which is committed with
# the fix and replayed first by every later run, the gate's included.
# The scheduled run never pushes.
set -euo pipefail

profile="${1:-}"
case "${profile}" in
  lint) label="lint:rust-properties" ;;
  audit) label="audit:rust-properties" ;;
  *)
    echo "rust-properties.sh: expected lint or audit, got '${profile}'" >&2
    exit 2
    ;;
esac
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"
python="${ORG_PYTHON:?${ORG_PYTHON_UNSET:-unset — the belt Python}}"

# The lock files decide: no proptest in one, nothing here is a property.
locks=$(git ls-files ':!vendor/**' Cargo.lock '*/Cargo.lock')
pins=""
if [[ -n ${locks} ]]; then
  pins=$(tr '\n' '\0' <<< "${locks}" | xargs -0 "${python}" "${belt}/rust_properties.py" locked) || {
    echo "${label}: a tracked Cargo.lock could not be read (above)" >&2
    exit 1
  }
fi
[[ -n ${pins} ]] || {
  echo "${label}: no tracked Cargo.lock locks proptest, skipped"
  exit 0
}

if [[ ${profile} == lint ]]; then
  findings=$(tr '\n' '\0' <<< "${locks}" | xargs -0 "${python}" "${belt}/rust_properties.py" lint) || {
    echo "${label}: proptest below 1.7.0 ignores PROPTEST_RNG_SEED, so the gate's seed never reaches it:" >&2
    while IFS= read -r finding; do
      echo "  ${finding}" >&2
    done <<< "${findings}"
    echo "  update proptest to 1.7.0 or later (cargo update -p proptest)" >&2
    exit 1
  }
  while IFS= read -r pin; do
    echo "${label}: ${pin}, reads the belt's seed"
  done <<< "${pins}"
  exit 0
fi

# The properties run inside the repository's own `test` task: with
# none, nothing runs them, and a green audit would say nothing.
if ! mise tasks info test > /dev/null 2>&1; then
  files=$(awk -F': proptest ' '{ print $1 }' <<< "${pins}" | sort -u | paste -sd ' ' -)
  echo "${label}: ${files} locks proptest, but the repository defines no test task —" >&2
  echo "  nothing runs its properties; the gate's and this run's seed reach proptest through mise run test" >&2
  exit 1
fi

seed=$("${python}" "${belt}/rust_properties.py" seed) || exit 2
echo "${label}: seed ${seed} (reproduce: ORG_PROPERTY_SEED=${seed})"
status=0
ORG_PROPERTY_SEED="${seed}" mise run test || status=$?
if ((status != 0)); then
  echo "${label}: seed ${seed} — ORG_PROPERTY_SEED=${seed} mise run test reproduces it and" >&2
  echo "  writes proptest's line into the *.proptest-regressions file beside the test;" >&2
  echo "  commit that line with the fix, and every run, the gate's included, replays it first" >&2
fi
exit "${status}"
