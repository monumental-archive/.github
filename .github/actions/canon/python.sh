#!/usr/bin/env bash
# Install the belt's Python and export its path as ORG_PYTHON (#1543).
#
# The canon action's last step. Every workflow step that runs a tracked
# helper runs `"${ORG_PYTHON}" <helper.py>`, so the helpers run under
# the interpreter the gate tests them with, on every job, and never
# under the runner's python3: that one is pinned by nothing, and on
# 2026-11-18 it refused the belt's own 3.14 spelling and broke every
# image publish in the org. It ships beside link.sh and stays shell for
# the same reason: it runs before any Python exists.
#
# The version is the belt's own `[tools]` pin and the install is locked
# (MISE_LOCKED), so the interpreter arrives checksummed from the belt's
# mise.lock exactly as the gate's does. mise is already on PATH: the
# action installs it, at the version the workflows pin, before this
# runs. PATH does not change, so the caller's own build code sees the
# python3 it saw before.
#
# Inputs: GITHUB_ENV, RUNNER_TEMP, and mise on PATH. Output: ORG_PYTHON
# in GITHUB_ENV, the interpreter's absolute path.
set -euo pipefail
: "${GITHUB_ENV:?}"
: "${RUNNER_TEMP:?}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# The canon's finding writer (#1890, #1934), in this tree: it reports
# through the one writer of workflow commands (#1660) beside it. No task
# runs this script, so a finding names the script's own file name.
# shellcheck source=mise/findings.sh
. "${root}/mise/findings.sh"
config="${root}/mise/config.toml"
lock="${root}/mise/mise.lock"
version=$(awk -F'"' '/^python = "/ { print $2 }' "${config}")
if [[ -z ${version} || ${version} == *$'\n'* ]]; then
  org_finding_annotated "" "${config} does not pin exactly one python version"
  exit 1
fi
# The lock must hold the version the config pins: a locked install of a
# version the lock does not hold has no checksum to hold it to.
locked=$(awk '$0 == "[[tools.python]]" { seen = 1; next }
  seen && /^version = / { gsub(/"/, "", $3); print $3; exit }' "${lock}")
if [[ ${locked} != "${version}" ]]; then
  org_finding_annotated "" \
    "${lock} does not hold python ${version} (it holds '${locked}'), so the install cannot be checked"
  exit 1
fi
# A scratch directory, so mise reads the belt and nothing the caller's
# checkout carries; and a data directory of its own, so the install
# leaves no python3 shim where a later mise step would put one on the
# caller's PATH.
work="${RUNNER_TEMP}/org-python"
mkdir -p "${work}/cwd" "${work}/data"
cd "${work}/cwd"
export MISE_DATA_DIR="${work}/data"
export MISE_GLOBAL_CONFIG_FILE="${config}"
export MISE_TRUSTED_CONFIG_PATHS="${root}"
export MISE_LOCKED=1
if ! mise install python; then
  org_finding_annotated "" "mise could not install python ${version} from ${lock}"
  exit 1
fi
if ! where=$(mise where "python@${version}"); then
  org_finding_annotated "" "mise installed python ${version} but cannot say where"
  exit 1
fi
python="${where}/bin/python3"
if [[ ! -x ${python} ]]; then
  org_finding_annotated "" "${python} is not an executable interpreter"
  exit 1
fi
echo "ORG_PYTHON=${python}" >> "${GITHUB_ENV}"
org_say "the belt's python ${version}: ${python}"
