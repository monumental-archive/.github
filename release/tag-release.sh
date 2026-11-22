#!/usr/bin/env bash
# Release phase 1, step 2: the Release PR has been merged, so tag it and cut
# the draft release. Publishes nothing — pushing the tag is what starts
# phase 2 (the repository's publish workflow), which builds, proves, signs
# and fills the draft. Org canon — see docs/release.md.
#
# The tag and the notes are the plan's (stele#155), derived here from the
# merged release commit rather than re-detected: the old path read the
# version out of a manifest with taplo when one existed and out of the
# commit subject when it did not — two detections of a fact the engine
# owns, and the same rendering of the notes done a second time at a
# different moment. What stays is capability and the one check the plan
# cannot make.
#
# The tag MUST be pushed with the tag-mint App token. Tags pushed with the
# default GITHUB_TOKEN do not trigger workflows (GitHub's recursion guard),
# and a release that silently triggers nothing looks exactly like a success.
# The App is also the sole bypass actor on the org's v* creation ruleset:
# this job is the only place in the org a release tag can come from.
set -euo pipefail

canon="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The canon's writer of findings (#1934), which brings its writer of
# workflow commands (#1660).
# shellcheck source=mise/findings.sh
. "${canon}/mise/findings.sh"
repo_slug="${GITHUB_REPOSITORY:-$(git remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')}"
repo_url="https://github.com/${repo_slug}"
groups="feat=Added,fix=Fixed,perf=Performance,refactor=Changed"
groups+=",docs=Documentation,test=Testing,build=Build,ci=CI"
groups+=",chore(deps)=Dependencies,revert=Reverted"
order="Breaking,Added,Changed,Fixed,Performance,Documentation"
order+=",Testing,Build,CI,Dependencies,Reverted"

# Idempotent resume, and it is asked of HEAD rather than of the plan
# (#864). A re-dispatch onto a commit that already carries its release
# tag has nothing left to do: the tag is minted, the version is spent,
# and the work this script exists for is done. That is not a failure,
# and a release path that reds when it should no-op is what trains
# people to re-run past red.
#
# It has to come BEFORE the plan, and the plan cannot answer it. Once
# the tag is on HEAD the commit range is empty, so `derive release-plan`
# correctly reports `release=false` and names no tag at all — the
# question "is this commit already released?" is invisible to it, and
# the empty-tag refusal below would fire first anyway. So the tag is
# read off the commit: the ONE thing HEAD can say for itself.
#
# The previous version of this check asked the plan for a tag name and
# then looked it up, three refusals too late to ever run. Measured in
# #864, both routes: with the tag on HEAD it died at the empty-tag
# refusal, and with the tag anywhere else `derive release-plan` refuses
# `tag-taken` and exits non-zero, which under `set -e` ends the script
# forty lines above the branch. It described behaviour that could not
# happen, which is worse than describing nothing.
#
# Highest tag wins if a commit somehow carries several: an anomaly
# worth reporting deterministically rather than arbitrarily.
resumed=$(git tag --points-at HEAD --list 'v[0-9]*' | sort -V | tail -1)
if [[ -n ${resumed} ]]; then
  org_say "already minted, nothing to do: ${resumed}"
  exit 0
fi

plan="${RUNNER_TEMP:-/tmp}/tag-plan.json"
# A refusal from the engine is reported as one. Without this the only
# `tag-taken` route out of here was a bare `set -e` exit: the job went
# red carrying stele's message and nothing of this script's, so the
# reader could not tell a refused plan from a crashed binary (#864).
# The detail is stele's own, already printed above — no plan file is
# written when it refuses, measured, so there is nothing here to re-read.
if ! stele derive release-plan \
  --git-dir . \
  --groups "${groups}" \
  --group-order "${order}" \
  --breaking-group "Breaking" \
  --compare-url "${repo_url}/compare/" \
  --release-url "${repo_url}/releases/tag/" \
  --pull-url "${repo_url}/pull/" \
  --out "${plan}"; then
  org_finding "" "the release plan was refused — see the refusal above" \
    "Nothing was minted and no version was spent. A 'tag-taken'" \
    "refusal means the namespace already carries this version:" \
    "the release is done, or the tag was minted outside this job."
  exit 1
fi

