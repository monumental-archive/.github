#!/usr/bin/env bash
# Assert that a published image's metadata equals the resolved facts map.
# Org canon — see docs/release.md, "Image metadata: one map".
#
# Runs at the existing pull-back points, against the PUBLISHED bytes by
# digest — the same posture as every other proof: a claim is asserted of
# the artifact a stranger will pull, never of a local twin. Equality, not
# presence: presence lets a wrong revision through, which is worse than a
# missing one.
#
# Two of the properties checked here belong to a remote object and cannot
# be made correct by construction, which is why this is a check at all:
#   - the index media type must be OCI. Without `oci-mediatypes=true` on
#     the per-arch push exporter, `imagetools create` assembles a Docker
#     manifest list, which has no annotations field — and buildx drops the
#     annotations SILENTLY (docker/buildx#1965). The failure mode looks
#     exactly like success; this is the assertion that keeps the release
#     from shipping green and empty.
#   - the index annotations and every per-arch config's labels must EQUAL
#     the facts map.
#
# Environment contract:
#   IMAGE   fully-qualified image name
#   DIGEST  index digest (sha256:…) — the attestation subject
#   FACTS   the resolver's JSON map
set -euo pipefail

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

[[ -n ${IMAGE:-} && -n ${FACTS:-} ]] || fail "IMAGE and FACTS are required"
[[ ${DIGEST:-} =~ ^sha256:[0-9a-f]{64}$ ]] || fail "DIGEST is not a digest: '${DIGEST:-}'"

index=$(docker buildx imagetools inspect "${IMAGE}@${DIGEST}" --raw)

media_type=$(jq -r '.mediaType // empty' <<< "${index}")
[[ ${media_type} == "application/vnd.oci.image.index.v1+json" ]] \
  || fail "index media type is '${media_type}', not an OCI index — annotations were dropped (oci-mediatypes=false somewhere)"

# The index annotations must equal the map exactly. jq -S canonicalises
# key order on both sides; a diff is printed so the failure names the
# drifted key rather than just the fact of drift.
if ! jq -e -S --argjson want "${FACTS}" '.annotations == $want' <<< "${index}" > /dev/null; then
  echo "index annotations != facts map" >&2
  diff <(jq -S '.annotations' <<< "${index}") <(jq -S . <<< "${FACTS}") >&2 || true
  fail "index annotations do not equal the resolved facts"
fi

# Every per-architecture image config's labels must equal the map too —
# the labels are what `docker inspect` shows a consumer, and what the
# smoke test ran against. Attestation manifests (unknown/unknown platform)
# are BuildKit provenance, not images; they carry no config labels.
checked=0
# shellcheck disable=SC2312  # process substitution: capturing first would
# turn an empty result into one blank line, which is a worse bug than the
# masked status. The producing command is git/jq over local state.
while IFS= read -r child; do
  cd_digest=$(jq -r '.digest' <<< "${child}")
  platform=$(jq -r '"\(.platform.os)/\(.platform.architecture)"' <<< "${child}")
  labels=$(docker buildx imagetools inspect "${IMAGE}@${cd_digest}" \
    --format '{{json .Image}}' | jq -S '.config.Labels // {}')
  if ! jq -e --argjson want "${FACTS}" '. == $want' <<< "${labels}" > /dev/null; then
    echo "labels for ${platform} (${cd_digest}) != facts map" >&2
    diff <(printf '%s\n' "${labels}") <(jq -S . <<< "${FACTS}") >&2 || true
    fail "config labels for ${platform} do not equal the resolved facts"
  fi
  checked=$((checked + 1))
done < <(jq -c '.manifests[] | select(.platform != null and .platform.os != "unknown")' <<< "${index}")

((checked > 0)) || fail "the index lists no platform manifests to check"

# The values themselves re-pass the hygiene rules, independently of the
# resolver — so a resolver bug cannot self-certify.
jq -e '
  to_entries | all(
    (.value | length > 0) and
    (.value | test("[\\x00-\\x1f\\x7f]") | not)
  )' <<< "${FACTS}" > /dev/null || fail "a fact value is empty or carries control characters"

echo "asserted: OCI index, annotations and ${checked} config label set(s) equal the facts map"
