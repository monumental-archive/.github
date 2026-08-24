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

# True when the repo carries publish.yml as an ENTRY workflow (tag-triggered
# caller stub), false when absent or when it is a reusable — the canon
# repo's publish.yml is `workflow_call` and must not grow an environment.
# shellcheck disable=SC2310  # deliberate predicate: it manages its own exit
# status (`|| return 1`, then the grep result), so the set -e suspension that
# calling it in an `if` causes has nothing to suspend.
publish_yml_is_entry() {
  local content
  content="$(gh api "repos/${org}/${1}/contents/.github/workflows/publish.yml" \
    --jq '.content' 2> /dev/null)" || return 1
  ! printf '%s\n' "${content}" | base64 -d | grep -q 'workflow_call'
}
baseline="$(dirname "$0")/repo-baseline.json"
mode="${1:?usage: repo-baseline.sh check|apply}"

# REST, not `gh repo list`: that is GraphQL under the hood, and the
# fine-grained PAT the audit runs with supports only the REST API.
repos="$(gh api "orgs/${org}/repos?per_page=100" --paginate --jq '.[].name')"

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

# Scratch for one call's stderr. `/branches/…/protection` answers 404
# "Branch not protected" when there is none, which is the CLEAN result —
# so that one call needs its message, not just its exit status, to tell
# a clean answer from a failed read.
protection_err="$(mktemp)"
trap 'rm -f "${protection_err}"' EXIT

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
      # The `publish` environment is a repo object — one of the three
      # irreducibly caller-side pieces of the release design — and both
      # registries' trusted-publisher configs name it. A repo that carries
      # the canonical publish.yml ENTRY workflow gets the environment; the
      # PUT is idempotent and creates it bare (no reviewers, no wait timer:
      # an environment gate mid-release would pause between publish and
      # attest, which is the one place a pause is unsafe). Entry, not
      # reusable: this repo's own publish.yml is the shared workflow_call
      # workflow, and the canon repo publishes nothing.
      # shellcheck disable=SC2310  # publish_yml_is_entry manages its own exit status
      if publish_yml_is_entry "${repo}"; then
        gh api -X PUT "repos/${org}/${repo}/environments/publish" > /dev/null
      fi
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
      # shellcheck disable=SC2310  # publish_yml_is_entry manages its own exit status
      if publish_yml_is_entry "${repo}" \
        && ! gh api "repos/${org}/${repo}/environments/publish" \
          > /dev/null 2>&1; then
        echo "drift: ${repo} has publish.yml but no publish environment"
        drift=1
      fi
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
      if classic="$(gh api \
        "repos/${org}/${repo}/branches/${branch}/protection" \
        2> "${protection_err}")"; then
        # Both shapes: `contexts` is the legacy list and `checks` the
        # app-aware one, and a protection can carry either.
        contexts="$(jq -r '
          [(.required_status_checks.contexts // [])[],
           (.required_status_checks.checks // [])[].context]
          | unique | join(", ")' <<< "${classic}")"
        echo "drift: ${repo} has CLASSIC branch protection on ${branch}"
        echo "       required contexts: ${contexts:-(none)}"
        echo "       delete it in Settings -> Branches. The org rulesets are"
        echo "       the enforcement; a context no workflow reports blocks"
        echo "       every PR with nothing able to satisfy it (#761)."
        drift=1
      elif ! grep -q "Branch not protected" "${protection_err}"; then
        # 404 is the clean answer; anything else means this check did not
        # run, which is drift and not silence.
        reason="$(head -1 "${protection_err}")"
        echo "drift: cannot read ${repo}'s classic branch protection"
        echo "       ${reason}"
        drift=1
      fi
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
  if ! installations="$(gh api \
    "orgs/${org}/installations?per_page=100" 2> /dev/null)"; then
    # Unreadable is drift, not a skip — the packages precedent below.
    # The endpoint wants an organisation owner's token; if AUDIT_TOKEN
    # ever stops qualifying, this check is doing nothing, and a check
    # that quietly does nothing manufactures the impression of coverage.
    # Report that and nothing else: running the loop over an empty list
    # would print three "not installed" lines that are not known to be
    # true, and a check that invents findings is read once and ignored.
    echo "drift: cannot read org App installations"
    echo "       (the endpoint wants an org owner's token — check AUDIT_TOKEN)"
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
