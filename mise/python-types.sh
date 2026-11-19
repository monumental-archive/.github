#!/usr/bin/env bash
# Type-check a repository's tracked Python with mypy at its strictest.
# Org canon — the body of `audit:python-types` (#1604), which becomes
# `lint:python-types`, collected by `ci`, once the canon's own findings
# are run down. The label it prints is its one argument.
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
#     (inline `# mypy:`, a bare `# type: ignore`, `no_type_check`),
#     named by python-types-seams.py.
# A mypy that is not the belt's pin is red by name: a stale install
# would check against another version's rules.
#
# The interpreter mypy resolves imports and the Python version from is
# the Hypothesis venv's when Hypothesis is installed (the `prop_*.py`
# files import it), else the repository's `python3`; either way it is
# asserted to be the same Python as `python3`, the belt's pin.
set -euo pipefail
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: conf.d symlink)}"
label="${1:?expected the task name}"

files=$(git ls-files ':!vendor/**' '*.py' '*.pyi')
[[ -n ${files} ]] || {
  echo "${label}: no Python tracked, skipped"
  exit 0
}

cfg="${belt}/mypy.ini"
[[ -r ${cfg} ]] || {
  echo "${label}: ${cfg} is missing — the belt did not arrive" >&2
  exit 1
}
command -v mypy > /dev/null || {
  echo "${label}: Python tracked but mypy is missing — the belt pins pipx:mypy; run: mise install" >&2
  exit 1
}
pinned=$(awk -F'"' '/^"pipx:mypy" = "/ { print $4 }' "${belt}/config.toml")
running=$(mypy --version | awk '{ print $2 }')
[[ -n ${pinned} && ${running} == "${pinned}" ]] || {
  echo "${label}: mypy ${running:-unknown} is running but the belt pins '${pinned:-nothing}'" >&2
  echo "  reinstall: mise install -f pipx:mypy" >&2
  exit 1
}

exe=$(command -v python3)
if command -v hypothesis > /dev/null; then
  on_path=$(command -v hypothesis)
  entry=$("${ORG_PYTHON}" -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "${on_path}")
  exe="$(dirname "${entry}")/python"
fi
want=$(python3 -c 'import sys; print(sys.version)' 2> /dev/null) || want=""
have=$("${exe}" -c 'import sys; print(sys.version)' 2> /dev/null) || have=""
[[ -n ${have} && ${have} == "${want}" ]] || {
  echo "${label}: mypy would read '${have:-no interpreter}' (${exe})" >&2
  echo "  but the repository's python3 is '${want:-missing}' — reinstall the tool venv on the belt's python pin" >&2
  exit 1
}

args=()
while IFS= read -r f; do
  args+=("${f}")
done <<< "${files}"

red=0
seams=$("${ORG_PYTHON}" "${belt}/python-types-seams.py" "${args[@]}") || red=1
if [[ -n ${seams} ]]; then
  echo "${label}: these lower the org's level for their own file:" >&2
  while IFS= read -r line; do
    echo "  ${line}" >&2
  done <<< "${seams}"
  echo "  suppress one line with '# type: ignore[<code>]', which names its rule" >&2
fi

status=0
out=$(mypy --config-file "${cfg}" --python-executable "${exe}" "${args[@]}" 2>&1) || status=$?
[[ -z ${out} ]] || printf '%s\n' "${out}"
if grep -qF "${cfg}:" <<< "${out}"; then
  echo "${label}: mypy did not load ${cfg} cleanly (above) — its defaults would be in force" >&2
  red=1
fi
if ((status != 0)); then
  echo "${label}: mypy exited ${status}; findings above, fixed in the code, never by lowering a level" >&2
  red=1
fi
((red == 0)) || exit 1
echo "${label}: ${#args[@]} file(s) clean under mypy ${running}"
