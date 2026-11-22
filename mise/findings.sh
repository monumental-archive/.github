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
#   <program> | org_finding_lines
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
#
# `org_finding_lines` is for a program that cannot call a shell
# function, an awk program above all (#1934): it prints one finding per
# line to stdout, piped in. Its fields are the subject, the message, then
# each detail line, separated by the unit separator (\037), never a tab:
# bash's `read` collapses empty tab-separated fields, and an empty
# subject is a field. Each line is one `org_finding` call. A line with no
# separator is not a finding and goes to stdout through `org_say`, so the
# program's own summary still prints and cannot open a workflow command.
# It returns 0; under pipefail the program's status is the pipeline's.
#
# Loading this file runs no command (#1934): under BASH_ENV every bash
# sources it, whatever its PATH holds, so the directory is found by
# parameter expansion alone. A path with no slash is in this directory.

# A belt that is not the one this tree pins answers for nothing (#1988).
# The belt's [env] sets ORG_BELT_REFUSED to one line when the belt came
# by the clone's link and names another pin's tree; empty otherwise.
# Checked first, before this file loads anything, and with builtins
# only: BASH_ENV sources this file before every task body, so this is
# every belt task's refusal, ci included.
if [[ -n ${ORG_BELT_REFUSED:-} ]]; then
  printf '%s\n' "${ORG_BELT_REFUSED}" >&2
  exit 1
fi

org__findings_dir=${BASH_SOURCE[0]%/*}
[[ ${org__findings_dir} != "${BASH_SOURCE[0]}" ]] || org__findings_dir=.
# shellcheck source=mise/workflow-command.sh
. "${org__findings_dir:-}/workflow-command.sh"
unset org__findings_dir

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
  # The task a finding belongs to: mise's name for it; where mise named
  # none, the job's when the running script is the runner's step script
  # (a file directly under RUNNER_TEMP, named by a random guid, #1934);
  # else the running script's file name.
  local task=${MISE_TASK_NAME:-} head
  if [[ -z ${task} ]]; then
    if [[ -n ${GITHUB_JOB:-} && -n ${RUNNER_TEMP:-} && ${0%/*} == "${RUNNER_TEMP%/}" ]]; then
      task=${GITHUB_JOB}
    else
      task=${0##*/}
    fi
  fi
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

org_finding_lines() {
  local line
  local -a field
  while IFS= read -r line || [[ -n ${line} ]]; do
    if [[ ${line} != *$'\037'* ]]; then
      org_say "${line}"
      continue
    fi
    IFS=$'\037' read -r -a field <<< "${line}"
    org_finding "${field[0]-}" "${field[1]-}" "${field[@]:2}"
  done
  return 0
}
