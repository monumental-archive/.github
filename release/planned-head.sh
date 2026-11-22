#!/usr/bin/env bash
# Sourced, never run: the one head check both Release-PR writers make
# before their first write (#1260). Org canon — see docs/release.md.
#
# A run acts only for the commit the default branch is at. The plan was
# made when the job started, and the job measures the tree, so it runs
# for minutes; if the branch moves in that time — the Release PR itself
# being merged is the case that was measured on edtf — the plan is
# stale. Opened from it, a PR names a version that already exists;
# closing from it, a run could close a PR a newer run just opened. The
# run for the newer commit maintains the PR, so this one writes nothing.
#
# The concurrency group cannot say this: it serialises runs, it does not
# tell a run its commit is stale. The window left between this read and
# the first write is backstopped by tag-release.sh, which refuses to mint
# on a stale path; no second lock.

# Exits 0 with the reason when the branch has moved, exits 1 by name when
# the head cannot be read, and otherwise returns with `head` set to the
# commit the run planned from.
require_planned_head() {
  : "${GITHUB_REPOSITORY:?}" "${GITHUB_SHA:?}"
  # On a push the run planned from GITHUB_SHA. On a release-published
  # refresh GITHUB_SHA is the tagged commit and the checkout is the
  # default branch, so the commit the plan was made from is the one
  # checked out.
  local planned="${GITHUB_SHA}"
  if [[ ${GITHUB_EVENT_NAME:-} == release ]]; then
    planned=$(git rev-parse HEAD)
  fi
  # A read that cannot be completed is a failure by name, never
  # "unchanged": reading an error as a match is exactly the stale write
  # this guards. The owner and name are written into the query rather
  # than bound as GraphQL variables: a single-quoted `$owner` is one the
  # linter cannot tell from an unexpanded shell variable (SC2016).
  local query="{ repository(owner: \"${GITHUB_REPOSITORY%%/*}\","
  query+=" name: \"${GITHUB_REPOSITORY#*/}\")"
  query+=" { defaultBranchRef { target { oid } } } }"
  head=$(gh api graphql -f query="${query}" \
    --jq '.data.repository.defaultBranchRef.target.oid') || head=""
  if [[ ! ${head} =~ ^[0-9a-f]{40}$ ]]; then
    org_finding "" "could not read the default branch head of ${GITHUB_REPOSITORY}"
    exit 1
  fi
  if [[ ${head} != "${planned}" ]]; then
    echo "main moved to ${head} while this run was planning;" \
      "the run for that commit maintains the Release PR"
    exit 0
  fi
}
