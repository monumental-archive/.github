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

# Inputs, declared so the contract is explicit and a missing one fails
# by name instead of expanding to nothing (#82).
: "${SA_WORK:?SA_WORK must be set by guard-identity}"
: "${SA_CANON_ROOT:?SA_CANON_ROOT must be set by guard-identity}"
: "${SA_CANON_REF:?SA_CANON_REF must be set by guard-identity}"
: "${GITHUB_REPOSITORY:?}"

# SA_RULES_FIXTURE_DIR is the dry run's seam (#236): recorded API
# responses replace the live read, the derivation logic is the same
# bytes — the point is exercising THIS file without a runner, not a
# parallel implementation that can drift.
if [[ -n ${SA_RULES_FIXTURE_DIR:-} ]]; then
  branch_rules=$(cat "${SA_RULES_FIXTURE_DIR}/branch-rules.json")
else
  [[ -n ${GH_TOKEN:-} ]] || {
    emsg="::error::no GH_TOKEN in the environment — the claims stage must be handed the source-attest"
    emsg+=" environment read token (#240)"
    echo "${emsg}"
    exit 1
  }
  branch_rules=$(gh api "repos/${GITHUB_REPOSITORY}/rules/branches/main" --paginate) || {
    echo "::error::reading the effective branch rules for main failed — refusing to claim from a blind read"
    exit 1
  }
fi
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
hist=$(jq -c '[.[] | select(.type == "deletion" or .type == "non_fast_forward"
  or .type == "required_linear_history")]' <<< "${branch_rules}")
hist_types=$(jq '[.[].type] | unique | length' <<< "${hist}")
if [[ ${hist_types} -eq 3 ]]; then
  add ORG_SOURCE_HISTORY_PROTECTED "${hist}"
fi

# ORG_SOURCE_SIGNED: required_signatures.
sig=$(jq -c '[.[] | select(.type == "required_signatures")]' <<< "${branch_rules}")
sig_rules=$(jq length <<< "${sig}")
if [[ ${sig_rules} -ge 1 ]]; then
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
gated_rules=$(jq length <<< "${gated}")
if [[ ${gated_rules} -ge 1 ]]; then
  add ORG_SOURCE_GATED "${gated}"
  gated_live=true
fi

# ORG_SOURCE_REVIEWED_THREADS: pull request required, review threads
# resolved, squash the only merge method.
pr=$(jq -c '[.[] | select(.type == "pull_request")
  | select(.parameters.required_review_thread_resolution == true)
  | select(.parameters.allowed_merge_methods == ["squash"])]' <<< "${branch_rules}")
pr_rules=$(jq length <<< "${pr}")
if [[ ${pr_rules} -ge 1 ]]; then
  add ORG_SOURCE_REVIEWED_THREADS "${pr}"
fi

# Tag properties come from ruleset details: effective per-ref rules only
# exist for branches, so each active tag ruleset is fetched and matched
# by content — conditions, rules and bypass actors together.
#
# The read is shape-identical to the branch read above: every API call
# is a plain command substitution with its own named error, the results
# land in one JSON array variable, and the derivation is pure jq over
# that array. There is no streaming and no process substitution — the
# structure that let a failed read masquerade as an absent control
# (#240) — so a read that fails cannot reach the claim logic at all.
# Failure, blindness and lapse are three different outcomes: an API
# error or an unreadable detail fails the run; an EMPTY list is a blind
# read and also fails (the org tag rulesets exist by the frozen table,
# so a token that sees none of them is proving its own incapability,
# exactly like the branch guard); only a ruleset that is visible,
# readable and does not match its content yields an absent property.
if [[ -n ${SA_RULES_FIXTURE_DIR:-} ]]; then
  tag_rulesets=$(jq -c '[.[] | select(.target == "tag" and .enforcement == "active")]' \
    "${SA_RULES_FIXTURE_DIR}/tag-rulesets.json")
  branch_ruleset_details=$(cat "${SA_RULES_FIXTURE_DIR}/branch-ruleset-details.json")
