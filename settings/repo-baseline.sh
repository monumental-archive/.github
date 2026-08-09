#!/usr/bin/env bash
# Apply or drift-check the repo settings baseline across every org repo.
#
#   settings/repo-baseline.sh check    # report drift, exit 1 if any
#   settings/repo-baseline.sh apply    # PATCH every repo to the baseline
#
# These are the per-repo toggles GitHub offers no org-level lever for.
# Run `check` any time; run `apply` after creating or transferring a repo.
set -euo pipefail

org="monumental-archive"
baseline="$(dirname "$0")/repo-baseline.json"
mode="${1:?usage: repo-baseline.sh check|apply}"

# REST, not `gh repo list`: that is GraphQL under the hood, and the
# fine-grained PAT the audit runs with supports only the REST API.
repos="$(gh api "orgs/${org}/repos?per_page=100" --paginate --jq '.[].name')"
keys="$(jq -r 'keys[]' "${baseline}")"
drift=0

for repo in ${repos}; do
  case "${mode}" in
    apply)
      gh api -X PATCH "repos/${org}/${repo}" --input "${baseline}" > /dev/null
      # Immutable OIDC subject claims: the sub claim carries the numeric
      # repository and owner ids alongside the names, so a token still
      # identifies its origin after a rename or transfer. GitHub says new
      # repos get this from 2026-07-15 and that renames adopt it too, but a
      # rename measured on 2026-08-09 left the flag false — so set it
      # explicitly rather than trusting it to happen.
      #
      # Safe to change at any point: the format appears in Fulcio OID .24
      # (Token Subject), while --signer-workflow and --signer-digest pin
      # OIDs .9 and .10, which are unaffected.
      gh api "repos/${org}/${repo}/actions/oidc/customization/sub" \
        --method PUT -F use_default=true -F use_immutable_subject=true \
        > /dev/null
      echo "applied: ${repo}"
      ;;
    check)
      actual="$(gh api "repos/${org}/${repo}")"
      for key in ${keys}; do
        want="$(jq -r --arg k "${key}" '.[$k]' "${baseline}")"
        have="$(jq -r --arg k "${key}" '.[$k]' <<< "${actual}")"
        if [[ ${want} != "${have}" ]]; then
          echo "drift: ${repo}.${key} = ${have} (baseline: ${want})"
          drift=1
        fi
      done
      immutable="$(gh api "repos/${org}/${repo}/actions/oidc/customization/sub" \
        --jq '.use_immutable_subject')"
      if [[ ${immutable} != "true" ]]; then
        echo "drift: ${repo} OIDC sub claim is not immutable (${immutable})"
        drift=1
      fi
      ;;
    *)
      echo "unknown mode: ${mode}" >&2
      exit 2
      ;;
  esac
done

exit "${drift}"
