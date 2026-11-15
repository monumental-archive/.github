#!/usr/bin/env bash
# Release phase 1, the nothing-to-release leg: close a Release PR the
# default branch no longer backs (#1260). Org canon — see docs/release.md.
#
# Merging a Release PR is the commitment point, and its title names a
# version. When the plan says there is nothing to release, a Release PR
# still open is stale by definition — opened before its own merge landed,
# or for a fix since reverted — and merging it would tag a version that
# exists or one main no longer holds. So it is closed, with one comment
# saying why. The branch is left; the next release overwrites it.
#
# The branch name is read from prepare-release.sh's step output, never
# from the plan: a nothing plan names no branch (stele v0.22.0 emits
# `{"schema":7,"release":false}`), and the name is the caller's to state.
set -euo pipefail

: "${RELEASE_BRANCH:?RELEASE_BRANCH must be set (the prepare step branch output)}"
: "${GH_TOKEN:?GH_TOKEN must be set (the tag-mint App token, not GITHUB_TOKEN)}"
: "${GITHUB_REPOSITORY:?}"
: "${GITHUB_SHA:?}"

# The same head check open-release-pr.sh makes: a stale run must not
# close a PR a newer run has just opened.
# shellcheck source=release/planned-head.sh
. "$(dirname "${BASH_SOURCE[0]}")/planned-head.sh"
require_planned_head

# A list that cannot be read is a failure, never "none open": that would
# leave the stale PR standing and look exactly like the quiet case.
if ! open=$(gh pr list --repo "${GITHUB_REPOSITORY}" --head "${RELEASE_BRANCH}" \
  --state open --json number --jq '.[].number'); then
  echo "FAIL: could not list the open pull requests on ${RELEASE_BRANCH}" >&2
  exit 1
fi
if [[ -z ${open} ]]; then
  echo "no open Release PR on ${RELEASE_BRANCH}; nothing to close"
  exit 0
fi
for number in ${open}; do
  gh pr close "${number}" --repo "${GITHUB_REPOSITORY}" \
    --comment "main holds nothing to release at ${head}; closed by the release workflow"
  echo "closed PR #${number}: main holds nothing to release at ${head}"
done
