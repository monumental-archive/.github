#!/usr/bin/env bash
# Emit the source provenance, then derive the VSA from it — in that
# order, with a verification between.
#
# The provenance predicate is org-defined: SLSA v1.2 leaves source
# provenance "undefined and up to the SCSs to determine", and this file
# plus docs/source-track.md IS the org's documentation of the format.
# The VSA predicate is the spec's: verification_summary/v1, verifiedLevels
# carrying the source level plus the ORG_SOURCE_ properties.
#
# PREDICATE-TYPE SURFACE. This emitter signs exactly two predicate
# types, enumerated here the way sign.yml's allowlist enumerates the
# release surface — growing this list is a reviewed change to the canon:
#   https://monumental-archive.github.io/attestations/source-provenance/v1
#   https://slsa.dev/verification_summary/v1
# It does not route through sign.yml: the chain append needs
# `contents: write`, the one grant the signer must never hold
# (docs/release.md), and the identity that must appear in the
# certificate is the calling repo's reserved path, not the signer's.
#
# The VSA is derived from the SCS-issued provenance ONLY (the L2+
# requirement): the just-signed bundle is verified against the pinned
# identity first, and the level and properties are read back out of the
# verified statement — never recomputed from live state, which by then
# is a different moment.
set -euo pipefail

prov_type="https://monumental-archive.github.io/attestations/source-provenance/v1"
vsa_type="https://slsa.dev/verification_summary/v1"

cd "${SA_WORK}/repo"
parents=$(git rev-parse "${GITHUB_SHA}^@" | jq -Rc . | jq -sc .)
commit_time=$(git show -s --format=%cI "${GITHUB_SHA}")

jq -n \
  --arg type "${prov_type}" \
  --arg repo "${GITHUB_REPOSITORY}" \
  --arg rev "${GITHUB_SHA}" \
  --arg actor "${SA_ACTOR}" \
  --arg actor_id "${SA_ACTOR_ID}" \
  --arg ct "${commit_time}" \
  --arg canon "${SA_CANON_REF}" \
  --argjson parents "${parents}" \
  --slurpfile claims "${SA_WORK}/claims.json" \
  --slurpfile prev "${SA_WORK}/prev.json" \
  '{
    _type: "https://in-toto.io/Statement/v1",
    subject: [{
      digest: {gitCommit: $rev},
      annotations: {sourceRefs: ["refs/heads/main"]}
    }],
    predicateType: $type,
    predicate: {
      repository: ("https://github.com/" + $repo),
      ref: "refs/heads/main",
      parents: $parents,
      actor: {login: $actor, id: $actor_id},
      commitTime: $ct,
      rulesReadAt: $claims[0].rulesReadAt,
      controls: $claims[0].controls,
      prev: $prev[0].prev,
      canonRef: $canon
    }
  }' > "${SA_WORK}/provenance.json"

# SA_SKIP_SIGN is the dry run's seam (#236): everything is shaped,
# nothing is signed — no OIDC token exists off-CI and no Rekor entry
# may ever be minted for a run that cannot complete.
if [[ ${SA_SKIP_SIGN:-false} == true ]]; then
  jq -n '{dryRun: true}' > "${SA_WORK}/provenance.bundle.json"
else
  cosign sign-blob --yes \
    --bundle "${SA_WORK}/provenance.bundle.json" \
    "${SA_WORK}/provenance.json" > /dev/null

  # Self-verify with exactly the stranger's inputs: the published SAN and
  # issuer, nothing this run knows that a consumer does not.
  cosign verify-blob \
    --bundle "${SA_WORK}/provenance.bundle.json" \
    --certificate-identity "${SA_IDENTITY}" \
    --certificate-oidc-issuer "${SA_ISSUER}" \
    "${SA_WORK}/provenance.json" > /dev/null 2>&1 || {
    echo "::error::the provenance just signed does not verify against ${SA_IDENTITY} — the certificate identity is not the published contract"
    exit 1
  }
fi

# Level, from the policy against the VERIFIED provenance. Presence of
# every required property means the target level; anything less
# under-claims to level 2 — provenance, history and identity controls
# are what this emitter existing at all demonstrates, and the missing
# property is the audit's signal, not a reason to claim nothing.
policy="${SA_CANON_ROOT}/source-policies/default.json"
repo_policy="${SA_CANON_ROOT}/source-policies/${GITHUB_REPOSITORY#*/}.json"
[[ -f ${repo_policy} ]] && policy="${repo_policy}"
target=$(jq -r '.protected_branches[] | select(.name == "main") | .target_level' "${policy}")
required=$(jq -c '[.protected_branches[] | select(.name == "main") | .required_properties[].name]' "${policy}")
present=$(jq -c '[.predicate.controls[].property]' "${SA_WORK}/provenance.json")
if jq -en --argjson req "${required}" --argjson has "${present}" \
  '$req - $has == []' > /dev/null; then
  level="${target}"
else
  level="SLSA_SOURCE_LEVEL_2"
  echo "::warning::required properties missing: $(jq -rn --argjson req "${required}" --argjson has "${present}" '($req - $has) | join(", ")') — under-claiming ${level}"
fi

verified_levels=$(jq -cn --arg l "${level}" --argjson p "${present}" '[$l] + $p')
time_verified=$(date -u +%Y-%m-%dT%H:%M:%SZ)
jq -n \
  --arg type "${vsa_type}" \
  --arg id "${SA_IDENTITY}" \
  --arg repo "${GITHUB_REPOSITORY}" \
  --arg rev "${GITHUB_SHA}" \
  --arg tv "${time_verified}" \
  --arg canon "${SA_CANON_REF}" \
  --arg policy "source-policies/$(basename "${policy}")" \
  --argjson levels "${verified_levels}" \
  '{
    _type: "https://in-toto.io/Statement/v1",
    subject: [{
      digest: {gitCommit: $rev},
      annotations: {sourceRefs: ["refs/heads/main"]}
    }],
    predicateType: $type,
    predicate: {
      verifier: {id: $id},
      timeVerified: $tv,
      resourceUri: ("git+https://github.com/" + $repo),
      policy: {uri: ("https://github.com/monumental-archive/.github/blob/" + $canon + "/" + $policy)},
      verificationResult: "PASSED",
      verifiedLevels: $levels
    }
  }' > "${SA_WORK}/vsa.json"

if [[ ${SA_SKIP_SIGN:-false} == true ]]; then
  jq -n '{dryRun: true}' > "${SA_WORK}/vsa.bundle.json"
else
  cosign sign-blob --yes \
    --bundle "${SA_WORK}/vsa.bundle.json" \
    "${SA_WORK}/vsa.json" > /dev/null
fi

echo "::notice::emitted ${level} for ${GITHUB_SHA}"
