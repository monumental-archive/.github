#!/usr/bin/env python3
"""Judge a run of the signer-identity watch, and keep its standing issue.

rekor-monitor.yml runs `stele assert signer-certificates` (#1339), which
reads Fulcio's certificate log in chunks from a checkpoint and seals a
report. This reads stele's exit status, its report and its checkpoint,
says whether the run is green, and keeps one standing issue while the
watch is behind (#1353). A certificate that does not reconcile files its
own issue (findings.py); that is not this tool.

  0, saved      judged to the head: green; close the standing issue.
  1, saved      a certificate did not reconcile (each has its issue):
                red.
  4, saved      a range is outstanding. Green when the checkpoint
                advanced, red when it did not or the report carries a
                finding. Behind when it did not advance, or when the
                checkpoint's signed tree head is more than 24 hours old.
  anything else red, behind: stele did not finish, exited otherwise, or
                its checkpoint was not saved (no report was written, or
                a finding could not be filed).

A run that advanced a saved checkpoint short of the head asks for the
next run (`dispatch=true` in GITHUB_OUTPUT): a backlog is read off run
after run, never left to the schedule.

Behind: no open issue with exactly the title, create it; one open,
replace its body in place, keeping "behind since" and counting this run.
Caught up: close it with one comment. The title is matched exactly, on
every open issue the forge lists; a search's ranking is never trusted to
find it. A listing that fails exits 2 by name and files nothing: a
failed read never opens a second issue.

The checkpoint is stele's own file (`next`, the first index not yet
judged, and `treeHead`, the last head it verified); the one named is the
one that persists: this run's when it was saved, the restored one when
it was not. A value that cannot be read is said by name in the body.

Usage:
  standing-issue.py --exit-code N --report FILE --checkpoint FILE
                    --before FILE --saved true|false --run-url URL

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
TARGET = "assert signer-certificates"
EXIT_RED = 1
EXIT_ERROR = 2
STALE = dt.timedelta(hours=24)

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


@dataclass(frozen=True)
class Report:
    """What this tool reads of stele's report, or why it could not."""

    verdict: str | None
    facts: dict[str, str]
    findings: int
    missing: str | None


@dataclass(frozen=True)
class Run:
    """The run being judged, as the standing issue names it."""

    log_size: int | None
    why: str
    url: str


@dataclass(frozen=True)
class Outcome:
    """The run's verdict: colour, why, the standing action, dispatch."""

    red: bool
    why: str
    standing: str | None
    dispatch: bool


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
    except ValueError, TypeError, KeyError:
        return Checkpoint(None, None, None, "the checkpoint file could not be read")
    return Checkpoint(nxt, size, when, None)


def read_report(path: Path) -> Report:
    """Read stele's report.

    Returns:
        Its verdict, facts and finding count, or why it could not be read.

    """
    if not path.is_file() or path.stat().st_size == 0:
        return Report(None, {}, 0, "stele wrote no report")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("target") != TARGET:
            return Report(None, {}, 0, f"the report is not stele's {TARGET} report")
        facts = {f["name"]: f["value"] for f in doc.get("facts") or []}
        return Report(doc["verdict"], facts, len(doc.get("findings") or []), None)
    except ValueError, TypeError, KeyError, AttributeError:
        return Report(None, {}, 0, "the report could not be read")


def head_size(report: Report) -> int | None:
    """Read the tree size stele verified this run, from its `treeHead` fact.

    Returns:
        The size, or None when stele verified no tree head this run.

    """
    match = re.match(r"^(\d+) at ", report.facts.get("treeHead", ""))
    return int(match.group(1)) if match else None


KILLED = (
    "stele did not finish: no exit status was recorded (a step before it"
    " failed, or the job was cancelled or timed out)"
)


def judge(code: str, report: Report, cp: Checkpoint, *, saved: bool) -> Outcome:
    """Decide the run from stele's exit status, its report and the checkpoint.

    Returns:
        The outcome; `cp` is the checkpoint that persists.

    """
    if not code.isdigit():
        return Outcome(red=True, why=KILLED, standing="behind", dispatch=False)
    if not saved:
        lost = report.missing or "a finding could not be filed"
        why = f"stele exited {code} and the checkpoint was not saved: {lost}"
        return Outcome(red=True, why=why, standing="behind", dispatch=False)
    if code == "0":
        why = "judged to the head of the log"
        return Outcome(red=False, why=why, standing="caught-up", dispatch=False)
    if code == "1":
        why = (
            f"{report.findings} finding(s): a certificate did not reconcile;"
            " each has its issue"
        )
        return Outcome(red=True, why=why, standing=None, dispatch=False)
    if code != "4":
        why = f"stele exited {code}"
        return Outcome(red=True, why=why, standing="behind", dispatch=False)
    return judge_outstanding(report, cp)


