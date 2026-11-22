#!/usr/bin/env python3
"""Run a stele assertion and record its findings (#1935).

A belt task that runs `stele assert <target>` used to show only stele's
human lines, so its findings never reached the red-check issue as
records (#1890). stele already holds each finding as data: its report,
`--json`, is its published contract (stele's docs/report-schema.md).
This is the one reader of it.

Usage:
  stele_findings.py assert <target> [<stele's arguments>...]

runs `stele assert <target> --json <arguments>`, stele's stderr passed
through, and decodes the one document stele writes to stdout. Each
entry of its `findings` goes through mise/findings.py: the subject is
the entry's, the message `<assertion>: <detail>` (the assertion alone
without a detail), the detail lines `expected: ...` and `actual: ...`
for those present. A verdict other than PASS with no findings is one
finding, the document's subject and the verdict. Then
`<task>: stele: <verdict>` goes to stdout.

The exit status is stele's own, always. The refusals exit 1, each by
name and recording nothing: no stele on PATH; a schema other than
SCHEMA (a consumer reading a schema it does not implement refuses, as
the report schema requires; a stele pin whose report moves turns these
tasks red until this moves with it); and output that is not one JSON
document, the message carrying stele's status. `excused`,
`staleExceptions` and `facts` do not move the verdict, so they are not
recorded.
"""

import shutil
import subprocess
import sys
from dataclasses import dataclass

import decoded
import findings

SCHEMA = 9
# The belt's stele, as PATH answers it; read as the command by the job
# install law (release/test_job_installs.py).
STELE = shutil.which("stele") or "stele"
# The one verdict that is not a finding.
SOUND = frozenset({"PASS"})
EXIT_REFUSED = 1
# `assert` and the target, before stele's own arguments.
NAMED = 2
USAGE = "usage: stele_findings.py assert <target> [<stele's arguments>...]"


class NotAReportError(ValueError):
    """stele's output is not a report this reader implements."""


@dataclass(frozen=True)
class Found:
    """One of the report's findings, as the writer records it."""

    subject: str
    message: str
    detail: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    """What this reads of stele's report."""

    subject: str
    verdict: str
    findings: tuple[Found, ...]


def optional(entry: dict[str, object], key: str, where: str) -> str | None:
    """Read an entry's optional text field.

    Returns:
        The text, or None when the entry has no such field.

    """
    value = entry.get(key)
    return None if value is None else decoded.as_str(value, f"{where}: {key}")


def found(item: object, where: str) -> Found:
    """Read one entry of the report's `findings`.

    Returns:
        Its subject; `<assertion>: <detail>`, or the assertion alone;
        and `expected: ...` and `actual: ...` for those present.

    """
    entry = decoded.as_object(item, where)
    subject = decoded.as_str(entry.get("subject"), f"{where}: subject")
    assertion = decoded.as_str(entry.get("assertion"), f"{where}: assertion")
    said = optional(entry, "detail", where)
    detail: list[str] = []
    for key in ("expected", "actual"):
        value = optional(entry, key, where)
        if value is not None:
            detail.append(f"{key}: {value}")
    return Found(subject, f"{assertion}: {said}" if said else assertion, tuple(detail))


def decode(output: bytes) -> Report:
    """Decode stele's report, every finding in it read.

    Returns:
        The report's subject, verdict and findings.

    Raises:
        NotAReportError: the report is not of SCHEMA.

    """
    doc = decoded.as_object(decoded.load_json(output), "the report")
    schema = decoded.as_int(doc.get("schema"), "schema")
    if schema != SCHEMA:
        msg = f"the report is schema {schema}; this adapter reads schema {SCHEMA}"
        raise NotAReportError(msg)
    listed = doc.get("findings")
    entries = [] if listed is None else decoded.as_array(listed, "findings")
    return Report(
        decoded.as_str(doc.get("subject"), "subject"),
        decoded.as_str(doc.get("verdict"), "verdict"),
        tuple(found(e, f"findings[{at}]") for at, e in enumerate(entries)),
    )


def read_report(output: bytes) -> Report:
    """Decode stele's report, every finding in it read before any is recorded.

    Returns:
        The report's subject, verdict and findings.

    Raises:
        NotAReportError: the output is not one JSON document of SCHEMA.

    """
    try:
        return decode(output)
    except decoded.UndecodableError as err:
        msg = f"the output is not stele's report: {err}"
        raise NotAReportError(msg) from err


def main(argv: list[str]) -> int:
    """Run stele's assertion and record what it found.

    Returns:
        stele's exit status; EXIT_REFUSED when its output is not a
        report of SCHEMA, stele is not on PATH, or the arguments name
        no assertion.

    """
    task = findings.task_name()
    if len(argv) < NAMED or argv[0] != "assert":
        findings.finding("", USAGE)
        return EXIT_REFUSED
    target, rest = argv[1], argv[NAMED:]
    named = f"stele assert {target}"
    try:
        ran = subprocess.run(
            [STELE, "assert", target, "--json", *rest],
            stdout=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        findings.finding(named, "stele is not on PATH")
        return EXIT_REFUSED
    try:
        report = read_report(ran.stdout)
    except NotAReportError as err:
        findings.finding("", f"{named} exited {ran.returncode}: {err}")
        return EXIT_REFUSED
    for each in report.findings:
        findings.finding(each.subject, each.message, each.detail)
    if report.verdict not in SOUND and not report.findings:
        findings.finding(report.subject, report.verdict)
    print(f"{task}: stele: {report.verdict}", flush=True)
    return ran.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
