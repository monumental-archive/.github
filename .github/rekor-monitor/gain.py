#!/usr/bin/env python3
"""Judge whether a run of the signer-identity watch gained on the log.

rekor-monitor.yml reads Fulcio's certificate log in legs, each one run of
`stele assert signer-certificates` from stele's checkpoint (#1339). Each
leg is judged on its own failures (verdict.py), and a certificate that
does not reconcile files its own issue (certificate_issues.py). This
runs once, after the last leg, as the `gaining` job, and asks one
question of stele's own checkpoint files: did this run gain on the log?
The outstanding count is the checkpoint's tree size minus its `next`,
the first index not yet judged.

  newest checkpoint at its head   caught up: green.
  no checkpoint at the start      the run founded one: no earlier count,
                                  said by name, green.
  did not advance                 red, behind.
  advanced, outstanding not       red, behind: a backlog that is not
  smaller than at the start       shrinking.
  advanced, outstanding smaller   gaining: green.
  no newest checkpoint, or the    red, behind: whether the run gained
  start's was not kept            cannot be told.

Behind is one finding (#1967), recorded by findings.finding with an
empty subject and one fixed message, so the job's red-check issue
(#1966) reads it as still red from run to run. Its detail is the
checkpoint, the counts, this run's outcome, then one line per distinct
reason a leg stopped, read from each leg's report, with the number of
legs that gave it; a leg whose report did not arrive is counted as "no
report". A fact is reported once: this files no issue of its own.

Usage:
  gain.py --start FILE --start-kept true|false|"" --checkpoint FILE
          --reports DIR --legs N

DIR holds each leg's report as a `report.json` at any depth (one
directory per downloaded artifact). No organisation, repository or log
is named here. Stdlib only.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path

# The canon's finding writer (#1890) and document loader (#1676), in
# mise/. Appended, so this file's own directory and the standard
# library come first.
sys.path.append(str(Path(__file__).resolve().parents[2] / "mise"))

import decoded
import findings
import verdict

MESSAGE = "the signer-identity watch is behind"
EXIT_RED = 1
FOUNDED = "none counted: this run founded the checkpoint"
NO_REPORT = "no report"
AT_HEAD = "judged to the head of the log"
NO_REASON = "no reason given"


class Args(argparse.Namespace):
    """The command line, typed."""

    start: Path
    start_kept: str
    checkpoint: Path
    reports: Path
    legs: int


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
    """The run's verdict: behind (red) or not, and why."""

    red: bool
    why: str


def read_checkpoint(path: Path) -> Checkpoint:
    """Read stele's checkpoint file.

    Returns:
        Its next index, tree size and head time, or why they could not be read.

    """
    if not path.is_file() or path.stat().st_size == 0:
        return Checkpoint(None, None, None, "no checkpoint file")
    # Not strict JSON (a `next` of Infinity, JSON nested past the parser's
    # depth), a field that is not an integer, or a timestamp past the
    # platform's range (overflow, OS and value errors): each was a
    # traceback until prop_gain.py (#1557), and each is one refusal.
    # decoded.UndecodableError is a ValueError.
    try:
        return decode_checkpoint(path.read_bytes())
    except ValueError, OverflowError, OSError:
        return Checkpoint(None, None, None, "the checkpoint file could not be read")


def decode_checkpoint(raw: bytes) -> Checkpoint:
    """Decode a checkpoint's next index, tree size and head time.

    Not strict JSON, or a field that is not an integer, is refused by
    decoded's readers; a time past the platform's range by datetime.

    Returns:
        The checkpoint, read.

    """
    doc = decoded.as_object(decoded.load_json(raw), "the checkpoint")
    head = decoded.as_object(doc.get("treeHead"), "treeHead")
    nxt = decoded.as_int(doc.get("next"), "next")
    size = decoded.as_int(head.get("treeSize"), "treeHead.treeSize")
    millis = decoded.as_int(head.get("timestamp"), "treeHead.timestamp")
    return Checkpoint(nxt, size, dt.datetime.fromtimestamp(millis / 1000, dt.UTC), None)


