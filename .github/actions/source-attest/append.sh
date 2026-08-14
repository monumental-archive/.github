#!/usr/bin/env bash
# Push the chain. The notes themselves were assembled and added by
# emit.sh, one per revision in the heal list (#265) — this script owns
# only the network step, so a push failure can never leave a
# half-assembled note behind.
#
# The note is the world-readable storage of record (docs/
# source-assessment.md): statements carried base64 so the signed bytes
# survive any JSON re-encoding, bundles carried as JSON for tooling.
# GitHub's attestation store is deliberately NOT written: its subjects
# are sha256 artifact digests, and the source track's subject is a
# gitCommit — a store entry would attest a different subject than the
# one the spec verifies.
#
# The push races admins and (across repos) nobody: the workflow's
# concurrency group serializes same-repo runs, so a rejected push means
# the ref moved underneath us — refetch, re-add every note from the
# manifest, retry. Three failures is a real error, not a retry budget
# to grow. A rejected-then-retried push re-adds ALL of this run's
# notes: the refetch resets the local notes ref, and a partial re-add
# would push a chain missing its own newest links.
set -euo pipefail

# Inputs, declared so the contract is explicit and a missing one fails
# by name instead of expanding to nothing (#82).
: "${SA_WORK:?SA_WORK must be set by guard-identity, the first step of the action}"

cd "${SA_WORK}/repo"

if [[ ! -s "${SA_WORK}/manifest.tsv" ]]; then
  echo "::notice::nothing to push — every revision already carried a link"
  exit 0
fi

# The dry run (#236) pushes --dry-run to a file-protocol remote: same
# notes add, same push negotiation, no auth header (no token off-CI)
# and nothing written to the remote.
push=(push -q)
[[ ${SA_PUSH_DRY_RUN:-false} == true ]] && push+=(--dry-run)
auth=()
[[ -n ${GH_TOKEN:-} ]] \
  && auth=(-c "http.extraheader=AUTHORIZATION: basic $(printf "x-access-token:%s" "${GH_TOKEN}" | base64 -w0)")

count=$(wc -l < "${SA_WORK}/manifest.tsv" | tr -d " ")
for attempt in 1 2 3; do
  if git ${auth[@]+"${auth[@]}"} "${push[@]}" origin refs/notes/commits:refs/notes/commits; then
    echo "::notice::${count} chain link(s) pushed (attempt ${attempt})"
    exit 0
  fi
  echo "::warning::notes push rejected (attempt ${attempt}) — refetching and re-adding this run's links"
  git fetch -q origin "+refs/notes/commits:refs/notes/commits"
  while IFS=$'\t' read -r rev notefile; do
    git notes add -f -F "${notefile}" "${rev}"
  done < "${SA_WORK}/manifest.tsv"
done
echo "::error::refs/notes/commits would not fast-forward after three attempts"
exit 1
