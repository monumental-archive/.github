#!/usr/bin/env bash
# Turn a leftover draft release into the written record of why it exists
# (#597). Org canon — see docs/release.md, "A draft explains itself".
#
# A tag is immutable, so a release that dies before publishing spends its
# version number and leaves a draft nothing ever cleans up. Four of them
# had accumulated in the org before anyone counted, and the count was
# wrong: thirty-two, because nobody could tell a burn from a release in
# flight by looking. Deleting the draft was the old instruction and is
# rejected — it destroys the only artifact of the failed attempt other
# than run logs, which expire at 90 days, and this org derives burns from
# run history. So the draft becomes the record instead.
#
# Called from three places, which is why it is a script and not three
# copies of a heredoc: the publish failure path (publish.yml's
# burn-record job), the rehearsal path (attach-release.yml, where a
# dry-run leaves a draft ON PURPOSE and is otherwise indistinguishable
# from a burn), and by hand for the backfill. `audit:drafts` reads the
# marker this writes, so the marker is stated once, here.
#
# Inputs are environment variables, like every other script in release/:
#
#   REPO           owner/name (default: $GITHUB_REPOSITORY)
#   TAG            the release tag, with its leading v
#   KIND           burn | rehearsal
#   RUN_URL        the run that burned or rehearsed it (optional but
#                  wanted: it is the evidence, and it outlives nothing —
#                  logs expire at 90 days, this record does not)
#   FIXED_FORWARD  the version that shipped the fix, when it is known.
#                  It is NOT known at burn time — the fix has not been
#                  cut yet — so the automatic path leaves it pending and
#                  a maintainer completes it. audit:drafts deliberately
#                  does not require it: an audit that goes red until a
#                  human types something is red by default.
set -euo pipefail

repo="${REPO:-${GITHUB_REPOSITORY:-}}"
tag="${TAG:-}"
kind="${KIND:-}"
run_url="${RUN_URL:-}"
fixed_forward="${FIXED_FORWARD:-}"

[[ -n ${repo} ]] || {
  echo "record-draft: REPO is unset" >&2
  exit 1
}
[[ -n ${tag} ]] || {
  echo "record-draft: TAG is unset" >&2
  exit 1
}
case "${kind}" in
  burn | rehearsal) ;;
  *)
    echo "record-draft: KIND must be burn or rehearsal, got '${kind}'" >&2
    exit 1
    ;;
esac

# A read that fails is never read as "no record yet": that would write a
# second record onto an annotated draft, or annotate a release that has
# already published. Same degraded-forge discipline as the audits.
if ! is_draft=$(gh release view "${tag}" --repo "${repo}" \
  --json isDraft --jq '.isDraft' 2> /dev/null); then
  echo "record-draft: no release at ${repo}@${tag}, or it could not be read" >&2
  exit 1
fi
if [[ ${is_draft} != "true" ]]; then
  echo "record-draft: ${repo}@${tag} is published — a published release is its own record, nothing to annotate"
  exit 0
fi

body=$(gh release view "${tag}" --repo "${repo}" --json body --jq .body)
if [[ ${body} == *"<!-- draft-record:"* ]]; then
  echo "record-draft: ${repo}@${tag} already carries a record, leaving it"
  exit 0
fi

recorded=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if [[ ${kind} == "burn" ]]; then
  headline="**Burned version — never released.**"
  # Two burns, and they are not the same claim. A record that asserts a
  # failed run for a tag that never had one is a fabricated citation —
  # the shape audit:citations exists to catch — so the sentence follows
  # the evidence rather than the other way round.
  if [[ -n ${run_url} ]]; then
    explain="\`${tag}\` was tagged, and its publish run failed before anything"
    explain+=" became public."
  else
    explain="\`${tag}\` was tagged and never published; no publish run for it"
    explain+=" survives in the record."
  fi
  explain+=" Tags are immutable, so this version number is"
  explain+=" spent: nothing shipped under it and nothing can. This draft is"
  explain+=" the record of that, not a release in flight."
  run_label="Burned by"
else
  headline="**Rehearsal — deliberately left unpublished.**"
  explain="\`${tag}\` was published with \`dry-run: true\`. The evidence bundle"
  explain+=" was attached and the release was left a draft on purpose. The tag"
  explain+=" is immutable all the same, so the version number is spent."
  run_label="Rehearsed by"
fi

record="<!-- draft-record: ${kind} -->"$'\n'
record+="${headline}"$'\n\n'
record+="${explain}"$'\n\n'
if [[ -n ${run_url} ]]; then
  record+="- ${run_label}: ${run_url}"$'\n'
else
  record+="- ${run_label}: no run recorded — written from the release history"$'\n'
fi
record+="- Recorded: ${recorded}"$'\n'
if [[ ${kind} == "burn" ]]; then
  if [[ -n ${fixed_forward} ]]; then
    record+="- Fixed forward in: ${fixed_forward}"$'\n'
  else
    record+="- Fixed forward in: pending — no fix had been cut when this record was written"$'\n'
  fi
fi
record+=$'\n---\n\n'

printf '%s%s' "${record}" "${body}" > "${TMPDIR:-/tmp}/draft-record-$$.md"
gh release edit "${tag}" --repo "${repo}" \
  --notes-file "${TMPDIR:-/tmp}/draft-record-$$.md" > /dev/null
rm -f "${TMPDIR:-/tmp}/draft-record-$$.md"
echo "record-draft: recorded ${kind} on ${repo}@${tag}"
