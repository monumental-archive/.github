# shellcheck shell=bash
# The one writer of GitHub workflow commands in the canon's shell (#1660).
# Sourced, never run: `. "<canon tree>/mise/workflow-command.sh"`.
#
# The runner reads a workflow command to the end of its line. A script
# that echoed `::error::${x}` with text it did not write (a manifest
# field, a tag, a file name, a registry's answer) let a newline in that
# text end the command and begin a line of the text's author's choosing,
# itself a workflow command if they liked. mise/workflow_command.py is
# the Python writer; this is the same writer for bash, escaping exactly
# as the runner unescapes: `%`, CR and LF as `%25`, `%0D` and `%0A`.
#
# A site whose message is a literal with no expansion may echo it as it
# is; a site that expands anything goes through these functions
# (mise/test_workflow_command_sh.py holds that law).
#
# Each function prints one line to stdout; redirect the call (`>&2`)
# for a site that writes to stderr. The message is every argument,
# joined by one space, as `echo` joins them.
#
# THE ESCAPE IS `${s//\%/%25}`, NEVER `${s//%/%25}`: an unquoted `%`
# after `//` is read by bash 3.2 as the end anchor, and the second form
# appends `%25` instead of replacing a single `%` (measured on macOS's
# /bin/bash 3.2.57).

org__workflow_command() {
  local name=$1
  shift
  local text="$*"
  text=${text//\%/%25}
  text=${text//$'\r'/%0D}
  text=${text//$'\n'/%0A}
  printf '::%s::%s\n' "${name}" "${text}"
}

org_error() { org__workflow_command error "$@"; }
org_warning() { org__workflow_command warning "$@"; }
org_notice() { org__workflow_command notice "$@"; }
# Unescaped, a value with a newline was masked only up to it.
org_mask() { org__workflow_command add-mask "$@"; }
org_group() { org__workflow_command group "$@"; }
org_endgroup() { printf '::endgroup::\n'; }
