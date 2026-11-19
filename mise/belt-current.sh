#!/usr/bin/env bash
# Is this machine's toolchain the one CI will judge this repository with?
# Called by lint:belt-current; the reasoning lives on that task.
#
# Both comparisons are DERIVED from files already on disk — the mise
# version from the canon's own ci.yml, the pinned sha from the gate
# stub's `uses:` line — so there is no second literal to keep in step and
# nothing here for Renovate to bump.
set -euo pipefail

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
  echo "belt-current: ${ci} is missing, so the mise version CI runs cannot" >&2
  echo "  be read. The belt's canon tree is incomplete." >&2
  exit 1
fi
want=$(awk '/uses: jdx\/mise-action@/ { seen = 1 }
  seen && /^[[:space:]]+version:/ { print $2; exit }' "${ci}")
if [[ -z ${want} ]]; then
  echo "belt-current: no mise-action version pin found in ${ci}" >&2
  echo "  (lint:installer-pinned requires one), so there is nothing to compare." >&2
  exit 1
fi
have=$(mise --version 2> /dev/null | sed -n '1p' | cut -d ' ' -f 1)
if [[ -z ${have} ]]; then
  echo "belt-current: could not read this machine's mise version" >&2
  exit 1
fi
# shellcheck disable=SC2310  # older's status is the answer, not an error
if older "${have}" "${want}"; then
  echo "belt-current: this machine runs mise ${have}; CI runs ${want}." >&2
  echo "  mise keeps the tools current and never updates itself, and an older" >&2
  echo "  mise reads the lockfile and the registry differently. Run:" >&2
  echo "    mise self-update ${want}" >&2
  status=1
elif [[ ${have} != "${want}" ]]; then
  echo "belt-current: note — mise ${have} here is newer than CI's ${want}"
fi

# 2. The belt, against what this repository pins (#1686). A clone loads
# the belt of its own pin through `.config/mise/conf.d/belt.toml`, which
# the org hooks point at the machine's cache tree of exactly the pinned
# sha. So the belt answering is either that tree or wrong: older and
# newer are one red. The canon has no pin; there the belt is the tree.
# In CI the canon action delivers the belt at the pin's own resolution,
# so this half is a LOCAL control and is silent there.
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
if [[ ${GITHUB_ACTIONS:-} == true ]]; then
  belt="the canon action's"
elif [[ -n ${sha} ]]; then
  want="${XDG_CACHE_HOME:-${HOME}/.cache}/org-belt/${sha}"
  belt=$(cd -P "${want}" 2> /dev/null && pwd || echo "${want}")
  if [[ ${here} != "${belt}" ]]; then
    echo "belt-current: the belt answering is ${canon}; this repository's gate" >&2
    echo "  pins ${sha}${version:+ (v${version})}, whose belt is ${want}." >&2
    if [[ ! -L .config/mise/conf.d/belt.toml ]]; then
      echo "  This clone has no .config/mise/conf.d/belt.toml link. Run:" >&2
    else
      echo "  This clone's link names another tree. Run:" >&2
    fi
    echo "    lefthook run post-checkout" >&2
    status=1
  fi
elif git ls-files --error-unmatch security/identity.toml > /dev/null 2>&1; then
  belt=$(cd -P "${top}" && pwd)
  if [[ ${here} != "${belt}" ]]; then
    echo "belt-current: this is the canon, and the belt answering is ${canon}," >&2
    echo "  not this tree (${top}). The tree is its own belt: unset" >&2
    echo "  MISE_GLOBAL_CONFIG_FILE, which is the one carrier ranked above it." >&2
    status=1
  fi
fi

[[ ${status} -eq 0 ]] || exit 1
if [[ ${GITHUB_ACTIONS:-} == true ]]; then
  echo "lint:belt-current: mise ${have}; in CI the canon action delivers the belt at the pin"
elif [[ -n ${sha} && -n ${belt} ]]; then
  echo "lint:belt-current: mise ${have}; the belt is the pinned ${sha}${version:+ (v${version})}"
elif [[ -n ${belt} ]]; then
  echo "lint:belt-current: mise ${have}; the belt is this tree, the canon"
else
  echo "lint:belt-current: mise ${have} is current; no belt pin to compare here"
fi
