#!/usr/bin/env bash
# Guard the identity and resolve the canon tree: the source-attest
# action's first step, moved out of action.yml when routing its
# workflow commands through the canon's writer grew the block past its
# ceiling (#1660, #1352).
#
# The action tree IS a canon tree: `uses:` fetched the whole repository
# at the caller's pin, the same single resolution the canon action
# relies on (#165), so the tree is this script's own, three levels up.
#
# Inputs: SA_STAGE (claims or attest, #240), GITHUB_REPOSITORY,
# GITHUB_SHA, RUNNER_TEMP, GITHUB_ENV. Writes SA_WORK, SA_CANON_ROOT,
# SA_CANON_REF and SA_ISSUER to GITHUB_ENV.
set -euo pipefail
: "${GITHUB_REPOSITORY:?}"
: "${GITHUB_SHA:?}"
: "${RUNNER_TEMP:?}"
: "${GITHUB_ENV:?}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# The canon's finding writer (#1890, #1934), in this tree: it reports
# through the one writer of workflow commands (#1660) beside it. No task
# runs this script, so a finding names the script's own file name.
# shellcheck source=mise/findings.sh
. "${root}/mise/findings.sh"
case "${SA_STAGE:-}" in
  claims | attest) ;;
  *)
    org_finding_annotated "" "source-attest stage must be 'claims' or 'attest', got '${SA_STAGE:-}' (#240)"
    exit 1
    ;;
esac
case "${root}" in
  */_actions/monumental-archive/.github/*)
    canon_ref="$(basename "${root}")"
    ;;
  *)
    if [[ ${GITHUB_REPOSITORY} != "monumental-archive/.github" ]]; then
      emsg="source-attest resolved to '${root}' in ${GITHUB_REPOSITORY} — not an _actions path and not a canon"
      emsg+=" self-call"
      org_finding_annotated "" "${emsg}"
      exit 1
    fi
    canon_ref="${GITHUB_SHA}"
    ;;
esac
if [[ ! -f "${root}/slsa/verify-policy.json" ]]; then
  org_finding_annotated "" "resolved canon tree at '${root}' has no slsa/verify-policy.json"
  exit 1
fi
work="${RUNNER_TEMP}/source-attest"
mkdir -p "${work}"
{
  echo "SA_WORK=${work}"
  echo "SA_CANON_ROOT=${root}"
  echo "SA_CANON_REF=${canon_ref}"
  echo "SA_ISSUER=https://token.actions.githubusercontent.com"
} >> "${GITHUB_ENV}"
