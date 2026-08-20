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

repo_slug="${GITHUB_REPOSITORY:-$(git remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')}"
repo_url="https://github.com/${repo_slug}"
groups="feat=Added,fix=Fixed,perf=Performance,refactor=Changed"
groups+=",docs=Documentation,test=Testing,build=Build,ci=CI"
groups+=",chore(deps)=Dependencies,revert=Reverted"
order="Breaking,Added,Changed,Fixed,Performance,Documentation"
order+=",Testing,Build,CI,Dependencies,Reverted"

plan="${RUNNER_TEMP:-/tmp}/tag-plan.json"
stele derive release-plan \
  --git-dir . \
  --groups "${groups}" \
  --group-order "${order}" \
  --breaking-group "Breaking" \
  --compare-url "${repo_url}/compare/" \
  --release-url "${repo_url}/releases/tag/" \
  --pull-url "${repo_url}/pull/" \
  --out "${plan}"

version=$(jq -r '.version // ""' "${plan}")
tag=$(jq -r '.tag // ""' "${plan}")
subject=$(jq -r '.commit.subject // ""' "${plan}")
if [[ -z ${tag} || -z ${version} ]]; then
  echo "FAIL: the plan names no tag to mint" >&2
  jq -r '(.refusals // [])[] | "  " + .cause + ": " + .detail' "${plan}" >&2
  exit 1
fi

# Idempotent resume, before any refusal is read: a re-dispatch onto a
# commit already tagged has nothing to do and is not a failure.
if git rev-parse -q --verify "refs/tags/${tag}" > /dev/null; then
  echo "${tag} already exists; nothing to do"
  exit 0
fi

refusals=$(jq -r '(.refusals // [])[] | "  " + .cause + ": " + .detail' "${plan}")
if [[ -n ${refusals} ]]; then
  echo "FAIL: the release plan refuses:" >&2
  echo "${refusals}" >&2
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
    echo "FAIL: HEAD is not the release commit for ${tag}" >&2
    echo "  expected: ${subject}" >&2
    echo "  subject:  ${head_subject}" >&2
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
# nothing burns. Same carve-outs as the generator and the publish
# guard: no extension control files, no previous release, or a previous
# release that shipped no tarballs for the extension all pass.
#
# It stays here rather than moving into the plan because it is not a fact
# about this repository's history: it compares a derived file against what
# a DIFFERENT release published, which the plan has no reach into.
# shellcheck disable=SC2312  # process substitution: capturing first would
# turn an empty result into one blank line, which is a worse bug than the
# masked status. The producing command is git/jq over local state.
while IFS= read -r control; do
  [[ -n ${control} ]] || continue
  name=$(basename "${control}" .control)
  crate_dir=$(dirname "${control}")
  prev=$(gh release view --json tagName --jq .tagName 2> /dev/null || true)
  [[ -n ${prev} && ${prev} == v* ]] || continue
  prev="${prev#v}"
  [[ ${prev} != "${version}" ]] || continue
  shipped=$(gh release view "v${prev}" --json assets --jq \
    "[.assets[].name | select(startswith(\"${name}-${prev}-pg\"))] | length")
  [[ ${shipped} != "0" ]] || continue
  upgrade="${crate_dir}/sql/${name}--${prev}--${version}.sql"
  if [[ ! -f ${upgrade} ]]; then
    echo "FAIL: refusing to mint ${tag} — no upgrade path ${upgrade} from published v${prev}" >&2
    echo "  The Release PR derived its path before v${prev} finished publishing" >&2
    echo "  (#358 seam 5). No tag was minted, so ${version} is NOT burned:" >&2
    echo "  once the in-flight publish is complete, refresh the derivation via" >&2
    echo "  the Release-PR leg (a push to the default branch, or the" >&2
    echo "  release-published trigger) and re-run this job on the refreshed" >&2
    echo "  release commit." >&2
    exit 1
  fi
done < <(git ls-files '*.control')

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
git -c gpg.format=x509 \
  -c gpg.x509.program=gitsign \
  tag -s "${tag}" -m "${tag}"
git push origin "${tag}"
echo "pushed ${tag} (signed)"

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
echo "created draft release ${tag}"
