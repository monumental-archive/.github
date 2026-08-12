#!/usr/bin/env bash
# Emit a chain link for every revision chain.sh listed — the pushed
# revision, plus any holes earlier lapses left (#265). For each: the
# source provenance, then the VSA derived from it — in that order, with
# a verification between.
#
# The provenance predicate is org-defined: SLSA v1.2 leaves source
# provenance "undefined and up to the SCSs to determine";
# docs/source-provenance.md documents every field (the L2+ MUST).
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
# HEALED LINKS are honest about being late. A link emitted for any
# revision other than the pushed one carries `repaired: {at: <now>}` in
# its provenance — the spec asks that provenance be created
# contemporaneously with the branch update, and a backfilled signature
# over git's contemporaneous record is the nearest reachable point to
# that, named rather than passed off. Its level is COMPUTED, not
# chosen: the target level only when every contributing ruleset's
# updated_at (read under the claims token, carried in the claims
# payload as epochs) predates the revision's commit time — the rules
# provably have not changed since before the commit — and
# SLSA_SOURCE_LEVEL_2 otherwise. A guard that cannot prove continuity
# under-claims; it never guesses.
#
# The VSA is derived from the SCS-issued provenance ONLY (the L2+
# requirement): the just-signed bundle is verified against the pinned
# identity first, and the level and properties are read back out of the
# verified statement — never recomputed from live state, which by then
# is a different moment.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

prov_type="https://monumental-archive.github.io/attestations/source-provenance/v1"
vsa_type="https://slsa.dev/verification_summary/v1"

# policy.digest wants the canon COMMIT (the spec's policy SHOULD, #267).
# The canon is consumed SHA-pinned by convention, so the resolution's ref
# already is the digest; a non-SHA ref here means the pin convention was
# broken, which is refused rather than guessed around — emit.sh holds no
# token to resolve refs with, on purpose.
if [[ ! ${SA_CANON_REF} =~ ^[0-9a-f]{40}$ ]]; then
  echo "::error::canon ref '${SA_CANON_REF}' is not a commit SHA — the emitter must be pinned by full SHA so the VSA can carry policy.digest (#267)"
  exit 1
fi

cd "${SA_WORK}/repo"

policy="${SA_CANON_ROOT}/source-policies/default.json"
repo_policy="${SA_CANON_ROOT}/source-policies/${GITHUB_REPOSITORY#*/}.json"
[[ -f ${repo_policy} ]] && policy="${repo_policy}"
target=$(jq -r '.protected_branches[] | select(.name == "main") | .target_level' "${policy}")
required=$(jq -c '[.protected_branches[] | select(.name == "main") | .required_properties[].name]' "${policy}")

# The continuity horizon: the newest moment any contributing ruleset
# changed, as epochs claims.sh normalised. Empty means unprovable, and
# unprovable under-claims.
rules_max_epoch=$(jq -r '[.rulesetsUpdatedAt[]?] | max // empty' "${SA_WORK}/claims.json")

# Manifest of everything this run emits, for append.sh's retry re-add —
# and the loop's own record of which parents it created: a parent in the
# manifest was self-verified at signing, so the stranger's check is not
# run against it a second time (and the dry run, which signs nothing,
# could not verify it at all). Plain file, not an associative array —
# these scripts also run under macOS's bash 3.2 in the dry run.
: > "${SA_WORK}/manifest.tsv"

