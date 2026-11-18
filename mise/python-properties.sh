#!/usr/bin/env bash
# A repository's Python properties, under the belt's Hypothesis profile.
# Org canon — the body of `test:python-properties` (the gate, `gate`)
# and `audit:python-properties` (the Monday audit, `audit`), one script
# so the two tasks find the same files the same way (#1332).
#
# The subject is every tracked `prop_*.py`. Those files sit beside the
# `test_*.py` suites they extend and are written as `unittest.TestCase`
# classes using `@given`; a repository's own `test` task never collects
# them, so its ordinary suites stay stdlib and run with nothing
# installed. The gate half also holds that line: a tracked Python file
# that is not a property file and imports Hypothesis is red by name.
#
# THE INTERPRETER IS THE TOOL VENV'S, asserted to be the belt's pin.
# The properties import the repository's own helpers, which the gate's
# `test` runs on `python3`; a Hypothesis venv uv built on an interpreter
# of its own (the hidden-Python trap the belt's UV_* env exists for)
# would test them on a different Python, silently.
set -euo pipefail

profile="${1:-}"
case "${profile}" in
  gate) label="test:python-properties" ;;
  audit) label="audit:python-properties" ;;
  *)
    echo "python-properties.sh: expected gate or audit, got '${profile}'" >&2
    exit 2
    ;;
esac
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: conf.d symlink)}"

# git's default pathspec lets `*` cross a slash, so this lists every
# `prop_*.py` at any depth; awk keeps the ones whose NAME starts prop_.
files=$(git ls-files ':!vendor/**' '*prop_*.py' | awk -F/ '$NF ~ /^prop_.*\.py$/')
[[ -n ${files} ]] || {
  echo "${label}: no prop_*.py tracked, skipped"
  exit 0
}

if [[ ${profile} == gate ]]; then
  # The design condition, enforced: ordinary tests never import
  # Hypothesis. Read from each file's syntax tree, so a fixture that
  # carries Hypothesis source as a string is not an importer. The
  # belt's own runner is the one other file that may.
  others=$(git ls-files ':!vendor/**' '*.py' \
    | awk -F/ '$NF !~ /^prop_.*\.py$/ && $0 != "mise/python-properties.py"')
  leaks=""
  if [[ -n ${others} ]]; then
    leaks=$(tr '\n' '\0' <<< "${others}" | xargs -0 python3 "${belt}/hypothesis-imports.py")
  fi
  if [[ -n ${leaks} ]]; then
    echo "${label}: these import Hypothesis (or cannot be read) but are not prop_*.py files:" >&2
    while IFS= read -r leak; do
      echo "  ${leak}" >&2
    done <<< "${leaks}"
    echo "  property tests live in prop_*.py, so the ordinary suites run with nothing installed" >&2
    exit 1
  fi
fi

command -v hypothesis > /dev/null || {
  echo "${label}: prop_*.py tracked but hypothesis is missing —" >&2
  echo "  the belt pins pipx:hypothesis; run: mise install" >&2
  exit 1
}
# The venv's interpreter sits beside its `hypothesis` entry point; mise
# puts a symlink to that entry point on PATH, so resolve it first.
on_path=$(command -v hypothesis)
entry=$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "${on_path}")
venv_python="$(dirname "${entry}")/python"
hyp_version=$("${venv_python}" -c 'import hypothesis, sys; print(sys.version)' 2> /dev/null) || hyp_version=""
run_version=$(python3 -c 'import sys; print(sys.version)' 2> /dev/null) || run_version=""
if [[ -z ${hyp_version} || ${hyp_version} != "${run_version}" ]]; then
  echo "${label}: hypothesis runs on '${hyp_version:-no interpreter}'" >&2
  echo "  but the repository's python3 is '${run_version:-missing}' — the tool venv" >&2
  echo "  must be built on the belt's python pin (reinstall: mise install -f pipx:hypothesis)" >&2
  exit 1
fi

args=()
while IFS= read -r f; do
  args+=("${f}")
done <<< "${files}"
"${venv_python}" "${belt}/python-properties.py" "${profile}" "${args[@]}"
