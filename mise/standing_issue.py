#!/usr/bin/env python3
"""Keep exactly one standing issue, and report a scheduled run (#1887).

Two halves. The first is the one implementation of "keep exactly one
issue", proved by the signer-identity watch (#1353) and moved here so
there is no second copy: list every open issue, match the title
exactly (never a search's ranking), create it or replace its body in
place, close it with one comment. A listing that fails raises by name,
and the caller files nothing. .github/rekor-monitor/standing_issue.py
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

Each red job's entry carries the tail of its log: the last 60 lines
before its first `##[error]` line (the last 60 when it has none),
timestamps and colour codes stripped, at most 4,000 characters, fenced
so no line in it can close the fence. The body stays under 60,000
characters: tails are dropped from the last job upwards, and the body
says so. The label is created from settings/repo-baseline.json's
definition when the repository does not have it.

Usage:
  standing_issue.py --workflow NAME --run-id ID --ref REF
                    [--default-branch BRANCH]

The repository is GITHUB_REPOSITORY, the server GITHUB_SERVER_URL.
Exit 0 when the issue was kept (or nothing was to change), 2 when a
read or write failed. Stdlib only, and `gh` on PATH.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import decoded

EXIT_ERROR = 2
LABEL = "red-check"
BASELINE = Path(__file__).resolve().parents[1] / "settings" / "repo-baseline.json"

RED = frozenset({"failure", "timed_out"})
GREEN = frozenset({"success", "skipped"})
TAIL_LINES = 60
TAIL_CHARS = 4000
BODY_LIMIT = 60000
ERROR_MARK = "##[error]"

RED_SINCE = re.compile(r"^- Red since: (.+)$", re.MULTILINE)
LATEST = re.compile(r"^- Latest red run: (.+)$", re.MULTILINE)
RED_RUNS = re.compile(r"^- Red runs: (\d+)$", re.MULTILINE)
TIMESTAMP = re.compile(r"^﻿?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z ?")
COLOUR = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
BACKTICKS = re.compile(r"`+")


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
class Entry:
    """A red job as the issue shows it: name, link, and its log tail."""

    job: Job
    tail: str | None
    missing: str | None


class Args(argparse.Namespace):
    """The command line, typed."""

    workflow: str
    run_id: str
    ref: str
    default_branch: str


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
        msg = f"{len(found)} open issues are titled exactly {title!r} ({numbers}): close all but one"
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
        doc = decoded.as_object(decoded.load_json(BASELINE.read_bytes()), "the baseline")
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
    gh("label", "create", LABEL, "--repo", repo, "--color", colour, "--description", description)
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
                    conclusion="" if conclusion is None else decoded.as_str(conclusion, f"job {at}: conclusion"),
                    url=decoded.as_str(item.get("html_url"), f"job {at}: html_url"),
                )
            )
    except decoded.UndecodableError as err:
        msg = f"the run's job list is not a list of jobs: {err}"
        raise StandingIssueError(msg) from err
    return found


def tail(log: str) -> str:
    """Cut a job's log to what the issue shows.

    Returns:
        The last TAIL_LINES lines before the first error line (the last
        TAIL_LINES when there is none), timestamps and colour codes
        stripped, at most TAIL_CHARS characters, whole lines kept.

    """
    lines = [COLOUR.sub("", TIMESTAMP.sub("", line)) for line in log.splitlines()]
    first = next((at for at, line in enumerate(lines) if ERROR_MARK in line), None)
    window = lines[max(0, first - TAIL_LINES) : first] if first is not None else lines[-TAIL_LINES:]
    text = "\n".join(window)
    if len(text) > TAIL_CHARS:
        text = text[-TAIL_CHARS:]
        cut = text.find("\n")
        text = text[cut + 1 :] if cut >= 0 else text
    return text


def fenced(text: str) -> str:
    """Fence a log tail so no line in it can close the fence.

    Returns:
        The text in a backtick fence longer than any run inside it.

    """
    longest = max((len(m) for m in BACKTICKS.findall(text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def read_tail(repo: str, job: Job) -> Entry:
    """Fetch a red job's log and cut its tail.

    Returns:
        The entry; a log that cannot be read is named, never fatal.

    """
    try:
        log = gh("api", f"repos/{repo}/actions/jobs/{job.id}/logs")
    except StandingIssueError as err:
        return Entry(job, None, f"the log could not be read: {err}")
    return Entry(job, tail(log), None)


def entry(item: Entry, *, drop: bool) -> str:
    """Write one red job's entry.

    Returns:
        The job's name, its link and its collapsed log tail.

    """
    head = f"### {item.job.name}\n\n[{item.job.conclusion}]({item.job.url})\n"
    if drop:
        shown = f"_Log tail dropped: the body would pass {BODY_LIMIT:,} characters._"
    elif item.tail is None:
        shown = f"_Log tail unknown: {item.missing}_"
    else:
        shown = fenced(item.tail or "(no lines before the first error line)")
    return f"{head}\n<details><summary>log tail</summary>\n\n{shown}\n\n</details>\n"


@dataclass(frozen=True)
class Red:
    """What the body says about this run beside its jobs."""

    workflow: str
    branch: str
    run_url: str
    since: str
    runs: int


def body(red: Red, entries: list[Entry]) -> str:
    """Write the red-check issue's body, under BODY_LIMIT characters.

    Returns:
        The body; tails dropped from the last job upwards when needed.

    """
    dropped = 0
    while True:
        keep = len(entries) - dropped
        parts = [entry(e, drop=at >= keep) for at, e in enumerate(entries)]
        lines = [
            f"The latest run of {red.workflow} on {red.branch} ended with "
            f"{len(entries)} red job{'s' if len(entries) != 1 else ''}. Every red run",
            "replaces this body; the next run with every job green or skipped",
            "closes this issue.",
            "",
            f"- Latest red run: {red.run_url}",
            f"- Red since: {red.since}",
            f"- Red runs: {red.runs}",
            "",
        ]
        if dropped:
            lines += [f"Log tails dropped for the last {dropped} job(s): the body would pass {BODY_LIMIT:,} characters.", ""]
        text = "\n".join([*lines, "## Red jobs", "", *parts])
        if len(text) < BODY_LIMIT or dropped >= len(entries):
            return text
        dropped += 1


def carried(previous: Issue | None, run_url: str) -> tuple[str, int]:
    """Carry "Red since" and the run count forward from the last body.

    Returns:
        Red since, and the count with this run counted once.

    """
    if previous is None:
        return run_url, 1
    since_match = RED_SINCE.search(previous.body)
    since = since_match.group(1) if since_match else "unknown: not in the previous body"
    runs_match = RED_RUNS.search(previous.body)
    latest = LATEST.search(previous.body)
    runs = int(runs_match.group(1)) if runs_match else 0
    if not (latest and latest.group(1) == run_url):
        runs += 1
    return since, max(runs, 1)


def keep_red(repo: str, red_jobs: list[Job], red: Red) -> str:
    """Create the red-check issue, or replace its body in place.

    Returns:
        What was done, for the run's log.

    """
    title = f"{red.workflow} is red"
    found = one(open_issues(repo, title), title)
    since, runs = carried(found, red.run_url)
    entries = [read_tail(repo, job) for job in red_jobs]
    text = body(Red(red.workflow, red.branch, red.run_url, since, runs), entries)
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
        return keep_red(repo, red_jobs, Red(args.workflow, branch, run_url, run_url, 1))
    unfinished = [j for j in every if j.status != "completed"]
    others = [j for j in every if j.status == "completed" and j.conclusion not in GREEN]
    if len(unfinished) > 1 or others:
        names = ", ".join(f"{j.name} ({j.conclusion or j.status})" for j in [*others, *unfinished[1:]])
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
            print(f"red-check: {args.ref} is not the default branch ({branch}); nothing changed")
            return 0
        done = report(repo, args, run_url)
    except StandingIssueError as err:
        print(f"red-check: {err}; nothing was filed", file=sys.stderr)
        return EXIT_ERROR
    print(f"red-check: {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
