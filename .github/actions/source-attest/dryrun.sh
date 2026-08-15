#!/usr/bin/env bash
# Off-CI dry run of the claims stage (#236), exercised by the canon's
# own gate (`lint:source-attest`).
#
# Since the emit cutover (stele#24) the emitter's execution layer is
# `stele emit chain`, proven by its own table tests and shadow runs —
# claims.sh is the last bash in this action, and this dry run now
# covers exactly it: the REAL script against recorded rule fixtures,
# no network, deterministic. The read guards are code too (#240): an
# empty or unreadable tag read must refuse loudly, and a content lapse
# must drop exactly its own properties. Both degraded fixture sets are
# DERIVED here from the one canonical testdata — jq transforms, never
# second copies that drift.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
canon_root="$(cd "${here}/../../.." && pwd)"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

export SA_WORK="${tmp}/work"
mkdir -p "${SA_WORK}"
export SA_CANON_ROOT="${canon_root}"
SA_CANON_REF="$(git -C "${canon_root}" rev-parse HEAD)"
export SA_CANON_REF
export SA_RULES_FIXTURE_DIR="${here}/testdata"
export GITHUB_REPOSITORY="monumental-archive/.github"

fail() {
  echo "lint:source-attest: ${1}" >&2
  exit 1
}

blind="${tmp}/fixtures-blind"
mkdir -p "${blind}"
cp "${here}/testdata/branch-rules.json" "${here}/testdata/branch-ruleset-details.json" "${blind}/"
jq '[]' "${here}/testdata/tag-rulesets.json" > "${blind}/tag-rulesets.json"
guard_work="${tmp}/work-blind"
mkdir -p "${guard_work}"
if out=$(SA_WORK="${guard_work}" SA_RULES_FIXTURE_DIR="${blind}" "${here}/claims.sh" 2>&1); then
  fail "claims.sh accepted a tag read that saw no rulesets — the blind-read guard is gone (#240)"
fi
grep -q "refusing to claim from a blind read" <<< "${out}" \
  || fail "claims.sh refused the blind tag read but without its named error (#240)"

lapsed="${tmp}/fixtures-lapsed"
mkdir -p "${lapsed}"
cp "${here}/testdata/branch-rules.json" "${here}/testdata/branch-ruleset-details.json" "${lapsed}/"
# The lapse: someone grants a bypass on the all-tags ruleset. The
# ruleset is still visible and readable — only its content no longer
# matches, so exactly ORG_SOURCE_TAG_IMMUTABLE must drop.
jq '(.[] | select(.conditions.ref_name.include == ["~ALL"]) | .bypass_actors)
  += [{actor_id: 1, actor_type: "OrganizationAdmin", bypass_mode: "always"}]' \
  "${here}/testdata/tag-rulesets.json" > "${lapsed}/tag-rulesets.json"
lapse_work="${tmp}/work-lapsed"
mkdir -p "${lapse_work}"
SA_WORK="${lapse_work}" SA_RULES_FIXTURE_DIR="${lapsed}" "${here}/claims.sh" \
  || fail "claims.sh refused a readable-but-lapsed ruleset — a lapse must under-claim, not fail (#240)"
jq -e '[.controls[].property] | (index("ORG_SOURCE_TAG_IMMUTABLE") == null)
  and (index("ORG_SOURCE_RELEASE_TAG_MINTED") != null)' \
  "${lapse_work}/claims.json" > /dev/null \
  || fail "a lapsed tag ruleset did not drop exactly its own property (#240)"

# The full fixture set derives the complete claim set, in the exact
# shape stele's strict decoder accepts: the three keys and no others,
# every control carrying a property and evidence.
"${here}/claims.sh"
jq -e '(keys | sort == ["controls", "rulesReadAt", "rulesetsUpdatedAt"])
  and (.rulesReadAt | type == "string")
  and (.rulesetsUpdatedAt | type == "array" and all(.[]; type == "number"))
  and (.controls | type == "array" and length > 0)
  and all(.controls[]; (.property | type == "string") and has("evidence"))' \
  "${SA_WORK}/claims.json" > /dev/null \
  || fail "claims.json is not the shape stele's decoder accepts"

echo "lint:source-attest: ok — read guards refuse blindness, a lapse under-claims, the claim shape holds"
