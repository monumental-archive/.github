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

# Every read goes through one shape, because a bare `gh` failure is the
# defect this replaces (#862). gh prints
#
#   gh: Resource not accessible by personal access token (HTTP 403)
#
# and nothing else — no endpoint, no repository, no permission — and
# under `set -e` the first one kills the run. Measured 2026-08-24: that
# one line was the ENTIRE output of a dead `audit:baseline-drift` leg,
# and learning which call produced it took a workflow dispatch and three
# log reads. The endpoint is in the script; it belongs in the message.
#
#   api_probe <what> <grant> <absent-marker> <endpoint> [gh args…]
#
# 0  the object exists, and its body is in `api_body`
# 1  gh's message matched <absent-marker>, so the object is legitimately
#    ABSENT. Whether that is drift is the CALLER's call — a branch with
#    no classic protection is clean, a repo with no publish environment
#    is not, and only the caller knows which it asked for.
# 2  the read did not happen. An attributed report is already printed and
#    the caller only counts it. Never a silent skip: a check that cannot
#    read is not a repository that is clean (#290 finding 7).
#
# Pass an empty marker when no failure is expected — nothing matches it,
# so every failure reports.
api_body=""
api_err="$(mktemp)"
trap 'rm -f "${api_err}"' EXIT

api_probe() {
  local what="$1" grant="$2" absent="$3" endpoint="$4"
  shift 4
  local status=0 said
  api_body="$(gh api "${endpoint}" "$@" 2> "${api_err}")" || status=$?
  if [[ ${status} -eq 0 ]]; then
    return 0
  fi
  said="$(head -1 "${api_err}")"
  if [[ -n ${absent} && ${said} == *"${absent}"* ]]; then
    return 1
  fi
  echo "drift: cannot read ${what}"
  echo "       endpoint: ${endpoint}"
  echo "       needs:    ${grant}"
  echo "       gh said:  ${said}"
  return 2
}

# Same shape for the two writes `apply` performs. A half-applied
# baseline that died on gh's one sentence is the same defect wearing a
# different verb.
api_write() {
  local what="$1" grant="$2"
  shift 2
  local status=0 said
  gh api "$@" > /dev/null 2> "${api_err}" || status=$?
  if [[ ${status} -eq 0 ]]; then
    return 0
  fi
  said="$(head -1 "${api_err}")"
  echo "failed: ${what}" >&2
  echo "        needs:   ${grant}" >&2
  echo "        gh said: ${said}" >&2
  return 1
}

# True when the repo carries publish.yml as an ENTRY workflow (tag-triggered
# caller stub), false when absent or when it is a reusable — the canon
# repo's publish.yml is `workflow_call` and must not grow an environment.
#
# 0 entry, 1 absent-or-reusable, 2 unreadable (already reported). The
# third is the point: this used to be `|| return 1`, so a 403 read as
# "no publish.yml" and silently skipped the environment check for that
# repository — a missing grant presenting as a clean repo.
publish_yml_is_entry() {
  local status=0 decoded
  # shellcheck disable=SC2310  # api_probe manages its own exit status
  api_probe "${1}'s publish.yml" "Contents: read" "Not Found" \
    "repos/${org}/${1}/contents/.github/workflows/publish.yml" \
    --jq '.content' || status=$?
  if [[ ${status} -ne 0 ]]; then
    return "${status}"
  fi
  # Consume the whole stream rather than `| grep -q`, which exits at the
  # first match and loses the upstream `printf` the SIGPIPE race — that
  # printed `write error: Broken pipe` on green runs (#862).
  decoded="$(printf '%s\n' "${api_body}" | base64 -d)"
  if [[ ${decoded} == *workflow_call* ]]; then
    return 1
  fi
  return 0
}
baseline="$(dirname "$0")/repo-baseline.json"
mode="${1:?usage: repo-baseline.sh check|apply}"

# REST, not `gh repo list`: that is GraphQL under the hood, and the
# fine-grained PAT the audit runs with supports only the REST API.
#
# This is the call that 403'd on 2026-08-24 and killed the leg saying
# nothing (#862), so it is attributed and fatal: with no population
# there is no check to run, and continuing would report a clean org.
listing_status=0
# shellcheck disable=SC2310  # api_probe manages its own exit status
api_probe "the org's repository listing" \
  "an org owner's token — a fine-grained PAT scoped to SELECTED repositories is refused here" \
  "" "orgs/${org}/repos?per_page=100" --paginate --jq '.[].name' \
  || listing_status=$?
if [[ ${listing_status} -ne 0 ]]; then
  echo "repo-baseline: nothing was checked — the population is unreadable." >&2
  exit 1