def judge(start: Checkpoint, kept: str, end: Checkpoint) -> Outcome:
    """Decide whether the run gained on the log.

    Returns:
        The outcome; `start` is the run's starting checkpoint, `end` the newest.

    """
    if end.next is None or end.outstanding is None:
        return Outcome(red=True, why=f"the newest checkpoint: {end.missing}")
    if end.outstanding == 0:
        return Outcome(red=False, why=AT_HEAD)
    if kept == "false":
        why = f"advanced to {end.next}; no earlier count, this run founded it"
        return Outcome(red=False, why=why)
    if kept != "true" or start.next is None or start.outstanding is None:
        lost = start.missing if kept == "true" else "the start job did not finish"
        why = f"the checkpoint the run started from was not kept ({lost})"
        return Outcome(red=True, why=why)
    return gained(start.next, start.outstanding, end.next, end.outstanding)


def gained(start_next: int, start_left: int, end_next: int, end_left: int) -> Outcome:
    """Decide a run with both checkpoints, short of the head.

    Returns:
        Red when it did not advance, or advanced with the outstanding
        count not smaller than at the start; otherwise gaining.

    """
    if end_next <= start_next:
        why = f"the checkpoint did not advance in this run (next {end_next})"
        return Outcome(red=True, why=why)
    counts = f"outstanding {start_left:,} at the start, {end_left:,} now"
    if end_left >= start_left:
        return Outcome(red=True, why=f"advanced but not gaining: {counts}")
    return Outcome(red=False, why=f"gaining: {counts}")


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


def stopped(path: Path) -> str:
    """Say why one leg stopped, from its report.

    Returns:
        stele's `stopped` fact; AT_HEAD for a leg that judged to the
        head and gave none; NO_REPORT for an empty report; otherwise
        why the report could not be read, or NO_REASON.

    """
    if path.stat().st_size == 0:
        return NO_REPORT
    report = verdict.read_report(path)
    if report.missing is not None:
        return report.missing
    if "stopped" in report.facts:
        return report.facts["stopped"]
    if report.facts.get("outstanding") == "none":
        return AT_HEAD
    return NO_REASON


def reasons(reports: Path, legs: int) -> list[str]:
    """Count the reasons the legs stopped.

    Each `report.json` under `reports` is one leg's; the legs that
    handed none on are counted as NO_REPORT.

    Returns:
        One line per distinct reason, the most given first.

    """
    found = sorted(reports.rglob("report.json")) if reports.is_dir() else []
    given = collections.Counter(stopped(path) for path in found)
    if legs > len(found):
        given[NO_REPORT] += legs - len(found)
    return [
        f"{n} {'leg' if n == 1 else 'legs'}: {why}" for why, n in given.most_common()
    ]


def detail(start: str, end: Checkpoint, why: str, stops: list[str]) -> list[str]:
    """Write the finding's detail: what #1793's body said, and why legs stopped.

    Returns:
        The lines, every value present or its absence named.

    """
    if end.size is None or end.when is None:
        head = f"unknown: {end.missing}"
    else:
        head = f"{end.size:,} at {end.when.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    nxt = count(end.next, end.missing)
    return [
        f"Checkpoint next index (everything before it is judged): {nxt}",
        f"Checkpoint signed tree head: {head}",
        f"Entries outstanding when this run started: {start}",
        f"Entries outstanding now: {count(end.outstanding, end.missing)}",
        f"This run: {why}",
        *stops,
    ]


def main(argv: list[str]) -> int:
    """Judge one run.

    Returns:
        0 green, EXIT_RED behind.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", type=Path, required=True)
    parser.add_argument("--start-kept", default="")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--legs", type=int, required=True)
    args = parser.parse_args(argv, namespace=Args())
    start = read_checkpoint(args.start)
    end = read_checkpoint(args.checkpoint)
    outcome = judge(start, args.start_kept, end)
    where = end.missing if end.next is None else f"next index {end.next}"
    print(f"gain: the newest checkpoint: {where}")
    if not outcome.red:
        print(f"gain: green: {outcome.why}")
        return 0
    lines = detail(
        start_count(start, args.start_kept),
        end,
        outcome.why,
        reasons(args.reports, args.legs),
    )
    findings.finding("", MESSAGE, lines, annotate=True)
    return EXIT_RED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
