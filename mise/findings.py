#!/usr/bin/env python3
r"""The Python writer of findings (#1890).

mise/findings.sh is the shell writer; docs/findings.md is the rule both
serve. A finding is printed for the person reading the log, and
recorded for the report that reads the run (mise/standing_issue.py):
one JSON object on one line,

    {"v": 1, "task": …, "subject": …, "message": …, "detail": […]}

appended to the file `ORG_FINDINGS` names. Unset or empty, nothing is
recorded.

`finding(subject, message, detail)` prints to stderr `<task>: <subject>:
<message>` (or `<task>: <message>` with an empty subject), then each
detail line indented two spaces. With `annotate=True` the first line is
an error annotation instead, through mise/workflow_command.py. The
record is the same.

`task` is `MISE_TASK_NAME`, which a Python child of a mise task
inherits. Where mise named none, it is `GITHUB_JOB` when the running
script is a workflow step's own (a file directly under `RUNNER_TEMP`),
else the running script's file name.

A character UTF-8 cannot carry (a lone surrogate, as `os.fsdecode`
makes of a file name that is not UTF-8) is printed and recorded as its
backslash escape, `\udcff`: the line and the record are the same text,
and the append cannot fail on an encoding (#1984).

A record that cannot be appended is said in one more stderr line and
raises nothing: the finding was printed, and the report falls back to
the step's output. The task's status is the task's.

A helper in another directory reaches this one as it reaches
workflow_command: `sys.path.append` of the sibling root, then
`import findings`.
"""

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import workflow_command

if TYPE_CHECKING:
    from collections.abc import Iterable

VERSION = 1


def task_name() -> str:
    """Name the task a finding belongs to.

    Returns:
        `MISE_TASK_NAME`; where mise named none, `GITHUB_JOB` when the
        running script is a workflow step's own (a file directly under
        `RUNNER_TEMP`, named by a guid, #1934); else the running
        script's file name.

    """
    named = os.environ.get("MISE_TASK_NAME")
    if named:
        return named
    script = Path(sys.argv[0])
    job = os.environ.get("GITHUB_JOB")
    temp = os.environ.get("RUNNER_TEMP")
    if job and temp and script.parent == Path(temp):
        return job
    return script.name


def record(task: str, subject: str, message: str, detail: Iterable[str]) -> str:
    """Encode one finding's record.

    Returns:
        The record: one JSON object, one line.

    """
    lines: list[str] = list(detail)
    fields: dict[str, object] = {
        "v": VERSION,
        "task": task,
        "subject": subject,
        "message": message,
        "detail": lines,
    }
    return json.dumps(fields, ensure_ascii=False)


def _text(s: str) -> str:
    """Write each character UTF-8 cannot carry as its backslash escape.

    The text stderr prints (its error handler is `backslashreplace`),
    so the log line and the record are the same text (#1984).

    Returns:
        The text, every character encodable as UTF-8.

    """
    return s.encode("utf-8", "backslashreplace").decode("utf-8")


def _say(text: str) -> None:
    """Print one plain log line to stderr, as mise/workflow-command.sh's org_say.

    The runner reads a line whose first non-blank characters are `::`,
    and `##[` anywhere, as a command; CR and LF would begin a new line.
    So CR and LF are written as a backslash and `r` or `n`, a leading
    `::` gets a backslash before it, and `##[` gets one before its `[`.
    """
    text = text.replace("\r", "\\r").replace("\n", "\\n").replace("##[", "##\\[")
    body = text.lstrip()
    if body.startswith("::"):
        text = f"{text[: len(text) - len(body)]}\\{body}"
    print(text, file=sys.stderr, flush=True)


def _append(task: str, line: str) -> None:
    """Append a record to ORG_FINDINGS, saying on stderr when it cannot."""
    file = os.environ.get("ORG_FINDINGS", "")
    if not file:
        return
    path = Path(file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as out:
            out.write(f"{line}\n")
    except OSError as err:
        _say(f"{task}: the finding was not recorded in {file}: {err}")


def finding(
    subject: str,
    message: str,
    detail: Iterable[str] = (),
    *,
    annotate: bool = False,
) -> None:
    """Print one finding and record it."""
    task = _text(task_name())
    subject = _text(subject)
    message = _text(message)
    lines = [_text(line) for line in detail]
    head = f"{task}: {subject}: {message}" if subject else f"{task}: {message}"
    if annotate:
        workflow_command.error(head, stream=sys.stderr)
    else:
        _say(head)
    for line in lines:
        _say(f"  {line}")
    _append(task, record(task, subject, message, lines))
