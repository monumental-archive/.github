#!/usr/bin/env bash
# A `ci:mutants` row's tool, by name (#1432): ci.yml's step, delivered
# so the step stays under lint:workflow-shell's ceiling. Org canon.
#
#   mutation-install.sh <language>
#
# The belt pins cargo-mutants, gomu and cosmic-ray lazy, so the gate's
# bare `mise install` skips them; a mutation row installs its own
# language's, from the lock, as repo-mutants.yml's jobs do. StrykerJS
# is the repository's, from its own tracked lockfile (#1358): pnpm's or
# npm's frozen install, and nothing where neither lockfile is tracked
# (the leg then names what is missing). Any other language is red.
set -euo pipefail

case "${1:-}" in
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
    echo "mutation-install.sh: no mutation tool for '${1:-}'" >&2
    exit 1
    ;;
esac
