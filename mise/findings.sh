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
# appended to the file the writer names (#1976): ORG_FINDINGS when it is
# set and not empty; unset on a runner (RUNNER_TEMP and GITHUB_JOB both
# set), ${RUNNER_TEMP}/findings/${GITHUB_JOB}-<16 lowercase hex>.jsonl,
# the hex drawn on the shell's first record and kept in a variable that
# is not exported, so each shell writes a file of its own. Empty, or
# unset off a runner, nothing is recorded. The record is encoded by the
# belt's jq, never by hand.
#
#   org_finding <subject> <message> [<detail>…]
#   org_finding_annotated <subject> <message> [<detail>…]
#   <program> | org_finding_lines
#   org_task <task>
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
# `org_task` states the task a script reports for, where mise named
# none (#1979): it exports MISE_TASK_NAME, so every child the script
# starts, a Python helper above all, records under the same name rather
# than its own file's. A name mise gave wins.
#
# It also loads `org_tracked` (mise/tracked.sh, #1986), the one producer
# of the tracked-file list, so every task body and every bash a body
# starts has it.
#
# Loading this file runs no command (#1934): under BASH_ENV every bash
# sources it, whatever its PATH holds, so the directory is found by
# parameter expansion alone. A path with no slash is in this directory.

org__findings_dir=${BASH_SOURCE[0]%/*}
[[ ${org__findings_dir} != "${BASH_SOURCE[0]}" ]] || org__findings_dir=.
# shellcheck source=mise/workflow-command.sh
. "${org__findings_dir:-}/workflow-command.sh"
# shellcheck source=mise/tracked.sh
. "${org__findings_dir:-}/tracked.sh"
unset org__findings_dir

# A belt that is not the one this tree pins answers for nothing (#1988).
# The belt's [env] sets ORG_BELT_REFUSED to one line when the belt came
# by the clone's link and names another pin's tree; empty otherwise.
# Checked first, once the writer is loaded (its org_say is builtins
# only, and every line of outside text reaches the log through it), and
# with builtins only: BASH_ENV sources this file before every task body,
# so this is every belt task's refusal, ci included.
if [[ -n ${ORG_BELT_REFUSED:-} ]]; then
  org_say "${ORG_BELT_REFUSED}" >&2
  exit 1
fi

# Append one record to the writer's file (#1976). Says why on stderr
# when it cannot.
org__finding_record() {
  local task=$1 subject=$2 message=$3
  shift 3
  local file
  if [[ -n ${ORG_FINDINGS+set} ]]; then
    file=${ORG_FINDINGS}
  elif [[ -n ${RUNNER_TEMP:-} && -n ${GITHUB_JOB:-} ]]; then
    if [[ -z ${org__findings_hex:-} ]]; then
      org__findings_hex=$(od -An -N8 -tx1 /dev/urandom 2>&1 | tr -d ' \n') || true
    fi
    if [[ ! ${org__findings_hex} =~ ^[0-9a-f]{16}$ ]]; then
      org_say "${task}: the finding was not recorded: no file suffix: ${org__findings_hex}" >&2
      org__findings_hex=
      return 0
    fi
    file=${RUNNER_TEMP%/}/findings/${GITHUB_JOB}-${org__findings_hex}.jsonl
  fi
  [[ -n ${file:-} ]] || return 0
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

org_task() { export MISE_TASK_NAME="${MISE_TASK_NAME:-$1}"; }

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
