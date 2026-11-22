#!/usr/bin/env python3
"""Keep exactly one standing issue, and report a scheduled run (#1887).

Two halves. The first is the one implementation of "keep exactly one
issue", proved by the signer-identity watch (#1353) and moved here so
there is no second copy: list every open issue, match the title
exactly (never a search's ranking), create it or replace its body in
place, close it with one comment. A listing that fails raises by name,
and the caller files nothing.

The second is the `report` job every scheduled workflow ends with. It
reads the run's own job list and keeps one issue per red job (#1966),
titled exactly `red: <job> (<workflow>)` and labelled `red-check`. A
job's name is its name in the list, a leading `<workflow> / ` dropped
(a reusable workflow's jobs are listed under their caller's job) and
one final ` (…)` dropped: jobs with the same name are one job's legs,
the form GitHub gives a matrix job by default. No job is named here;
the run's list is the only source.

  cancelled run                   nothing changes: the job's step that
                                  runs this is `if: !cancelled()`.
  run not on the default branch   nothing changes; gh is never called.
  a leg failed or timed out       a red job. No open issue with its
                                  title: create it; one open: replace
                                  its body, keeping "Red since".
  every leg green or skipped      the job is green: its open issue
                                  labelled red-check is closed, naming
                                  the run.
  otherwise (a leg cancelled, or  the job is neither; its issue is
  one not finished)               unchanged.
  an open issue labelled          its job is not in the run (renamed
  red-check, `red: <x> (<wf>)`,   or removed): closed, saying so.
  and no job <x> in the list
  an open `<workflow> is red`     the one issue a workflow kept before
                                  #1966: closed by the first report,
                                  naming the run.

The job list is the run's latest attempt. The report job is itself
unfinished while it reads it, so one unfinished job is expected; more
than one means a job outside this workflow's `needs:` is still running
(a reusable workflow's report, with a sibling job in its caller), and a
later report in the same run has the last word: red jobs are still
kept, and nothing is closed. A run is counted once per issue, by its
URL, however many reports in it write the body.

Each red leg's entry carries its failing step's output, whole (#1925),
found from the runner's own markers: the step is the last `##[group]Run `
before the first `##[error]` line, its output runs from after that
group's first `##[endgroup]` (the command and its environment) to the
next `##[group]Run `, a `Post job cleanup.` line or the end of the log.
An `##[error]` line is kept without its marker; the runner's own
`Process completed with exit code <n>.` is dropped. The entry names the
step. Output over 200 lines or 12,000 characters shows its end, whole
lines, under a line counting the earlier lines not shown. A log with no
`##[error]` line, or none after a `##[group]Run ` line, shows its last
60 lines, at most 4,000 characters. Each line drops its timestamp and
its colour codes. The log is the job-log endpoint's own answer, read
over HTTPS with GH_TOKEN and its redirect followed, the token on an
unredirected header so it never reaches the blob host; no gh is in the
log path (`gh run view --log` refuses while the run, this job's own,
is in progress, and `gh api` refuses a text body carrying escapes). A
log that cannot be read is named in its entry. Legs with the same
output are listed together over one excerpt. The excerpt is fenced so
no line in it can close the fence. The body stays under 60,000
characters: excerpts are dropped from the last one upwards, and the
body says so. The label is created from settings/repo-baseline.json's
definition when the repository does not have it.

With `--findings DIR` the body is also written from the run's findings
(#1890, docs/findings.md): every `*.jsonl` in DIR, one record per line
as mise/findings.sh and mise/findings.py append them. A line that is
not a record is counted, and the body says how many. A failing step
ran a task when the task is named after `mise run ` in the step's name,
in a `[<task>] ` prefix of its output, or is the file name of a `.py`
or `.sh` script the step's name runs (a record written outside mise
carries its script's file name). `## Findings` lists the findings
whose task one of this job's failing steps ran; a finding whose task
no red job's failing step ran is listed in every red job's issue,
under `## Recorded outside this job's failing step`. Each section
lists each task's findings, its name in order, those new in this run
apart from those still red: a finding is its task, subject and
message, and the body ends with a comment line carrying the first 16
hex characters of the SHA-256 of each finding the body shows, which the
next run reads back. New means not listed in this issue's previous
body: a finding the limit cut is new when a later run first shows it.
A red leg whose failing step ran a task with a record points at its
findings; any other says "no finding was recorded". Its output is shown
either way: a tool's own report beside a finding is otherwise in no
issue. Over the limit, step outputs go first, then detail blocks, then
findings from the end of the list, each said with how many. Without
the flag, or with no record and no unreadable line in DIR, the body is
as above.

Usage:
  standing_issue.py --workflow NAME --run-id ID --ref REF
                    [--default-branch BRANCH] [--findings DIR]

The repository is GITHUB_REPOSITORY, the server GITHUB_SERVER_URL, the
API GITHUB_API_URL (https://api.github.com when unset).
Exit 0 when the issues were kept (or nothing was to change), 2 when a
read or write failed. Stdlib only, and `gh` on PATH for every read but
the logs.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

import decoded
import typed_std

EXIT_ERROR = 2
LABEL = "red-check"
BASELINE = Path(__file__).resolve().parents[1] / "settings" / "repo-baseline.json"

RED = frozenset({"failure", "timed_out"})
GREEN = frozenset({"success", "skipped"})
TAIL_LINES = 60
TAIL_CHARS = 4000
STEP_LINES = 200
STEP_CHARS = 12000
LOG_TIMEOUT = 60
BODY_LIMIT = 60000
ERROR_MARK = "##[error]"
STEP_MARK = "##[group]Run "
GROUP_END = "##[endgroup]"
CLEANUP = "Post job cleanup."

RED_SINCE = re.compile(r"^- Red since: (.+)$", re.MULTILINE)
LATEST = re.compile(r"^- Latest red run: (.+)$", re.MULTILINE)
RED_RUNS = re.compile(r"^- Red runs: (\d+)$", re.MULTILINE)
TIMESTAMP = re.compile(r"^﻿?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z ?")
COLOUR = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
EXITED = re.compile(r"^Process completed with exit code \d+\.$")
BACKTICKS = re.compile(r"`+")
# The tasks a failing step ran: `mise run <task>` in its name, and the
# `[<task>] ` prefix mise writes before a task's own lines.
RUN_TASK = re.compile(r"(?:^|\s)mise run ([^\s;&|]+)")
TASK_PREFIX = re.compile(r"^\[([^\]\s]+)\] ")
# A script a failing step's name runs: a record written outside mise
# carries its file name as its task (#1890 item 1).
SCRIPT = re.compile(r"([^\s/\"';&|()]+\.(?:py|sh))(?=$|[\s\"';&|)])")
# One final ` (…)` of a listed job name: a matrix leg's values (#1966).
LEG = re.compile(r" \([^()]*\)$")
OLD_TITLE = "{workflow} is red"
# The last line of a body written from findings: each one's hash.
HASHES = re.compile(r"^<!-- findings: ([0-9a-f ]*) -->$", re.MULTILINE)
HASH_LENGTH = 16
RECORD_KEYS = frozenset({"v", "task", "subject", "message", "detail"})
RECORD_VERSION = 1


class StandingIssueError(Exception):
    """A read or write that could not complete; nothing more is filed."""


@dataclass(frozen=True)
class Issue:
    """An open issue with the standing title, as the forge lists it."""

    number: int
    body: str
    title: str = ""
    labels: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Job:
    """One job of the run, as the run's job list gives it."""

    id: int
    name: str
    status: str
    conclusion: str
    url: str


