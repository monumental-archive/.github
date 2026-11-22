# shellcheck shell=bash
# The one producer of the tracked-file list (#1986). Sourced, never run:
# mise/findings.sh sources it, so BASH_ENV delivers it to every task
# body and to every bash a body starts. mise/tracked.py is the same
# producer for Python.
#
#   org_tracked [<pathspec>…]
#
# Prints the tracked files the pathspecs match, git's default pathspec
# meaning, NUL-separated and never quoted: a name with a space, a
# newline or a non-ASCII letter is one entry, spelled as on disk. No
# pathspec is the whole index. A linter reads exactly these files and
# never walks the tree itself, so an untracked file (a tool's output, a
# scratch note) never reaches a verdict.
#
# Read it NUL-safely: `org_tracked … | xargs -0 <tool>`, or into an
# array with `while IFS= read -r -d '' f; do a+=("${f}"); done <
# <(org_tracked …)` (bash 3.2 has no `mapfile -d`). Decide an empty list
# from the list, never from xargs: BSD xargs runs nothing on empty
# input, GNU xargs runs the tool once with no arguments.
#
# Loading this file runs no command: a function definition only.

org_tracked() {
  git -c core.quotePath=false ls-files -z -- "$@"
}
