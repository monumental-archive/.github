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
# The ratchet law lives in mise/coverage_floor.py, with the drift check
# `coverage:check` runs, so there is one definition of "the floor only
# rises" rather than a release-time copy of it. If the measurement is
# below `floor + band`, that script refuses and this step fails the
# Release PR — loudly, before a version number is spent.
#
# Reads, when the gate kept one: the `coverage-figures-<sha>` artifact of
# the commit being released, through `gh api` with GH_TOKEN (#1622).
#
# Writes GITHUB_OUTPUT keys:
#   files  `.coverage-floor` when there is a floor to carry, else empty
#
# Leaves the working tree modified; open-release-pr.sh commits it via the
# API, as EXTRA_FILES.
set -euo pipefail
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"

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

# The canon tree this script was fetched with, so the measurement and the
# derivation are the ones the caller pinned — the same resolution that
# delivered this file (#165). Not ORG_BELT_DIR: that is computed in the
# belt's own [env] and this runs as a plain step, not inside a mise task.
belt="$(cd "$(dirname "${BASH_SOURCE[0]}")/../mise" && pwd)"

# A floor with no measurable language is a repository mid-migration, not
# a release to fail: `coverage:check` skips the same shape, and asks the
# same `probe` which languages apply (#1331), so the two cannot disagree.
applies=$("${belt}/coverage-measure.sh" "release:coverage-floor" probe)
if [[ -z ${applies} ]]; then
  echo ".coverage-floor with no measurable language (go.mod, Cargo.toml, tracked *.py"
  echo "  with a test task, or package.json with vitest), skipped"
  emit "files="
  exit 0
fi

# The gate already measured this commit (#1622): the merge queue's run
# judged the tree that lands, and kept the leg lines coverage:check held
# to the floor as `coverage-figures-<sha>`. This reads that artifact
# when every fact about it is this commit's — coverage_figures.py
# decides each one and starts nothing — and names the run it read;
# otherwise it says why, and measures as it always did. Either way the
# figure is coverage-measure.sh's own, and coverage_floor.py judges it
# below by one law.
figures="$(dirname "${BASH_SOURCE[0]}")/coverage_figures.py"
sha=$(git rev-parse HEAD)
legs=""
if [[ -z ${GH_TOKEN:-} || -z ${GITHUB_REPOSITORY:-} ]]; then
  echo "release:coverage-floor: not reading the gate's figures: no GH_TOKEN or GITHUB_REPOSITORY" >&2
else
  api="repos/${GITHUB_REPOSITORY}/actions"
  if choice=$(gh api "${api}/artifacts?name=coverage-figures-${sha}&per_page=100" \
    | "${ORG_PYTHON}" "${figures}" choose "${sha}"); then
    read -r artifact run <<< "${choice}"
    if where=$(gh api "${api}/runs/${run}" | "${ORG_PYTHON}" "${figures}" judge "${sha}"); then
      zip=$(mktemp)
      if gh api "${api}/artifacts/${artifact}/zip" > "${zip}" \
        && legs=$("${ORG_PYTHON}" "${figures}" figures "${sha}" < "${zip}"); then
        echo "release:coverage-floor: read the gate's figures for ${sha} from ${where}" >&2
      else
        legs=""
      fi
      rm -f "${zip}"
    fi
  fi
fi
if [[ -z ${legs} ]]; then
  # One measurement shared with the gate, so the flags cannot drift apart
  # (#316's shape, #652's fix).
  echo "release:coverage-floor: measuring ${sha}" >&2
  legs=$("${belt}/coverage-measure.sh" "release:coverage-floor")
fi
# A pgrx extension's `pgrx-pgNN` legs (#1656) are measured by its own
# suite, test:pgrx, every declared major, through the `pgrx` selector:
# the gate holds them to the floor in its pgrx jobs, never in
# coverage:check, so they are not in the gate's figures and are
# measured here whichever way the rest arrived. Nothing is printed where
# there is no extension.
legs+=$'\n'$("${belt}/coverage-measure.sh" "release:coverage-floor" pgrx)
"${ORG_PYTHON}" "${belt}/coverage_floor.py" write --provenance "v${VERSION}" <<< "${legs}"

emit "files=.coverage-floor"
