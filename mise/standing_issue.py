#!/usr/bin/env python3
"""Keep exactly one standing issue, and report a scheduled run (#1887).

Two halves. The first is the one implementation of "keep exactly one
issue", proved by the signer-identity watch (#1353) and moved here so
there is no second copy: list every open issue, match the title
exactly (never a search's ranking), create it or replace its body in
place, close it with one comment. A listing that fails raises by name,
and the caller files nothing. .github/rekor-monitor/watch_issue.py
keeps its own judgment and imports these.

The second is the `report` job every scheduled workflow ends with. It
reads the run's own job list and keeps one issue titled exactly
`<workflow> is red`, labelled `red-check`:

  cancelled run                   nothing changes: the job's step that
                                  runs this is `if: !cancelled()`.
  run not on the default branch   nothing changes; gh is never called.
  a job failed or timed out       no open issue: create it; one open:
                                  replace its body, keeping "Red since".
  every job green or skipped      close the open issue, naming the run.
  otherwise (a job cancelled, or  nothing changes: the run says neither.
  one not finished)

The job list is the run's latest attempt. The report job is itself
unfinished while it reads it, so one unfinished job is expected; more
than one means a job outside this workflow's `needs:` is still running
(a reusable workflow's report, with a sibling job in its caller), and a
later report in the same run has the last word. A run is counted once,
by its URL, however many reports in it write the body.

Each red job's entry carries its failing step's output, whole (#1925),
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
log that cannot be read is named in its entry. The excerpt is fenced
so no line in it can close the fence. The body stays
under 60,000 characters: excerpts are dropped from the last job
upwards, and the body says so. The label is created from settings/repo-baseline.json's
definition when the repository does not have it.

With `--findings DIR` the body is also written from the run's findings
(#1890, docs/findings.md): every `*.jsonl` in DIR, one record per line
as mise/findings.sh and mise/findings.py append them. A line that is
not a record is counted, and the body says how many. `## Findings`
lists each task's findings, its name in order, those new in this run
apart from those still red: a finding is its task, subject and
message, and the body ends with a comment line carrying the first 16
hex characters of the SHA-256 of each finding the body shows, which the
next run reads back. New means not listed in this issue's previous
body: a finding the limit cut is new when a later run first shows it. A
red job whose failing step ran a task with a record (named after
`mise run ` in the step's name, or in a `[<task>] ` prefix of its
output) points at its findings; any other shows its failing step's
output under "no finding was recorded". Over the limit, step outputs
go first, then detail blocks, then findings from the end of the list,
each said with how many. Without the flag, or with no record and no
unreadable line in DIR, the body is as above.

Usage:
  standing_issue.py --workflow NAME --run-id ID --ref REF
                    [--default-branch BRANCH] [--findings DIR]

The repository is GITHUB_REPOSITORY, the server GITHUB_SERVER_URL, the
API GITHUB_API_URL (https://api.github.com when unset).
Exit 0 when the issue was kept (or nothing was to change), 2 when a
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
    """What a red job's entry shows of its log.

    `step` names the failing step; None when the log marks none, and
    the text is then the log's last lines. `tasks` are the tasks the
    failing step ran, read from its name and its whole output.
    """

    text: str
    step: str | None = None
    tasks: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Entry:
    """A red job as the issue shows it: name, link, and its excerpt."""

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
        Each task named after `mise run ` in the step's name, and each
        named in a `[<task>] ` prefix of its output.

    """
    named: set[str] = set()
    for match in RUN_TASK.finditer(step):
        task = typed_std.group(match, 1)
        if task:
            named.add(task)
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
    """Fetch a red job's log and cut its excerpt.

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
    """A finding as the body lists it: new in this run, or still red."""

    finding: Finding
    new: bool


@dataclass(frozen=True)
class Listing:
    """The run's findings in the order the body lists them."""

    items: tuple[Listed, ...]
    tasks: frozenset[str]
    unreadable: int


