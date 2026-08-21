#!/usr/bin/env bash
# Run committed against the org's delivered rules, composed with the
# repo's own scope list. Org canon — the ONE place committed is invoked.
#
# Two callers (lint:commits over a branch range, commits:check over a
# single message file), so this is a script rather than a task body: the
# composition has one definition, and as an ordinary .sh it is linted and
# formatted like every other (the derive-badges.sh precedent, #82).
#
# WHY COMPOSE AT ALL. mise/committed.toml is delivered, and `--config`
# REPLACES a repo-local committed.toml rather than merging with it
# (measured on 1.1.11). A repo's `allowed_scopes` is repo identity — the
# .golangci.yml module-path class — so it cannot ride in the delivered
# file, and it cannot sit in a repo-local committed.toml either, because
# a partial config there would silently become the WHOLE config for any
# bare `committed` invocation. It is declared instead as ORG_COMMIT_SCOPES
# in the repo's mise.toml [env], the FUZZ_TOOLCHAIN pattern, and spliced
# in here. Repos that restrict no scopes set nothing and lose nothing.
#
# Arguments are passed through to committed verbatim.
set -euo pipefail

if [[ -z ${ORG_BELT_DIR:-} || ! -f "${ORG_BELT_DIR}/committed.toml" ]]; then
  echo "committed.sh: ORG_BELT_DIR is unset or carries no committed.toml" >&2
  echo "  the org belt did not arrive; CI sets MISE_GLOBAL_CONFIG_FILE," >&2
  echo "  locally it is a ~/.config/mise/conf.d symlink into a canon checkout" >&2
  exit 1
fi

cfg="$(mktemp)"
# Not `exec committed`: that would replace this shell and the trap would
# never fire, leaking a config file per commit on every developer machine.
trap 'rm -f "${cfg}"' EXIT
cat "${ORG_BELT_DIR}/committed.toml" > "${cfg}"

if [[ -n ${ORG_COMMIT_SCOPES:-} ]]; then
  {
    echo
    echo "# Composed from ORG_COMMIT_SCOPES; declared in this repo's mise.toml."
    echo "allowed_scopes = ["
  } >> "${cfg}"
  IFS=',' read -r -a scopes <<< "${ORG_COMMIT_SCOPES}"
  for scope in "${scopes[@]}"; do
    scope="${scope#"${scope%%[![:space:]]*}"}"
    scope="${scope%"${scope##*[![:space:]]}"}"
    [[ -n ${scope} ]] || continue
    # Fail closed on anything that is not a bare scope token: a stray
    # quote or bracket would corrupt the TOML, and committed reports a
    # parse error rather than the scope the repo meant to enforce.
    if [[ ! ${scope} =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
      echo "committed.sh: ORG_COMMIT_SCOPES holds \"${scope}\", which is not a scope" >&2
      echo '  expected a comma-separated list of lowercase names, e.g. "derive,emit"' >&2
      exit 1
    fi
    echo "  \"${scope}\"," >> "${cfg}"
  done
  echo "]" >> "${cfg}"
fi

status=0
committed --config "${cfg}" "$@" || status=$?
exit "${status}"
