#!/usr/bin/env bash
# Release phase 1, step 1b: put the prepared bump on a branch and keep exactly
# one Release PR open against it. Org canon — see docs/release.md; proven in
# iiif-server before being promoted here.
#
# The commit is created through GitHub's API rather than with `git commit`,
# because org repositories require signed commits and a runner has no signing
# key. A locally-made commit is `verified: false`, and the Release PR is then
# unmergeable. Commits created via the API are signed by GitHub with its own
# key, and createCommitOnBranch writes every file in a single commit, which
# the REST contents endpoint cannot do.
#
# The branch is updated to the commit being released on every run, so the PR
# always shows the release that would happen if it were merged now. It is
# updated in ONE move, from its old release commit straight to the new one,
# via a staging ref. The obvious two-step — reset the branch to main, then
# commit — leaves the branch momentarily identical to main, and GitHub closes
# a pull request whose diff is empty; that churns the PR number and throws
# away its review thread.
set -euo pipefail

: "${VERSION:?VERSION must be set}"
: "${FILES:?FILES must be set (space-separated release commit contents)}"
: "${GH_TOKEN:?GH_TOKEN must be set (the tag-mint App token, not GITHUB_TOKEN)}"
: "${GITHUB_REPOSITORY:?}"
: "${GITHUB_SHA:?}"

branch="release/v${VERSION}"
title="chore: release v${VERSION}"

# The staging ref is where the commit is built. It is disposable and no pull
# request ever points at it, so it is free to sit at main's head.
staging="release-staging/v${VERSION}"
if gh api "repos/${GITHUB_REPOSITORY}/git/ref/heads/${staging}" > /dev/null 2>&1; then
  gh api "repos/${GITHUB_REPOSITORY}/git/refs/heads/${staging}" \
    --method PATCH -f sha="${GITHUB_SHA}" -F force=true > /dev/null
else
  gh api "repos/${GITHUB_REPOSITORY}/git/refs" \
    --method POST -f "ref=refs/heads/${staging}" -f sha="${GITHUB_SHA}" > /dev/null
fi
cleanup() {
  gh api "repos/${GITHUB_REPOSITORY}/git/refs/heads/${staging}" \
    --method DELETE > /dev/null 2>&1 || true
}
trap cleanup EXIT

# prepare-release.sh has already exited early when there was nothing to bump,
# so every file in FILES differs from the base commit by the time we get here.
request=$(
  STAGING="${staging}" TITLE="${title}" python3 - << 'PY'
import base64, json, os

additions = []
for path in os.environ["FILES"].split():
    with open(path, "rb") as handle:
        additions.append(
            {"path": path, "contents": base64.b64encode(handle.read()).decode()}
        )

print(json.dumps({
    "query": (
        "mutation($input: CreateCommitOnBranchInput!) {"
        "  createCommitOnBranch(input: $input) { commit { oid } }"
        "}"
    ),
    "variables": {
        "input": {
            "branch": {
                "repositoryNameWithOwner": os.environ["GITHUB_REPOSITORY"],
                "branchName": os.environ["STAGING"],
            },
            "message": {"headline": os.environ["TITLE"]},
            "fileChanges": {"additions": additions},
            "expectedHeadOid": os.environ["GITHUB_SHA"],
        }
    },
}))
PY
)

oid=$(printf '%s' "${request}" | gh api graphql --input - \
  --jq '.data.createCommitOnBranch.commit.oid')
if [[ -z ${oid} ]]; then
  echo "FAIL: the API returned no commit" >&2
  exit 1
fi

# Prove the point of all this rather than assuming it.
verified=$(gh api "repos/${GITHUB_REPOSITORY}/commits/${oid}" \
  --jq '.commit.verification.verified')
if [[ ${verified} != "true" ]]; then
  echo "FAIL: release commit ${oid} is unsigned; the PR would be unmergeable" >&2
  exit 1
fi

# One move: old release commit -> new release commit. The branch never holds
# a tree equal to main, so an open PR stays open and simply shows a
# force-push.
if gh api "repos/${GITHUB_REPOSITORY}/git/ref/heads/${branch}" > /dev/null 2>&1; then
  gh api "repos/${GITHUB_REPOSITORY}/git/refs/heads/${branch}" \
    --method PATCH -f sha="${oid}" -F force=true > /dev/null
else
  gh api "repos/${GITHUB_REPOSITORY}/git/refs" \
    --method POST -f "ref=refs/heads/${branch}" -f sha="${oid}" > /dev/null
fi
echo "committed ${oid} to ${branch}, signature verified"

body=$(
  cat << EOF
Merging this PR is the commitment point: it tags \`v${VERSION}\` and cuts a
draft GitHub release, which triggers this repository's publish workflow to
build, prove, sign and attach the release artifacts.

Nothing is published until this is merged, and nothing outside this path
publishes at all.

- Version bumped to \`${VERSION}\` across the workspace and its internal
  dependency constraints
- \`CHANGELOG.md\` regenerated from the conventional commits since the last
  tag

Review the changelog as release notes: this text is what the GitHub release
will carry.
EOF
)

existing=$(gh pr list --head "${branch}" --state open --json number --jq '.[0].number // empty')
if [[ -n ${existing} ]]; then
  gh pr edit "${existing}" --title "${title}" --body "${body}"
  echo "updated PR #${existing}"
else
  gh pr create --head "${branch}" --base main --title "${title}" --body "${body}"
  echo "opened the release PR for v${VERSION}"
fi
