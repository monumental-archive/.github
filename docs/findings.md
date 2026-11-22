# Findings

A belt check that finds something wrong reports it as a **finding**: a
record written where the finding is known, by one of two writers. A
scheduled run keeps one red-check issue per red job (#1966), titled
`red: <job> (<workflow>)`, and writes each from those records.
Nothing reads a finding back out of a log.

## The rule

1. **A check reports a finding through a writer**, never by printing
   it alone. Shell sources `mise/findings.sh` and calls
   `org_finding <subject> <message> [<detail>…]`; Python imports
   `mise/findings.py` and calls `finding(subject, message, detail)`.
   Nothing else writes a record.
2. **Each red job's issue is written from records.** The report
   (`mise/standing_issue.py --findings <dir>`) reads every `*.jsonl`
   in the directory. A red job's issue lists the findings whose task
   one of its failing steps ran: named after `mise run` in the step's
   name, in a `[<task>]` prefix of its output, or the file name of a
   `.py` or `.sh` script the step's name runs. A finding whose task no
   red job's failing step ran is listed in every red job's issue,
   under `## Recorded outside this job's failing step`. Each task's
   findings are listed new apart from those still red since the
   issue's last body.
3. **A red leg always shows its failing step.** A leg whose step ran a
   task with a record points at its findings above the output; any
   other says no finding was recorded. The output is shown either way:
   a tool's own report beside its finding, or a stranger's own audit
   task that reported nothing through a writer, is otherwise in no
   issue. Legs with the same output share one excerpt.
4. **A fact is reported once** (#1967). Either its job is red and the
   red-check issue carries it as findings, or a filer carries it in
   its own issue and its job is green. Never both. The signer-identity
   watch takes the first form: its `gaining` job is red when a run did
   not gain on the log, with one finding, and keeps no issue of its
   own. A security release takes the second: it files its own issue
   and its job is green.

## How the writer reaches a task

A belt task body never sources `mise/findings.sh`. The belt's `[env]`
names it in `BASH_ENV`, beside the belt (`${ORG_BELT_DIR}/findings.sh`),
and bash sources that file before it runs any non-interactive script.
So every task body under the belt's shell (`bash --norc -euo pipefail -c`),
and every bash a body starts, can call `org_finding` and
`org_finding_annotated`. It holds locally through the clone's belt
link and in CI through `MISE_GLOBAL_CONFIG_FILE`, as `ORG_BELT_DIR`
does. With no belt, `BASH_ENV` is empty and nothing is sourced. A
`sh` script never reads it.

A test that runs a body by hand gets the shell and the variables
mise gives it from `hermetic.task_run`. Loading the writer runs no
command, so a bash whose `PATH` holds nothing still has it. The cost,
accepted by the owner (#1934): a fault in `findings.sh` or
`workflow-command.sh` reaches every bash under the belt's environment.

## A program that cannot call the writer

An awk program, or any program that is not shell, prints its findings
to stdout and pipes them into `org_finding_lines`: one finding per
line, its fields the subject, the message and each detail line,
separated by the unit separator (`\037`), never a tab, since bash's
`read` collapses empty tab-separated fields. Each line is one
`org_finding` call. A line with no separator is not a finding and goes
to stdout as a plain line (`org_say`), so the program's own summary
still prints. Under
`pipefail` the program's status is the pipeline's.

## The record

One JSON object on one line:

```json
{"v": 1, "task": "audit:deny", "subject": "chrono@0.4.45", "message": "RUSTSEC-2026-0543 is undecided", "detail": ["reached through object_store 0.14.2"]}
```

| Key | What it holds |
| --- | --- |
| `v` | The record's version, `1`. |
| `task` | `MISE_TASK_NAME`, which a child of a mise task inherits. Where mise named none: `GITHUB_JOB` when the running script is a workflow step's own (a file directly under `RUNNER_TEMP`, named by a guid), else the running script's file name. |
| `subject` | What the finding is about: a file, a repository, a crate, an advisory. May be empty. |
| `message` | One line. |
| `detail` | A list of lines. May be empty. |

A finding's identity is its task, subject and message. The report keeps
a short hash of the identity of each finding an issue shows in its
body, per issue. A finding listed in that issue's previous body reads
as still red; any other is new. So a finding the body's size limit
cut is new when a later run first shows it.

## Where records go

`ORG_FINDINGS` names the file. Unset or empty, a writer prints the
finding and records nothing, so a check run by hand behaves as it
always has.

On a runner the belt's `[env]` names it for every `mise run`:
`${RUNNER_TEMP}/findings/<job>-<16 random hex>.jsonl`, unless the
caller set one. Each job that runs a belt task uploads that directory,
and the report job downloads every job's into one (#1933). Off a
runner it is empty. Set, the writer creates the file's directory and appends
one line per finding.

A record that cannot be appended is said in one more line on stderr
and does not change the check's status: the finding was printed, and
the issue shows it in the step's output.

## What a writer prints

To stderr, the form the belt already used by hand:

```text
audit:deny: chrono@0.4.45: RUSTSEC-2026-0543 is undecided
  reached through object_store 0.14.2
```

`<task>: <message>` when the subject is empty; each detail line
indented two spaces. A site that annotates calls the annotated entry
(`org_finding_annotated`, `finding(..., annotate=True)`), which prints
that first line as an error annotation through the canon's
workflow-command writer instead. The record is the same.

Annotations are a display, never the record: the runner keeps ten of
each type per step and cuts a message at 4,096 characters.

## Why a record, not the log

The belt's lines already share a shape, but nothing holds it: the
Python helpers speak in their own names, stele in its own, and cargo's
`error:` lines match the same shape. A finding's fields are known only
where the finding is made, so that is where they are written down.
