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
notes=$(git cliff --latest --strip all)

# Draft, always. Immutability applies when a release is published rather than
# when it is created, so a release made public now could never receive the
# assets phase 2 attaches; and a phase 2 that dies leaves nothing public
# instead of an empty release.
printf '%s\n' "${notes}" | gh release create "${tag}" \
  --draft \
  --title "${tag}" \
  --notes-file -
echo "created draft release ${tag}"
