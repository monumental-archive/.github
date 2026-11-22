#!/usr/bin/env bash
# Release phase 1, step 1b: execute the release plan — put its commit on its
# branch and keep exactly one Release PR open against it. Org canon — see
# docs/release.md.
#
# This script makes no release decisions (stele#155). The version, the commit
# subject, the files the commit carries, the branch it lands on and the
# staging ref it is built through are all read from the plan document
# prepare-release.sh emitted. What remains is API plumbing, and it is here
# rather than in the engine because it is capability, not judgment.
#
# The commit is created through GitHub's API, signed by GitHub, by the
# shared helper release/signed-commit.sh, which says why.
#
# The branch is updated to the commit being released on every run, so the PR
# always shows the release that would happen if it were merged now. It is
# updated in ONE move, from its old release commit straight to the new one,
# via a staging ref. The obvious two-step — reset the branch to main, then
# commit — leaves the branch momentarily identical to main, and GitHub closes
# a pull request whose diff is empty; that churns the PR number and throws
# away its review thread.
set -euo pipefail

# The writer of findings in this tree, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/../mise/findings.sh"
unset org__here

: "${PLAN:?PLAN must be set (the plan document prepare-release.sh emitted)}"
: "${GH_TOKEN:?GH_TOKEN must be set (the tag-mint App token, not GITHUB_TOKEN)}"
: "${GITHUB_REPOSITORY:?}"
: "${GITHUB_SHA:?}"
# No apostrophe in this message: inside ${VAR:?...} it opens a quote the
# parser never closes, and shfmt then misreads the rest of the file.
: "${APP_SLUG:?APP_SLUG must be set (the create-github-app-token app-slug output)}"

version=$(jq -r '.version' "${PLAN}")
title=$(jq -r '.commit.subject' "${PLAN}")
# ONE branch, not one per version — the plan says so and this script does
# not spell it. Keying the branch on the computed version looks tidier and
# is a bug: when new commits change the bump, a version-keyed branch opens
# a SECOND pull request and abandons the first, which is exactly what "keep
# exactly one Release PR open" forbids. The orphan then sits there forever,
# showing a version that will never ship. Observed in the lab: a v0.7.1 PR
# stranded when the range grew a feat and the bump moved to v0.8.0.
branch=$(jq -r '.branch.name' "${PLAN}")
# The staging ref is where the commit is built. It is disposable and no pull
# request ever points at it, so it is free to sit at main's head.
staging=$(jq -r '.branch.staging' "${PLAN}")

for v in version title branch staging; do
  [[ -n ${!v} && ${!v} != null ]] || {
    org_finding "" "the plan states no ${v}"
    exit 1
  }
done

# Nothing is written for a plan the default branch has moved past
# (#1260); the reasoning lives with the check.
# shellcheck source=release/planned-head.sh
. "$(dirname "${BASH_SOURCE[0]}")/planned-head.sh"
require_planned_head

# The forge calls and the signed commit (#633, #1897): forge names a
# refused call, and org_signed_commit writes the release commit and
# proves it signed.
# shellcheck source=release/signed-commit.sh
. "$(dirname "${BASH_SOURCE[0]}")/signed-commit.sh"

if gh api "repos/${GITHUB_REPOSITORY}/git/ref/heads/${staging}" > /dev/null 2>&1; then
  forge "move the staging ref ${staging} to ${GITHUB_SHA}" \
    "repos/${GITHUB_REPOSITORY}/git/refs/heads/${staging}" \
    --method PATCH -f sha="${GITHUB_SHA}" -F force=true > /dev/null
else
  forge "create the staging ref ${staging} at ${GITHUB_SHA}" \
    "repos/${GITHUB_REPOSITORY}/git/refs" \
    --method POST -f "ref=refs/heads/${staging}" -f sha="${GITHUB_SHA}" > /dev/null
