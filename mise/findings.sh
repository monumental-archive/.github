# shellcheck shell=bash
# The shell writer of findings (#1890). Sourced, never run:
# `. "<canon tree>/mise/findings.sh"`. mise/findings.py is the Python
# writer; docs/findings.md is the rule both serve.
#
# A finding is printed for the person reading the log, and recorded for
# the report that reads the run (mise/standing_issue.py): one JSON
# object on one line,
#
#   {"v": 1, "task": …, "subject": …, "message": …, "detail": […]}
#
# appended to the file ORG_FINDINGS names. Unset or empty, nothing is
# recorded. The record is encoded by the belt's jq, never by hand.
#
#   org_finding <subject> <message> [<detail>…]
#   org_finding_annotated <subject> <message> [<detail>…]
#
# Each prints to stderr `<task>: <subject>: <message>` (or `<task>:
# <message>` with an empty subject), then each detail line indented two
# spaces. The annotated form prints that first line as an error
# annotation instead, through the canon's workflow-command writer. The
# record is the same.
#
# A record that cannot be appended is said in one more stderr line, and
# the call still returns 0: the finding was printed, and the report
# falls back to the step's output. The task's status is the task's.

# shellcheck source=mise/workflow-command.sh
. "$(dirname "${BASH_SOURCE[0]}")/workflow-command.sh"

# Append one record to ORG_FINDINGS. Says why on stderr when it cannot.
org__finding_record() {
  local task=$1 subject=$2 message=$3
  shift 3
  local file=${ORG_FINDINGS:-}
  [[ -n ${file} ]] || return 0
  local record
  if ! record=$(jq -cn \
    --arg task "${task}" --arg subject "${subject}" --arg message "${message}" \
    '{v: 1, task: $task, subject: $subject, message: $message, detail: $ARGS.positional}' \
    --args -- "$@" 2>&1); then
    org_say "${task}: the finding was not recorded in ${file}: jq: ${record}" >&2
    return 0
  fi
  local why
  if ! why=$(mkdir -p -- "$(dirname -- "${file}")" 2>&1 && printf '%s\n' "${record}" 2>&1 >> "${file}"); then
    org_say "${task}: the finding was not recorded in ${file}: ${why:-the append failed}" >&2
  fi
  return 0
}

org__finding() {
  local annotate=$1 subject=$2 message=$3
  shift 3
  # The task a finding belongs to: mise's name for it, or the running
  # script's file name where mise named none.
  local task=${MISE_TASK_NAME:-${0##*/}} head
  if [[ -n ${subject} ]]; then
    head="${task}: ${subject}: ${message}"
  else
    head="${task}: ${message}"
  fi
  if [[ ${annotate} == 1 ]]; then
    org_error "${head}" >&2
  else
    org_say "${head}" >&2
  fi
  local line
  for line in "$@"; do
    org_say "  ${line}" >&2
  done
  org__finding_record "${task}" "${subject}" "${message}" "$@"
}

org_finding() { org__finding 0 "$@"; }
org_finding_annotated() { org__finding 1 "$@"; }
