#!/usr/bin/env bash
# Read the enforcement state and derive the ORG_SOURCE_ claims.
#
# Ground truth is GitHub's rules API at this moment, never configuration
# intent (docs/source-assessment.md, control effectiveness). Every
# property is matched by RULE CONTENT — the parameters that make the
# control what it is — not by ruleset name: a renamed ruleset still
# enforces, a gutted one still carries the name. The mapping implements
# the frozen table in docs/source-track.md verbatim; a property whose
# rule is not live is simply absent, which is how a lapse under-claims
# and resets its own clock by construction.
#
# A partial read is a failed read: claiming from an API response that
# errored or paginated away would assert controls nobody checked.
set -euo pipefail

branch_rules=$(gh api "repos/${GITHUB_REPOSITORY}/rules/branches/main" --paginate)
[[ -n ${branch_rules} && ${branch_rules} != "[]" ]] || {
  echo "::error::rules API returned no effective rules for main — refusing to claim from a blind read"
  exit 1
}

claims="[]"
add() { # property, evidence-json
  claims=$(jq -c --arg p "${1}" --argjson e "${2}" '. + [{property: $p, evidence: $e}]' <<< "${claims}")
}

# ORG_SOURCE_HISTORY_PROTECTED: deletion + non-fast-forward blocked,
# linear history required.
hist=$(jq -c '[.[] | select(.type == "deletion" or .type == "non_fast_forward" or .type == "required_linear_history")]' <<< "${branch_rules}")
if [[ $(jq '[.[].type] | unique | length' <<< "${hist}") -eq 3 ]]; then
  add ORG_SOURCE_HISTORY_PROTECTED "${hist}"
fi

# ORG_SOURCE_SIGNED: required_signatures.
sig=$(jq -c '[.[] | select(.type == "required_signatures")]' <<< "${branch_rules}")
if [[ $(jq length <<< "${sig}") -ge 1 ]]; then
  add ORG_SOURCE_SIGNED "${sig}"
fi

# ORG_SOURCE_GATED: required check `ci / ci` bound to the GitHub Actions
# app (integration 15368), strict policy — the binding is the control
# (docs/source-track.md): an unbound context is satisfiable by anyone.
gated=$(jq -c '[.[] | select(.type == "required_status_checks")
  | select(.parameters.strict_required_status_checks_policy == true)
  | select([.parameters.required_status_checks[]?
      | select(.context == "ci / ci" and .integration_id == 15368)] | length > 0)]' <<< "${branch_rules}")
gated_live=false
if [[ $(jq length <<< "${gated}") -ge 1 ]]; then
  add ORG_SOURCE_GATED "${gated}"
  gated_live=true
fi

# ORG_SOURCE_REVIEWED_THREADS: pull request required, review threads
# resolved, squash the only merge method.
pr=$(jq -c '[.[] | select(.type == "pull_request")
  | select(.parameters.required_review_thread_resolution == true)
  | select(.parameters.allowed_merge_methods == ["squash"])]' <<< "${branch_rules}")
if [[ $(jq length <<< "${pr}") -ge 1 ]]; then
  add ORG_SOURCE_REVIEWED_THREADS "${pr}"
fi

# Tag properties come from ruleset details: effective per-ref rules only
# exist for branches, so each active tag ruleset is fetched and matched
# by content — conditions, rules and bypass actors together.
tag_ids=$(gh api "repos/${GITHUB_REPOSITORY}/rulesets?includes_parents=true" --paginate \
  --jq '.[] | select(.target == "tag" and .enforcement == "active") | .id')
while read -r id; do
  [[ -n ${id} ]] || continue
  rs=$(gh api "repos/${GITHUB_REPOSITORY}/rulesets/${id}")
  # ORG_SOURCE_TAG_IMMUTABLE: update, move and deletion blocked, all
  # tags, nobody bypasses.
  if jq -e '(.conditions.ref_name.include == ["~ALL"])
      and (.conditions.ref_name.exclude == [])
      and ([.rules[].type] | contains(["update", "deletion", "non_fast_forward"]))
      and (.bypass_actors == [])' <<< "${rs}" > /dev/null; then
    add ORG_SOURCE_TAG_IMMUTABLE "$(jq -c '{id, rules: [.rules[].type], conditions}' <<< "${rs}")"
  fi
  # ORG_SOURCE_RELEASE_TAG_MINTED: v* creation blocked for everyone
  # except exactly the minting App (integration 4534781).
  if jq -e '(.conditions.ref_name.include == ["refs/tags/v*"])
      and ([.rules[].type] | contains(["creation"]))
      and (.bypass_actors == [{actor_id: 4534781, actor_type: "Integration", bypass_mode: "always"}])' <<< "${rs}" > /dev/null; then
    add ORG_SOURCE_RELEASE_TAG_MINTED "$(jq -c '{id, rules: [.rules[].type], conditions, bypass_actors}' <<< "${rs}")"
  fi
done <<< "${tag_ids}"

# Belt-carried properties: enforced INSIDE the gated check, so they are
# claimable exactly when the gate is live and the canon tree this run
# resolved — the org's own reviewed code, never the attested repo's
# working tree — defines the lint.
if [[ ${gated_live} == true ]]; then
  belt_evidence=$(jq -cn --arg ref "${SA_CANON_REF}" '{via: "ci / ci", canon: $ref}')
  grep -q '^\[tasks."lint:dco"\]' "${SA_CANON_ROOT}/mise/config.toml" \
    && add ORG_SOURCE_DCO "${belt_evidence}"
  grep -q '^\[tasks."lint:capability-boundary"\]' "${SA_CANON_ROOT}/mise/config.toml" \
    && add ORG_SOURCE_CAPABILITY_BOUNDARY "${belt_evidence}"
fi

read_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
jq -n --argjson c "${claims}" --arg t "${read_at}" '{rulesReadAt: $t, controls: $c}' \
  > "${SA_WORK}/claims.json"
echo "::notice::claims: $(jq -r '[.controls[].property] | join(", ")' "${SA_WORK}/claims.json")"