def listing(findings: Findings | None, seen: frozenset[str] | None) -> Listing | None:
    """Order the findings: by task name, the new before those still red.

    A finding recorded twice (the same task, subject and message) is
    listed once.

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
    items = [Listed(f, f.digest not in known) for f in unique.values()]
    items.sort(key=lambda i: (i.finding.task, not i.new))
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


def findings_section(found: Listing, shown: int, details: int) -> str:
    """Write `## Findings`: each task's findings, new apart from still red.

    Returns:
        The section, listing the first `shown` findings, the first
        `details` of them with their detail.

    """
    counts: dict[str, int] = {}
    for item in found.items:
        counts[item.finding.task] = counts.get(item.finding.task, 0) + 1
    parts = ["## Findings", ""]
    if found.unreadable:
        parts += [
            (
                f"{found.unreadable} line(s) of the findings files were not a record"
                " and are not counted."
            ),
            "",
        ]
    task: str | None = None
    group: bool | None = None
    for at, item in enumerate(found.items[:shown]):
        if item.finding.task != task:
            task, group = item.finding.task, None
            parts += [f"### {one_line(task)} ({counts[task]})", ""]
        if item.new != group:
            group = item.new
            parts += ["#### New in this run" if item.new else "#### Still red", ""]
        parts.append(listed_finding(item.finding, detail=at < details))
    return "\n".join(parts)


def entry(item: Entry, *, drop: bool, recorded: frozenset[str] | None = None) -> str:
    """Write one red job's entry.

    `recorded` is every task with a finding, when the body is written
    from findings: a job whose failing step ran one points at them.

    Returns:
        The job's name, its link and its collapsed log tail.

    """
    head = f"### {item.job.name}\n\n[{item.job.conclusion}]({item.job.url})\n"
    ran = (item.tail.tasks if item.tail else frozenset()) & (recorded or frozenset())
    if ran:
        named = ", ".join(f"<code>{html.escape(t)}</code>" for t in sorted(ran))
        return f"{head}\nIts findings are above, under {named}.\n"
    if recorded is not None:
        head += "\n_No finding was recorded._\n"
    summary = "log tail"
    if drop:
        shown = f"_Log tail dropped: the body would pass {BODY_LIMIT:,} characters._"
    elif item.tail is None:
        shown = f"_Log tail unknown: {item.missing}_"
    elif item.tail.step is None:
        shown = fenced(item.tail.text or "(the log is empty)")
    else:
        summary = f"failing step: <code>{html.escape(item.tail.step)}</code>"
        shown = fenced(item.tail.text or "(the step printed nothing)")
    return f"{head}\n<details><summary>{summary}</summary>\n\n{shown}\n\n</details>\n"


@dataclass(frozen=True)
class Red:
    """What the body says about this run beside its jobs."""

    workflow: str
    branch: str
    run_url: str
    since: str
    runs: int


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
    keep = len(entries) - cut.tails
    recorded = found.tasks if found else None
    parts = [
        entry(e, drop=at >= keep, recorded=recorded) for at, e in enumerate(entries)
    ]
    plural = "s" if len(entries) != 1 else ""
    lines = [
        f"The latest run of {red.workflow} on {red.branch} ended with",
        f"{len(entries)} red job{plural}. Every red run replaces this body;",
        "the next run with every job green or skipped closes this issue.",
        "",
        f"- Latest red run: {red.run_url}",
        f"- Red since: {red.since}",
        f"- Red runs: {red.runs}",
        "",
    ]
    limit = f"the body would pass {BODY_LIMIT:,} characters."
    if cut.tails:
        lines += [f"Log tails dropped for the last {cut.tails} job(s): {limit}", ""]
    if cut.details:
        lines += [f"Detail dropped for the last {cut.details} finding(s): {limit}", ""]
    if cut.findings:
        lines += [f"The last {cut.findings} finding(s) are not shown: {limit}", ""]
    if found is None:
        return "\n".join([*lines, "## Red jobs", "", *parts])
    shown = len(found.items) - cut.findings
    details = len(found.items) - cut.details
    hashes = " ".join(i.finding.digest for i in found.items[:shown])
    return "\n".join([
        *lines,
        findings_section(found, shown, details),
        "## Red jobs",
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
) -> str:
    """Write the red-check issue's body, under BODY_LIMIT characters.

    Returns:
        The body; step outputs dropped from the last job upwards, then
        detail blocks, then findings, from the end of the list.

    """
    found = listing(findings, seen)
    jobs_cut = len(entries)
    count = len(found.items) if found else 0

    def text(cut: Cut) -> str:
        return compose(red, entries, found, cut)

    def fits(cut: Cut) -> bool:
        return len(text(cut)) < BODY_LIMIT

    for tails in range(jobs_cut + 1):
        if fits(Cut(tails)):
            return text(Cut(tails))
    details = least(lambda n: fits(Cut(jobs_cut, n)), count)
    if details is not None:
        return text(Cut(jobs_cut, details))
    dropped = least(lambda n: fits(Cut(jobs_cut, count, n)), count)
    return text(Cut(jobs_cut, count, count if dropped is None else dropped))


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


def keep_red(
    repo: str, red_jobs: list[Job], red: Red, findings: Findings | None = None
) -> str:
    """Create the red-check issue, or replace its body in place.

    Returns:
        What was done, for the run's log.

    """
    title = f"{red.workflow} is red"
    found = one(open_issues(repo, title), title)
    since, runs = carried(found, red.run_url)
    entries = [read_tail(repo, job) for job in red_jobs]
    text = body(
        Red(red.workflow, red.branch, red.run_url, since, runs),
        entries,
        findings,
        seen_hashes(found),
    )
    if found is None:
        made = ensure_label(repo)
        create(repo, title, text, (LABEL,))
        return f"{made}opened {title!r}"
    replace(repo, found.number, text)
    return f"updated #{found.number} ({runs} red runs)"


def keep_green(repo: str, workflow: str, run_url: str) -> str:
    """Close the red-check issue, if one is open.

    Returns:
        What was done, for the run's log.

    """
    title = f"{workflow} is red"
    found = one(open_issues(repo, title), title)
    if found is None:
        return "green: no red-check issue open"
    close(repo, found.number, f"Green: {run_url} ran every job green or skipped.")
    return f"green: closed #{found.number}"


def report(repo: str, args: Args, run_url: str) -> str:
    """Read the run's jobs and keep the red-check issue.

    Returns:
        What was done, for the run's log.

    """
    every = jobs(repo, args.run_id)
    red_jobs = [j for j in every if j.status == "completed" and j.conclusion in RED]
    branch = args.ref.removeprefix("refs/heads/")
    if red_jobs:
        findings = read_findings(Path(args.findings)) if args.findings else None
        red = Red(args.workflow, branch, run_url, run_url, 1)
        return keep_red(repo, red_jobs, red, findings)
    unfinished = [j for j in every if j.status != "completed"]
    others = [j for j in every if j.status == "completed" and j.conclusion not in GREEN]
    if len(unfinished) > 1 or others:
        names = ", ".join(
            f"{j.name} ({j.conclusion or j.status})" for j in [*others, *unfinished[1:]]
        )
        return f"neither red nor green yet, nothing changed: {names}"
    return keep_green(repo, args.workflow, run_url)


def default_branch(repo: str, given: str) -> str:
    """Name the repository's default branch.

    Returns:
        The branch the event named, or the repository's own answer.

    """
    if given:
        return given
    return gh("api", f"repos/{repo}", "--jq", ".default_branch").strip()


def main(argv: list[str]) -> int:
    """Report one scheduled run in its workflow's red-check issue.

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
