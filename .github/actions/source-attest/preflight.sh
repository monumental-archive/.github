#!/usr/bin/env bash
# Preflight: prove the run can finish before anything irreversible
# happens (#236).
#
# Signing mints certificates into Rekor's append-only log; a push that
# fails AFTER that leaves orphan log entries for a chain link that never
# landed. So every environment assumption the later steps make is
# asserted here, each with its own named error — an unguarded call dying
# with a bare `exit 128` is the bug, not the symptom. The three lab
# failures this contract answers were all of this class: an action not
# on the allowlist, a tool assumed present, an identity assumed
# configured.
#
# The push proof is real, not a tautology: a dry-run push of an
# unchanged ref is "Everything up-to-date" and proves nothing, so a
# throwaway note is added first (exercising the committer identity on
# the exact code path append.sh uses), the advanced ref is dry-run
# pushed (exercising auth and fast-forward server-side), and the ref is
# restored. --dry-run never updates the remote.
set -euo pipefail

# Inputs, declared so the contract is explicit and a missing one fails
# by name instead of expanding to nothing (#82).
: "${SA_WORK:?SA_WORK must be set by guard-identity}"
: "${GH_TOKEN:?GH_TOKEN must be set (the source-attest environment read token)}"
: "${GITHUB_SHA:?}"

cd "${SA_WORK}/repo"
fail() {
  echo "::error::preflight: ${1}"
  exit 1
}

command -v cosign > /dev/null 2>&1 \
  || fail "cosign is not on PATH — the belt install step did not deliver it"
cosign version > /dev/null 2>&1 \
  || fail "cosign is on PATH but not executable"
git var GIT_COMMITTER_IDENT > /dev/null 2>&1 \
  || fail "no usable committer identity in the scratch repo — the contract identity chain.sh declares is missing (docs/source-assessment.md, storage)"

orig=$(git rev-parse refs/notes/commits) \
  || fail "refs/notes/commits is absent from the scratch repo after chain.sh"
git notes add -f -m "source-attest preflight — never pushed" "${GITHUB_SHA}" \
  || fail "could not annotate ${GITHUB_SHA} in the scratch repo"
# shellcheck disable=SC2312  # cannot meaningfully fail (printf/id on local state)
git -c http.extraheader="AUTHORIZATION: basic $(printf "x-access-token:%s" "${GH_TOKEN}" | base64 -w0)" \
  push -q --dry-run origin refs/notes/commits:refs/notes/commits \
  || fail "notes push dry-run rejected — the token cannot write refs/notes/commits (needs contents: write) or the ref moved underneath the run"
git update-ref refs/notes/commits "${orig}"

echo "::notice::preflight ok — identity, cosign and the notes push are all proven"
