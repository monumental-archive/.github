#!/usr/bin/env bash
# Propose what the scheduled fuzz search kept as a reviewed pull request
# (#1897). Org canon — run by repo-deep-tests.yml's `propose` job, which
# runs no repository code and is the only job of that workflow to mint a
# token.
#
#   propose-fuzz-corpus.sh handover  (the fuzz job) say whether it staged
#   propose-fuzz-corpus.sh check     refuse any entry outside the shapes
#   propose-fuzz-corpus.sh propose   check, then commit and open the PR
#
# CANDIDATES is the downloaded `fuzz-corpus-candidates` artifact: each
# file at its repository path. That artifact was written by the job that
# ran the repository's code, so its paths are input, not instructions.
# The whole artifact is refused, red and naming each path, unless every
# entry is a regular file at
#
#   fuzz/corpus/<target>/<file>
#   <dirs>/testdata/fuzz/Fuzz<Name>/<file>
#
# with every segment `[A-Za-z0-9._-]+` and none `.` or `..`. A refused
# artifact commits nothing.
#
# `propose` adds every file in ONE signed commit (release/signed-commit.sh)
# on the branch `fuzz-corpus`: on its head when it exists, so a night's
# finds accumulate until reviewed, else created from the default
# branch's head. Then it opens the branch's pull request, or updates the
# open one, with SUMMARY, the search's per-target summary, as its body.
#
# Inputs: CANDIDATES; for `propose` also GH_TOKEN (the release App's
# token), APP_SLUG, GITHUB_REPOSITORY, DEFAULT_BRANCH and SUMMARY.
set -euo pipefail

branch=fuzz-corpus
title="test(fuzz): add inputs that reach new code"
segment='[A-Za-z0-9._-]+'

# corpus_path_refusal <path>: why <path> is no corpus path, or nothing.
corpus_path_refusal() {
  local path=$1 part rest
  rest=${path}
  while [[ -n ${rest} ]]; do
    part=${rest%%/*}
    if [[ ${part} == "${rest}" ]]; then rest=""; else rest=${rest#*/}; fi
    if [[ ${part} == . || ${part} == .. ]]; then
      echo "a '${part}' segment"
      return 0
    fi
    if [[ ! ${part} =~ ^${segment}$ ]]; then
      echo "a segment outside [A-Za-z0-9._-]"
      return 0
    fi
  done
  if [[ ! ${path} =~ ^fuzz/corpus/${segment}/${segment}$ &&
    ! ${path} =~ ^(${segment}/)*testdata/fuzz/Fuzz[A-Za-z0-9_]*/${segment}$ ]]; then
    echo "outside fuzz/corpus/<target>/<file> and <dirs>/testdata/fuzz/Fuzz<Name>/<file>"
  fi
}

