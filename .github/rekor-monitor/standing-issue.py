#!/usr/bin/env python3
"""Keep one standing issue while the signer-identity watch is not gaining.

rekor-monitor.yml reads Fulcio's certificate log in legs, each one run of
`stele assert signer-certificates` from stele's checkpoint (#1339). Each
leg is judged on its own (verdict.py), and a certificate that does not
reconcile files its own issue (findings.py). This runs once, after the
last leg, and asks one question of stele's own checkpoint files: did
this run gain on the log? The outstanding count is the checkpoint's
tree size minus its `next`, the first index not yet judged.

  newest checkpoint at its head   caught up: close the standing issue.
  no checkpoint at the start      the run founded one: no earlier count,
                                  said by name, not an alert.
  did not advance                 red, behind.
  advanced, outstanding not       behind (green: it advanced): a backlog
  smaller than at the start       that is not shrinking.
  advanced, outstanding smaller   gaining: nothing to say.
  no newest checkpoint, or the    red, behind: whether the run gained
  start's was not kept            cannot be told.

Behind: no open issue with exactly the title, create it; one open,
replace its body in place, keeping "behind since" and counting this run.
Caught up: close it with one comment. The title is matched exactly, on
every open issue the forge lists; a search's ranking is never trusted to
find it. A listing that fails exits 2 by name and files nothing: a
failed read never opens a second issue.

Usage:
  standing-issue.py --start FILE --start-kept true|false|""
                    --checkpoint FILE --legs RESULT --run-url URL

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
EXIT_RED = 1
EXIT_ERROR = 2
FOUNDED = "none counted: this run founded the checkpoint"

BEHIND_SINCE = re.compile(r"^- Behind since: (.+)$", re.MULTILINE)
RUNS_BEHIND = re.compile(r"^- Runs behind since it opened: (\d+)$", re.MULTILINE)


class StandingIssueError(Exception):
    """A read or write that could not complete; the run must go red."""


@dataclass(frozen=True)
class Checkpoint:
    """stele's checkpoint: next index, verified tree head, or why not."""

    next: int | None
    size: int | None
    when: dt.datetime | None
    missing: str | None

    @property
    def outstanding(self) -> int | None:
        """Entries the checkpoint has not judged, up to its tree head."""
        if self.next is None or self.size is None:
            return None
        return self.size - self.next


@dataclass(frozen=True)
class Outcome:
    """The run's verdict: colour, why, and the standing action."""

    red: bool
    why: str
    standing: str | None


@dataclass(frozen=True)
class Run:
    """The run being judged, as the standing issue names it."""

    start: str
    why: str
    legs: str
    url: str


def read_checkpoint(path: Path) -> Checkpoint:
    """Read stele's checkpoint file.

    Returns:
        Its next index, tree size and head time, or why they could not be read.

    """
    if not path.is_file() or path.stat().st_size == 0:
        return Checkpoint(None, None, None, "no checkpoint file")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        head = doc["treeHead"]
        nxt, size = int(doc["next"]), int(head["treeSize"])
        when = dt.datetime.fromtimestamp(int(head["timestamp"]) / 1000, dt.UTC)
    # Overflow and OS errors: a `next` of Infinity, or a timestamp past the
    # platform's range; recursion: JSON nested past the parser's depth.
    # Each was a traceback until prop_standing_issue.py (#1557).
    except ValueError, TypeError, KeyError, OverflowError, OSError, RecursionError:
        return Checkpoint(None, None, None, "the checkpoint file could not be read")
    return Checkpoint(nxt, size, when, None)


def judge(start: Checkpoint, kept: str, end: Checkpoint) -> Outcome:
    """Decide whether the run gained on the log.

    Returns:
        The outcome; `start` is the run's starting checkpoint, `end` the newest.

    """
    if end.next is None or end.outstanding is None:
        why = f"the newest checkpoint: {end.missing}"
        return Outcome(red=True, why=why, standing="behind")
    if end.outstanding == 0:
        why = "judged to the head of the log"
        return Outcome(red=False, why=why, standing="caught-up")
    if kept == "false":
        why = f"advanced to {end.next}; no earlier count, this run founded it"
        return Outcome(red=False, why=why, standing=None)
    if kept != "true" or start.next is None or start.outstanding is None:
        lost = start.missing if kept == "true" else "the start job did not finish"
        why = f"the checkpoint the run started from was not kept ({lost})"
        return Outcome(red=True, why=why, standing="behind")
    return gained(start.next, start.outstanding, end.next, end.outstanding)


def gained(start_next: int, start_left: int, end_next: int, end_left: int) -> Outcome:
    """Decide a run with both checkpoints, short of the head.

    Returns:
        Red and behind when it did not advance; behind when the outstanding
        count is not smaller than at the start; otherwise gaining.

    """
    if end_next <= start_next:
        why = f"the checkpoint did not advance in this run (next {end_next})"
        return Outcome(red=True, why=why, standing="behind")
    counts = f"outstanding {start_left:,} at the start, {end_left:,} now"
    if end_left >= start_left:
        why = f"advanced but not gaining: {counts}"
        return Outcome(red=False, why=why, standing="behind")
    return Outcome(red=False, why=f"gaining: {counts}", standing=None)


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


