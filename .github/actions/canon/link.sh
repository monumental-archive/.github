#!/usr/bin/env bash
# Link the canon tree this run resolved to .org-canon and name its ref.
#
# The canon action's one step (#1386). It ships beside action.yml and
# runs from the action's own path, because it is the step that makes
# the canon tree available: it cannot be a canon helper under mise/ or
# release/, and it stays shell because it runs before any tool is
# installed and must depend on nothing.
#
# The tree is the one `$/.github/actions/canon` was resolved from: this
# script's own directory, three levels down from the tree's root. The
# canon's repository is CANON_REPOSITORY, which action.yml passes in, so
# no organisation name is written here.
#
# Inputs: CANON_REPOSITORY, GITHUB_REPOSITORY, GITHUB_SHA,
# GITHUB_WORKSPACE, GITHUB_OUTPUT. Output: `ref`, the canon ref this run
# resolved.
set -euo pipefail
: "${CANON_REPOSITORY:?CANON_REPOSITORY must be set (action.yml passes the canon repository)}"
: "${GITHUB_REPOSITORY:?}"
: "${GITHUB_SHA:?}"
: "${GITHUB_WORKSPACE:?}"
: "${GITHUB_OUTPUT:?}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
case "${root}" in
  */_actions/"${CANON_REPOSITORY}"/*)
    # Remote resolution: the path segment under _actions is the ref the
    # caller pinned, verbatim.
    ref="$(basename "${root}")"
    ;;
  *)
    # Local resolution: the canon running itself, where the tree is the
    # running commit by definition. Anything else here is a resolution
    # shape this action does not understand — refuse rather than guess.
    if [[ ${GITHUB_REPOSITORY} != "${CANON_REPOSITORY}" ]]; then
      emsg="canon action resolved to '${root}' in ${GITHUB_REPOSITORY} — not an _actions path and not a canon"
      emsg+=" self-call"
      echo "::error::${emsg}"
      exit 1
    fi
    ref="${GITHUB_SHA}"
    ;;
esac
if [[ ! -f "${root}/mise/config.toml" ]]; then
  echo "::error::resolved canon tree at '${root}' has no mise/config.toml — not a canon tree"
  exit 1
fi
ln -sfn "${root}" "${GITHUB_WORKSPACE}/.org-canon"
echo "canon resolved: ${ref} at ${root}"
echo "ref=${ref}" >> "${GITHUB_OUTPUT}"
