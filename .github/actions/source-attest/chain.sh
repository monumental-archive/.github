#!/usr/bin/env bash
# Fetch the history and the chain, and decide what needs a link.
#
# Since #265 the emitter is self-healing: every push walks first-parent
# from the pushed revision down to the genesis link and collects EVERY
# revision without a link — not just the pushed one. A lapse (the mise
# download died twice on 2026-08-12 alone) leaves a hole; the next push
# emits the missing links, oldest first, each marked as repaired in its
# provenance and level-guarded by ruleset continuity (emit.sh). A hole
# is therefore a transient state the system exits on its own; the
# Monday audit (audit:source-vsa) is the alarm while it is in it.
#
# Because every revision between genesis and the tip is either linked
# or in the heal list, each heal target's previous link is simply its
# first-parent parent by the time emit.sh reaches it. Verification of
# pre-existing links happens in emit.sh, immediately before anything
# signs against them.
#
# Genesis is explicit (`genesis: "true"`, a dispatch), and refused the
# moment any link exists on the walked history — a gap is debt, never a
# reason to re-found the chain.
#
# Everything here is git plumbing against a scratch clone — no checkout
# of a working tree, nothing from the attested repository is executed.
set -euo pipefail

# Inputs, declared so the contract is explicit and a missing one fails
# by name instead of expanding to nothing (#82).
: "${SA_WORK:?SA_WORK must be set by guard-identity}"
: "${SA_GENESIS:?SA_GENESIS must be set (true|false)}"
: "${GITHUB_SHA:?}"
# shellcheck source=lib.sh
# shellcheck source-path=SCRIPTDIR
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

scratch="${SA_WORK}/repo"
git init -q "${scratch}"
cd "${scratch}"
# The note's committer identity is part of the storage contract, not
# incidental config (#236): the author of every chain-link note lands in
# a world-readable ledger permanently, so it is declared here as a
# constant and documented in docs/source-assessment.md (storage). A
# runner has no global git config — without this, `git notes add` dies
# with `fatal: empty ident name`.
git config user.name "source-attest"
git config user.email "source-attest@monumental-archive.github.io"
# SA_REMOTE_URL is the dry run's seam (#236): a file-protocol stand-in
# repo replaces the network, nothing else changes.
git remote add origin "${SA_REMOTE_URL:-https://github.com/${GITHUB_REPOSITORY}.git}"
# Public repos, anonymous fetch; the push in append.sh authenticates.
git fetch -q origin "+refs/heads/main:refs/sa/main" "+refs/notes/commits:refs/notes/commits" || {
  echo "::error::could not fetch main and refs/notes/commits — the notes ref must exist (seeded org-wide, #202)"
  exit 1
}
git merge-base --is-ancestor "${GITHUB_SHA}" refs/sa/main || {
  echo "::error::${GITHUB_SHA} is not on main — the emitter attests protected-ref revisions only"
  exit 1
}

# Walk first-parent from the pushed revision toward the root, collecting
# unlinked revisions, until the genesis link (predicate.prev == null)
# ends the walk. Linked revisions along the way are passed over, not
# re-emitted — a redundant rerun with nothing to do is a success.
holes=()
genesis_found=""
c="${GITHUB_SHA}"
while [[ -n ${c} ]]; do
  # shellcheck disable=SC2310  # predicate, manages its own exit status
  if is_link "${c}"; then
    if git notes show "${c}" | jq -r '.provenance.statement' | base64 -d \
      | jq -e '.predicate.prev == null' > /dev/null 2>&1; then
      genesis_found="${c}"
      break
    fi
  else
    holes+=("${c}")
  fi
  c=$(git rev-parse -q --verify "${c}^" 2> /dev/null || true)
done

if [[ ${SA_GENESIS} == "true" ]]; then
  if [[ -n ${genesis_found} ]] || ((${#holes[@]} < 1)) || [[ ${holes[0]} != "${GITHUB_SHA}" ]] \
    || {
      walked=$(git rev-list --first-parent --count "${GITHUB_SHA}")
      ((${#holes[@]} != walked))
    }; then
    # Any link on the walked history — genesis or not — refuses a
    # re-founding: a full-history walk that stopped early, or that
    # collected fewer holes than there are revisions, saw a link.
    echo "::error::genesis refused — a chain link already exists on this history; a gap is debt, not a new founding"
    exit 1
  fi
  echo "${GITHUB_SHA}" > "${SA_WORK}/heal.list"
  echo "::notice::genesis: founding the chain at ${GITHUB_SHA}"
  exit 0
fi

if [[ -z ${genesis_found} ]]; then
  echo "::error::no genesis link on this history — found the chain first with a genesis dispatch (genesis: true)"
  exit 1
fi

# Oldest first: healing in order keeps every target's first-parent
# parent linked by the time its turn comes.
: > "${SA_WORK}/heal.list"
for ((i = ${#holes[@]} - 1; i >= 0; i--)); do
  echo "${holes[${i}]}" >> "${SA_WORK}/heal.list"
done

count=$(wc -l < "${SA_WORK}/heal.list" | tr -d " ")
if ((count == 0)); then
  echo "::notice::every revision since genesis already carries a link — nothing to emit"
elif ((count > 1)); then
  echo "::warning::healing $((count - 1)) unattested revision(s) left by earlier lapses — see the repaired marker in their provenance"
fi
echo "::notice::genesis at ${genesis_found}; ${count} revision(s) to link"
