#!/usr/bin/env bash
# Reconcile the org listing a token returned against the roster, by NAME.
#
#   gh api orgs/<org>/repos --paginate --jq '.[].name' \
#     | roster-reconcile.sh <caller> <roster.json>
#
# The one comparison behind two guards: settings/repo-baseline.sh and
# audit:org-pins (#680). Both walk every repository the token lists, and
# a token that sees fewer repositories than exist makes that walk exit 0
# with nothing checked (#290 finding 7) — so each guard must know the
# population before it trusts the listing. It used to know it as a
# hand-written count beside the roster that already names every member,
# and a mismatch said "edit the constant": the reflex that hides an
# unseen repository, and two offsetting errors (one missing, one extra)
# passed it. The names are compared instead, and each difference is red
# with its name and the action it owes. No message here tells the reader
# to edit a number; there is no number.
#
# The roster is `.population.repositories[].repo` in
# slsa/assert-policy.json — the canon's file. An import edits it and
# nothing else.
#
# Exit 0 when the sets are equal; 1 with every difference named on
# stderr; 2 when the roster cannot be read, which is a refusal and never
# a fallback to trusting the listing.
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here

caller="${1:?usage: roster-reconcile.sh <caller> <roster.json> < listing}"
roster="${2:?usage: roster-reconcile.sh <caller> <roster.json> < listing}"
# The task this script reports for, where mise named none (#1934).
: "${MISE_TASK_NAME:=${caller}}"

[[ -f ${roster} ]] || {
  org_finding "" "no roster at ${roster} — the population is named there, and" \
    "without it an unseen repository cannot be told from a clean one."
  exit 2
}
named="$(jq -r '.population.repositories[].repo' "${roster}")" || {
  org_finding "" "${roster} has no readable .population.repositories[].repo"
  exit 2
}
[[ -n ${named} ]] || {
  org_finding "" "${roster} names no repositories — an empty roster" \
    "reconciles against nothing."
  exit 2
}

roster_sorted="$(sort -u <<< "${named}")"
# `|| true`: grep exits 1 on an empty listing, which is a case to report
# below, not a reason to die mute under pipefail.
listing_sorted="$(grep -v '^[[:space:]]*$' | sort -u || true)"

unlisted="$(comm -23 <(printf '%s\n' "${listing_sorted}") <(printf '%s\n' "${roster_sorted}") | grep -v '^$' || true)"
unseen="$(comm -13 <(printf '%s\n' "${listing_sorted}") <(printf '%s\n' "${roster_sorted}") | grep -v '^$' || true)"

status=0
while IFS= read -r repo; do
  [[ -n ${repo} ]] || continue
  org_finding "${repo}" "in the org, not in the roster — add it to the roster or remove the repository"
  status=1
done <<< "${unlisted}"
while IFS= read -r repo; do
  [[ -n ${repo} ]] || continue
  org_finding "${repo}" "in the roster, not visible to this token — an unseen repository is unchecked, not clean"
  status=1
done <<< "${unseen}"
exit "${status}"
