#!/usr/bin/env bash
# Publish the measured levels to this repository's levels branch:
# level.yml's `level` step, moved out of the workflow when routing its
# refusal through the canon's finding writer grew the block past its
# ceiling (#1934, #1352).
#
# The good judgments are published before the run goes red: a cell
# judged before that cannot be judged now (stele level's exit 4, read
# into `investigate` by the step before) is refused after the push.
#
# Run from the job's workspace, with the rendered cells in levels/.
# Inputs: GH_TOKEN, GITHUB_REPOSITORY, investigate (0 or 4).
set -euo pipefail
: "${GH_TOKEN:?}"
: "${GITHUB_REPOSITORY:?}"
: "${investigate:?}"
# The canon's finding writer (#1934), in this tree.
# shellcheck source=mise/findings.sh
. "$(dirname "${BASH_SOURCE[0]}")/findings.sh"
cd levels
git init -q -b levels
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A
git commit -q -m "chore: publish measured levels" \
  -m "Rendered by stele level in ${GITHUB_REPOSITORY}; this repository judges itself."
git push -q --force \
  "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" \
  levels:levels
if [[ ${investigate} -ne 0 ]]; then
  org_finding_annotated "" \
    "a cell that was judged before cannot be judged now — investigate; good reports were not overwritten"
  exit 1
fi
