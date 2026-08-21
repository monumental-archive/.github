#!/usr/bin/env bash
# Release phase 1, step 1c: re-derive the coverage floor from the tree
# being released. Org canon — see docs/release.md and #652.
#
# The floor is DERIVED STATE, like the version, the changelog and the
# pgrx upgrade scripts beside it: the machinery measures, writes
# `floor = measured - band`, and the file rides the release commit. It
# was a hand-typed number until the org measured what that costs —
# stele's ceiling fell 6.5 points in two days with nothing red and
# nothing noticed, because the floor sat far enough below the ceiling
# that the headroom became a landing zone.
#
# The ratchet law lives in mise/coverage-floor.py, with the drift check
# `coverage:check` runs, so there is one definition of "the floor only
# rises" rather than a release-time copy of it. If the measurement is
# below `floor + band`, that script refuses and this step fails the
# Release PR — loudly, before a version number is spent.
#
# Writes GITHUB_OUTPUT keys:
#   files  `.coverage-floor` when there is a floor to carry, else empty
#
# Leaves the working tree modified; open-release-pr.sh commits it via the
# API, as EXTRA_FILES.
set -euo pipefail

: "${VERSION:?VERSION must be set (the version being released)}"

emit() {
  if [[ -n ${GITHUB_OUTPUT:-} ]]; then
    echo "$1" >> "${GITHUB_OUTPUT}"
  fi
}

# Opt-in, unchanged and explicit (#652): a repository with no floor has
# adopted no coverage ratchet and owes nothing. The belt offers the
# mechanism; the committed file is the repository's adoption.
if [[ ! -f .coverage-floor ]]; then
  echo "no .coverage-floor, skipped"
  emit "files="
  exit 0
fi

# A floor with no measurable language is a repository mid-migration, not
# a release to fail: `coverage:check` skips the same shape.
if [[ ! -f Cargo.toml && ! -f go.mod ]]; then
  echo ".coverage-floor with no Cargo.toml or go.mod, skipped"
  emit "files="
  exit 0
fi

# The canon tree this script was fetched with, so the measurement and the
# derivation are the ones the caller pinned — the same resolution that
# delivered this file (#165). Not ORG_BELT_DIR: that is computed in the
# belt's own [env] and this runs as a plain step, not inside a mise task.
belt="$(cd "$(dirname "${BASH_SOURCE[0]}")/../mise" && pwd)"

# One measurement, shared with the gate: `coverage:check` enforces the
# number this prints and this derives the floor from it, so the flags
# cannot drift apart (#316's shape, #652's fix).
legs=$("${belt}/coverage-measure.sh" "release:coverage-floor")
python3 "${belt}/coverage-floor.py" write --provenance "v${VERSION}" <<< "${legs}"

emit "files=.coverage-floor"
