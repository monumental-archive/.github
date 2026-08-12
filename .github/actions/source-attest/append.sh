#!/usr/bin/env bash
# Append the link and push the chain.
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
# the ref moved underneath us — refetch, re-annotate, retry. Three
# failures is a real error, not a retry budget to grow.
set -euo pipefail

cd "${SA_WORK}/repo"

jq -n \
  --arg ps "$(base64 -w0 < "${SA_WORK}/provenance.json")" \
  --arg vs "$(base64 -w0 < "${SA_WORK}/vsa.json")" \
  --slurpfile pb "${SA_WORK}/provenance.bundle.json" \
  --slurpfile vb "${SA_WORK}/vsa.bundle.json" \
  '{
    version: 1,
    provenance: {statement: $ps, bundle: $pb[0]},
    vsa: {statement: $vs, bundle: $vb[0]}
  }' > "${SA_WORK}/note.json"

# The dry run (#236) pushes --dry-run to a file-protocol remote: same
# notes add, same push negotiation, no auth header (no token off-CI)
# and nothing written to the remote.
push=(push -q)
[[ ${SA_PUSH_DRY_RUN:-false} == true ]] && push+=(--dry-run)
auth=()
[[ -n ${GH_TOKEN:-} ]] \
  && auth=(-c "http.extraheader=AUTHORIZATION: basic $(printf "x-access-token:%s" "${GH_TOKEN}" | base64 -w0)")

for attempt in 1 2 3; do
  git notes add -f -F "${SA_WORK}/note.json" "${GITHUB_SHA}"
  if git ${auth[@]+"${auth[@]}"} "${push[@]}" origin refs/notes/commits:refs/notes/commits; then
    echo "::notice::chain link pushed for ${GITHUB_SHA} (attempt ${attempt})"
    exit 0
  fi
  echo "::warning::notes push rejected (attempt ${attempt}) — refetching"
  git fetch -q origin "+refs/notes/commits:refs/notes/commits"
done
echo "::error::refs/notes/commits would not fast-forward after three attempts"
exit 1
