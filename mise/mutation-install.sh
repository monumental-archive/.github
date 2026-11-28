#!/usr/bin/env bash
# The mutation tools a gate row runs, installed by name (#1432): ci.yml's
# step, delivered so the step stays under lint:workflow-shell's ceiling.
# Org canon.
#
#   mutation-install.sh "<language> [<language>...]"
#
# One argument or several, each word a language.
# The belt pins cargo-mutants, gomu and cosmic-ray lazy, so the gate's
# bare `mise install` skips them. A `ci:mutants` row installs its own
# language's, from the lock, as repo-mutants.yml's jobs do. StrykerJS is
# the repository's, from its own tracked lockfile (#1358): pnpm's or
# npm's frozen install, and
# nothing where neither is tracked (the leg then names what is
# missing). Any other language is red.
set -euo pipefail

read -r -a languages <<< "$*"
[[ ${#languages[@]} -gt 0 ]] || {
  echo "mutation-install.sh: name at least one language" >&2
  exit 1
}
for language in "${languages[@]}"; do
  case "${language}" in
    rust) mise install cargo:cargo-mutants ;;
    go) mise install go:github.com/sivchari/gomu/cmd/gomu ;;
    python) mise install pipx:cosmic-ray ;;
    typescript)
      if [[ -f pnpm-lock.yaml ]]; then
        pnpm install --frozen-lockfile
      elif [[ -f package-lock.json ]]; then
        npm ci
      fi
      ;;
    *)
      echo "mutation-install.sh: no mutation tool for '${language}'" >&2
      exit 1
      ;;
  esac
done
