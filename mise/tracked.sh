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
#   <array>=() && org_tracked_read <array> [<pathspec>…]
#
# Fills the named array with the same list, one name per element, and
# returns 1 when git fails. A list used once is piped:
# `org_tracked … | xargs -0 <tool>`. A list a task needs twice, or whose
# emptiness it decides, is read: the list goes to a file by a plain
# command, so git's status is the function's, then is read back with
# `read -r -d ''`, and the file is removed either way. Never
# `mapfile -d ''` (bash 3.2 has none) and never `< <(org_tracked …)`:
# the process substitution hides a failed git behind an empty list
# (SC2312), and the task would print "skipped" and pass. The caller
# assigns the array first, so shellcheck sees it set, and the `&&` keeps
# errexit on the call. Decide an empty list from the list, never from
# xargs: BSD xargs runs nothing on empty input, GNU xargs runs the tool
# once with no arguments.
#
#   org_tracked_has <path>
#
# Answers whether git tracks <path> (#2013): 0 when it does, 1 when it
# does not, whether or not the file is on disk. A file on disk that git
# does not track (a tool's output, a scratch file with a meaningful
# name) is absent, and a tracked file deleted from the working tree is
# present: a task decides from the repository, never the directory. Git
# tracks no directory, so a directory is tested by the list under it
# (`org_tracked_read` and its emptiness). On any other git status (not a
# repository, a broken index) it writes the finding `git could not say
# whether <path> is tracked (exit <n>)` and exits the shell with 1, so
# no caller can read a failed git as "absent" and print "skipped". It is
# called as a command (`if org_tracked_has X; then`,
# `org_tracked_has X || {`), never inside `$( )`, where the exit would
# leave only the subshell.
#
# Loading this file runs no command: function definitions only.

org_tracked() {
  git -c core.quotePath=false ls-files -z -- "$@"
}

org_tracked_read() {
  local org__into=$1 org__list org__i=0
  shift
  org__list=$(mktemp) || return 1
  if ! org_tracked "$@" > "${org__list}"; then
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

org_tracked_has() {
  local org__status=0
  git ls-files --error-unmatch -- "$1" > /dev/null 2>&1 || org__status=$?
  case ${org__status} in
    0 | 1) return "${org__status}" ;;
    *)
      # The writer is loaded wherever findings.sh is (BASH_ENV); a
      # script that sources this file alone still says why.
      if declare -F org_finding > /dev/null; then
        org_finding "" "git could not say whether $1 is tracked (exit ${org__status})"
      else
        printf '%s\n' "git could not say whether $1 is tracked (exit ${org__status})" >&2
      fi
      exit 1
      ;;
  esac
}