while IFS= read -r rev; do
  [[ -n ${rev} ]] || continue
  linkdir="${SA_WORK}/links/${rev}"
  mkdir -p "${linkdir}"

  # The previous link is the first-parent parent: chain.sh's heal list
  # is oldest-first over a contiguous history, so by this iteration the
  # parent is either a pre-existing link (verified here, with the
  # published root of trust, before anything signs against it) or one
  # this loop just emitted.
  if [[ ${SA_GENESIS} == "true" ]]; then
    echo '{"prev": null}' > "${linkdir}/prev.json"
  else
    parent=$(git rev-parse -q --verify "${rev}^")
    if ! grep -q "^${parent}	" "${SA_WORK}/manifest.tsv"; then
      [[ ${SA_SKIP_SIGN:-false} == true ]] || verify_link "${parent}"
    fi
    prev_sha=$(note_sha "${parent}")
    jq -n --arg r "${parent}" --arg d "${prev_sha}" \
      '{prev: {revision: $r, noteSha256: $d}}' > "${linkdir}/prev.json"
  fi

  parents=$(git rev-parse "${rev}^@" | jq -Rc . | jq -sc .)
  commit_time=$(git show -s --format=%cI "${rev}")
  repaired="null"
  if [[ ${rev} != "${GITHUB_SHA}" ]]; then
    repaired=$(jq -cn --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '{at: $at}')
  fi

  jq -n \
    --arg type "${prov_type}" \
    --arg repo "${GITHUB_REPOSITORY}" \
    --arg rev "${rev}" \
    --arg actor "${SA_ACTOR}" \
    --arg actor_id "${SA_ACTOR_ID}" \
    --arg ct "${commit_time}" \
    --arg canon "${SA_CANON_REF}" \
    --argjson parents "${parents}" \
    --argjson repaired "${repaired}" \
    --slurpfile claims "${SA_WORK}/claims.json" \
    --slurpfile prev "${linkdir}/prev.json" \
    '{
      _type: "https://in-toto.io/Statement/v1",
      subject: [{
        uri: ("https://github.com/" + $repo + "/commit/" + $rev),
        digest: {gitCommit: $rev},
        annotations: {sourceRefs: ["refs/heads/main"]}
      }],
      predicateType: $type,
      predicate: ({
        repository: ("https://github.com/" + $repo),
        ref: "refs/heads/main",
        parents: $parents,
        actor: {login: $actor, id: $actor_id},
        commitTime: $ct,
        rulesReadAt: $claims[0].rulesReadAt,
        controls: $claims[0].controls,
        prev: $prev[0].prev,
        canonRef: $canon
      } + (if $repaired == null then {} else {repaired: $repaired} end))
    }' > "${linkdir}/provenance.json"

  # SA_SKIP_SIGN is the dry run's seam (#236): everything is shaped,
  # nothing is signed — no OIDC token exists off-CI and no Rekor entry
  # may ever be minted for a run that cannot complete.
  if [[ ${SA_SKIP_SIGN:-false} == true ]]; then
    jq -n '{dryRun: true}' > "${linkdir}/provenance.bundle.json"
  else
    cosign sign-blob --yes \
      --bundle "${linkdir}/provenance.bundle.json" \
      "${linkdir}/provenance.json" > /dev/null

    # Self-verify with exactly the stranger's inputs: the published SAN
    # and issuer, nothing this run knows that a consumer does not.
    cosign verify-blob \
      --bundle "${linkdir}/provenance.bundle.json" \
      --certificate-identity "${SA_IDENTITY}" \
      --certificate-oidc-issuer "${SA_ISSUER}" \
      "${linkdir}/provenance.json" > /dev/null 2>&1 || {
      echo "::error::the provenance just signed does not verify against ${SA_IDENTITY} — the certificate identity is not the published contract"
      exit 1
    }
  fi

  # Level, from the policy against the VERIFIED provenance. Presence of
  # every required property means the target level; anything less
  # under-claims to level 2 — provenance, history and identity controls
  # are what this emitter existing at all demonstrates, and the missing
  # property is the audit's signal, not a reason to claim nothing. A
  # healed link additionally passes the continuity guard above.
  present=$(jq -c '[.predicate.controls[].property]' "${linkdir}/provenance.json")
  level="${target}"
  if ! jq -en --argjson req "${required}" --argjson has "${present}" \
    '$req - $has == []' > /dev/null; then
    level="SLSA_SOURCE_LEVEL_2"
    echo "::warning::${rev}: required properties missing: $(jq -rn --argjson req "${required}" --argjson has "${present}" '($req - $has) | join(", ")') — under-claiming ${level}"
  elif [[ ${repaired} != "null" ]]; then
    commit_epoch=$(git log -1 --format=%ct "${rev}")
    if [[ -z ${rules_max_epoch} ]] || ((rules_max_epoch >= commit_epoch)); then
      level="SLSA_SOURCE_LEVEL_2"
      echo "::warning::${rev}: healed link, and ruleset continuity across the gap is unprovable (a contributing ruleset changed after the commit, or no change times were readable) — under-claiming ${level}"
    fi
  fi

  verified_levels=$(jq -cn --arg l "${level}" --argjson p "${present}" '[$l] + $p')
  time_verified=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  # The predicate renders through the shared assembler (#267): one file,
  # release/vsa-predicate.jq in the canon tree, owns the VSA field set
  # for both of the org's VSA kinds — see its header.
  predicate=$(jq -cn \
    --arg verifier "${SA_IDENTITY}" \
    --arg time "${time_verified}" \
    --arg resource "git+https://github.com/${GITHUB_REPOSITORY}" \
    --arg policyUri "https://github.com/monumental-archive/.github/blob/${SA_CANON_REF}/source-policies/$(basename "${policy}")" \
    --arg policySha "${SA_CANON_REF}" \
    --arg result "PASSED" \
    --argjson levels "${verified_levels}" \
    -f "${SA_CANON_ROOT}/release/vsa-predicate.jq")
  jq -n \
    --arg type "${vsa_type}" \
    --arg repo "${GITHUB_REPOSITORY}" \
    --arg rev "${rev}" \
    --argjson predicate "${predicate}" \
    '{
      _type: "https://in-toto.io/Statement/v1",
      subject: [{
        uri: ("https://github.com/" + $repo + "/commit/" + $rev),
        digest: {gitCommit: $rev},
        annotations: {sourceRefs: ["refs/heads/main"]}
      }],
      predicateType: $type,
      predicate: $predicate
    }' > "${linkdir}/vsa.json"

  if [[ ${SA_SKIP_SIGN:-false} == true ]]; then
    jq -n '{dryRun: true}' > "${linkdir}/vsa.bundle.json"
  else
    cosign sign-blob --yes \
      --bundle "${linkdir}/vsa.bundle.json" \
      "${linkdir}/vsa.json" > /dev/null
  fi

  # The note is assembled here — the same iteration that signed its
  # contents — and added locally; append.sh owns the push.
  jq -n \
    --arg ps "$(base64 -w0 < "${linkdir}/provenance.json")" \
    --arg vs "$(base64 -w0 < "${linkdir}/vsa.json")" \
    --slurpfile pb "${linkdir}/provenance.bundle.json" \
    --slurpfile vb "${linkdir}/vsa.bundle.json" \
    '{
      version: 1,
      provenance: {statement: $ps, bundle: $pb[0]},
      vsa: {statement: $vs, bundle: $vb[0]}
    }' > "${linkdir}/note.json"
  git notes add -f -F "${linkdir}/note.json" "${rev}"
  printf '%s\t%s\n' "${rev}" "${linkdir}/note.json" >> "${SA_WORK}/manifest.tsv"

  healed=""
  [[ ${repaired} != "null" ]] && healed=" (healed)"
  echo "::notice::emitted ${level} for ${rev}${healed}"
done < "${SA_WORK}/heal.list"
