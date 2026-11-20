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
# ORG_PROPERTY_SHARD=k/n passes through to the runner, which takes that
# shard of the files listed here (#1631): the audit runs eight legs.
#
# THE INTERPRETER IS THE TOOL VENV'S, asserted to be the belt's pin.
# The properties import the repository's own helpers, which the gate's
# `test` runs on `python3`; a Hypothesis venv uv built on an interpreter
# of its own (the hidden-Python trap the belt's UV_* env exists for)
# would test them on a different Python, silently.
set -euo pipefail
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"

profile="${1:-}"
case "${profile}" in
  gate) label="test:python-properties" ;;
  audit) label="audit:python-properties" ;;
  *)
    echo "python-properties.sh: expected gate or audit, got '${profile}'" >&2
    exit 2
    ;;
esac
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"

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
  # belt's own runner and its typed `given` are the two other files
  # that may.
  others=$(git ls-files ':!vendor/**' '*.py' \
    | awk -F/ '$NF !~ /^prop_.*\.py$/ && $0 != "mise/python_properties.py" && $0 != "mise/typed_hypothesis.py"')
  leaks=""
  if [[ -n ${others} ]]; then
    leaks=$(tr '\n' '\0' <<< "${others}" | xargs -0 "${ORG_PYTHON}" "${belt}/hypothesis_imports.py")
  fi
  if [[ -n ${leaks} ]]; then
    echo "${label}: these import Hypothesis (or cannot be read) but are not prop_*.py files:" >&2
    while IFS= read -r leak; do
      echo "  ${leak}" >&2
    done <<< "${leaks}"
    echo "  property tests live in prop_*.py, so the ordinary suites run with nothing installed" >&2
    exit 1
  fi
  # A property takes the belt's typed `given` (#1604): Hypothesis's own
  # is typed `Callable[..., None]`, which the belt's mypy reds on every
  # property, and the typed one also refuses a strategy that does not
  # match its parameter.
  untyped=$(tr '\n' '\0' <<< "${files}" | xargs -0 "${ORG_PYTHON}" "${belt}/hypothesis_imports.py" --given)
  if [[ -n ${untyped} ]]; then
    echo "${label}: these take given from hypothesis, not the belt's typed one:" >&2
    while IFS= read -r file; do
      echo "  ${file}" >&2
    done <<< "${untyped}"
    echo "  write: from typed_hypothesis import given" >&2
    exit 1
  fi
fi

command -v hypothesis > /dev/null || {
  echo "${label}: prop_*.py tracked but hypothesis is missing —" >&2
  echo "  the belt pins pipx:hypothesis; run: mise install" >&2
  exit 1
}
# The venv must sit on the belt's Python, and import Hypothesis: the
# belt's one check asks both (tool_python.py, #1788). The first call
# names the interpreter the properties run on; the second, with
# `--site`, has that venv import the package, and its directory is not
# needed here.
venv_python=$("${ORG_PYTHON}" "${belt}/tool_python.py" "${label}" hypothesis) || exit 1
"${ORG_PYTHON}" "${belt}/tool_python.py" "${label}" hypothesis --site hypothesis > /dev/null || exit 1

args=()
while IFS= read -r f; do
  args+=("${f}")
done <<< "${files}"
"${venv_python}" "${belt}/python_properties.py" "${profile}" "${args[@]}"