def judge_outstanding(report: Report, cp: Checkpoint) -> Outcome:
    """Decide a run that saved its checkpoint with a range outstanding (exit 4).

    Returns:
        Green when the checkpoint advanced and the report holds no finding;
        the next run asked for whenever it advanced short of the head.

    """
    stopped = report.facts.get("stopped", "no reason given")
    if report.facts.get("judged", "none") == "none":
        why = f"the checkpoint did not advance: stele judged nothing ({stopped})"
        return Outcome(red=True, why=why, standing="behind", dispatch=False)
    short = cp.next is not None and cp.size is not None and cp.next < cp.size
    stale = cp.when is None or dt.datetime.now(dt.UTC) - cp.when > STALE
    standing = "behind" if stale else None
    if report.findings:
        why = f"{report.findings} finding(s) in the chunks judged; each has its issue"
        return Outcome(red=True, why=why, standing=standing, dispatch=short)
    left = report.facts.get("outstanding", "unknown")
    why = f"advanced; outstanding {left} ({stopped})"
    return Outcome(red=False, why=why, standing=standing, dispatch=short)


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


def body(cp: Checkpoint, run: Run, since: str, runs: int) -> str:
    """Write the standing issue's body.

    Returns:
        The body, every value present or its absence named.

    """
    nxt = thousands(cp.next) if cp.next is not None else f"unknown: {cp.missing}"
    if cp.size is None or cp.when is None:
        head = f"unknown: {cp.missing}"
    else:
        head = f"{thousands(cp.size)} at {cp.when.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    unverified = "unknown: stele verified no tree head this run"
    log = thousands(run.log_size) if run.log_size is not None else unverified
    if cp.next is None:
        left = f"unknown: {cp.missing}"
    elif run.log_size is None:
        left = unverified
    else:
        left = thousands(run.log_size - cp.next)
    return "\n".join([
        "The signer-identity watch is behind on Fulcio's certificate log:",
        "certificates logged at or after the checkpoint's next index have not",
        "been reconciled. Every run that leaves it behind replaces this body; a",
        "run that judges to the head of the log closes this issue. A certificate",
        "that does not reconcile files its own issue, never this one.",
        "",
        f"- Checkpoint next index (everything before it is judged): {nxt}",
        f"- Checkpoint signed tree head: {head}",
        f"- Log size this run: {log}",
        f"- Entries outstanding: {left}",
        f"- This run: {run.why}",
        f"- Behind since: {since}",
        f"- Latest run behind: {run.url}",
        f"- Runs behind since it opened: {runs}",
        "",
    ])


def behind(repo: str, cp: Checkpoint, run: Run) -> str:
    """Create the standing issue, or replace its body in place.

    Returns:
        What was done, for the run's log.

    """
    found = open_standing(repo)
    if not found:
        text = body(cp, run, run.url, 1)
        gh("issue", "create", "--repo", repo, "--title", TITLE, "--body", text)
        return "opened the standing issue"
    issue = found[0]
    previous = issue.get("body") or ""
    since_match = BEHIND_SINCE.search(previous)
    since = since_match.group(1) if since_match else "unknown: not in the previous body"
    count_match = RUNS_BEHIND.search(previous)
    runs = int(count_match.group(1)) + 1 if count_match else 1
    text = body(cp, run, since, runs)
    number = str(issue["number"])
    gh("issue", "edit", number, "--repo", repo, "--body", text)
    return f"updated the standing issue #{number} ({runs} runs behind)"


def caught_up(repo: str, cp: Checkpoint, run_url: str) -> str:
    """Close the standing issue, if one is open.

    Returns:
        What was done, for the run's log.

    """
    found = open_standing(repo)
    if not found:
        return "no standing issue open"
    size = thousands(cp.size) if cp.size is not None else f"unknown ({cp.missing})"
    comment = f"Caught up: {run_url} judged the log to its head, tree size {size}."
    for issue in found:
        gh("issue", "close", str(issue["number"]), "--repo", repo, "--comment", comment)
    return "closed " + ", ".join(f"#{i['number']}" for i in found)


def main(argv: list[str] | None = None) -> int:
    """Judge one run and keep the standing issue.

    Returns:
        0 green, EXIT_RED red, EXIT_ERROR when a read or write failed.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exit-code", default="")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--saved", choices=["true", "false"], required=True)
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args(argv)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        print("standing-issue: GITHUB_REPOSITORY is not set", file=sys.stderr)
        return EXIT_ERROR
    saved = args.saved == "true"
    report = read_report(args.report)
    cp = read_checkpoint(args.checkpoint if saved else args.before)
    outcome = judge(args.exit_code, report, cp, saved=saved)
    which = "saved by this run" if saved else "restored, not saved by this run"
    where = cp.missing if cp.next is None else f"next index {cp.next}"
    print(f"standing-issue: the checkpoint that persists ({which}): {where}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as f:
            f.write(f"dispatch={str(outcome.dispatch).lower()}\n")
    try:
        if outcome.standing == "behind":
            run = Run(head_size(report), outcome.why, args.run_url)
            print(f"standing-issue: {behind(repo, cp, run)}")
        elif outcome.standing == "caught-up":
            print(f"standing-issue: {caught_up(repo, cp, args.run_url)}")
    except StandingIssueError as err:
        print(f"standing-issue: {err}; nothing was filed", file=sys.stderr)
        return EXIT_ERROR
    if outcome.red:
        print(f"::error::{outcome.why}")
        return EXIT_RED
    print(f"standing-issue: green: {outcome.why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