# check: refuses the artifact when any entry is not a regular file at a
# corpus path, naming each; sets `files` to the paths otherwise.
check() {
  : "${CANDIDATES:?CANDIDATES must be set (the downloaded fuzz-corpus-candidates artifact)}"
  local entry path why list refused=0
  files=()
  if [[ ! -d ${CANDIDATES} || -L ${CANDIDATES} ]]; then
    echo "FAIL: ${CANDIDATES} is not the downloaded artifact directory" >&2
    exit 1
  fi
  list=$(mktemp)
  # In path order, so a proposal's commit lists its files the same way
  # every time.
  if ! find "${CANDIDATES}" -mindepth 1 -print0 > "${list}" \
    || ! LC_ALL=C sort -z -o "${list}" "${list}"; then
    rm -f -- "${list}"
    echo "FAIL: could not list ${CANDIDATES}" >&2
    exit 1
  fi
  while IFS= read -r -d '' entry; do
    path=${entry#"${CANDIDATES}/"}
    if [[ -L ${entry} ]]; then
      why="a symbolic link"
    elif [[ -d ${entry} ]]; then
      continue
    elif [[ ! -f ${entry} ]]; then
      why="not a regular file"
    else
      why=$(corpus_path_refusal "${path}")
    fi
    if [[ -n ${why} ]]; then
      echo "FAIL: refused ${path}: ${why}" >&2
      refused=1
      continue
    fi
    files+=("${path}")
  done < "${list}"
  rm -f -- "${list}"
  if ((refused)); then
    echo "FAIL: the artifact is refused whole; nothing is committed" >&2
    exit 1
  fi
  if [[ -z ${files[*]-} ]]; then
    echo "FAIL: the artifact holds no file; nothing to propose" >&2
    exit 1
  fi
}

# handover: the fuzz job's last word to the propose job, run whether the
# search passed or failed. SUMMARY (a file here) goes to the job's own
# page; when CANDIDATES holds a file, `staged=true` and the summary go to
# GITHUB_OUTPUT, behind a random delimiter, since the search's job wrote
# it. Otherwise `staged=false`, and nothing is uploaded or proposed.
handover() {
  : "${CANDIDATES:?}" "${SUMMARY:?}" "${GITHUB_OUTPUT:?}" "${GITHUB_STEP_SUMMARY:?}"
  local first="" delimiter random
  if [[ -f ${SUMMARY} ]]; then
    cat -- "${SUMMARY}" >> "${GITHUB_STEP_SUMMARY}"
  fi
  if [[ -d ${CANDIDATES} ]]; then
    first=$(find "${CANDIDATES}" -type f -print -quit)
  fi
  if [[ -z ${first} ]]; then
    echo "staged=false" >> "${GITHUB_OUTPUT}"
    echo "the search staged nothing; nothing to propose"
    return 0
  fi
  random=$(od -An -N16 -tx1 /dev/urandom)
  delimiter="summary-${random//[^0-9a-f]/}"
  {
    echo "staged=true"
    echo "summary<<${delimiter}"
    if [[ -f ${SUMMARY} ]]; then cat -- "${SUMMARY}"; fi
    echo "${delimiter}"
  } >> "${GITHUB_OUTPUT}"
  echo "the search staged inputs; the propose job opens them as a pull request"
}

propose() {
  : "${GH_TOKEN:?GH_TOKEN must be set (the release App token)}"
  : "${GITHUB_REPOSITORY:?}"
  : "${DEFAULT_BRANCH:?}"
  : "${APP_SLUG:?APP_SLUG must be set (the create-github-app-token app-slug output)}"
  local head base bot_id bot changes path contents oid existing body
  # shellcheck source=release/signed-commit.sh
  . "$(dirname "${BASH_SOURCE[0]}")/signed-commit.sh"

  # On the branch's head when it exists: an unmerged proposal is built
  # on, never reset (#1897).
  if head=$(gh api "repos/${GITHUB_REPOSITORY}/git/ref/heads/${branch}" \
    --jq '.object.sha' 2> /dev/null) && [[ -n ${head} ]]; then
    echo "${branch} exists at ${head}; adding to it"
  else
    base=$(forge "read the head of ${DEFAULT_BRANCH}" \
      "repos/${GITHUB_REPOSITORY}/git/ref/heads/${DEFAULT_BRANCH}" --jq '.object.sha')
    forge "create ${branch} at ${base}" "repos/${GITHUB_REPOSITORY}/git/refs" \
      --method POST -f "ref=refs/heads/${branch}" -f sha="${base}" > /dev/null
    head=${base}
    echo "created ${branch} at ${head}"
  fi

  # Signed off as the App's bot user, as the release commit is
  # (open-release-pr.sh says why the users endpoint, not `user`).
  bot_id=$(forge "read the bot user of ${APP_SLUG}" \
    "users/${APP_SLUG}%5Bbot%5D" --jq '.id')
  bot="${APP_SLUG}[bot] <${bot_id}+${APP_SLUG}[bot]@users.noreply.github.com>"

  changes=$(mktemp)
  for path in "${files[@]}"; do
    contents=$(base64 < "${CANDIDATES}/${path}")
    jq -n --arg path "${path}" --arg contents "${contents//$'\n'/}" \
      '{path: $path, contents: $contents}'
  done > "${changes}"
  jq -s '{additions: ., deletions: []}' "${changes}" > "${changes}.json"
  mv -- "${changes}.json" "${changes}"
  oid=$(org_signed_commit "fuzz corpus commit" "${branch}" "${head}" "${title}" \
    "Signed-off-by: ${bot}" "${changes}")
  rm -f -- "${changes}"
  echo "committed ${oid} to ${branch}, signature verified"

  body=$(
    printf '%s\n\n' "The scheduled fuzz search kept these inputs (#1897):" \
      "each raised its target's corpus coverage, or crashed it."
    printf '%s\n\n' "${SUMMARY:-}"
    printf '%s\n' "A crash- file keeps the gate red until its fix is pushed here."
  )
  # No checkout here, so no git remote for gh to read the repository
  # from: every pull-request call names it.
  existing=$(gh pr list --repo "${GITHUB_REPOSITORY}" --head "${branch}" --state open \
    --json number --jq '.[0].number // empty')
  if [[ -n ${existing} ]]; then
    gh pr edit "${existing}" --repo "${GITHUB_REPOSITORY}" --title "${title}" --body "${body}"
    echo "updated PR #${existing}"
  else
    gh pr create --repo "${GITHUB_REPOSITORY}" --head "${branch}" --base "${DEFAULT_BRANCH}" \
      --title "${title}" --body "${body}"
    echo "opened the fuzz corpus PR"
  fi
}

# Sourced, the functions alone (the path rows of
# release/test_propose_fuzz_corpus.py); run, the mode.
[[ ${BASH_SOURCE[0]} == "$0" ]] || return 0
case "${1:-}" in
  handover)
    handover
    ;;
  check)
    check
    echo "the artifact holds ${#files[@]} corpus file(s), every one at a corpus path"
    ;;
  propose)
    check
    propose
    ;;
  *)
    echo "usage: propose-fuzz-corpus.sh handover|check|propose" >&2
    exit 1
    ;;
esac
