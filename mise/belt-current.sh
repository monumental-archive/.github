#!/usr/bin/env bash
# Is this machine's toolchain the one CI will judge this repository with?
# Called by lint:belt-current; the reasoning lives on that task.
#
# The version is DERIVED from a file already on disk, the canon's own
# ci.yml, so there is no second literal to keep in step and nothing here
# for Renovate to bump. Whether the belt answering is the one this tree
# pins is said by every belt task (ORG_BELT_REFUSED, #1988), not here.
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here
# The task this script reports for, where mise named none (#1934).
org_task "lint:belt-current"

canon="${ORG_CANON_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"

# 1. mise itself. It updates the tools and never updates itself.
ci="${canon}/.github/workflows/ci.yml"
if [[ ! -f ${ci} ]]; then
  org_finding "" "${ci} is missing, so the mise version CI runs cannot" \
    "be read. The belt's canon tree is incomplete."
  exit 1
fi
want=$(awk '/uses: jdx\/mise-action@/ { seen = 1 }
  seen && /^[[:space:]]+version:/ { print $2; exit }' "${ci}")
if [[ -z ${want} ]]; then
  org_finding "" "no mise-action version pin found in ${ci}" \
    "(lint:installer-pinned requires one), so there is nothing to compare."
  exit 1
fi
have=$(mise --version 2> /dev/null | sed -n '1p' | cut -d ' ' -f 1)
if [[ -z ${have} ]]; then
  org_finding "" "could not read this machine's mise version"
  exit 1
fi
# The lower of the two versions, as a plain assignment so `set -e` and
# pipefail hold over the sort (#1997): a failed sort stops the task
# rather than reading as "not older".
first=$(printf '%s\n%s\n' "${have}" "${want}" | sort -V | sed -n '1p')
if [[ ${have} != "${want}" && ${first} == "${have}" ]]; then
  org_finding "" "this machine runs mise ${have}; CI runs ${want}." \
    "mise keeps the tools current and never updates itself, and an older" \
    "mise reads the lockfile and the registry differently. Run:" \
    "  mise self-update ${want}"
  exit 1
elif [[ ${have} != "${want}" ]]; then
  echo "belt-current: note — mise ${have} here is newer than CI's ${want}"
fi

echo "lint:belt-current: mise ${have}; CI runs ${want}"