@dataclass(frozen=True)
class Excerpt:
    """What a red leg's entry shows of its log.

    `step` names the failing step; None when the log marks none, and
    the text is then the log's last lines. `tasks` are the tasks the
    failing step ran, read from its name and its whole output.
    """

    text: str
    step: str | None = None
    tasks: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Entry:
    """A red leg as the issue shows it: name, link, and its excerpt."""

    job: Job
    tail: Excerpt | None
    missing: str | None


@dataclass(frozen=True)
class Finding:
    """One record a check wrote through a findings writer (#1890)."""

    task: str
    subject: str
    message: str
    detail: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        """Hash the finding's identity: its task, subject and message.

        Returns:
            The first HASH_LENGTH hex characters of its SHA-256.

        """
        fields: list[str] = [self.task, self.subject, self.message]
        identity = json.dumps(
            fields,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(identity.encode()).hexdigest()[:HASH_LENGTH]


@dataclass(frozen=True)
class Findings:
    """Every record in a run's findings files, and the lines that were not."""

    records: tuple[Finding, ...]
    unreadable: int


class Args(argparse.Namespace):
    """The command line, typed."""

    workflow: str
    run_id: str
    ref: str
    default_branch: str
    findings: str | None


def gh(*args: str) -> str:
    """Run gh and return its output.

    Returns:
        gh's standard output.

    Raises:
        StandingIssueError: gh exited non-zero.

    """
    proc = subprocess.run(
        ["gh", *args],  # ruff: ignore[start-process-with-partial-path] - gh is resolved on PATH, as in the workflow
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        verb = " ".join(args[:2])
        detail = proc.stderr.strip() or "no output"
        msg = f"gh {verb} failed (exit {proc.returncode}): {detail}"
        raise StandingIssueError(msg)
    return proc.stdout


def open_issues(repo: str, title: str) -> list[Issue]:
    """List the open issues titled exactly `title`.

    Returns:
        Each open issue whose title is exactly `title`.

    Raises:
        StandingIssueError: the listing could not be read, or an issue in
            it has no number or a body that is not text.

    """
    out = gh(
        "issue", "list", "--repo", repo, "--state", "open",
        "--limit", "1000", "--json", "number,title,body",
    )  # fmt: skip
    try:
        answer = decoded.load_json(out)
    except decoded.UndecodableError as err:
        msg = f"gh issue list answered something that is not JSON: {err}"
        raise StandingIssueError(msg) from err
    try:
        return titled(answer, title)
    except decoded.UndecodableError as err:
        msg = f"gh issue list answered something that is not a list of issues: {err}"
        raise StandingIssueError(msg) from err


def titled(answer: object, title: str) -> list[Issue]:
    """Decode the listing's issues titled exactly `title`.

    Returns:
        Each one's number and body; a body the forge left empty is "".

    """
    found: list[Issue] = []
    for at, item in enumerate(decoded.as_array(answer, "the answer")):
        issue = decoded.as_object(item, f"issue {at}")
        if issue.get("title") != title:
            continue
        number = decoded.as_int(issue.get("number"), f"issue {at}: number")
        text = issue.get("body")
        text = "" if text is None else decoded.as_str(text, f"issue {at}: body")
        found.append(Issue(number, text))
    return found


def every_open(repo: str) -> list[Issue]:
    """List every open issue, with its title and labels.

    Returns:
        Each open issue the forge lists.

    Raises:
        StandingIssueError: the listing could not be read, or is not a
            list of issues.

    """
    out = gh(
        "issue", "list", "--repo", repo, "--state", "open",
        "--limit", "1000", "--json", "number,title,body,labels",
    )  # fmt: skip
    try:
        return [
            labelled(item, at)
            for at, item in enumerate(
                decoded.as_array(decoded.load_json(out), "the answer")
            )
        ]
    except decoded.UndecodableError as err:
        msg = f"gh issue list answered something that is not a list of issues: {err}"
        raise StandingIssueError(msg) from err


def labelled(item: object, at: int) -> Issue:
    """Decode one listed issue with its title and labels.

    Returns:
        The issue; a body the forge left empty is "".

    """
    issue = decoded.as_object(item, f"issue {at}")
    number = decoded.as_int(issue.get("number"), f"issue {at}: number")
    title = decoded.as_str(issue.get("title"), f"issue {at}: title")
    text = issue.get("body")
    text = "" if text is None else decoded.as_str(text, f"issue {at}: body")
    names = frozenset(
        decoded.as_str(
            decoded.as_object(label, f"issue {at}: label").get("name"),
            f"issue {at}: label name",
        )
        for label in decoded.as_array(issue.get("labels", []), f"issue {at}: labels")
    )
    return Issue(number, text, title, names)


def create(repo: str, title: str, body: str, labels: tuple[str, ...] = ()) -> None:
    """Open the standing issue."""
    flags = [arg for label in labels for arg in ("--label", label)]
    gh("issue", "create", "--repo", repo, "--title", title, "--body", body, *flags)


def replace(repo: str, number: int, body: str) -> None:
    """Replace the standing issue's body in place."""
    gh("issue", "edit", str(number), "--repo", repo, "--body", body)


def close(repo: str, number: int, comment: str) -> None:
    """Close the standing issue with one comment."""
    gh("issue", "close", str(number), "--repo", repo, "--comment", comment)


def one(found: list[Issue], title: str) -> Issue | None:
    """Take the one open standing issue, if there is one.

    Returns:
        The issue, or None when none is open.

    Raises:
        StandingIssueError: more than one is open; which to keep is a
            person's call, and a third is never filed.

    """
    if len(found) > 1:
        numbers = ", ".join(f"#{i.number}" for i in found)
        msg = f"{len(found)} open issues are titled exactly {title!r} ({numbers})"
        msg += ": close all but one"
        raise StandingIssueError(msg)
    return found[0] if found else None


def label_definition() -> dict[str, object]:
    """Read the red-check label from the settings baseline.

    Returns:
        The label's name, colour and description.

    Raises:
        StandingIssueError: the baseline does not define it.

    """
    try:
        doc = decoded.as_object(
            decoded.load_json(BASELINE.read_bytes()), "the baseline"
        )
        for at, item in enumerate(decoded.as_array(doc.get("labels"), "labels")):
            label = decoded.as_object(item, f"labels[{at}]")
            if label.get("name") == LABEL:
                return label
    except (OSError, ValueError) as err:
        msg = f"{BASELINE} could not be read: {err}"
        raise StandingIssueError(msg) from err
    msg = f"{BASELINE} defines no {LABEL!r} label"
    raise StandingIssueError(msg)


def ensure_label(repo: str) -> str:
    """Create the red-check label from the baseline when it is missing.

    Returns:
        What was done, for the run's log.

    Raises:
        StandingIssueError: the label list is not a list of labels.

    """
    out = gh("label", "list", "--repo", repo, "--limit", "1000", "--json", "name")
    try:
        names = [
            decoded.as_object(item, "label").get("name")
            for item in decoded.as_array(decoded.load_json(out), "the labels")
        ]
    except decoded.UndecodableError as err:
        msg = f"gh label list answered something that is not a list of labels: {err}"
        raise StandingIssueError(msg) from err
    if LABEL in names:
        return ""
    label = label_definition()
    colour = decoded.as_str(label.get("color"), "the label's color")
    description = decoded.as_str(label.get("description"), "the label's description")
    gh(
        "label",
        "create",
        LABEL,
        "--repo",
        repo,
        "--color",
        colour,
        "--description",
        description,
    )
    return f"created the {LABEL} label; "


def jobs(repo: str, run_id: str) -> list[Job]:
    """Read the run's job list, its latest attempt.

    Returns:
        Every job, matrix legs and a reusable workflow's jobs included.

    Raises:
        StandingIssueError: the list could not be read or decoded.

    """
    out = gh(
        "api", "--paginate", "--jq", ".jobs[]",
        f"repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100",
    )  # fmt: skip
    found: list[Job] = []
    try:
        for at, line in enumerate(out.splitlines()):
            if not line.strip():
                continue
            item = decoded.as_object(decoded.load_json(line), f"job {at}")
            conclusion = item.get("conclusion")
            found.append(
                Job(
                    id=decoded.as_int(item.get("id"), f"job {at}: id"),
                    name=decoded.as_str(item.get("name"), f"job {at}: name"),
                    status=decoded.as_str(item.get("status"), f"job {at}: status"),
                    conclusion=""
                    if conclusion is None
                    else decoded.as_str(conclusion, f"job {at}: conclusion"),
                    url=decoded.as_str(item.get("html_url"), f"job {at}: html_url"),
                )
            )
    except decoded.UndecodableError as err:
        msg = f"the run's job list is not a list of jobs: {err}"
        raise StandingIssueError(msg) from err
    return found


def cleaned(line: str) -> str:
    """Read one log line down to what the step wrote.

    Returns:
        The line without its timestamp and colour codes.

    """
    return COLOUR.sub("", TIMESTAMP.sub("", line))


def last_lines(lines: list[str]) -> Excerpt:
    """Keep a log's last lines, when it marks no failing step.

    Returns:
        The last TAIL_LINES lines, at most TAIL_CHARS characters, whole
        lines kept.

    """
    text = "\n".join(lines[-TAIL_LINES:])
    if len(text) > TAIL_CHARS:
        text = text[-TAIL_CHARS:]
        cut = text.find("\n")
        text = text[cut + 1 :] if cut >= 0 else text
    return Excerpt(text)


def step_output(lines: list[str]) -> list[str]:
    """Read a failing step's output as the step wrote it.

    Returns:
        Each line, an error line without its marker, the runner's own
        exit line dropped.

    """
    shown: list[str] = []
    for line in lines:
        if line.startswith(ERROR_MARK):
            said = line.removeprefix(ERROR_MARK)
            if EXITED.match(said):
                continue
            shown.append(said)
        else:
            shown.append(line)
    return shown


def ending(lines: list[str]) -> str:
    """Keep a step's output whole, or its end under a count of the rest.

    Returns:
        Every line when within STEP_LINES lines and STEP_CHARS
        characters; else the last whole lines within both, under a line
        saying how many earlier lines are not shown.

    """
    kept: list[str] = []
    size = 0
    for line in reversed(lines):
        grown = size + len(line) + (1 if kept else 0)
        if len(kept) >= STEP_LINES or grown > STEP_CHARS:
            break
        kept.append(line)
        size = grown
    kept.reverse()
    hidden = len(lines) - len(kept)
    if not hidden:
        return "\n".join(kept)
    return "\n".join([f"({hidden} earlier lines not shown)", *kept])


def tail(log: str) -> Excerpt:
    """Cut a job's log to what the issue shows.

    Returns:
        The failing step's output and its name, found from the runner's
        markers; the log's last lines when it marks no failing step.

    """
    lines = [cleaned(line) for line in log.splitlines()]
    first = next(
        (at for at, line in enumerate(lines) if line.startswith(ERROR_MARK)), None
    )
    if first is None:
        return last_lines(lines)
    start = next(
        (at for at in range(first - 1, -1, -1) if lines[at].startswith(STEP_MARK)),
        None,
    )
    if start is None:
        return last_lines(lines)
    header_end = next(
        (at for at in range(start + 1, first) if lines[at].startswith(GROUP_END)),
        start,
    )
    stop = next(
        (
            at
            for at in range(first, len(lines))
            if lines[at].startswith(STEP_MARK) or lines[at] == CLEANUP
        ),
        len(lines),
    )
    output = step_output(lines[header_end + 1 : stop])
    step = lines[start].removeprefix(STEP_MARK).strip()
    return Excerpt(ending(output), step, step_tasks(step, output))


def step_tasks(step: str, output: list[str]) -> frozenset[str]:
    """Name the tasks a failing step ran.

    Returns:
        Each task named after `mise run ` in the step's name, the file
        name of each `.py` or `.sh` script its name runs, and each task
        named in a `[<task>] ` prefix of its output.

    """
    named: set[str] = set()
    for match in RUN_TASK.finditer(step):
        task = typed_std.group(match, 1)
        if task:
            named.add(task)
    for match in SCRIPT.finditer(step):
        script = typed_std.group(match, 1)
        if script:
            named.add(script)
    for line in output:
        prefixed = TASK_PREFIX.match(line)
        task = typed_std.group(prefixed, 1) if prefixed else None
        if task:
            named.add(task)
    return frozenset(named)


def fenced(text: str) -> str:
    """Fence a log tail so no line in it can close the fence.

    Returns:
        The text in a backtick fence longer than any run inside it.

    """
    longest = max((m.end() - m.start() for m in BACKTICKS.finditer(text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def job_log(repo: str, job_id: int) -> str:
    """Read a job's log from the job-log endpoint, its redirect followed.

    Returns:
        The log text.

    Raises:
        StandingIssueError: no token, or the read failed.

    """
    token = os.environ.get("GH_TOKEN", "")
    api = (os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    path = f"repos/{repo}/actions/jobs/{job_id}/logs"
    if not token:
        msg = f"GET {path}: GH_TOKEN is not set"
        raise StandingIssueError(msg)
    # ruff: ignore[suspicious-url-open-usage] - the API base is the runner's own GITHUB_API_URL
    request = urllib.request.Request(f"{api}/{path}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    # Unredirected: the token goes to the API, never to the blob host.
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with typed_std.urlopen(request, timeout=LOG_TIMEOUT) as answer:
            return answer.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as err:
        msg = f"GET {path} answered HTTP {err.code}"
        raise StandingIssueError(msg) from err
    except (OSError, typed_std.NotAResponseError) as err:
        msg = f"GET {path} failed: {err}"
        raise StandingIssueError(msg) from err


def read_tail(repo: str, job: Job) -> Entry:
    """Fetch a red leg's log and cut its excerpt.

    Returns:
        The entry; a log that cannot be read is named, never fatal.

    """
    try:
        log = job_log(repo, job.id)
    except StandingIssueError as err:
        return Entry(job, None, f"the log could not be read: {err}")
    return Entry(job, tail(log), None)


def record(line: bytes) -> Finding | None:
    """Read one line of a findings file as a record.

    Returns:
        The finding, or None when the line is not a record with the
        five keys, each of its type.

    """
    try:
        item = decoded.as_object(decoded.load_json(line), "the record")
        if set(item) != RECORD_KEYS or item["v"] != RECORD_VERSION:
            return None
        detail = item["detail"]
        if not decoded.is_strings(detail):
            return None
        return Finding(
            decoded.as_str(item["task"], "task"),
            decoded.as_str(item["subject"], "subject"),
            decoded.as_str(item["message"], "message"),
            tuple(detail),
        )
    except decoded.UndecodableError:
        return None


def read_findings(directory: Path) -> Findings:
    """Read every record in a run's findings files.

    Returns:
        The records, file by file in name order, and how many lines (or
        files that could not be read) were not records. A blank line is
        neither. A directory that is not there holds none.

    """
    records: list[Finding] = []
    unreadable = 0
    for path in sorted(directory.glob("*.jsonl")):
        try:
            data = path.read_bytes()
        except OSError:
            unreadable += 1
            continue
        for line in data.splitlines():
            if not line.strip():
                continue
            found = record(line)
            if found is None:
                unreadable += 1
            else:
                records.append(found)
    return Findings(tuple(records), unreadable)


def seen_hashes(previous: Issue | None) -> frozenset[str] | None:
    """Read the hashes the previous body ended with.

    Returns:
        Each hash, or None when the previous body has no findings line.

    """
    if previous is None:
        return None
    last: str | None = None
    for match in HASHES.finditer(previous.body):
        last = typed_std.group(match, 1)
    if last is None:
        return None
    return frozenset(last.split())


@dataclass(frozen=True)
class Listed:
    """A finding as the body lists it: new in this run, or still red.

    `outside` is a finding whose task no red job's failing step ran.
    """

    finding: Finding
    new: bool
    outside: bool = False


@dataclass(frozen=True)
class Listing:
    """A red job's findings in the order its body lists them.

    `tasks` is every task with a record in the run.
    """

    items: tuple[Listed, ...]
    tasks: frozenset[str]
    unreadable: int


def listing(
    findings: Findings | None,
    seen: frozenset[str] | None,
    mine: frozenset[str],
    ran: frozenset[str],
) -> Listing | None:
    """Order a red job's findings: its own, then those no failing step ran.

    `mine` is every task this job's failing steps ran, `ran` every task
    a red job's failing step ran in the run. Within each section, by
    task name, the new before those still red. A finding recorded twice
    (the same task, subject and message) is listed once.

    Returns:
        The listing, or None without findings files or with nothing in
        them, so the body is written without findings.

    """
    if findings is None or not (findings.records or findings.unreadable):
        return None
    unique: dict[str, Finding] = {}
    for found in findings.records:
        unique.setdefault(found.digest, found)
    known = seen or frozenset()
    items: list[Listed] = []
    for found in unique.values():
        new = found.digest not in known
        if found.task in mine:
            items.append(Listed(found, new))
        elif found.task not in ran:
            items.append(Listed(found, new, outside=True))
    items.sort(key=lambda i: (i.outside, i.finding.task, not i.new))
    tasks = frozenset(f.task for f in unique.values())
    return Listing(tuple(items), tasks, findings.unreadable)


def one_line(text: str) -> str:
    """Write a field of a record as one line of the body, HTML escaped.

    Returns:
        The text with its line breaks as spaces and `<`, `>`, `&` and
        quotes escaped, so it can open no comment, tag or line.

    """
    return html.escape(" ".join(text.splitlines()))


def listed_finding(item: Finding, *, detail: bool) -> str:
    """Write one finding as a list item, its detail collapsed under it.

    Returns:
        The item's lines.

    """
    said = one_line(item.message)
    line = f"- {one_line(item.subject)}: {said}" if item.subject else f"- {said}"
    if not (detail and item.detail):
        return f"{line}\n"
    block = f"<details><summary>detail</summary>\n\n{fenced(chr(10).join(item.detail))}"
    indented = "\n".join(f"  {x}" if x else "" for x in block.splitlines())
    return f"{line}\n\n{indented}\n\n  </details>\n"


OUTSIDE = "## Recorded outside this job's failing step"


def findings_section(found: Listing, shown: int, details: int) -> str:
    """Write `## Findings`: each task's findings, new apart from still red.

    The job's own findings come first; those no red job's failing step
    ran follow under OUTSIDE.

    Returns:
        The sections, listing the first `shown` findings, the first
        `details` of them with their detail.

    """
    counts: dict[tuple[bool, str], int] = {}
    for item in found.items:
        key = (item.outside, item.finding.task)
        counts[key] = counts.get(key, 0) + 1
    parts = ["## Findings", ""]
    if found.unreadable:
        parts += [
            (
                f"{found.unreadable} line(s) of the findings files were not a record"
                " and are not counted."
            ),
            "",
        ]
    if all(item.outside for item in found.items):
        parts += ["_This job's failing steps recorded no finding._", ""]
    task: str | None = None
    group: bool | None = None
    outside = False
    for at, item in enumerate(found.items[:shown]):
        if item.outside and not outside:
            outside, task = True, None
            parts += [OUTSIDE, ""]
        if item.finding.task != task:
            task, group = item.finding.task, None
            count = counts[item.outside, task]
            parts += [f"### {one_line(task)} ({count})", ""]
        if item.new != group:
            group = item.new
            parts += ["#### New in this run" if item.new else "#### Still red", ""]
        parts.append(listed_finding(item.finding, detail=at < details))
    return "\n".join(parts)


@dataclass(frozen=True)
class Legs:
    """A red job's legs with one output, listed together over one excerpt."""

    legs: tuple[Job, ...]
    tail: Excerpt | None
    missing: str | None


def grouped(entries: list[Entry]) -> list[Legs]:
    """Gather red legs whose failing step wrote the same output.

    Returns:
        One group per output, in the order a leg first shows it.

    """
    groups: dict[tuple[Excerpt | None, str | None], list[Job]] = {}
    for item in entries:
        groups.setdefault((item.tail, item.missing), []).append(item.job)
    return [
        Legs(tuple(legs), tail, missing) for (tail, missing), legs in groups.items()
    ]


def legs_entry(
    group: Legs, *, drop: bool, recorded: frozenset[str] | None = None
) -> str:
    """Write one group of red legs: each leg's link, then their excerpt.

    `recorded` is every task with a finding, when the body is written
    from findings: legs whose failing step ran one point at them, and
    their output is shown either way.

    Returns:
        The group's heading, links, pointer and collapsed output.

    """
    first = group.legs[0].name
    more = len(group.legs) - 1
    heading = f"### {first}"
    if more:
        heading += f" and {more} more leg{'s' if more != 1 else ''}"
    links = "\n".join(
        f"- [{leg.name}]({leg.url}): {leg.conclusion}" for leg in group.legs
    )
    head = f"{heading}\n\n{links}\n"
    ran = (group.tail.tasks if group.tail else frozenset()) & (recorded or frozenset())
    if ran:
        named = ", ".join(f"<code>{html.escape(t)}</code>" for t in sorted(ran))
        whose = "Its" if not more else "Their"
        head += f"\n{whose} findings are above, under {named}.\n"
    elif recorded is not None:
        head += "\n_No finding was recorded._\n"
    summary = "log tail"
    if drop:
        shown = f"_Log tail dropped: the body would pass {BODY_LIMIT:,} characters._"
    elif group.tail is None:
        shown = f"_Log tail unknown: {group.missing}_"
    elif group.tail.step is None:
        shown = fenced(group.tail.text or "(the log is empty)")
    else:
        summary = f"failing step: <code>{html.escape(group.tail.step)}</code>"
        shown = fenced(group.tail.text or "(the step printed nothing)")
    return f"{head}\n<details><summary>{summary}</summary>\n\n{shown}\n\n</details>\n"


@dataclass(frozen=True)
class Red:
    """What a red job's body says beside its legs.

    `legs` counts the job's legs in the run, red or not.
    """

    workflow: str
    job: str
    branch: str
    run_url: str
    since: str
    runs: int
    legs: int


@dataclass(frozen=True)
class Cut:
    """What a body leaves out to stay under BODY_LIMIT, each counted from the end."""

    tails: int = 0
    details: int = 0
    findings: int = 0


def compose(red: Red, entries: list[Entry], found: Listing | None, cut: Cut) -> str:
    """Write the body with what `cut` leaves out.

    Returns:
        The body.

    """
    groups = grouped(entries)
    keep = len(groups) - cut.tails
    recorded = found.tasks if found else None
    parts = [
        legs_entry(g, drop=at >= keep, recorded=recorded) for at, g in enumerate(groups)
    ]
    plural = "s" if red.legs != 1 else ""
    lines = [
        f"The job **{html.escape(red.job)}** of {red.workflow} on {red.branch} ended",
        f"its latest run with {len(entries)} of its {red.legs} leg{plural} red. Every",
        "red run replaces this body; the next run with every leg of this",
        "job green or skipped closes this issue.",
        "",
        f"- Latest red run: {red.run_url}",
        f"- Red since: {red.since}",
        f"- Red runs: {red.runs}",
        "",
    ]
    limit = f"the body would pass {BODY_LIMIT:,} characters."
    if cut.tails:
        lines += [f"Log tails dropped for the last {cut.tails} excerpt(s): {limit}", ""]
    if cut.details:
        lines += [f"Detail dropped for the last {cut.details} finding(s): {limit}", ""]
    if cut.findings:
        lines += [f"The last {cut.findings} finding(s) are not shown: {limit}", ""]
    if found is None:
        return "\n".join([*lines, "## Red legs", "", *parts])
    shown = len(found.items) - cut.findings
    details = len(found.items) - cut.details
    hashes = " ".join(i.finding.digest for i in found.items[:shown])
    return "\n".join([
        *lines,
        findings_section(found, shown, details),
        "## Red legs",
        "",
        *parts,
        f"<!-- findings: {hashes} -->",
    ])


def least(fits: Callable[[int], bool], top: int) -> int | None:
    """Find the fewest left out, from 0 to `top`, that fits.

    Returns:
        The least count that fits, or None when even `top` does not.

    """
    if not fits(top):
        return None
    low, high = 0, top
    while low < high:
        mid = (low + high) // 2
        if fits(mid):
            high = mid
        else:
            low = mid + 1
    return low


def body(
    red: Red,
    entries: list[Entry],
    findings: Findings | None = None,
    seen: frozenset[str] | None = None,
    ran: frozenset[str] | None = None,
) -> str:
    """Write a red job's issue body, under BODY_LIMIT characters.

    `entries` are the job's red legs; `ran` is every task a red job's
    failing step ran in the run, this job's own when None.

    Returns:
        The body; step outputs dropped from the last excerpt upwards,
        then detail blocks, then findings, from the end of the list.

    """
    mine = frozenset(t for e in entries if e.tail for t in e.tail.tasks)
    found = listing(findings, seen, mine, mine if ran is None else ran)
    tails_cut = len(grouped(entries))
    count = len(found.items) if found else 0

    def text(cut: Cut) -> str:
        return compose(red, entries, found, cut)

    def fits(cut: Cut) -> bool:
        return len(text(cut)) < BODY_LIMIT

    for tails in range(tails_cut + 1):
        if fits(Cut(tails)):
            return text(Cut(tails))
    details = least(lambda n: fits(Cut(tails_cut, n)), count)
    if details is not None:
        return text(Cut(tails_cut, details))
    dropped = least(lambda n: fits(Cut(tails_cut, count, n)), count)
    return text(Cut(tails_cut, count, count if dropped is None else dropped))


def field(line: re.Pattern[str], body: str) -> str | None:
    """Read one `- Name: value` line of an earlier body.

    Returns:
        The value, or None when the body has no such line.

    """
    match = line.search(body)
    return typed_std.group(match, 1) if match else None


def carried(previous: Issue | None, run_url: str) -> tuple[str, int]:
    """Carry "Red since" and the run count forward from the last body.

    Returns:
        Red since, and the count with this run counted once.

    """
    if previous is None:
        return run_url, 1
    since = field(RED_SINCE, previous.body) or "unknown: not in the previous body"
    counted = field(RED_RUNS, previous.body)
    runs = int(counted) if counted else 0
    if field(LATEST, previous.body) != run_url:
        runs += 1
    return since, max(runs, 1)


def job_name(listed: str, workflow: str) -> str:
    """Name the job a listed job is a leg of.

    Returns:
        The listed name without a leading `<workflow> / ` and without
        one final ` (…)`.

    """
    return LEG.sub("", listed.removeprefix(f"{workflow} / "), count=1)


def by_name(every: list[Job], workflow: str) -> dict[str, list[Job]]:
    """Gather the run's jobs into jobs and their legs.

    Returns:
        Each job's name and its legs, in the order the list gives them.

    """
    named: dict[str, list[Job]] = {}
    for item in every:
        named.setdefault(job_name(item.name, workflow), []).append(item)
    return named


def is_red_leg(leg: Job) -> bool:
    """Say whether a leg failed or timed out.

    Returns:
        True when it did.

    """
    return leg.status == "completed" and leg.conclusion in RED


def is_green(legs: list[Job]) -> bool:
    """Say whether every leg of a job is success or skipped.

    Returns:
        True when every one is.

    """
    return all(j.status == "completed" and j.conclusion in GREEN for j in legs)


def red_title(job: str, workflow: str) -> str:
    """Write a red job's issue title.

    Returns:
        `red: <job> (<workflow>)`.

    """
    return f"red: {job} ({workflow})"


def titled_job(title: str, workflow: str) -> str | None:
    """Read the job a `red: <job> (<workflow>)` title names.

    Returns:
        The job, or None when the title has another shape or workflow.

    """
    prefix, suffix = "red: ", f" ({workflow})"
    if len(title) <= len(prefix) + len(suffix):
        return None
    if not (title.startswith(prefix) and title.endswith(suffix)):
        return None
    return title[len(prefix) : -len(suffix)]


@dataclass(frozen=True)
class Run:
    """What every issue one report keeps shares."""

    repo: str
    workflow: str
    branch: str
    url: str


@dataclass(frozen=True)
class Write:
    """One red job's issue, decided before anything is written."""

    title: str
    found: Issue | None
    text: str
    runs: int


def keep_red(
    run: Run,
    named: dict[str, list[Job]],
    listed: list[Issue],
    findings: Findings | None = None,
) -> list[str]:
    """Create each red job's issue, or replace its body in place.

    Every body is written before any issue is: two open issues with one
    title raise before anything is filed.

    Returns:
        What was done, for the run's log.

    """
    entries = {
        name: [read_tail(run.repo, leg) for leg in legs if is_red_leg(leg)]
        for name, legs in named.items()
        if any(is_red_leg(leg) for leg in legs)
    }
    ran = frozenset(
        t for legs in entries.values() for e in legs if e.tail for t in e.tail.tasks
    )
    writes: list[Write] = []
    for name, legs in entries.items():
        title = red_title(name, run.workflow)
        found = one([i for i in listed if i.title == title], title)
        since, runs = carried(found, run.url)
        red = Red(
            run.workflow, name, run.branch, run.url, since, runs, len(named[name])
        )
        text = body(red, legs, findings, seen_hashes(found), ran)
        writes.append(Write(title, found, text, runs))
    done: list[str] = []
    label_kept = False
    for write in writes:
        if write.found is None:
            if not label_kept:
                made = ensure_label(run.repo)
                label_kept = True
                if made:
                    done.append(made.removesuffix("; "))
            create(run.repo, write.title, write.text, (LABEL,))
            done.append(f"opened {write.title!r}")
        else:
            replace(run.repo, write.found.number, write.text)
            done.append(f"updated #{write.found.number} ({write.runs} red runs)")
    return done


def keep_green(run: Run, named: dict[str, list[Job]], listed: list[Issue]) -> list[str]:
    """Close each red-check issue whose job is green, or not in the run.

    Only an open issue labelled red-check with a `red: <job>
    (<workflow>)` title is read; any other is a person's.

    Returns:
        What was done, for the run's log.

    """
    done: list[str] = []
    for issue in listed:
        name = titled_job(issue.title, run.workflow)
        if LABEL not in issue.labels or name is None:
            continue
        legs = named.get(name)
        if legs is None:
            said = (
                f"Not in the run: {run.url} lists no job named `{name}`;"
                " it was renamed or removed."
            )
            close(run.repo, issue.number, said)
            done.append(f"not in the run: closed #{issue.number}")
        elif is_green(legs):
            said = f"Green: {run.url} ran every leg of this job green or skipped."
            close(run.repo, issue.number, said)
            done.append(f"green: closed #{issue.number}")
    return done


def close_old(run: Run, listed: list[Issue]) -> list[str]:
    """Close the one issue a workflow kept before each red job had its own.

    Returns:
        What was done, for the run's log.

    """
    title = OLD_TITLE.format(workflow=run.workflow)
    said = (
        f"{run.url} reported this workflow with one issue per red job"
        f" (monumental-archive/.github#1966), titled `red: <job> ({run.workflow})`:"
        " each red job now has its own issue."
    )
    done: list[str] = []
    for issue in listed:
        if issue.title == title:
            close(run.repo, issue.number, said)
            done.append(f"closed #{issue.number}, the workflow's one issue")
    return done


def report(repo: str, args: Args, run_url: str) -> str:
    """Read the run's jobs and keep each red job's issue.

    Returns:
        What was done, for the run's log.

    """
    every = jobs(repo, args.run_id)
    named = by_name(every, args.workflow)
    red = any(is_red_leg(j) for j in every)
    unfinished = [j for j in every if j.status != "completed"]
    settled = len(unfinished) <= 1
    if not (red or settled):
        names = ", ".join(f"{j.name} ({j.status})" for j in unfinished[1:])
        return f"neither red nor green yet, nothing changed: {names}"
    listed = every_open(repo)
    run = Run(repo, args.workflow, args.ref.removeprefix("refs/heads/"), run_url)
    done: list[str] = []
    if red:
        findings = read_findings(Path(args.findings)) if args.findings else None
        done += keep_red(run, named, listed, findings)
    if settled:
        done += keep_green(run, named, listed)
    done += close_old(run, listed)
    neither = [
        name
        for name, legs in named.items()
        if all(j.status == "completed" for j in legs)
        and not is_green(legs)
        and not any(is_red_leg(j) for j in legs)
    ]
    if neither:
        done.append(f"neither red nor green, unchanged: {', '.join(neither)}")
    return "; ".join(done) or "nothing to change"


def default_branch(repo: str, given: str) -> str:
    """Name the repository's default branch.

    Returns:
        The branch the event named, or the repository's own answer.

    """
    if given:
        return given
    return gh("api", f"repos/{repo}", "--jq", ".default_branch").strip()


def main(argv: list[str]) -> int:
    """Report one scheduled run in an issue per red job.

    Returns:
        0 when kept or nothing was to change, EXIT_ERROR when a read or
        write failed.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--default-branch", default="")
    parser.add_argument("--findings", default=None)
    args = parser.parse_args(argv, namespace=Args())
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        print("red-check: GITHUB_REPOSITORY is not set", file=sys.stderr)
        return EXIT_ERROR
    server = os.environ.get("GITHUB_SERVER_URL") or "https://github.com"
    run_url = f"{server}/{repo}/actions/runs/{args.run_id}"
    try:
        branch = default_branch(repo, args.default_branch)
        if args.ref != f"refs/heads/{branch}":
            where = f"{args.ref} is not the default branch ({branch})"
            print(f"red-check: {where}; nothing changed")
            return 0
        done = report(repo, args, run_url)
    except StandingIssueError as err:
        print(f"red-check: {err}; nothing was filed", file=sys.stderr)
        return EXIT_ERROR
    print(f"red-check: {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