fi
repos="${api_body}"

# Refuse to claim from a blind read (#290 finding 7, the claims.sh/#240
# pattern): a token that sees an empty or partial population makes the
# loop below run zero or few times and exit 0 with no output — a clean
# check indistinguishable from no check. The count is the org's known
# population, and growing it is a reviewed edit here, exactly so a
# narrowed AUDIT_TOKEN repo selection is a red run rather than silence.
expected_repos=9
seen_repos="$(wc -w <<< "${repos}" | tr -d ' ')"
if [[ ${seen_repos} -ne ${expected_repos} ]]; then
  echo "repo-baseline: token sees ${seen_repos} repos, population is ${expected_repos} —" >&2
  echo "  an unseen repo is unchecked, not clean. Fix the token's repo" >&2
  echo "  selection, or update expected_repos for a real population change." >&2
  exit 1
fi

keys="$(jq -r 'keys[]' "${baseline}")"
drift=0

for repo in ${repos}; do
  case "${mode}" in
    apply)
      api_write "${repo}: settings baseline PATCH" "Administration: write" \
        -X PATCH "repos/${org}/${repo}" --input "${baseline}"
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
      api_write "${repo}: immutable OIDC subject claim" \
        "Administration: write" \
        "repos/${org}/${repo}/actions/oidc/customization/sub" \
        --method PUT -F use_default=true -F use_immutable_subject=true
      # The `publish` environment is a repo object — one of the three
      # irreducibly caller-side pieces of the release design — and both
      # registries' trusted-publisher configs name it. A repo that carries
      # the canonical publish.yml ENTRY workflow gets the environment; the
      # PUT is idempotent and creates it bare (no reviewers, no wait timer:
      # an environment gate mid-release would pause between publish and
      # attest, which is the one place a pause is unsafe). Entry, not
      # reusable: this repo's own publish.yml is the shared workflow_call
      # workflow, and the canon repo publishes nothing.
      #
      # An unreadable publish.yml stops the apply for this repo rather
      # than guessing: creating the environment where none belongs is a
      # settings change nobody asked for, and skipping it where one does
      # leaves a release to fail at its publish step.
      entry=0
      # shellcheck disable=SC2310  # publish_yml_is_entry manages its own exit status
      publish_yml_is_entry "${repo}" || entry=$?
      case ${entry} in
        0)
          api_write "${repo}: create the publish environment" \
            "Administration: write" \
            -X PUT "repos/${org}/${repo}/environments/publish"
          ;;
        2)
          echo "applied: ${repo} (settings and OIDC only — publish.yml unreadable)"
          continue
          ;;
        *) ;;
      esac
      echo "applied: ${repo}"
      ;;
    check)
      # Every check below reads this body, so an unreadable repo skips to
      # the next one — counted as drift, never passed over.
      repo_status=0
      # shellcheck disable=SC2310  # api_probe manages its own exit status
      api_probe "${repo}'s settings" "Administration: read" "" \
        "repos/${org}/${repo}" || repo_status=$?
      if [[ ${repo_status} -ne 0 ]]; then
        drift=1
        continue
      fi
      actual="${api_body}"
      for key in ${keys}; do
        want="$(jq -r --arg k "${key}" '.[$k]' "${baseline}")"
        have="$(jq -r --arg k "${key}" '.[$k]' <<< "${actual}")"
        if [[ ${want} != "${have}" ]]; then
          echo "drift: ${repo}.${key} = ${have} (baseline: ${want})"
          drift=1
        fi
      done
      oidc_status=0
      # shellcheck disable=SC2310  # api_probe manages its own exit status
      api_probe "${repo}'s OIDC subject customisation" \
        "Administration: read" "" \
        "repos/${org}/${repo}/actions/oidc/customization/sub" \
        --jq '.use_immutable_subject' || oidc_status=$?
      if [[ ${oidc_status} -ne 0 ]]; then
        drift=1
      elif [[ ${api_body} != "true" ]]; then
        echo "drift: ${repo} OIDC sub claim is not immutable (${api_body})"
        drift=1
      fi
      # Two reads, two absences, and they mean opposite things: no
      # publish.yml is clean, no publish environment under one is drift.
      # Neither may be inferred from a failed read (#862).
      entry=0
      # shellcheck disable=SC2310  # publish_yml_is_entry manages its own exit status
      publish_yml_is_entry "${repo}" || entry=$?
      case ${entry} in
        0)
          env_status=0
          # shellcheck disable=SC2310  # api_probe manages its own exit status
          api_probe "${repo}'s publish environment" "Administration: read" \
            "Not Found" "repos/${org}/${repo}/environments/publish" \
            || env_status=$?
          case ${env_status} in
            1)
              echo "drift: ${repo} has publish.yml but no publish environment"
              drift=1
              ;;
            2) drift=1 ;;
            *) ;;
          esac
          ;;
        2) drift=1 ;;
        *) ;;
      esac
      # Web-UI commit signoff is enforced at the org level, and once it is,
      # the repos API refuses the key outright (422 on PATCH, even to the
      # enforced value) — so it cannot live in the baseline JSON. Assert it
      # here instead: if the org setting ever regresses, this goes red.
      signoff="$(jq -r '.web_commit_signoff_required' <<< "${actual}")"
      if [[ ${signoff} != "true" ]]; then
        echo "drift: ${repo} web commit signoff is ${signoff} (org enforcement regressed?)"
        drift=1
      fi
      # CLASSIC branch protection on the default branch (#761). It is a
      # different API object from the org rulesets — `/branches/…/protection`
      # against `/rulesets` — so it survives a transfer, the import-time
      # ruleset sweep cannot see it, and until now nothing here looked.
      # Measured 2026-08-21: iiif-server arrived requiring five contexts from
      # its deleted pipeline, and its PR #119 sat BLOCKED with every ruleset
      # rule satisfied and auto-merge armed — "5 of 5 required status checks
      # are expected", not one of which could ever report.
      #
      # NOT the branch object's `protected` flag: measured 2026-08-24, that
      # is `true` on every repo here because the ORG RULESETS make it true,
      # while `/branches/main/protection` 404s. A check keyed on it would
      # fire everywhere and mean nothing.
      #
      # Reported, never deleted — `apply` does not touch this either. The
      # org rulesets are the enforcement (`docs/rulesets.md`), so classic
      # protection is always redundant with them or contradicting them, and
      # a script quietly widening or narrowing merge rules is the wrong kind
      # of helpful. The remedy is the settings page.
      branch="$(jq -r '.default_branch' <<< "${actual}")"
      prot_status=0
      # shellcheck disable=SC2310  # api_probe manages its own exit status
      api_probe "${repo}'s classic branch protection" \
        "Administration: read" "Branch not protected" \
        "repos/${org}/${repo}/branches/${branch}/protection" || prot_status=$?
      case ${prot_status} in
        0)
          # Both shapes: `contexts` is the legacy list and `checks` the
          # app-aware one, and a protection can carry either.
          contexts="$(jq -r '
            [(.required_status_checks.contexts // [])[],
             (.required_status_checks.checks // [])[].context]
            | unique | join(", ")' <<< "${api_body}")"
          echo "drift: ${repo} has CLASSIC branch protection on ${branch}"
          echo "       required contexts: ${contexts:-(none)}"
          echo "       delete it in Settings -> Branches. The org rulesets are"
          echo "       the enforcement; a context no workflow reports blocks"
          echo "       every PR with nothing able to satisfy it (#761)."
          drift=1
          ;;
        2) drift=1 ;;
        *) ;;
      esac
      ;;
    *)
      echo "unknown mode: ${mode}" >&2
      exit 2
      ;;
  esac
