#!/usr/bin/env bash
# The VEX decisions vex-attest signs this run, as stele's arguments:
# the listing and its refusals, moved out of the workflow when routing
# its commands through the canon's writer grew the block past its
# ceiling (#1660, #1352). The `stele derive vex-subjects` call stays in
# the step, where the population-read law reads it (#1602).
#
# A push is a RANGE: the merge changed one decision, read by
# lint:vex-one-per-merge's own reader (#1615). A dispatch, or an
# unusable before-sha, is a STATE: every tracked decision, and stele
# asks the store which it already holds (#596).
#
# Usage: vex-decisions.sh OUT. Inputs: BEFORE, REPO, DECISIONS,
# ONE_PER_MERGE, ORG_PYTHON. Writes stele's arguments to OUT,
# NUL-terminated (the mode, then one --decision= per file), a file and
# not stdout, which is the log; leaves OUT empty after a notice when
# there is nothing to sign; refuses, exit 1, a push that changed more
# than one decision.
set -euo pipefail
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"
: "${REPO:?}"
: "${DECISIONS:?}"
: "${ONE_PER_MERGE:?}"
out="${1:?usage: vex-decisions.sh OUT, the file the arguments for stele are written to}"
here="$(dirname "${BASH_SOURCE[0]}")"
: > "${out}"
# The canon's one writer of workflow commands (#1660), beside this file.
# shellcheck source=mise/workflow-command.sh
. "${here}/workflow-command.sh"
if [[ -n ${BEFORE:-} ]] && git cat-file -e "${BEFORE}^{commit}" 2> /dev/null; then
  mode=() none="no changed statements"
  list=$("${ORG_PYTHON}" "${here}/vex_one_per_merge.py" --list "${BEFORE}..")
else
  mode=(--unsigned-only --store "${REPO}") none="security/vex/ holds no decisions"
  list=$(git ls-files "${DECISIONS}")
fi
files=()
while IFS= read -r file; do
  [[ -n ${file} ]] && files+=("${file}")
done <<< "${list}"
# Nothing to sign ends here, with nothing printed: no output is `any`
# unset, which every consumer reads as false.
if ((${#files[@]} == 0)); then
  org_notice "${none}"
  exit 0
fi
if ((${#mode[@]} == 0 && ${#files[@]} != 1)); then
  org_error "$(< "${ONE_PER_MERGE}")"
  exit 1
fi
# `${mode[@]+…}`: an empty array under set -u is unbound on bash 3.2.
for arg in ${mode[@]+"${mode[@]}"} "${files[@]/#/--decision=}"; do
  printf '%s\0' "${arg}" >> "${out}"
done