else
  tag_ids=$(gh api "repos/${GITHUB_REPOSITORY}/rulesets?includes_parents=true" --paginate \
    --jq '.[] | select(.target == "tag" and .enforcement == "active") | .id') || {
    echo "::error::listing rulesets for ${GITHUB_REPOSITORY} failed — refusing to claim from a blind read (#240)"
    exit 1
  }
  tag_rulesets="[]"
  for id in ${tag_ids}; do
    detail=$(gh api "repos/${GITHUB_REPOSITORY}/rulesets/${id}") || {
      emsg="::error::ruleset ${id} is listed but its details are unreadable — the token cannot see org-level"
      emsg+=" ruleset content; the claims job needs the source-attest environment read token (#240)"
      echo "${emsg}"
      exit 1
    }
    tag_rulesets=$(jq -c --argjson d "${detail}" '. + [$d]' <<< "${tag_rulesets}")
  done
  # The rulesets behind the branch rules, fetched for their updated_at:
  # the continuity horizon healed links are level-guarded against
  # (#265). Same read discipline as above — an unreadable detail fails
  # the run, because a healed link guarded against a partial horizon
  # would over-claim.
  branch_ids=$(jq -r '[.[].ruleset_id] | unique | .[]' <<< "${branch_rules}")
  branch_ruleset_details="[]"
  for id in ${branch_ids}; do
    detail=$(gh api "repos/${GITHUB_REPOSITORY}/rulesets/${id}") || {
      emsg="::error::branch ruleset ${id} is listed but its details are unreadable — the token cannot see"
      emsg+=" org-level ruleset content; the claims job needs the source-attest environment read token (#240)"
      echo "${emsg}"
      exit 1
    }
    branch_ruleset_details=$(jq -c --argjson d "${detail}" '. + [$d]' <<< "${branch_ruleset_details}")
  done
fi
tag_ruleset_count=$(jq length <<< "${tag_rulesets}")
[[ ${tag_ruleset_count} -ge 1 ]] || {
  emsg="::error::no active tag rulesets visible — the org tag rulesets exist (docs/source-track.md), so this"
  emsg+=" token cannot see them; refusing to claim from a blind read (#240)"
  echo "${emsg}"
  exit 1
}

# ORG_SOURCE_TAG_IMMUTABLE: update, move and deletion blocked, all
# tags, nobody bypasses.
imm=$(jq -c '[.[] | select((.conditions.ref_name.include == ["~ALL"])
  and (.conditions.ref_name.exclude == [])
  and ([.rules[].type] | contains(["update", "deletion", "non_fast_forward"]))
  and (.bypass_actors == []))
  | {id, rules: [.rules[].type], conditions}]' <<< "${tag_rulesets}")
imm_rules=$(jq length <<< "${imm}")
if [[ ${imm_rules} -ge 1 ]]; then
  add ORG_SOURCE_TAG_IMMUTABLE "${imm}"
fi

# ORG_SOURCE_RELEASE_TAG_MINTED: v* creation blocked for everyone
# except exactly the minting App (integration 4534781).
minted=$(jq -c '[.[] | select((.conditions.ref_name.include == ["refs/tags/v*"])
  and ([.rules[].type] | contains(["creation"]))
  and (.bypass_actors == [{actor_id: 4534781, actor_type: "Integration", bypass_mode: "always"}]))
  | {id, rules: [.rules[].type], conditions, bypass_actors}]' <<< "${tag_rulesets}")
minted_rules=$(jq length <<< "${minted}")
if [[ ${minted_rules} -ge 1 ]]; then
  add ORG_SOURCE_RELEASE_TAG_MINTED "${minted}"
fi

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

# The continuity horizon (#265): when every contributing ruleset last
# changed, normalised to epochs here so the attest stage compares plain
# integers. A healed link may claim the target level only when this
# whole set predates its commit — the rules provably have not changed
# since before the revision landed. updated_at arrives with arbitrary
# UTC offsets; jq's own date built-ins are UTC-only, so the offset is
# applied by hand.
updated_epochs=$(jq -cn --argjson b "${branch_ruleset_details}" --argjson t "${tag_rulesets}" '
  def iso_epoch:
    capture("(?<d>[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(\\.[0-9]+)?(?<tz>Z|[+-][0-9]{2}:[0-9]{2})")
    | ((.d + "Z") | fromdateiso8601)
      - (if .tz == "Z" then 0
         else ((.tz[0:1] + "1") | tonumber) * ((.tz[1:3] | tonumber) * 3600 + (.tz[4:6] | tonumber) * 60)
         end);
  [($b + $t)[] | (.updated_at // .created_at // empty) | iso_epoch]')

read_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
jq -n --argjson c "${claims}" --arg t "${read_at}" --argjson u "${updated_epochs}" \
  '{rulesReadAt: $t, rulesetsUpdatedAt: $u, controls: $c}' \
  > "${SA_WORK}/claims.json"
properties=$(jq -r '[.controls[].property] | join(", ")' "${SA_WORK}/claims.json")
echo "::notice::claims: ${properties}"
