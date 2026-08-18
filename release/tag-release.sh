#!/usr/bin/env bash
# Release phase 1, step 2: the Release PR has been merged, so tag it and cut
# the draft release. Publishes nothing — pushing the tag is what starts
# phase 2 (the repository's publish workflow), which builds, proves, signs
# and fills the draft. Org canon — see docs/release.md.
#
# The tag MUST be pushed with the tag-mint App token. Tags pushed with the
# default GITHUB_TOKEN do not trigger workflows (GitHub's recursion guard),
# and a release that silently triggers nothing looks exactly like a success.
# The App is also the sole bypass actor on the org's v* creation ruleset:
# this job is the only place in the org a release tag can come from.
set -euo pipefail

# The version source is detected, never configured — the phase-1 contract
# in docs/release.md. With no manifest to read, the release commit's own
# subject names the version; the guard below already refuses any HEAD that
# is not a release commit, so the subject is exactly as trusted as the
# manifest path.
if [[ -f Cargo.toml ]]; then
  version=$(taplo get -f Cargo.toml 'workspace.package.version')
else
  version=$(git log -1 --pretty=%s | sed -nE 's/^chore: release v([0-9][^ ]*).*/\1/p')
  if [[ -z ${version} ]]; then
    echo "FAIL: no manifest, and HEAD's subject names no release version" >&2
    # shellcheck disable=SC2312  # diagnostic output only; a failure here
    # degrades a log line, it does not change what is written or decided.
    echo "  subject: $(git log -1 --pretty=%s)" >&2
    exit 1
  fi
fi
tag="v${version}"

# Guard: only ever tag a commit that is a release commit. A workflow_dispatch
# on an ordinary commit would otherwise mint a tag for a version whose
# manifests and changelog were never prepared.
subject=$(git log -1 --pretty=%s)
case "${subject}" in
  "chore: release ${tag}"*) ;;
  *)
    echo "FAIL: HEAD is not the release commit for ${tag}" >&2
    echo "  subject: ${subject}" >&2
    exit 1
    ;;
esac

if git rev-parse -q --verify "refs/tags/${tag}" > /dev/null; then
  echo "${tag} already exists; nothing to do"
  exit 0
fi

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
# it written by a machine at a different time.
# The notes conventions, previously cliff.toml: groups and URLs are the
# org convention stated once here; the bump rules (0.x breaking bumps
# minor, chore/ci/docs/style/test release nothing) are stele derive's
# own defaults — and unlike the pinned git-cliff, whose
# no_increment_regex was silently inert (2.13.1 drops unknown [bump]
# keys), the silent-types rule is now real. Bare chore is unmapped:
# release commits and self-pin bumps stay out of the notes, at the
# recorded cost of cliff's Miscellaneous heading.
repo_slug="${GITHUB_REPOSITORY:-$(git remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')}"
repo_url="https://github.com/${repo_slug}"
groups="feat=Added,fix=Fixed,perf=Performance,refactor=Changed"
groups+=",docs=Documentation,test=Testing,build=Build,ci=CI"
groups+=",chore(deps)=Dependencies,revert=Reverted"
order="Breaking,Added,Changed,Fixed,Performance,Documentation"
order+=",Testing,Build,CI,Dependencies,Reverted"
notes_flags=(
  --groups "${groups}"
  --group-order "${order}"
  --breaking-group "Breaking"
  --compare-url "${repo_url}/compare/"
  --release-url "${repo_url}/releases/tag/"
  --pull-url "${repo_url}/pull/"
)
notes=$(stele derive notes --git-dir . "${notes_flags[@]}")

# Draft, always. Immutability applies when a release is published rather than
# when it is created, so a release made public now could never receive the
# assets phase 2 attaches; and a phase 2 that dies leaves nothing public
# instead of an empty release.
printf '%s\n' "${notes}" | gh release create "${tag}" \
  --draft \
  --title "${tag}" \
  --notes-file -
echo "created draft release ${tag}"
