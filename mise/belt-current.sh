#!/usr/bin/env bash
# Is this machine's toolchain as current as the one CI will judge with?
# Called by lint:belt-current; the reasoning lives on that task.
#
# Both comparisons are DERIVED from files already on disk — the mise
# version from the canon's own ci.yml, the canon version from the gate
# stub's pin comment — so there is no second literal to keep in step and
# nothing here for Renovate to bump.
set -euo pipefail

canon="${ORG_CANON_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: conf.d symlink)}"
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

# 2. The belt checkout, against what this repository pins. Every repo on
# the machine reads ONE canon checkout through the conf.d symlink, so a
# checkout left behind lints all of them with old tools. The canon calls
# its own gate with a local `uses:` and has no pin to compare, so this
# half is silent there.
pinned=""
if compgen -G ".github/workflows/*.y*ml" > /dev/null; then
  stub_re='uses:[[:space:]]*[^[:space:]]*/\.github/workflows/ci\.ya\{0,1\}ml@[0-9a-f]\{40\}'
  pinned=$(cat .github/workflows/*.y*ml \
    | sed -n "s|.*${stub_re}[[:space:]]*#[[:space:]]*v\([0-9][0-9.]*\).*|\1|p" \
    | sed -n '1p')
fi
if [[ -n ${pinned} ]]; then
  cff="${canon}/CITATION.cff"
  belt=""
  [[ ! -f ${cff} ]] || belt=$(sed -n 's/^version:[[:space:]]*\([0-9][0-9.]*\).*/\1/p' "${cff}" | sed -n '1p')
  behind=0
  # shellcheck disable=SC2310  # older's status is the answer, not an error
  if [[ -n ${belt} ]] && older "${belt}" "${pinned}"; then
    behind=1
  fi
  if [[ -z ${belt} ]]; then
    echo "belt-current: cannot read the belt's own version from ${cff}" >&2
    status=1
  elif [[ ${behind} -eq 1 ]]; then
    echo "belt-current: this machine's belt is canon v${belt}; this repository's" >&2
    echo "  gate is pinned to v${pinned}, so CI judges with newer tools than" >&2
    echo "  this run did. Update the checkout the belt is read from:" >&2
    echo "    git -C ${canon} pull --ff-only" >&2
    status=1
  fi
fi

[[ ${status} -eq 0 ]] || exit 1
if [[ -n ${pinned} ]]; then
  echo "lint:belt-current: mise ${have} and the belt cover this repository's pin (v${pinned})"
else
  echo "lint:belt-current: mise ${have} is current; no canon pin in this tree to compare"
fi