# Each refusal the plan names, one annotated finding each: a detail is the
# planner's text, and read raw a newline in it began a log line of its
# own (#1660). NUL-delimited, so the newline stays inside its refusal.
refused() {
  local refusal listed
  listed=$(mktemp)
  jq -j '(.refusals // [])[] | .cause + ": " + .detail + "\u0000"' "${plan}" > "${listed}"
  while IFS= read -r -d '' refusal; do
    org_finding_annotated "" "${refusal}"
  done < "${listed}"
  rm -f "${listed}"
}

version=$(jq -r '.version // ""' "${plan}")
tag=$(jq -r '.tag // ""' "${plan}")
subject=$(jq -r '.commit.subject // ""' "${plan}")
if [[ -z ${tag} || -z ${version} ]]; then
  org_finding "" "the plan names no tag to mint"
  refused
  exit 1
fi

refusal_count=$(jq '(.refusals // []) | length' "${plan}")
if [[ ${refusal_count} -gt 0 ]]; then
  org_finding "" "the release plan refuses:"
  refused
  exit 1
fi

# Guard: only ever tag a commit that is a release commit. A workflow_dispatch
# on an ordinary commit would otherwise mint a tag for a version whose
# manifests and changelog were never prepared. The subject compared against
# is the plan's own rendering, so the guard and the commit that satisfies it
# come from one template.
head_subject=$(git log -1 --pretty=%s)
case "${head_subject}" in
  "${subject}"*) ;;
  *)
    org_finding "" "HEAD is not the release commit for ${tag}" \
      "expected: ${subject}" \
      "subject:  ${head_subject}"
    exit 1
    ;;
esac

# Seam 5 of #358: the pgrx upgrade path is derived on the Release PR
# against the release published AT DERIVATION TIME, and the PR machinery
# runs before the previous release's publish has finished — so a PR
# derived inside that window starts its path from a version about to be
# superseded, and the publish guard (build-pgrx-extension.yml) discovers
# it only AFTER this tag exists, burning an immutable version number
# (release-lab v0.24.3, all ten cells). This is the same check moved to
# the last reversible moment: a stale derivation refuses the MINT, and
# nothing burns. The predecessor is the one answer the generator and the
# publish guard use, previous_release.py's (#1592): the newest version in
# the extension's upgrade graph LOWER than this one that a non-draft
# release carries tarballs for, and that release's tag whatever it is
# named. It used to be GitHub's "latest" release with a `v` on its tag,
# which an imported tag scheme, a burned head or a maintenance release
# published after a newer one each made a different release, or none.
# No extension control files, or no installable predecessor, pass.
#
# It stays here rather than moving into the plan because it is not a fact
# about this repository's history: it compares a derived file against what
# a DIFFERENT release published, which the plan has no reach into.
controls=$(git ls-files '*.control')
while IFS= read -r control; do
  [[ -n ${control} ]] || continue
  name=$(basename "${control}" .control)
  crate_dir=$(dirname "${control}")
  answer=$("${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}" \
    "${canon}/release/pgrx/previous_release.py" predecessor \
    --repo "${repo_slug}" --version "${version}" --control "${control}") || {
    org_finding "" "refusing to mint ${tag} — the release listing could not be read" \
      "to find ${name}'s predecessor; nothing was minted, re-run this job."
    exit 1
  }
  [[ -n ${answer} ]] || continue
  prev="${answer%%$'\t'*}"
  prev_tag="${answer#*$'\t'}"
  upgrade="${crate_dir}/sql/${name}--${prev}--${version}.sql"
  if [[ ! -f ${upgrade} ]]; then
    org_finding "" "refusing to mint ${tag} — no upgrade path ${upgrade} from published ${prev} (${prev_tag})" \
      "The Release PR derived its path before ${prev_tag} finished publishing" \
      "(#358 seam 5). No tag was minted, so ${version} is NOT burned:" \
      "once the in-flight publish is complete, refresh the derivation via" \
      "the Release-PR leg (a push to the default branch, or the" \
      "release-published trigger) and re-run this job on the refreshed" \
      "release commit."
    exit 1
  fi
done <<< "${controls}"

