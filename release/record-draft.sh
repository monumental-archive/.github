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
# marker this writes, so the marker is stated once, here. One other thing
# writes it without coming through this script: that audit's own
# visibility probe (#604), which annotates its ephemeral draft at
# CREATION rather than afterwards, because the whole point of annotating
# it is to explain a probe that failed to be cleaned up.
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
#   REGISTRIES     one `<registry>=<job result>` line per job of the run
#                  that publishes to a registry (#1280): the crates.io and
#                  npm uploads and the image-tag jobs. A burn is not a
#                  release, but the uploads run beside each other after
#                  the commit point and cannot be taken back, so a run
#                  that dies at one of them may already have shipped
#                  another — and a record saying "nothing became public"
#                  would then be false. A result is what GitHub reports
#                  for the job: success, failure, cancelled, skipped, or
#                  empty when the job is not in the run. Evidence of a
#                  run, so it needs RUN_URL and KIND=burn.
set -euo pipefail

repo="${REPO:-${GITHUB_REPOSITORY:-}}"
tag="${TAG:-}"
kind="${KIND:-}"
run_url="${RUN_URL:-}"
fixed_forward="${FIXED_FORWARD:-}"
registries="${REGISTRIES:-}"

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

# What became public, read from the job results before anything is read
# from the forge, so a malformed list never reaches a release. A success
# is public and stays public (neither registry takes a version back, and
# a tag is only applied once the release itself is out); a failure or a
# cancellation may have stopped part-way through an upload, and the
# record says exactly that rather than guessing either way.
public=""
partial=""
if [[ -n ${registries} ]]; then
  if [[ ${kind} != "burn" || -z ${run_url} ]]; then
    echo "record-draft: REGISTRIES is evidence of a publish run: it needs KIND=burn and RUN_URL" >&2
    exit 1
  fi
  while IFS= read -r line; do
    [[ -n ${line} ]] || continue
    if [[ ${line} != *=* || -z ${line%=*} ]]; then
      echo "record-draft: REGISTRIES line '${line}' is not <registry>=<job result>" >&2
      exit 1
    fi
    name="${line%=*}"
    result="${line##*=}"
    case "${result}" in
      success) public+="- ${name}"$'\n' ;;
      failure | cancelled) partial+="- ${name}: may be partly published"$'\n' ;;
      skipped | "") ;;
      *)
        echo "record-draft: REGISTRIES: ${name} has job result '${result}'," \
          "not success, failure, cancelled, skipped or empty" >&2
        exit 1
        ;;
    esac
  done <<< "${registries}"
fi

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
  #
  # And a run that died after a registry upload is a third claim: the
  # release never published, but something did, and a stranger reading
  # the record must learn what (#1280).
  if [[ -n ${public}${partial} ]]; then
    explain="\`${tag}\` was tagged, and its publish run failed after these"
    explain+=" became public, and they remain:"$'\n\n'
    explain+="${public}${partial}"$'\n'
    explain+="Tags are immutable, so this version number is spent: the"
    explain+=" release never published, and nothing more can ship under it."
  else
    if [[ -n ${run_url} ]]; then
      explain="\`${tag}\` was tagged, and its publish run failed before anything"
      explain+=" became public."
    else
      explain="\`${tag}\` was tagged and never published; no publish run for it"
      explain+=" survives in the record."
    fi
    explain+=" Tags are immutable, so this version number is"
    explain+=" spent: nothing shipped under it and nothing can."
  fi
  explain+=" This draft is the record of that, not a release in flight."
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
