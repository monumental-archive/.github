#!/usr/bin/env bash
# Apply or drift-check the ruleset canon across every org repo.
#
#   rulesets/apply.sh check    # report drift, exit 1 if any
#   rulesets/apply.sh apply    # create or update every ruleset on every repo
#
# Org rulesets need the Team plan. Until then the identical JSON applies at
# REPOSITORY level, which works on Free for public repositories — so this is
# a uniformity gap, never a capability gap, and nothing here waits on a plan
# upgrade. When Team lands, apply these once at org level and delete this
# script; the JSON does not change.
#
# Idempotent: creates a ruleset the repo lacks, PUTs the canon over one it
# already has. Run `apply` after creating or transferring a repo.
set -euo pipefail

org="monumental-archive"
dir="$(dirname "$0")"
mode="${1:?usage: apply.sh check|apply}"

# Every ruleset in this directory is canon for every repo. Enforcement is a
# property of the file, not of this script: org-release-tag ships disabled
# until a repo's release pipeline exists, because a creation lock without a
# minting pipeline is a lockout.
rulesets=("${dir}"/*.json)

# REST, not `gh repo list`: that is GraphQL under the hood, and the
# fine-grained PAT the audit runs with supports only the REST API.
repos="$(gh api "orgs/${org}/repos?per_page=100" --paginate --jq '.[].name')"
drift=0

for repo in ${repos}; do
  existing="$(gh api "repos/${org}/${repo}/rulesets" --jq '[.[] | {name, id}]')"

  for file in "${rulesets[@]}"; do
    name="$(jq -r '.name' "${file}")"
    id="$(jq -r --arg n "${name}" '.[] | select(.name == $n) | .id // empty' <<< "${existing}")"

    # repository_name conditions are meaningful only in an org-level
    # ruleset; the repo-level API rejects them.
    payload="$(jq 'del(.conditions.repository_name)' "${file}")"

    # A ruleset lands with its enabler, never before it. The tag rules need
    # the minting App (org-wide, so always satisfied); the branch ruleset
    # needs the shared gate, because required_status_checks naming a context
    # the repo never reports makes every pull request permanently
    # unmergeable — a lockout indistinguishable from a hung check.
    contexts="$(jq -r '[.rules[]? | select(.type == "required_status_checks")
                        | .parameters.required_status_checks[]?.context] | .[]' <<< "${payload}")"
    blocked=""
    if [[ -n ${contexts} ]]; then
      reported="$(gh api "repos/${org}/${repo}/commits/HEAD/check-runs" \
        --jq '[.check_runs[].name] | join("\n")' 2> /dev/null || true)"
      # Read line by line: a check context contains spaces ("ci / ci"), so
      # an unquoted word-split loop tests for "ci", "/" and "ci" separately
      # and reports a conforming repo as blocked.
      while IFS= read -r ctx; do
        [[ -n ${ctx} ]] || continue
        grep -qxF "${ctx}" <<< "${reported}" || blocked="${blocked} [${ctx}]"
      done <<< "${contexts}"
    fi
    if [[ -n ${blocked} ]]; then
      # NOT an exemption. Every repo adopts the shared gate; a repo that
      # does not report the canonical context is non-conforming, and the fix
      # is to add the gate stub there — never to soften this ruleset. The
      # interlock exists only so the rule is not applied BEFORE the check
      # exists, which would brick every pull request in that repo.
      echo "BLOCKED: ${repo}/${name} — repo does not report:${blocked}"
      echo "  conformance gap: add the shared gate stub"
      echo "  (workflow-templates/ci.yml), then re-run. Applying it now"
      echo "  would make every pull request in ${repo} unmergeable."
      drift=1
      continue
    fi

    case "${mode}" in
      apply)
        if [[ -n ${id} ]]; then
          gh api "repos/${org}/${repo}/rulesets/${id}" --method PUT \
            --input - <<< "${payload}" > /dev/null
          echo "updated: ${repo}/${name}"
        else
          gh api "repos/${org}/${repo}/rulesets" --method POST \
            --input - <<< "${payload}" > /dev/null
          echo "created: ${repo}/${name}"
        fi
        ;;
      check)
        if [[ -z ${id} ]]; then
          echo "drift: ${repo} is missing ruleset ${name}"
          drift=1
          continue
        fi
        actual="$(gh api "repos/${org}/${repo}/rulesets/${id}")"

        # The question is whether the repo SATISFIES the canon, not whether
        # it byte-matches it. GitHub returns rules in its own order and
        # fills in parameter defaults the canon never declares
        # (dismissal_restriction, required_reviewers, ...), so a naive
        # comparison reports drift on a perfectly conforming repo. Sort by
        # rule type, and compare only the parameter keys the canon asserts.
        norm='def canonise($c): [
                $c[] as $want
                | (.[] | select(.type == $want.type)) as $have
                | { type: $want.type,
                    parameters: ( (($want.parameters // {}) | keys) as $ks
                                  | ($have.parameters // {})
                                  | with_entries(select(.key as $k | $ks | index($k))) ) }
              ] | sort_by(.type);'
        want_rules="$(jq -cS '[.rules[] | {type, parameters: (.parameters // {})}] | sort_by(.type)' <<< "${payload}")"
        have_rules="$(jq -cS --argjson c "$(jq -c '.rules' <<< "${payload}")" \
          "${norm} .rules | canonise(\$c)" <<< "${actual}")"
        if [[ ${want_rules} != "${have_rules}" ]]; then
          echo "drift: ${repo}/${name}.rules"
          echo "  want: ${want_rules}"
          echo "  have: ${have_rules}"
          drift=1
        fi

        for field in enforcement conditions; do
          want="$(jq -cS --arg f "${field}" '.[$f] // null' <<< "${payload}")"
          have="$(jq -cS --arg f "${field}" '.[$f] // null' <<< "${actual}")"
          if [[ ${want} != "${have}" ]]; then
            echo "drift: ${repo}/${name}.${field}"
            echo "  want: ${want}"
            echo "  have: ${have}"
            drift=1
          fi
        done

        # Bypass actors are a set, not a list: order carries no meaning, and
        # an unexpected EXTRA actor is the failure that matters most.
        want_bypass="$(jq -cS '[.bypass_actors[]? | {actor_id, actor_type, bypass_mode}] | sort_by(.actor_id)' <<< "${payload}")"
        have_bypass="$(jq -cS '[.bypass_actors[]? | {actor_id, actor_type, bypass_mode}] | sort_by(.actor_id)' <<< "${actual}")"
        if [[ ${want_bypass} != "${have_bypass}" ]]; then
          echo "drift: ${repo}/${name}.bypass_actors"
          echo "  want: ${want_bypass}"
          echo "  have: ${have_bypass}"
          drift=1
        fi
        ;;
      *)
        echo "unknown mode: ${mode}" >&2
        exit 2
        ;;
    esac
  done
done

exit "${drift}"
