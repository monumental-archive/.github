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

caller="${1:?usage: roster-reconcile.sh <caller> <roster.json> < listing}"
roster="${2:?usage: roster-reconcile.sh <caller> <roster.json> < listing}"

[[ -f ${roster} ]] || {
  echo "${caller}: no roster at ${roster} — the population is named there, and" >&2
  echo "  without it an unseen repository cannot be told from a clean one." >&2
  exit 2
}
named="$(jq -r '.population.repositories[].repo' "${roster}")" || {
  echo "${caller}: ${roster} has no readable .population.repositories[].repo" >&2
  exit 2
}
[[ -n ${named} ]] || {
  echo "${caller}: ${roster} names no repositories — an empty roster" >&2
  echo "  reconciles against nothing." >&2
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
  echo "${caller}: ${repo}: in the org, not in the roster — add it to the roster or remove the repository" >&2
  status=1
done <<< "${unlisted}"
while IFS= read -r repo; do
  [[ -n ${repo} ]] || continue
  echo "${caller}: ${repo}: in the roster, not visible to this token — an unseen repository is unchecked, not clean" >&2
  status=1
done <<< "${unseen}"
exit "${status}"
