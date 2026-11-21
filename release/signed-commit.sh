#!/usr/bin/env bash
# Sourced, never run: the one way a canon script writes a commit to a
# branch (#1897). Org canon — see docs/release.md. The Release PR writer
# (open-release-pr.sh) and the fuzz-corpus proposer
# (propose-fuzz-corpus.sh) both call it, so the signed commit and the
# check that it is signed are written once.
#
# The commit is created through GitHub's API rather than with `git
# commit`, because org repositories require signed commits and a runner
# has no signing key. A locally-made commit is `verified: false`, and the
# pull request is then unmergeable. Commits created via the API are
# signed by GitHub with its own key, and createCommitOnBranch writes
# every file in a single commit, which the REST contents endpoint cannot
# do.
#
# Needs gh, jq, GH_TOKEN and GITHUB_REPOSITORY.

# forge <what> <gh api arguments...>
#
# Every forge call a script cannot survive names itself when it is
# refused (#633). The step died four times on `gh: Resource not
# accessible by integration (HTTP 403)` and nothing else: no line said
# which of its calls was refused, so a red that reads as a permissions
# fact could not be traced to a call, let alone a cause. This is not a
# retry — a refused call still fails the run, exactly as before. It adds
# what the forge said about the refusal: the status, the request id
# GitHub support can look up, and the permissions the endpoint would
# have accepted.
#
# `--include` goes last so the call still reads as the call; gh prints
# the response headers, a blank line, then the body any `--jq` has
# filtered.
#
# A refused call also leaves the forge's whole answer in `forge_refusal`,
# read through forge_refused by the one caller that tells one refusal
# from another.
forge() {
  local what=$1 out status=0
  shift
  out=$(gh api "$@" --include) || status=$?
  if ((status != 0)); then
    forge_refusal=${out}
    {
      echo "FAIL: could not ${what}"
      tr -d '\r' <<< "${out}" \
        | grep -iE '^(HTTP/|x-github-request-id:|x-accepted-github-permissions:)' || true
    } >&2
    return "${status}"
  fi
  awk 'body { print; next } /^\r?$/ { body = 1 }' <<< "${out}"
}

# forge_refused <ERE>: whether the last call forge saw refused answered
# with a line matching <ERE> (`grep -E`). Reset `forge_refusal` to ""
# before the call whose refusal is read.
forge_refused() {
  grep -qE -- "$1" <<< "${forge_refusal:-}"
}

# org_signed_commit <noun> <branch> <expected head> <headline> <body> <changes>
#
# Writes one commit onto <branch>, whose head must be <expected head>,
# carrying <changes>: a file holding createCommitOnBranch's
# `fileChanges` object, `{"additions": [...], "deletions": [...]}`. The
# message is <headline>, then <body>. Prints the new commit's oid once
# GitHub reports it signed.
#
# Returns 1, naming <noun>, when the forge refuses the commit, answers
# with no commit, or reports the commit unsigned. Every step is checked
# here rather than left to errexit, which a caller's `$(...)` switches
# off.
org_signed_commit() {
  local noun=$1 branch=$2 head=$3 headline=$4 body=$5 changes=$6
  local request oid verified
  : "${GITHUB_REPOSITORY:?}"
  request=$(jq -n \
    --arg repo "${GITHUB_REPOSITORY}" --arg branch "${branch}" \
    --arg head "${head}" --arg headline "${headline}" --arg body "${body}" \
    --slurpfile changes "${changes}" \
    '{
      query: ("mutation($input: CreateCommitOnBranchInput!) {"
        + "  createCommitOnBranch(input: $input) { commit { oid } }"
        + "}"),
      variables: {input: {
        branch: {repositoryNameWithOwner: $repo, branchName: $branch},
        message: {headline: $headline, body: $body},
        fileChanges: $changes[0],
        expectedHeadOid: $head
      }}
    }') || {
    echo "FAIL: could not build the ${noun} request from ${changes}" >&2
    return 1
  }
  oid=$(printf '%s' "${request}" \
    | forge "create the ${noun} on ${branch}" graphql --input - \
      --jq '.data.createCommitOnBranch.commit.oid') || return 1
  if [[ -z ${oid} ]]; then
    echo "FAIL: the API returned no commit" >&2
    return 1
  fi
  # Prove the point of all this rather than assuming it.
  verified=$(forge "read back the ${noun} ${oid}" \
    "repos/${GITHUB_REPOSITORY}/commits/${oid}" \
    --jq '.commit.verification.verified') || return 1
  if [[ ${verified} != "true" ]]; then
    echo "FAIL: ${noun} ${oid} is unsigned; the PR would be unmergeable" >&2
    return 1
  fi
  printf '%s\n' "${oid}"
}