# Signed, not merely annotated (#349 S4): gitsign puts a keyless x509
# signature in the tag object, under the same Sigstore root of trust as
# every other org signature — so an App-minted tag and a hand-minted
# break-glass tag are distinguishable by inspection instead of by
# recall, and `git verify-tag` answers for a verifier with gitsign
# configured (the limit docs/runbook.md states: x509 in the PGP slot is
# not universal). The push still authenticates as the App, so the
# sole-bypass tag ruleset holds unchanged. Fail closed: a tag the
# signer cannot sign is not minted at all — the calling job holds
# `id-token: write` for exactly this step (the capability-boundary
# marker in release.yml records why that is safe).
# No connector configuration: in Actions gitsign takes the ambient
# OIDC token (ACTIONS_ID_TOKEN_REQUEST_URL), which is the identity the
# certificate should carry — the workflow, not a human.
#
# rekorMode=offline, and it is a MINT-SEMANTICS decision rather than a
# flag (stele#167, stele#173). gitsign's default is `online`, which
# writes the transparency-log entry and then drops the receipt: the
# signature carries no proof it was ever logged, so verification can
# only reach the certificate's own issuance instant and never a
# countersigned one. Every tag this org minted before this line is in
# that state — 43 of 43 carry no embedded entry.
#
# Only `offline` reaches gitsign's `attachRekorLogEntry`, so only
# `offline` produces a tag that verifies against a countersigned
# instant with no network at read time. The cost, stated because it is
# permanent and public: the two modes log DIFFERENT SUBJECTS —
# `LegacySHASign` writes a commit-SHA entry, `git.Sign` a hashedrekord
# over the signed attributes — so this changes what this organisation's
# tags are recorded as in Rekor, forever, from this tag onward.
#
# The alternative considered and rejected (stele#173): resolving the
# entry online at verification time needs no mint change and works on
# every existing tag, but it makes the verdict depend on a search index
# the signer does not control and history can lose, re-derived at read
# time rather than carried. Legitimate for healing an existing span;
# wrong as the standing design, and the org refuses that shape
# everywhere else.
#
# Tags minted before this line never carry a receipt, so the policy
# floor stays `certificate-transparency` until an epoch declares where
# `observer-timestamp` begins (stele#173 item 3) — raising it without
# one reddens every tag ever minted, the #128/#109 shape.
git -c gpg.format=x509 \
  -c gpg.x509.program=gitsign \
  -c gitsign.rekorMode=offline \
  tag -s "${tag}" -m "${tag}"

# One refusal is named rather than passed through (#1322). GitHub judges
# an App creating a ref by comparing that ref's workflow files with the
# default branch's TIP, and the tag-mint App holds no `workflows`
# permission, by decision (#165). So a workflow-changing commit merged
# after the release commit — a canon bump queued behind the Release PR,
# which changes every pin — makes the push of this tag refusable, and no
# re-run can mint it until the default branch's workflow files equal the
# release commit's again. Measured on edtf v1.3.5: refused twice, minted
# on the third run once edtf#261 had put the tree back. The bare git
# output names a ruleset and a permission and says nothing of either.
#
# A refused push mints nothing and spends nothing, so the local tag is
# dropped with it: nothing in this job may claim the tag exists. Any
# other push failure keeps git's own output and status, as before.
push_log="${RUNNER_TEMP:-/tmp}/tag-push-$$.log"
push_rc=0
git push origin "${tag}" 2> "${push_log}" || push_rc=$?
if [[ ${push_rc} -ne 0 ]] \
  && grep -qF "refusing to allow a GitHub App to create or update workflow" "${push_log}"; then
  git tag -d "${tag}" > /dev/null
  msg="the push of ${tag} was refused: a workflow-changing commit reached the default"
  msg+=" branch after the release commit, so its workflow files differ from the release commit's, and"
  msg+=" GitHub will not let the tag-mint App create a ref that would change them. ${tag} has not been"
  msg+=" minted and ${version} is not spent. Land one change on the default branch that makes its"
  msg+=" workflow files equal to the release commit's again (the commit that moved them, reverted),"
  msg+=" then re-run this job (docs/runbook.md)."
  org_finding_annotated "" "${msg}"
  exit 1
fi
cat "${push_log}" >&2
[[ ${push_rc} -eq 0 ]] || exit "${push_rc}"
org_say "pushed ${tag} (signed)"

# Release notes are the changelog section for this version — the same text
# reviewers already approved in the Release PR, not a second description of
# it written by a machine at a different time. It is the plan's rendering,
# so "the same text" is a property of the document rather than of two
# invocations agreeing.
#
# Draft, always. Immutability applies when a release is published rather than
# when it is created, so a release made public now could never receive the
# assets phase 2 attaches; and a phase 2 that dies leaves nothing public
# instead of an empty release.
jq -r '.notes' "${plan}" | gh release create "${tag}" \
  --draft \
  --title "${tag}" \
  --notes-file -
org_say "created draft release ${tag}"
