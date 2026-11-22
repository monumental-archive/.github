#!/usr/bin/env python3
"""Run pinact and record each finding of its SARIF report (#1955).

`audit:action-pins` used to show only pinact's human lines, so a lying
version comment or a pin younger than the floor never reached the
red-check issue as a record (#1890). pinact already holds each finding
as data: `--format sarif`, its only machine format, a SARIF 2.1.0 log
on stdout. This is the one reader of it; mise/stele_findings.py is the
shape it follows.

Usage:
  pinact_findings.py [<pinact run's arguments>...]

runs `pinact run --format sarif <arguments>` once, pinact's stderr (its
diff) passed through, and decodes the one log pinact writes to stdout.
Each result of each run goes through mise/findings.py: the subject is
the result's artifact URI, the message `<ruleId>: <the file's line at
region.startLine, trimmed>` (the owner's ruling on #1955), read here
from that file, the detail its `message.text` and
`line <region.startLine>`. The line is never in the subject or the
message, so an edit above a finding does not make it a new one. Then
`<task>: pinact: <n> finding(s)` goes to stdout.

The exit status is pinact's own, always. The refusals exit 1, each by
name and recording nothing: no pinact on PATH, output that is not a
SARIF VERSION log of the shape read here, and a result whose line
cannot be read (no such file, or fewer lines), the message carrying
pinact's status. A pinact pin whose report moves turns `audit:action-pins` red
until this moves with it.
"""

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import decoded
import findings
from decoded import UndecodableError

VERSION = "2.1.0"
# The belt's pinact, as PATH answers it; read as the command by the job
# install law (release/test_job_installs.py).
PINACT = shutil.which("pinact") or "pinact"
EXIT_REFUSED = 1


class NotAReportError(ValueError):
    """pinact's output is not a report this reader implements."""


@dataclass(frozen=True)
class Found:
    """One SARIF result, as the writer records it."""

    subject: str
    message: str
    detail: tuple[str, ...]


def message(rule: str, line: str) -> str:
    """Write a result's message (the owner's ruling on #1955).

    pinact's text is one text per rule for `parse-error`, and carries
    the resolved SHA for `unpinned-action`, so it is no identity: two
    results of a rule in one file would read the same, and a release
    would make an old one new. The source line it points at is.

    Returns:
        `<ruleId>: <the line, trimmed>`.

    """
    return f"{rule}: {line.strip()}"


def source(uri: str, line: int, where: str) -> str:
    """Read the line a result points at, from the file its URI names.

    Returns:
        The line, as the file holds it.

    Raises:
        UndecodableError: no such file, or no such line in it.

    """
    try:
        lines = Path(uri).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as err:
        msg = f"{where}: {uri} cannot be read: {err}"
        raise UndecodableError(msg) from err
    if not 1 <= line <= len(lines):
        msg = f"{where}: {uri} has no line {line}"
        raise UndecodableError(msg)
    return lines[line - 1]


def found(item: object, where: str) -> Found:
    """Read one SARIF result.

    Returns:
        Its first location's artifact URI; `<ruleId>: <the line it
        points at>`; and its `message.text` and `line <startLine>`.

    Raises:
        UndecodableError: the result is not of the shape read here.

    """
    result = decoded.as_object(item, where)
    said = decoded.as_object(result.get("message"), f"{where}: message")
    text = decoded.as_str(said.get("text"), f"{where}: message.text")
    locations = decoded.as_array(result.get("locations"), f"{where}: locations")
    if not locations:
        msg = f"{where}: locations is empty"
        raise UndecodableError(msg)
    at = f"{where}: locations[0]"
    location = decoded.as_object(locations[0], at)
    physical = decoded.as_object(
        location.get("physicalLocation"), f"{at}: physicalLocation"
    )
    artifact = decoded.as_object(
        physical.get("artifactLocation"), f"{at}: artifactLocation"
    )
    uri = decoded.as_str(artifact.get("uri"), f"{at}: artifactLocation.uri")
    region = decoded.as_object(physical.get("region"), f"{at}: region")
    line = decoded.as_int(region.get("startLine"), f"{at}: region.startLine")
    rule = decoded.as_str(result.get("ruleId"), f"{where}: ruleId")
    return Found(uri, message(rule, source(uri, line, at)), (text, f"line {line}"))


def decode(output: bytes) -> tuple[Found, ...]:
    """Decode pinact's SARIF log, every result in it read.

    Returns:
        Each result, in the log's order.

    Raises:
        NotAReportError: the log is not SARIF VERSION.

    """
    doc = decoded.as_object(decoded.load_json(output), "the report")
    version = decoded.as_str(doc.get("version"), "version")
    if version != VERSION:
        msg = f"the report is SARIF {version}; this adapter reads SARIF {VERSION}"
        raise NotAReportError(msg)
    out: list[Found] = []
    for at, run in enumerate(decoded.as_array(doc.get("runs"), "runs")):
        where = f"runs[{at}]"
        listed = decoded.as_object(run, where).get("results")
        results = decoded.as_array(listed, f"{where}: results")
        out.extend(found(r, f"{where}.results[{n}]") for n, r in enumerate(results))
    return tuple(out)


def read_report(output: bytes) -> tuple[Found, ...]:
    """Decode pinact's log, every result read before any is recorded.

    Returns:
        Each result, in the log's order.

    Raises:
        NotAReportError: the output is not one SARIF VERSION log of the
            shape read here.

    """
    try:
        return decode(output)
    except decoded.UndecodableError as err:
        msg = f"the output is not pinact's report: {err}"
        raise NotAReportError(msg) from err


def main(argv: list[str]) -> int:
    """Run pinact and record each result of its log.

    Returns:
        pinact's exit status; EXIT_REFUSED when its output is not a
        SARIF VERSION log of the shape read here, or pinact is not on
        PATH.

    """
    task = findings.task_name()
    try:
        ran = subprocess.run(
            [PINACT, "run", "--format", "sarif", *argv],
            stdout=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        print(f"{task}: pinact is not on PATH", file=sys.stderr)
        return EXIT_REFUSED
    try:
        report = read_report(ran.stdout)
    except NotAReportError as err:
        print(f"{task}: pinact exited {ran.returncode}: {err}", file=sys.stderr)
        return EXIT_REFUSED
    for each in report:
        findings.finding(each.subject, each.message, each.detail)
    print(f"{task}: pinact: {len(report)} finding(s)", flush=True)
    return ran.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