def count(n: int | None, missing: str | None) -> str:
    """Format a count for a person, or name its absence.

    Returns:
        n with thousands separators, or why it is unknown.

    """
    return f"{n:,}" if n is not None else f"unknown: {missing}"


def start_count(start: Checkpoint, kept: str) -> str:
    """Say the outstanding count the run started from.

    Returns:
        The count, FOUNDED, or why it is unknown.

    """
    if kept == "false":
        return FOUNDED
    if kept != "true":
        return "unknown: the start job did not finish"
    return count(start.outstanding, start.missing)


def body(end: Checkpoint, run: Run, since: str, runs: int) -> str:
    """Write the standing issue's body.

    Returns:
        The body, every value present or its absence named.

    """
    if end.size is None or end.when is None:
        head = f"unknown: {end.missing}"
    else:
        head = f"{end.size:,} at {end.when.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    nxt = count(end.next, end.missing)
    return "\n".join([
        "The signer-identity watch is not gaining on Fulcio's certificate log:",
        "certificates logged at or after the checkpoint's next index have not",
        "been reconciled. Every run that does not gain replaces this body; a",
        "run that judges to the head of the log closes this issue. A certificate",
        "that does not reconcile files its own issue, never this one.",
        "",
        f"- Checkpoint next index (everything before it is judged): {nxt}",
        f"- Checkpoint signed tree head: {head}",
        f"- Entries outstanding when this run started: {run.start}",
        f"- Entries outstanding now: {count(end.outstanding, end.missing)}",
        f"- This run: {run.why}",
        f"- Legs: {run.legs or 'unknown: no result'}",
        f"- Behind since: {since}",
        f"- Latest run behind: {run.url}",
        f"- Runs behind since it opened: {runs}",
        "",
    ])


def behind(repo: str, end: Checkpoint, run: Run) -> str:
    """Create the standing issue, or replace its body in place.

    Returns:
        What was done, for the run's log.

    """
    found = open_standing(repo)
    if not found:
        text = body(end, run, run.url, 1)
        gh("issue", "create", "--repo", repo, "--title", TITLE, "--body", text)
        return "opened the standing issue"
    issue = found[0]
    previous = issue.get("body") or ""
    since_match = BEHIND_SINCE.search(previous)
    since = since_match.group(1) if since_match else "unknown: not in the previous body"
    count_match = RUNS_BEHIND.search(previous)
    runs = int(count_match.group(1)) + 1 if count_match else 1
    text = body(end, run, since, runs)
    number = str(issue["number"])
    gh("issue", "edit", number, "--repo", repo, "--body", text)
    return f"updated the standing issue #{number} ({runs} runs behind)"


def caught_up(repo: str, end: Checkpoint, run_url: str) -> str:
    """Close the standing issue, if one is open.

    Returns:
        What was done, for the run's log.

    """
    found = open_standing(repo)
    if not found:
        return "no standing issue open"
    size = count(end.size, end.missing)
    comment = f"Caught up: {run_url} judged the log to its head, tree size {size}."
    for issue in found:
        gh("issue", "close", str(issue["number"]), "--repo", repo, "--comment", comment)
    return "closed " + ", ".join(f"#{i['number']}" for i in found)


def keep(repo: str, standing: str | None, end: Checkpoint, run: Run) -> str:
    """Take the standing action the outcome names.

    Returns:
        What was done, for the run's log.

    """
    if standing == "behind":
        return behind(repo, end, run)
    if standing == "caught-up":
        return caught_up(repo, end, run.url)
    return "nothing to keep"


def main(argv: list[str] | None = None) -> int:
    """Judge one run and keep the standing issue.

    Returns:
        0 green, EXIT_RED red, EXIT_ERROR when a read or write failed.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", type=Path, required=True)
    parser.add_argument("--start-kept", default="")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--legs", default="")
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args(argv)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        print("standing-issue: GITHUB_REPOSITORY is not set", file=sys.stderr)
        return EXIT_ERROR
    start = read_checkpoint(args.start)
    end = read_checkpoint(args.checkpoint)
    outcome = judge(start, args.start_kept, end)
    where = end.missing if end.next is None else f"next index {end.next}"
    print(f"standing-issue: the newest checkpoint: {where}")
    run = Run(start_count(start, args.start_kept), outcome.why, args.legs, args.run_url)
    try:
        done = keep(repo, outcome.standing, end, run)
    except StandingIssueError as err:
        print(f"standing-issue: {err}; nothing was filed", file=sys.stderr)
        return EXIT_ERROR
    print(f"standing-issue: {done}")
    if outcome.red:
        print(f"::error::{outcome.why}")
        return EXIT_RED
    print(f"standing-issue: green: {outcome.why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
