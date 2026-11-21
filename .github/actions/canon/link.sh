#!/usr/bin/env bash
# Link the canon tree this run resolved to .org-canon and name its ref.
#
# The canon action's one step (#1386). It ships beside action.yml and
# runs from the action's own path, because it is the step that makes
# the canon tree available: it cannot be a canon helper under mise/ or
# release/, and it stays shell because it runs before any tool is
# installed and must depend on nothing the runner does not carry (bash,
# coreutils, and git, which every runner image carries).
#
# The tree is the one `$/.github/actions/canon` was resolved from: this
# script's own directory, three levels down from the tree's root. The
# canon's repository is CANON_REPOSITORY, which action.yml passes in, so
# no organisation name is written here.
#
# Inputs: CANON_REPOSITORY, GITHUB_REPOSITORY, GITHUB_SHA,
# GITHUB_WORKSPACE, GITHUB_OUTPUT. Output: `ref`, the canon ref this run
# resolved.
# Side effect: when the workspace is in a git repository, `.org-canon`
# is listed in that clone's info/exclude.
set -euo pipefail
: "${CANON_REPOSITORY:?CANON_REPOSITORY must be set (action.yml passes the canon repository)}"
: "${GITHUB_REPOSITORY:?}"
: "${GITHUB_SHA:?}"
: "${GITHUB_WORKSPACE:?}"
: "${GITHUB_OUTPUT:?}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# A tree without the belt is refused before anything is read from it,
# so its one command is a literal and the path follows on a plain line.
if [[ ! -f "${root}/mise/config.toml" || ! -f "${root}/mise/workflow-command.sh" ]]; then
  echo "::error::the resolved canon tree has no mise/config.toml or mise/workflow-command.sh — not a canon tree"
  printf '  resolved: %q\n' "${root}"
  exit 1
fi
# The canon's one writer of workflow commands (#1660), in this tree.
# shellcheck source=mise/workflow-command.sh
. "${root}/mise/workflow-command.sh"
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
      org_error "${emsg}"
      exit 1
    fi
    ref="${GITHUB_SHA}"
    ;;
esac
ln -sfn "${root}" "${GITHUB_WORKSPACE}/.org-canon"
# The link is never part of the caller's tree, so git is told so where
# the link is made: an untracked path in a checkout is a modified tree
# to every tool that asks git (go stamps vcs.modified=true; #1944), and
# a step that unlinked it mid-job left the job two names for one tree.
# info/exclude is the clone's own, never committed; --git-path resolves
# it through a worktree to the common dir, and --show-prefix anchors the
# entry where the workspace sits in the repository. No repository here
# means nothing reads the link as a change, so there is nothing to
# write. safe.directory on the command line, so a container job whose
# workspace another uid owns is still read.
if prefix="$(git -c safe.directory='*' -C "${GITHUB_WORKSPACE}" rev-parse --show-prefix 2> /dev/null)"; then
  exclude="$(git -c safe.directory='*' -C "${GITHUB_WORKSPACE}" rev-parse --path-format=absolute --git-path info/exclude)"
  entry="/${prefix}.org-canon"
  if ! grep -qxF -- "${entry}" "${exclude}" 2> /dev/null; then
    mkdir -p -- "$(dirname -- "${exclude}")"
    printf '%s\n' "${entry}" >> "${exclude}"
  fi
fi
org_say "canon resolved: ${ref} at ${root}"
echo "ref=${ref}" >> "${GITHUB_OUTPUT}"
