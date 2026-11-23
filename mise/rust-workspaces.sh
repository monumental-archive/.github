# shellcheck shell=bash
# The tree's Rust workspace roots, for a task body (#2004). Sourced,
# never run: `. "${ORG_BELT_DIR}/rust-workspaces.sh"`.
#
#   <array>=() && org_rust_workspace_roots <array>
#
# Fills the named array with mise/rust_workspaces.py's roots: one
# manifest path per workspace in the tree, relative to its top, the root
# Cargo.toml first when there is one and the rest sorted. A task that
# runs cargo runs it once per element, `--manifest-path "${root}"`.
#
# The org_tracked_read form (mise/tracked.sh): the list goes to a file
# by a plain command, so the helper's status is the function's, then is
# read back with `read -r -d ''`, and the file is removed either way.
# The function returns 1 when the helper fails, its findings already
# printed: never `< <(…)`, which would hide a failure behind an empty
# list and let the task print "skipped" and pass.
#
# Loading this file runs no command: function definitions only.

org_rust_workspace_roots() {
  local org__into=$1 org__list org__i=0
  org__list=$(mktemp) || return 1
  if ! "${ORG_PYTHON:?${ORG_PYTHON_UNSET:-unset}}" \
    "${ORG_BELT_DIR:?unset}/rust_workspaces.py" roots > "${org__list}"; then
    rm -- "${org__list}"
    return 1
  fi
  while IFS= read -r -d '' "${org__into}[org__i]"; do
    org__i=$((org__i + 1))
  done < "${org__list}"
  # The read that met the end set one more, empty, element.
  unset "${org__into}[org__i]"
  rm -- "${org__list}"
}