done

# App installations, checked once rather than per repo (#751).
#
# The org's grant model is all-repositories, for every installation and
# every org secret alike: an arriving repository is granted them by
# arriving, and no import carries a tick step. That is a decision (Carl,
# 2026-08-21), and what lets the playbook state it as "nothing to do" is
# that a regression is caught HERE — on a Monday — rather than at a
# transferred repo's first release, where it presented as
# `GET /repos/…/installation` 404 followed by `Token is not set`, with
# nothing in the run naming the cause (#757).
#
# CHECK ONLY, by ruling and not by limitation: `apply` never touches
# installations. Selection is not a settings PATCH, and a script that
# silently widens a third party's reach across every repo in the org is
# the wrong kind of convenient (#757's ruling, kept).
if [[ ${mode} == check ]]; then
  # Named, not enumerated: a token that saw an empty list would pass a
  # "nothing is selected" loop in silence (#290 finding 7). Each app is
  # asked for by slug, so a missing one is as red as a narrowed one —
  # tag-mint uninstalled and tag-mint scoped to a list fail the same
  # releases.
  expected_apps="renovate monumental-archive-tag-mint codecov"
  # No --paginate: this endpoint answers with an OBJECT, and gh's
  # pagination concatenates bare JSON documents that jq then refuses.
  # per_page covers a population of three many times over.
  #
  # Unreadable is drift, not a skip, and it reports THAT and nothing
  # else: running the loop over an empty list would print three "not
  # installed" lines nothing measured, and a check that invents findings
  # is read once and ignored.
  apps_status=0
  # shellcheck disable=SC2310  # api_probe manages its own exit status
  api_probe "the org's App installations" \
    "an org owner's token (check AUDIT_TOKEN)" "" \
    "orgs/${org}/installations?per_page=100" || apps_status=$?
  installations="${api_body}"
  if [[ ${apps_status} -ne 0 ]]; then
    drift=1
    expected_apps=""
  fi
  for app in ${expected_apps}; do
    selection="$(jq -r --arg a "${app}" \
      '.installations[] | select(.app_slug == $a) | .repository_selection' \
      <<< "${installations}")"
    case "${selection}" in
      all) ;;
      "")
        echo "drift: App ${app} is not installed on the org"
        echo "       install it across all repositories:"
        echo "       https://github.com/organizations/${org}/settings/installations"
        drift=1
        ;;
      *)
        echo "drift: App ${app} installation is '${selection}', not 'all'"
        echo "       set it back to All repositories:"
        echo "       https://github.com/organizations/${org}/settings/installations"
        echo "       A selected installation strands every repo off the list —"
        echo "       its first release dies on an unreadable 404 (#757)."
        drift=1
        ;;
    esac
  done
