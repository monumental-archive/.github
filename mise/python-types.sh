#!/usr/bin/env bash
# Type-check a repository's tracked Python with mypy at its strictest.
# Org canon — the body of `lint:python-types` (#1604), collected by
# `ci`. The label it prints is its one argument.
#
# The config is the belt's (mise/mypy.ini), passed with --config-file,
# which replaces discovery: a repository's own mypy config is not read.
# Three things mypy itself lets through are red here, each measured on
# mypy 2.4.1 (docs/tooling-verdicts.md):
#   - the delivered config missing (mypy would refuse, exit 2, but the
#     message would not say the belt did not arrive);
#   - a line mypy prints about the config file: an unknown key or an
#     unparsable file is a warning and exit 0 under mypy's defaults;
#   - a seam in a source file that lowers the org's level for itself
#     (inline `# mypy:`, any `# type: ignore`, `no_type_check`),
#     named by python_types_seams.py.
# A mypy that is not the belt's pin is red by name: a stale install
# would check against another version's rules.
#
# The interpreter mypy resolves imports and the Python version from is
# the Hypothesis venv's when Hypothesis is installed (the `prop_*.py`
# files import it), else the repository's `python3`; either way it is
# asserted to be the same Python as `python3`, the belt's pin.
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"
label="${1:?expected the task name}"
# The task this script reports for, where mise named none (#1934).
org_task "${label}"

files=$(git ls-files ':!vendor/**' '*.py' '*.pyi')
[[ -n ${files} ]] || {
  echo "${label}: no Python tracked, skipped"
  exit 0
}

cfg="${belt}/mypy.ini"
[[ -r ${cfg} ]] || {
  org_finding "" "${cfg} is missing — the belt did not arrive"
  exit 1
}
command -v mypy > /dev/null || {
  org_finding "" "Python tracked but mypy is missing — the belt pins pipx:mypy; run: mise install"
  exit 1
}
pinned=$(awk -F'"' '/^"pipx:mypy" = "/ { print $4 }' "${belt}/config.toml")
running=$(mypy --version | awk '{ print $2 }')
[[ -n ${pinned} && ${running} == "${pinned}" ]] || {
  org_finding "" "mypy ${running:-unknown} is running but the belt pins '${pinned:-nothing}'" \
    "reinstall: mise install -f pipx:mypy"
  exit 1
}

# mypy reads the Hypothesis venv, where a property's imports resolve;
# the belt's one check asks that it sits on the belt's Python (#1788).
exe=$(command -v python3)
if command -v hypothesis > /dev/null; then
  exe=$("${ORG_PYTHON}" "${belt}/tool_python.py" "${label}" hypothesis) || exit 1
fi

args=()
while IFS= read -r f; do
  args+=("${f}")
done <<< "${files}"

red=0
seams=$("${ORG_PYTHON}" "${belt}/python_types_seams.py" "${args[@]}") || red=1
if [[ -n ${seams} ]]; then
  detail=()
  while IFS= read -r line; do detail+=("${line}"); done <<< "${seams}"
  org_finding "" "these lower the org's level for their own file:" \
    "${detail[@]}"
fi

# The import roots, derived from the tracked files (python_roots.py,
# #1634): mypy follows neither a script's own directory nor the sibling
# root a file names from its `__file__`, so it is handed both, and the
# belt's own directory, where a property file's `from typed_hypothesis
# import given` resolves as the properties runner resolves it (#1604).
# Two files that would be one module are red by name, before mypy stops.
roots=$("${ORG_PYTHON}" "${belt}/python_roots.py" "${belt}" "${args[@]}") || {
  org_finding "" "rename one of each pair above so every module name is one file's"
  exit 1
}

status=0
out=$(MYPYPATH="${roots}" mypy --config-file "${cfg}" --python-executable "${exe}" "${args[@]}" 2>&1) || status=$?
[[ -z ${out} ]] || printf '%s\n' "${out}"
if grep -qF "${cfg}:" <<< "${out}"; then
  org_finding "" "mypy did not load ${cfg} cleanly (above) — its defaults would be in force"
  red=1
fi
if ((status != 0)); then
  org_finding "" "mypy exited ${status}; findings above, fixed in the code, never by lowering a level"
  red=1
fi
((red == 0)) || exit 1
echo "${label}: ${#args[@]} file(s) clean under mypy ${running}"