fi
cleanup() {
  rm -f -- "${changes:-}"
  gh api "repos/${GITHUB_REPOSITORY}/git/refs/heads/${staging}" \
    --method DELETE > /dev/null 2>&1 || true
}
rc=0
trap 'rc=$?; cleanup; [[ -n ${org_done:-} || ${rc} -ne 0 ]] || rc=1; exit "${rc}"' EXIT

# The release commit signs itself off like any other (OSPS-LE-01.01, and
# `lint:dco` admits no exemption list). The identity is derived rather than
# hardcoded, because it is the App's bot user and differs per installation
# — and because a sign-off naming the wrong identity is worse than none.
#
# NOT via `gh api user`: an App installation token has no user context, so
# that endpoint answers 403 "Resource not accessible by integration". The
# slug comes from the token action's own output, and the numeric id from
# the public users endpoint, which an installation token may read. The
# bracketed `[bot]` suffix is part of the login and part of the address —
# `lint:dco` matches the sign-off against the commit author verbatim, and
# GitHub authors API-created commits as exactly this identity.
bot_login="${APP_SLUG}[bot]"
bot_id=$(forge "read the bot user of ${APP_SLUG}" \
  "users/${APP_SLUG}%5Bbot%5D" --jq '.id')
bot="${bot_login} <${bot_id}+${bot_login}@users.noreply.github.com>"
signoff="Signed-off-by: ${bot}"

# The commit's contents are the plan's, plus whatever a later derivation
# step produced that the plan could not have known about — the pgrx upgrade
# scripts, derived against the published release after the plan was made.
# A plan addition that no longer exists on disk is the old half of a rename
# and rides the commit as a deletion; the plan already states deletions of
# its own.
changes=$(mktemp)
PLAN="${PLAN}" python3 - > "${changes}" << 'PY'
import base64, json, os

with open(os.environ["PLAN"], "rb") as handle:
    plan = json.load(handle)

commit = plan.get("commit") or {}
paths = list(commit.get("additions") or [])
for extra in os.environ.get("EXTRA_FILES", "").split():
    if extra not in paths:
        paths.append(extra)

additions = []
deletions = [{"path": p} for p in (commit.get("deletions") or [])]
for path in paths:
    if not os.path.exists(path):
        deletions.append({"path": path})
        continue
    with open(path, "rb") as handle:
        additions.append(
            {"path": path, "contents": base64.b64encode(handle.read()).decode()}
        )

print(json.dumps({"additions": additions, "deletions": deletions}))
PY

oid=$(org_signed_commit "release commit" "${staging}" "${GITHUB_SHA}" \
  "${title}" "${signoff}" "${changes}")

# Takes the Release PR out of the merge queue (#1504), setting `dequeued`
# to the line the run logs and comments once the PR is regenerated. A
# dequeue the forge refuses for want of permission exits 0 with nothing
# moved, saying the next push to the default branch regenerates it: the
# canon's next queued Release PR is the measurement of whether the App
# may dequeue (#1504). Any other failure, and any read this needs, fails
# the run by name.
dequeue_queued_pr() {
  local pr number id planned landed mutation
  pr=$(gh pr view "${branch}" --json number,id,body) || {
    org_finding "" "could not read the queued pull request for ${branch}"
    exit 1
  }
  number=$(jq -r '.number' <<< "${pr}")
  id=$(jq -r '.id' <<< "${pr}")
  planned=$(jq -r '.body' <<< "${pr}" | sed -n 's/^Planned-At: \([0-9a-f]\{40\}\)$/\1/p')
  landed="commits landed"
  if [[ -n ${planned} ]]; then
    landed=$(forge "compare ${planned} with ${head}" \
      "repos/${GITHUB_REPOSITORY}/compare/${planned}...${head}" --jq '.ahead_by')
    if [[ ${landed} == 1 ]]; then
      landed="1 commit landed"
    else
      landed="${landed} commits landed"
    fi
  fi
  # The id is written into the mutation, as planned-head.sh writes the
  # repository into its query: a single-quoted `$id` is one shellcheck
  # cannot tell from an unexpanded shell variable (SC2016).
  mutation="mutation { dequeuePullRequest(input: {id: \"${id}\"})"
  mutation+=" { mergeQueueEntry { id } } }"
  forge_refusal=""
  if ! forge "take #${number} out of the merge queue" graphql \
    -f query="${mutation}" > /dev/null; then
    if forge_refused '^HTTP/[0-9.]+ 403|"type": *"FORBIDDEN"|Resource not accessible by integration'; then
      echo "the forge refused to take #${number} out of the merge queue;" \
        "it stays planned at ${planned:-an older commit}; the next push" \
        "to the default branch regenerates it"
      org_done=1
      exit 0
    fi
    exit 1
  fi
  dequeued_pr=${number}
  dequeued="taken out of the merge queue: ${landed} after this release was"
  dequeued+=" planned; regenerated at ${head}; queue it again"
}

