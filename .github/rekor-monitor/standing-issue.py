#!/usr/bin/env python3
"""Keep one standing issue while the signer-identity watch is behind.

rekor-monitor.yml has two kinds of issue (#1353). A certificate that
does not reconcile files its own, one per CT index; that is not this
tool. A run that could not read the log is this one: at most one issue
open, under one fixed title, saying how far behind the watch is.

  behind     a run failed or was cancelled. No open issue with exactly
             the title: create it. One open: replace its body in place,
             keeping "behind since" and counting this run. No comment,
             no second issue.
  caught-up  a run saved an advanced checkpoint. One open: close it
             with one comment naming the run and the size it reached.
             None open: nothing to do.

The title is matched exactly, here, on every open issue the forge
lists; a search's ranking is never trusted to find it. If the listing
fails, this exits non-zero by name and files nothing: a failed read
never opens a second issue.

The checkpoint is the monitor's file: one line per signed tree head,
the last one current, as JSON with `tree_size` and `timestamp`
(milliseconds since the epoch, RFC 6962). A value that cannot be read
(no checkpoint, the log unreachable) is said by name in the body.

Usage:
  standing-issue.py behind --checkpoint FILE --log-size N --run-url URL
  standing-issue.py caught-up --checkpoint FILE --run-url URL

The repository is GITHUB_REPOSITORY; no organisation, repository or log
is named here. Stdlib only, and `gh` on PATH.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import] - gh is the forge client this workflow uses
import sys
from dataclasses import dataclass
from pathlib import Path

TITLE = "rekor-monitor: the signer-identity watch is behind"
EXIT_ERROR = 2

BEHIND_SINCE = re.compile(r"^- Behind since: (.+)$", re.MULTILINE)
FAILED_RUNS = re.compile(r"^- Failed runs since it opened: (\d+)$", re.MULTILINE)


class StandingIssueError(Exception):
    """A read or write that could not complete; the run must go red."""


@dataclass(frozen=True)
class Checkpoint:
    """The checkpoint's tree size and signed tree head time, or why not."""

    size: int | None
    timestamp: str | None
    missing: str | None


def read_checkpoint(path: Path) -> Checkpoint:
    """Read the last signed tree head of the monitor's checkpoint file.

    Returns:
        The size and UTC time, or the reason they could not be read.

    """
    if not path.is_file() or path.stat().st_size == 0:
        return Checkpoint(None, None, "no checkpoint file")
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        sth = json.loads(lines[-1])
        size = int(sth["tree_size"])
        millis = int(sth["timestamp"])
    except IndexError, ValueError, TypeError, KeyError:
        return Checkpoint(None, None, "the checkpoint file could not be read")
    when = dt.datetime.fromtimestamp(millis / 1000, dt.UTC)
    return Checkpoint(size, when.strftime("%Y-%m-%dT%H:%M:%SZ"), None)


def parse_log_size(text: str) -> int | None:
    """Read the log's size as the workflow passed it.

    Returns:
        The size, or None when the log could not be read.

    """
    return int(text) if text.isdigit() else None


def gh(*args: str) -> str:
    """Run gh and return its output.

    Returns:
        gh's standard output.

    Raises:
        StandingIssueError: gh exited non-zero.

    """
    proc = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
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


def open_standing(repo: str) -> list[dict]:
    """List the open issues titled exactly TITLE.

    Returns:
        Each open issue whose title is exactly the standing title.

    Raises:
        StandingIssueError: the listing could not be read.

    """
    out = gh(
        "issue", "list", "--repo", repo, "--state", "open",
        "--limit", "1000", "--json", "number,title,body",
    )  # fmt: skip
    try:
        issues = json.loads(out)
    except json.JSONDecodeError as err:
        msg = f"gh issue list answered something that is not JSON: {err}"
        raise StandingIssueError(msg) from err
    return [i for i in issues if i.get("title") == TITLE]


def thousands(n: int) -> str:
    """Format a count for a person.

    Returns:
        n with thousands separators.

    """
    return f"{n:,}"


def body(
    cp: Checkpoint,
    log_size: int | None,
    since: str,
    run_url: str,
    failed: int,
) -> str:
    """Write the standing issue's body.

    Returns:
        The body, every value present or its absence named.

    """
    size = thousands(cp.size) if cp.size is not None else f"unknown: {cp.missing}"
    head = cp.timestamp or f"unknown: {cp.missing}"
    unreachable = "unknown: the log was unreachable"
    log = thousands(log_size) if log_size is not None else unreachable
    if cp.size is None:
        outstanding = f"unknown: {cp.missing}"
    elif log_size is None:
        outstanding = unreachable
    else:
        outstanding = thousands(log_size - cp.size)
    return "\n".join([
        "The signer-identity watch could not read Fulcio's certificate log on",
        "its latest run, so certificates logged after the checkpoint have not",
        "been reconciled. Every failed run replaces this body; a run that saves",
        "an advanced checkpoint closes this issue. A certificate that does not",
        "reconcile files its own issue, never this one.",
        "",
        f"- Checkpoint tree size: {size}",
        f"- Checkpoint signed tree head: {head}",
        f"- Log size now: {log}",
        f"- Entries outstanding: {outstanding}",
        f"- Behind since: {since}",
        f"- Latest failed run: {run_url}",
        f"- Failed runs since it opened: {failed}",
        "",
    ])


def behind(repo: str, cp: Checkpoint, log_size: int | None, run_url: str) -> str:
    """Create the standing issue, or replace its body in place.

    Returns:
        What was done, for the run's log.

    """
    found = open_standing(repo)
    if not found:
        text = body(cp, log_size, run_url, run_url, 1)
        gh("issue", "create", "--repo", repo, "--title", TITLE, "--body", text)
        return "opened the standing issue"
    issue = found[0]
    previous = issue.get("body") or ""
    since_match = BEHIND_SINCE.search(previous)
    since = since_match.group(1) if since_match else "unknown: not in the previous body"
    count_match = FAILED_RUNS.search(previous)
    failed = int(count_match.group(1)) + 1 if count_match else 1
    text = body(cp, log_size, since, run_url, failed)
    number = str(issue["number"])
    gh("issue", "edit", number, "--repo", repo, "--body", text)
    return f"updated the standing issue #{number} ({failed} failed runs)"


def caught_up(repo: str, cp: Checkpoint, run_url: str) -> str:
    """Close the standing issue, if one is open.

    Returns:
        What was done, for the run's log.

    """
    found = open_standing(repo)
    if not found:
        return "no standing issue open"
    size = thousands(cp.size) if cp.size is not None else f"unknown ({cp.missing})"
    comment = f"Caught up: {run_url} saved a checkpoint at tree size {size}."
    for issue in found:
        gh("issue", "close", str(issue["number"]), "--repo", repo, "--comment", comment)
    return "closed " + ", ".join(f"#{i['number']}" for i in found)


def main(argv: list[str] | None = None) -> int:
    """Run one mode.

    Returns:
        0 on success, EXIT_ERROR when a read or write could not complete.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["behind", "caught-up"])
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--log-size", default="")
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args(argv)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        print("standing-issue: GITHUB_REPOSITORY is not set", file=sys.stderr)
        return EXIT_ERROR
    cp = read_checkpoint(args.checkpoint)
    try:
        if args.mode == "behind":
            done = behind(repo, cp, parse_log_size(args.log_size), args.run_url)
        else:
            done = caught_up(repo, cp, args.run_url)
    except StandingIssueError as err:
        print(f"standing-issue: {err}; nothing was filed", file=sys.stderr)
        return EXIT_ERROR
    print(f"standing-issue: {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
