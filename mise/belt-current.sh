#!/usr/bin/env bash
# Is this machine's toolchain the one CI will judge this repository with?
# Called by lint:belt-current; the reasoning lives on that task.
#
# Both comparisons are DERIVED from files already on disk — the mise
# version from the canon's own ci.yml, the pinned sha from the gate
# stub's `uses:` line — so there is no second literal to keep in step and
# nothing here for Renovate to bump.
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here
# The task this script reports for, where mise named none (#1934).
: "${MISE_TASK_NAME:=lint:belt-current}"

canon="${ORG_CANON_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"
status=0

# older <a> <b>: true when version a sorts strictly before version b.
older() {
  [[ $1 != "$2" ]] || return 1
  local first
  first=$(printf '%s\n%s\n' "$1" "$2" | sort -V | sed -n '1p')
  [[ ${first} == "$1" ]]
}

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
# shellcheck disable=SC2310  # older's status is the answer, not an error
if older "${have}" "${want}"; then
  org_finding "" "this machine runs mise ${have}; CI runs ${want}." \
    "mise keeps the tools current and never updates itself, and an older" \
    "mise reads the lockfile and the registry differently. Run:" \
    "  mise self-update ${want}"
  status=1
elif [[ ${have} != "${want}" ]]; then
  echo "belt-current: note — mise ${have} here is newer than CI's ${want}"
fi

# 2. The belt, against what this repository pins (#1686). A clone loads
# the belt of its own pin through `.config/mise/conf.d/belt.toml`, which
# the org hooks point at the machine's cache tree of exactly the pinned
# sha. So the belt answering is either that tree or wrong: older and
# newer are one red. The canon has no pin; there the belt is the tree.
# A belt that arrived by MISE_GLOBAL_CONFIG_FILE was named on purpose
# (CI's canon action delivers it at the pin's own resolution, or a
# person chose one): that carrier is said in one line and passes. The
# decision is the carrier, never a variable such as GITHUB_ACTIONS.
sha=""
version=""
top=$(git rev-parse --show-toplevel)
if compgen -G ".github/workflows/*.y*ml" > /dev/null; then
  pin_re='uses:[[:space:]]*[^[:space:]/]+/[^[:space:]/]+/\.github/workflows/ci\.ya?ml@([0-9a-f]{40})'
  stubs=$(cat .github/workflows/*.y*ml)
  sha=$(sed -nE "s|.*${pin_re}.*|\1|p" <<< "${stubs}" | sed -n '1p')
  version=$(sed -nE "s|.*${pin_re}[[:space:]]*#[[:space:]]*v([0-9][0-9.]*).*|\2|p" <<< "${stubs}" | sed -n '1p')
fi
here=$(cd -P "${canon}" 2> /dev/null && pwd || echo "${canon}")
belt=""
carrier="${MISE_GLOBAL_CONFIG_FILE:-}"
if [[ -n ${carrier} ]]; then
  belt="${carrier}"
elif [[ -n ${sha} ]]; then
  want="${XDG_CACHE_HOME:-${HOME}/.cache}/org-belt/${sha}"
  belt=$(cd -P "${want}" 2> /dev/null && pwd || echo "${want}")
  if [[ ${here} != "${belt}" ]]; then
    detail=("pins ${sha}${version:+ (v${version})}, whose belt is ${want}.")
    if [[ ! -L .config/mise/conf.d/belt.toml ]]; then
      detail+=("This clone has no .config/mise/conf.d/belt.toml link. Run:")
    else
      detail+=("This clone's link names another tree. Run:")
    fi
    org_finding "" "the belt answering is ${canon}; this repository's gate" \
      "${detail[@]}" "  lefthook run post-checkout"
    status=1
  fi
elif git ls-files --error-unmatch security/identity.toml > /dev/null 2>&1; then
  belt=$(cd -P "${top}" && pwd)
  if [[ ${here} != "${belt}" ]]; then
    org_finding "" "this is the canon, and the belt answering is ${canon}," \
      "not this tree (${top}). The tree is its own belt: unset" \
      "MISE_GLOBAL_CONFIG_FILE, which is the one carrier ranked above it."
    status=1
  fi
fi

[[ ${status} -eq 0 ]] || exit 1
if [[ -n ${carrier} ]]; then
  echo "lint:belt-current: mise ${have}; the belt arrived by MISE_GLOBAL_CONFIG_FILE (${carrier}), named on purpose"
elif [[ -n ${sha} && -n ${belt} ]]; then
  echo "lint:belt-current: mise ${have}; the belt is the pinned ${sha}${version:+ (v${version})}"
elif [[ -n ${belt} ]]; then
  echo "lint:belt-current: mise ${have}; the belt is this tree, the canon"
else
  echo "lint:belt-current: mise ${have} is current; no belt pin to compare here"
fi
