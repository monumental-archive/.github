#!/usr/bin/env bash
# Emit the chain links and push the ledger: the source-attest action's
# attest step, moved out of action.yml when routing its refusal through
# the canon's finding writer grew the block past its ceiling (#1934,
# #1352).
#
# stele emits the links and appends them to the ledger (action.yml has
# why). The released, attested binary: no Go toolchain on the runner;
# the belt holds its per-platform checksums and attestation in
# mise/mise.lock (stele#7). SA_REMOTE_URL is the test seam: a
# file-protocol stand-in replaces the network, nothing else changes.
#
# Inputs: SA_ACTOR, SA_ACTOR_ID, SA_GENESIS ("true" founds a chain),
# SA_CANON_ROOT, SA_CANON_REF, SA_WORK (holding claims.json),
# GITHUB_REPOSITORY, GITHUB_SHA, and SA_REMOTE_URL optionally.
set -euo pipefail
: "${SA_ACTOR:?}"
: "${SA_ACTOR_ID:?}"
: "${SA_GENESIS?}"
: "${SA_CANON_ROOT:?}"
: "${SA_CANON_REF:?}"
: "${SA_WORK:?}"
: "${GITHUB_REPOSITORY:?}"
: "${GITHUB_SHA:?}"
# The canon's finding writer (#1934), in this tree.
# shellcheck source=mise/findings.sh
. "$(dirname "${BASH_SOURCE[0]}")/../../../mise/findings.sh"
command -v stele > /dev/null || {
  org_finding_annotated "" "stele is not on the runner — it is a belt tool"
  exit 1
}
genesis=()
[[ ${SA_GENESIS} == "true" ]] && genesis=(--genesis)
stele emit chain \
  --policy "${SA_CANON_ROOT}/slsa/verify-policy.json" \
  --repo "${GITHUB_REPOSITORY}" \
  --clone "${SA_REMOTE_URL:-https://github.com/${GITHUB_REPOSITORY}.git}" \
  --committer "source-attest <source-attest@monumental-archive.github.io>" \
  --rev "${GITHUB_SHA}" \
  --claims "${SA_WORK}/claims.json" \
  --actor "${SA_ACTOR}" \
  --actor-id "${SA_ACTOR_ID}" \
  --machinery-digest "${SA_CANON_REF}" \
  --policy-uri "https://github.com/monumental-archive/.github/blob/${SA_CANON_REF}/slsa/verify-policy.json" \
  ${genesis[@]+"${genesis[@]}"}
