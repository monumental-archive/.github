#!/usr/bin/env python3
"""Judge one leg of the signer-identity watch (#1339).

Each leg of rekor-monitor.yml runs `stele assert signer-certificates`
once, files the report's findings (certificate_issues.py) and saves the checkpoint
only when they are filed. This says whether the leg is green, from
stele's exit status, its report and whether the checkpoint was saved.
It files nothing: the standing issue is the run's, judged once after the
last leg (watch_issue.py).

  0, saved      judged to the head: green.
  1, saved      a certificate did not reconcile (each has its issue): red.
  4, saved      a range is outstanding: green when the leg advanced the
                checkpoint, red when it did not. A finding in the report
                is red whatever the exit code: a certificate that does not
                reconcile is what this watch exists to raise.
  anything else red: stele did not finish, exited otherwise, the
                checkpoint was not saved (no report was written, or a
                finding could not be filed), or the report could not be
                read here (its findings cannot be counted).

Usage:
  verdict.py --exit-code N --report FILE --saved true|false

Stdlib only.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

# The canon's one writer of workflow commands, in mise/ (#1660).
# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[2] / "mise"))

import decoded
import workflow_command

TARGET = "assert signer-certificates"
EXIT_RED = 1
SAVED = ("true", "false")
KILLED = (
    "stele did not finish: no exit status was recorded"
    " (a step before it failed, or the leg was cancelled or timed out)"
)


class Args(argparse.Namespace):
    """The command line, typed."""

    exit_code: str
    report: Path
    saved: str


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
    # Every way the file is not stele's report shape is one refusal:
    # not strict JSON (nested past the parser's depth was a traceback
    # until prop_verdict.py, #1557), or a field of the wrong kind.
    try:
        doc = decoded.as_object(decoded.load_json(path.read_bytes()), "the report")
        if doc.get("target") != TARGET:
            return Report({}, 0, f"the report is not stele's {TARGET} report")
        facts = read_facts(doc.get("facts"))
        findings = doc.get("findings")
        count = 0 if findings is None else len(decoded.as_array(findings, "findings"))
    except decoded.UndecodableError:
        return Report({}, 0, "the report could not be read")
    return Report(facts, count, None)


def read_facts(value: object) -> dict[str, str]:
    """Read stele's facts: name and value, both strings.

    A fact that is not that shape is refused by decoded's readers.

    Returns:
        Each fact's value by its name; none when there are none.

    """
    if value is None:
        return {}
    facts: dict[str, str] = {}
    for index, item in enumerate(decoded.as_array(value, "facts")):
        fact = decoded.as_object(item, f"fact {index}")
        name = decoded.as_str(fact.get("name"), f"fact {index}: name")
        facts[name] = decoded.as_str(fact.get("value"), f"fact {index}: value")
    return facts


def judge(code: str, report: Report, *, saved: bool) -> tuple[bool, str]:
    """Decide the leg.

    Returns:
        Whether the leg is red, and why.

    """
    if not code.isdigit():
        return True, KILLED
    if not saved or report.missing:
        # Saved, yet unreadable here, is red too: its findings cannot be
        # counted, and a finding is red whatever the exit code. Facts that
        # were not stele's shape hid a finding, green on exit 0, until #1557.
        lost = report.missing or "a finding could not be filed"
        state = "its report could not be judged"
        if not saved:
            state = "the checkpoint was not saved"
        return True, f"stele exited {code} and {state}: {lost}"
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


def main(argv: list[str]) -> int:
    """Judge one leg.

    Returns:
        0 green, EXIT_RED red.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exit-code", default="")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--saved", choices=SAVED, required=True)
    args = parser.parse_args(argv, namespace=Args())
    report = read_report(args.report)
    red, why = judge(args.exit_code, report, saved=args.saved == "true")
    if red:
        workflow_command.error(why)
        return EXIT_RED
    print(f"verdict: green: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
