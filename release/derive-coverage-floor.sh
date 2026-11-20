#!/usr/bin/env bash
# Release phase 1, step 1c: re-derive the coverage floor from the tree
# being released. Org canon — see docs/release.md and #652.
#
# The floor is DERIVED STATE, like the version, the changelog and the
# pgrx upgrade scripts beside it: the machinery measures, writes
# `floor = min(90, measured - band)`, and the file rides the release
# commit (the cap, #1786). It
# was a hand-typed number until the org measured what that costs —
# stele's ceiling fell 6.5 points in two days with nothing red and
# nothing noticed, because the floor sat far enough below the ceiling
# that the headroom became a landing zone.
#
# The ratchet law lives in mise/coverage_floor.py, with the drift check
# `coverage:check` runs, so there is one definition of "the floor never
# falls" rather than a release-time copy of it. If the measurement is
# below the floor — the line the gate already held, so a merged pull
# request cannot red this step (#1786) — that script refuses and this
# step fails the Release PR, loudly, before a version number is spent.
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
# otherwise it says why, and measures the way the gate does. Either way
# the figure is coverage-measure.sh's own, and coverage_floor.py judges
# it below by one law.
figures="$(dirname "${BASH_SOURCE[0]}")/coverage_figures.py"
sha=$(git rev-parse HEAD)

# One artifact's figures: the gate job's (no argument) or one pgrx job's
# (`pgrx-pg<major>`, #1692). Sets `read_legs` to the leg lines, empty
# when anything about the artifact is refused (coverage_figures.py
# prints why). Never fails: every step is a condition, so a refusal is
# an empty answer and the caller measures.
read_figures() { # usage: read_figures [pgrx-pg<major>]
  local leg="${1:-}" artifact_name api choice artifact run where zip
  read_legs=""
  if [[ -z ${GH_TOKEN:-} || -z ${GITHUB_REPOSITORY:-} ]]; then
    echo "release:coverage-floor: not reading the gate's figures: no GH_TOKEN or GITHUB_REPOSITORY" >&2
    return 0
  fi
  artifact_name="coverage-figures-${leg:+${leg}-}${sha}"
  api="repos/${GITHUB_REPOSITORY}/actions"
  if choice=$(gh api "${api}/artifacts?name=${artifact_name}&per_page=100" \
    | "${ORG_PYTHON}" "${figures}" choose "${sha}" ${leg:+"${leg}"}); then
    read -r artifact run <<< "${choice}"
    if where=$(gh api "${api}/runs/${run}" | "${ORG_PYTHON}" "${figures}" judge "${sha}"); then
      zip=$(mktemp)
      if gh api "${api}/artifacts/${artifact}/zip" > "${zip}" \
        && read_legs=$("${ORG_PYTHON}" "${figures}" figures "${sha}" ${leg:+"${leg}"} < "${zip}"); then
        echo "release:coverage-floor: read ${artifact_name} from ${where}" >&2
      else
        read_legs=""
      fi
      rm -f "${zip}"
    fi
  fi
  return 0
}

read_figures
legs=${read_legs}
if [[ -z ${legs} ]]; then
  # The gate's own measurement, made here (#1667): coverage:check depends
  # on the repository's `test`, whose test command runs through
  # `measured`, and keeps the leg lines it read in coverage-figures.txt
  # (#1622). A plain step reads no run's directory, so the run is mise's,
  # and the flags cannot drift from the gate's (#316's shape, #652's fix).
  echo "release:coverage-floor: measuring ${sha}" >&2
  reports=$(mktemp -d)
  if ! ORG_COVERAGE_REPORTS="${reports}" mise run coverage:check >&2; then
    echo "release:coverage-floor: coverage:check failed measuring ${sha} (above)" >&2
    exit 1
  fi
  legs=$(< "${reports}/coverage-figures.txt")
  rm -rf "${reports}"
fi

# A pgrx extension's `pgrx-pgNN` legs (#1656) are held to the floor by
# the gate's pgrx jobs, one per major, never by coverage:check, so they
# are not in the gate job's figures: each pgrx job keeps its own
# (#1692). Every major the `pgrx` selector measures, listed by the same
# derivation (`pgrx-majors`), is read; all of them read, nothing is
# measured. Any one missing or refused, the step names it and measures
# the extension at every major, as before: never a floor from some
# majors read and others measured. Nothing where there is no extension.
majors=$("${belt}/coverage-measure.sh" "release:coverage-floor" pgrx-majors)
if [[ -n ${majors} ]]; then
  pgrx_legs=""
  unread=""
  while read -r major; do
    read_figures "pgrx-pg${major}"
    if [[ -n ${read_legs} ]]; then
      pgrx_legs+="${read_legs}"$'\n'
    else
      unread="${unread} pg${major}"
    fi
  done <<< "${majors}"
  if [[ -n ${unread} ]]; then
    echo "release:coverage-floor: no figure read for the extension at${unread}; measuring every major" >&2
    pgrx_legs=$("${belt}/coverage-measure.sh" "release:coverage-floor" pgrx)
  fi
  legs+=$'\n'${pgrx_legs}
fi
"${ORG_PYTHON}" "${belt}/coverage_floor.py" write --provenance "v${VERSION}" <<< "${legs}"

emit "files=.coverage-floor"
