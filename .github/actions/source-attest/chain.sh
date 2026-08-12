#!/usr/bin/env bash
# Fetch the history and the chain, and verify the previous link before
# anything signs.
#
# The previous link is the NEAREST first-parent ancestor that carries a
# link, not necessarily the parent: an emission lapse leaves a hole, and
# holes are debt the Monday audit reports (audit:source-vsa) — never a
# reason to refuse all future emission, and emphatically never a reason
# to re-found the chain. Genesis is explicit (`genesis: "true"`, a
# dispatch), and refused the moment any link exists on the walked
# history.
#
# Everything here is git plumbing against a scratch clone — no checkout
# of a working tree, nothing from the attested repository is executed.
set -euo pipefail

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

# A link is a note that parses as a chain link (version + provenance) —
# the seed notes and unrelated annotations are not links.
is_link() {
  git notes show "${1}" 2> /dev/null \
    | jq -e '.version and .provenance.bundle and .vsa.bundle' > /dev/null 2>&1
}

prev=""
c="${GITHUB_SHA}"
while c=$(git rev-parse -q --verify "${c}^" 2> /dev/null); do
  if is_link "${c}"; then
    prev="${c}"
    break
  fi
done

if [[ ${SA_GENESIS} == "true" ]]; then
  if [[ -n ${prev} ]] || is_link "${GITHUB_SHA}"; then
    echo "::error::genesis refused — a chain link already exists on this history; a gap is debt, not a new founding"
    exit 1
  fi
  echo '{"prev": null}' > "${SA_WORK}/prev.json"
  echo "::notice::genesis: founding the chain at ${GITHUB_SHA}"
  exit 0
fi

if [[ -z ${prev} ]]; then
  echo "::error::no previous chain link on this history — found the chain first with a genesis dispatch (genesis: true)"
  exit 1
fi

# Verify the link against the pinned org identity — the same check a
# stranger runs with the published root of trust, nothing more.
note=$(git notes show "${prev}")
jq -r '.provenance.statement' <<< "${note}" | base64 -d > "${SA_WORK}/prev-statement.json"
jq -c '.provenance.bundle' <<< "${note}" > "${SA_WORK}/prev-bundle.json"
cosign verify-blob \
  --bundle "${SA_WORK}/prev-bundle.json" \
  --certificate-identity "${SA_IDENTITY}" \
  --certificate-oidc-issuer "${SA_ISSUER}" \
  "${SA_WORK}/prev-statement.json" > /dev/null 2>&1 || {
  echo "::error::previous link at ${prev} does not verify against ${SA_IDENTITY} — refusing to extend a chain that fails the published root of trust"
  exit 1
}
jq -e --arg c "${prev}" '.subject[0].digest.gitCommit == $c' \
  "${SA_WORK}/prev-statement.json" > /dev/null || {
  echo "::error::previous link at ${prev} attests a different revision than the commit it annotates"
  exit 1
}

# The chain digest is over the raw note blob — command substitution
# strips trailing newlines, and a digest two tools compute differently
# is no digest at all.
note_obj=$(git notes list "${prev}")
note_sha=$(git cat-file blob "${note_obj}" | sha256sum | cut -d" " -f1)
jq -n --arg r "${prev}" --arg d "${note_sha}" \
  '{prev: {revision: $r, noteSha256: $d}}' > "${SA_WORK}/prev.json"
echo "::notice::previous link verified: ${prev}"
