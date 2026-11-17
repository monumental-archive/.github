#!/usr/bin/env python3
"""Judge one leg of the signer-identity watch (#1339).

Each leg of rekor-monitor.yml runs `stele assert signer-certificates`
once, files the report's findings (findings.py) and saves the checkpoint
only when they are filed. This says whether the leg is green, from
stele's exit status, its report and whether the checkpoint was saved.
It files nothing: the standing issue is the run's, judged once after the
last leg (standing-issue.py).

  0, saved      judged to the head: green.
  1, saved      a certificate did not reconcile (each has its issue): red.
  4, saved      a range is outstanding: green when the leg advanced the
                checkpoint, red when it did not. A finding in the report
                is red whatever the exit code: a certificate that does not
                reconcile is what this watch exists to raise.
  anything else red: stele did not finish, exited otherwise, or the
                checkpoint was not saved (no report was written, or a
                finding could not be filed).

Usage:
  verdict.py --exit-code N --report FILE --saved true|false

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

TARGET = "assert signer-certificates"
EXIT_RED = 1
KILLED = (
    "stele did not finish: no exit status was recorded"
    " (a step before it failed, or the leg was cancelled or timed out)"
)


@dataclass(frozen=True)
class Report:
    """What this tool reads of stele's report, or why it could not."""

    facts: dict[str, str]
    findings: int
    missing: str | None


def read_report(path: Path) -> Report:
    """Read stele's report.

    Returns:
        Its facts and finding count, or why it could not be read.

    """
    if not path.is_file() or path.stat().st_size == 0:
        return Report({}, 0, "stele wrote no report")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("target") != TARGET:
            return Report({}, 0, f"the report is not stele's {TARGET} report")
        facts = {f["name"]: f["value"] for f in doc.get("facts") or []}
        return Report(facts, len(doc.get("findings") or []), None)
    except ValueError, TypeError, KeyError, AttributeError:
        return Report({}, 0, "the report could not be read")


def judge(code: str, report: Report, *, saved: bool) -> tuple[bool, str]:
    """Decide the leg.

    Returns:
        Whether the leg is red, and why.

    """
    if not code.isdigit():
        return True, KILLED
    if not saved:
        lost = report.missing or "a finding could not be filed"
        return True, f"stele exited {code} and the checkpoint was not saved: {lost}"
    if report.findings:
        return True, (
            f"{report.findings} finding(s): a certificate did not reconcile;"
            " each has its issue"
        )
    if code == "0":
        return False, "judged to the head of the log"
    if code != "4":
        return True, f"stele exited {code}"
    return outstanding(report)


def outstanding(report: Report) -> tuple[bool, str]:
    """Decide a saved leg that ended with a range outstanding (exit 4).

    Returns:
        Red when it judged nothing, green when it advanced; stele's
        reason for stopping either way.

    """
    stopped = report.facts.get("stopped", "no reason given")
    if report.facts.get("judged", "none") == "none":
        return True, f"the checkpoint did not advance: stele judged nothing ({stopped})"
    left = report.facts.get("outstanding", "unknown")
    return False, f"advanced; outstanding {left} ({stopped})"


def main(argv: list[str] | None = None) -> int:
    """Judge one leg.

    Returns:
        0 green, EXIT_RED red.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exit-code", default="")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--saved", choices=["true", "false"], required=True)
    args = parser.parse_args(argv)
    report = read_report(args.report)
    red, why = judge(args.exit_code, report, saved=args.saved == "true")
    if red:
        print(f"::error::{why}")
        return EXIT_RED
    print(f"verdict: green: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