# One move: old release commit -> new release commit. The branch never holds
# a tree equal to main, so an open PR stays open and simply shows a
# force-push.
#
# A queued Release PR is taken out of the queue and regenerated (#1504).
# The forge refuses to move a branch whose pull request is in the merge
# queue (HTTP 422, "Branches that are queued for merging cannot be
# updated"). Left there, the queue removes it for landing past its plan
# (lint:release-planned, #1472), and nothing regenerates it until another
# push: v1.69.4's Release PR was queued and removed four times in an hour.
# So the run dequeues it, moves the branch as for any other, and says so
# on the pull request. It never queues it again: merging a release stays
# a person's act, and dequeuing merges nothing. lint:release-planned
# stays the guard for the race in which the queue has already built its
# group. Every other refusal to move the branch is still a failure.
move_branch() {
  forge "move ${branch} to the release commit ${oid}" \
    "repos/${GITHUB_REPOSITORY}/git/refs/heads/${branch}" \
    --method PATCH -f sha="${oid}" -F force=true > /dev/null
}
dequeued=""
if gh api "repos/${GITHUB_REPOSITORY}/git/ref/heads/${branch}" > /dev/null 2>&1; then
  forge_refusal=""
  if ! move_branch; then
    if ! forge_refused '^HTTP/[0-9.]+ 422' \
      || ! forge_refused 'queued for merging cannot be updated'; then
      exit 1
    fi
    dequeue_queued_pr
    move_branch
  fi
else
  forge "create ${branch} at the release commit ${oid}" \
    "repos/${GITHUB_REPOSITORY}/git/refs" \
    --method POST -f "ref=refs/heads/${branch}" -f sha="${oid}" > /dev/null
fi
echo "committed ${oid} to ${branch}, signature verified"

body=$(
  cat << EOF
Merging this PR is the commitment point: it tags \`v${version}\` and cuts a
draft GitHub release, which triggers this repository's publish workflow to
build, prove, sign and attach the release artifacts.

Nothing is published until this is merged, and nothing outside this path
publishes at all.

- Version bumped to \`${version}\` across the workspace and its internal
  dependency constraints
- \`CHANGELOG.md\` regenerated from the conventional commits since the last
  tag

Review the changelog as release notes: this text is what the GitHub release
will carry.

The line below is the default-branch commit this release was planned
from. The squash carries it into the release commit, and the gate refuses
to land that commit anywhere else (lint:release-planned).

Planned-At: ${head}
EOF
)

existing=$(gh pr list --head "${branch}" --state open --json number --jq '.[0].number // empty')
if [[ -n ${existing} ]]; then
  gh pr edit "${existing}" --title "${title}" --body "${body}"
  echo "updated PR #${existing}"
  if [[ -n ${dequeued} ]]; then
    gh pr comment "${dequeued_pr}" --body "${dequeued}"
    echo "#${dequeued_pr} ${dequeued}"
  fi
else
  gh pr create --head "${branch}" --base main --title "${title}" --body "${body}"
  echo "opened the release PR for v${version}"
fi
org_done=1
