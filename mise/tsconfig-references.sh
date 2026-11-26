#!/usr/bin/env bash
# Refuse project references in the tsconfigs named. Org canon: the
# refusal is `lint:types`' (#445), moved here from its body with #2133.
#
# `tsc -b` refuses every compiler option it is handed (TS5094), so the
# org's flags and reference-following are mutually exclusive; under
# `-p` a referenced project's declarations do not exist (TS6305). A
# project that declares `references` is therefore refused outright.
#
# What a config declares is asked of the compiler, through the one
# resolver (mise/tsconfig-resolve.sh), never of the file's text. The
# refusal used to be a grep for the word, so a config saying
# `"references"` in a comment was a finding. `references` is not
# inherited through `extends` (measured, tsc 7.0.2: `--showConfig`
# prints it for the config that writes it and not for a child), so the
# resolved document of each tracked config is the whole answer. A
# config tsc cannot parse ends the run with the resolver's finding.
#
# Usage: tsconfig-references.sh <tsconfig>…
# Exit 0 when none declares references; exit 1 with one finding naming
# every config that does, reported under the caller's task.
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
# The task this script reports for, where mise named none (#1979).
org_task "lint:types"

work=$(mktemp -d)
rc=0
trap 'rc=$?; rm -rf "${work}"; [[ -n ${org_done:-} || ${rc} -ne 0 ]] || rc=1; exit "${rc}"' EXIT
detail=()
for cfg in "$@"; do
  bash "${org__here}/tsconfig-resolve.sh" "${cfg}" "${work}/resolved.json" || exit 1
  refs=$(jq 'has("references")' "${work}/resolved.json")
  [[ ${refs} != true ]] || detail+=("declares references: ${cfg}")
done
if [[ -n ${detail[0]+set} ]]; then
  org_finding "" "project references are not supported org-wide" "${detail[@]}" \
    "tsc -b refuses the org's compiler flags (TS5094), so following references would" \
    "mean checking at whatever level the repo chose. Give each project its own config."
  exit 1
fi
org_done=1
