#!/usr/bin/env bash
# Release phase 1, step 1: derive the release plan and write the tree it
# describes. Org canon — see docs/release.md.
#
# Every DECISION here belongs to the engine (stele#155): the version, the
# notes, the commit subject, the files the commit carries, the branch it
# lands on and the staging ref it is built through are one typed document,
# `stele derive release-plan`. This script asks for that document, applies
# the parts of it that touch the working tree, and states the plan's own
# answers as step outputs. It decides nothing, and a refusal is the plan's
# to make.
#
# Writes GITHUB_OUTPUT keys:
#   release  true when there is something to release
#   version  bare version, e.g. 0.2.0
#   current  the version being released FROM, for the pgrx upgrade derivation
#   plan     path to the emitted plan document, which open-release-pr.sh executes
#   branch   the release branch, on BOTH paths: close-release-pr.sh reads it
#            when there is nothing to release, and a nothing plan names none
#
# Leaves the working tree modified; open-release-pr.sh commits it via the API.
set -euo pipefail

# The writer of findings in this tree, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/../mise/findings.sh"
unset org__here

emit() {
  if [[ -n ${GITHUB_OUTPUT:-} ]]; then
    echo "$1" >> "${GITHUB_OUTPUT}"
  fi
}

# The notes conventions: groups and URLs are the org's, stated once here.
# The bump rules (0.x breaking bumps minor, chore/ci/docs/style/test
# release nothing) are the engine's own defaults, and its docs and tests
# are their spec (stele#31). Bare chore is unmapped: release commits and
# self-pin bumps stay out of the notes, at the recorded cost of cliff's
# Miscellaneous heading.
repo_slug="${GITHUB_REPOSITORY:-$(git remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')}"
repo_url="https://github.com/${repo_slug}"
groups="feat=Added,fix=Fixed,perf=Performance,refactor=Changed"
groups+=",docs=Documentation,test=Testing,build=Build,ci=CI"
groups+=",chore(deps)=Dependencies,revert=Reverted"
order="Breaking,Added,Changed,Fixed,Performance,Documentation"
order+=",Testing,Build,CI,Dependencies,Reverted"

# The lockfiles an ecosystem's own derivation refreshes below. Declared to
# the plan rather than appended after it, so the commit's contents are one
# list with one author — the plan's.
also=""
if [[ -f Cargo.lock ]]; then also="Cargo.lock"; fi
if [[ -f fuzz/Cargo.lock ]]; then also="${also:+${also},}fuzz/Cargo.lock"; fi

plan="${RUNNER_TEMP:-/tmp}/release-plan.json"

# The release branch is the caller's fact, not the plan's: the engine is
# told it and echoes it back, and only when there is something to release
# (a nothing plan is `{"schema":7,"release":false}`, measured on stele
# v0.22.0). The step that closes a Release PR left with nothing to release
# needs it on exactly that path (#1260), so it is spelled here, once,
# handed to the engine, and emitted on both paths. One branch, never one
# per version: see open-release-pr.sh.
release_branch="release/next"

# One call: derive the plan and, with --prepare, write the tree it
# describes — the version mirrors (workspace or single crate, internal
# path-dependency constraints, CITATION.cff version and date-released)
# and the changelog section spliced above the newest one. Mirrors are
# parsed for location, byte-spliced and re-read through the same reader
# before disk, never pattern-matched, and a drifted mirror refuses by
# name rather than being repaired (stele#102, #514). --prepare applies
# the plan's own file list rather than taking a second reading of the
# tree, so the document and the tree cannot describe different releases.
stele derive release-plan \
  --git-dir . \
  --changelog CHANGELOG.md \
  ${also:+--also "${also}"} \
  --groups "${groups}" \
  --group-order "${order}" \
  --breaking-group "Breaking" \
  --compare-url "${repo_url}/compare/" \
  --release-url "${repo_url}/releases/tag/" \
  --pull-url "${repo_url}/pull/" \
  --branch "${release_branch}" \
  --prepare \
  --out "${plan}"

# A refused plan is a document saying why, carrying no instructions an
# executor could half-run. Surface every cause rather than the first.
refusals=$(jq -r '(.refusals // [])[] | .cause + ": " + .detail' "${plan}")
if [[ -n ${refusals} ]]; then
  refused=()
  while IFS= read -r line; do
    refused+=("${line}")
  done <<< "${refusals}"
  org_finding "" "the release plan refuses:" "${refused[@]}"
  exit 1
fi

emit "branch=${release_branch}"

releasing=$(jq -r '.release' "${plan}")
if [[ ${releasing} != true ]]; then
  echo "nothing to release"
  emit "release=false"
  exit 0
fi

version=$(jq -r '.version' "${plan}")
base=$(jq -r '.base // ""' "${plan}")
echo "current: ${base:-<no tag yet>}"
echo "next:    ${version}"

# Cargo's own derivation, never ours: refresh the lockfile's copy of the
# member versions and prove the tree still resolves before anyone is
# asked to review it. The paths are already in the plan's file list.
if [[ -f Cargo.lock ]]; then
  cargo update --workspace --offline 2> /dev/null || cargo update --workspace
  # fuzz/ is its own cargo workspace by cargo-fuzz convention, with its own
  # lockfile that path-depends on the crate being released. Left alone it
  # names the superseded version after every release, which is what made
  # lint:fuzz-build rewrite it on the next local gate run — four releases of
  # drift accumulated in the lab before anyone looked (#374). The lint now
  # refuses a stale fuzz lockfile instead of silently repairing it, so
  # refreshing it here is what keeps the Release PR green.
  if [[ -f fuzz/Cargo.lock ]]; then
    cargo update --workspace --manifest-path fuzz/Cargo.toml --offline 2> /dev/null \
      || cargo update --workspace --manifest-path fuzz/Cargo.toml
  fi
  cargo metadata --format-version 1 --no-deps > /dev/null
fi

# pgrx upgrade scripts are DERIVED, never authored: the release workflow
# runs generate-pgrx-upgrade.sh after this script, which builds the
# candidate schema, diffs it against the published release's own tarball,
# and proves the result with a live ALTER EXTENSION UPDATE before it
# rides the release commit. The old hand-authored <ext>--<from>--next.sql
# stub was derived state written by humans, and forgetting it burned
# immutable version numbers (#132) — a leftover stub is therefore a
# refusal, not a rename.
stubs=$(git ls-files '*--next.sql')
if [[ -n ${stubs} ]]; then
  org_finding "" "--next.sql stubs are retired; upgrade scripts are derived by" \
    "release/generate-pgrx-upgrade.sh. Delete: ${stubs}" \
    "(A data migration for this cycle belongs in sql/next-data.sql.)"
  exit 1
fi

emit "release=true"
emit "version=${version}"
# The pre-bump version: generate-pgrx-upgrade.sh cross-checks it against
# the published release when deriving upgrade scripts.
emit "current=${base}"
emit "plan=${plan}"
carries=$(jq -r '.commit.additions // [] | join(" ")' "${plan}")
echo "prepared ${version} (${carries})"