fi

# Container packages, checked once rather than per repo.
#
# CHECK ONLY, and not by choice: the Packages REST API offers GET, DELETE
# and restore, and no way to SET visibility — that is web UI only. So this
# reports and you click. Applying it is not possible from here, and
# pretending otherwise would be worse than saying so.
#
# Why it matters: a package pushed to GHCR is PRIVATE by default, even from
# a public repository. A private image cannot be pulled by a consumer,
# cannot be verified by a stranger, and is invisible to Scorecard — while
# everything upstream of it looks green.
if [[ ${mode} == check ]]; then
  # Its own credential, deliberately: fine-grained PATs offer no Packages
  # permission (measured 2026-08-12 — the picker returns "No items
  # available"), so the admin token cannot carry this scope, and growing
  # it a classic-PAT alternative would over-credential every other
  # check. PACKAGES_TOKEN is a classic PAT holding read:packages and
  # nothing else, living beside AUDIT_TOKEN in the audit environment.
  if ! packages="$(GH_TOKEN="${PACKAGES_TOKEN:-}" gh api \
    "orgs/${org}/packages?package_type=container&per_page=100" \
    --paginate 2> /dev/null)"; then
    # Deliberately drift rather than a silent skip. If the token is
    # absent or lacks the scope this check is doing nothing, and a check
    # that quietly does nothing is worse than no check — it manufactures
    # the impression of coverage.
    echo "drift: cannot read org packages (is PACKAGES_TOKEN set, with read:packages?)"
    drift=1
    packages='[]'
  fi
  # shellcheck disable=SC2312  # process substitution: capturing first would
  # turn an empty result into one blank line, which is a worse bug than the
  # masked status. The producing command is git/jq over local state.
  while IFS=$'\t' read -r name visibility repo_private; do
    [[ -n ${name} ]] || continue
    if [[ ${repo_private} == "false" && ${visibility} != "public" ]]; then
      echo "drift: package ${name} is ${visibility}, but its repository is public"
      echo "       fix in the package settings — no API sets this"
      drift=1
    fi
    # NOT `.repository.private // true`: jq's `//` falls through on false
    # as well as null, so `false // true` is true — which would mark every
    # PUBLIC repository private and make this check incapable of firing.
    # Found by running it against real data rather than trusting it.
  done < <(jq -r '.[] | [.name, .visibility,
      (if .repository.private == null then "true" else (.repository.private | tostring) end)]
      | @tsv' <<< "${packages}")
fi

# State what was covered on success (#310 finding 2, the audit:source-vsa
# shape): the population guard above makes a silent pass SOUND, but a
# reader of a green Monday audit should not have to reason their way to
# what was checked — the output says it, like this check's three siblings.
if [[ ${mode} == check && ${drift} -eq 0 ]]; then
  key_count="$(wc -w <<< "${keys}" | tr -d ' ')"
  emsg="repo-baseline: ${seen_repos} repos checked"
  app_count="$(wc -w <<< "${expected_apps}" | tr -d ' ')"
  emsg+=" (${key_count} baseline keys + OIDC sub + signoff + publish env"
  emsg+=" + classic branch protection each, plus org packages and"
  emsg+=" ${app_count} App installations), no drift"
  echo "${emsg}"
fi

exit "${drift}"
