#!/usr/bin/env python3
"""Run lychee and record each broken link (#1955).

`audit:links` used to show only lychee's human lines, so a dead link
never reached the red-check issue as a record (#1890). lychee already
holds each failure as data: its `--format json` report. This is the one
reader of it; mise/stele_findings.py is the shape it follows.

Usage:
  lychee_findings.py [<lychee's arguments>...]

runs `lychee --format json <arguments>` once, lychee's stderr passed
through, and decodes the one document lychee writes to stdout. Each
entry of its `error_map` goes through mise/findings.py: the subject is
the file the entry is about (the map's key), the message
`<url>: <status.text>`, the detail `line <span.line>`. The line is
never in the subject or the message, so an edit above a broken link
does not make it a new one. Then `<task>: lychee: <n> broken link(s)`
goes to stdout.

The exit status is lychee's own, always. The refusals exit 1, each by
name and recording nothing: no lychee on PATH, and output that is not a
report of the shape read here (the message carrying lychee's status). A
lychee pin whose report moves turns `audit:links` red until this moves
with it. The other maps (excluded, redirected, timed out) are not
failures lychee's status counts as broken links, so they are not
recorded.
"""

import shutil
import subprocess
import sys
from dataclasses import dataclass

import decoded
import findings

# The belt's lychee, as PATH answers it; read as the command by the job
# install law (release/test_job_installs.py).
LYCHEE = shutil.which("lychee") or "lychee"
EXIT_REFUSED = 1


class NotAReportError(ValueError):
    """lychee's output is not a report this reader implements."""


@dataclass(frozen=True)
class Broken:
    """One entry of the report's `error_map`, as the writer records it."""

    subject: str
    message: str
    detail: tuple[str, ...]


def broken(subject: str, item: object, where: str) -> Broken:
    """Read one entry of a file's list in the report's `error_map`.

    Returns:
        The file; `<url>: <status.text>`; and `line <span.line>`.

    """
    entry = decoded.as_object(item, where)
    url = decoded.as_str(entry.get("url"), f"{where}: url")
    status = decoded.as_object(entry.get("status"), f"{where}: status")
    text = decoded.as_str(status.get("text"), f"{where}: status.text")
    span = decoded.as_object(entry.get("span"), f"{where}: span")
    line = decoded.as_int(span.get("line"), f"{where}: span.line")
    return Broken(subject, f"{url}: {text}", (f"line {line}",))


def decode(output: bytes) -> tuple[Broken, ...]:
    """Decode lychee's report, every entry of its `error_map` read.

    Returns:
        Each broken link, in the report's order.

    """
    doc = decoded.as_object(decoded.load_json(output), "the report")
    errors = decoded.as_object(doc.get("error_map"), "error_map")
    out: list[Broken] = []
    for subject, listed in errors.items():
        entries = decoded.as_array(listed, f"error_map[{subject}]")
        out.extend(
            broken(subject, e, f"error_map[{subject}][{at}]")
            for at, e in enumerate(entries)
        )
    return tuple(out)


def read_report(output: bytes) -> tuple[Broken, ...]:
    """Decode lychee's report, every entry read before any is recorded.

    Returns:
        Each broken link, in the report's order.

    Raises:
        NotAReportError: the output is not one report of the shape read
            here.

    """
    try:
        return decode(output)
    except decoded.UndecodableError as err:
        msg = f"the output is not lychee's report: {err}"
        raise NotAReportError(msg) from err


def main(argv: list[str]) -> int:
    """Run lychee and record each link it found broken.

    Returns:
        lychee's exit status; EXIT_REFUSED when its output is not a
        report of the shape read here, or lychee is not on PATH.

    """
    task = findings.task_name()
    try:
        ran = subprocess.run(
            [LYCHEE, "--format", "json", *argv],
            stdout=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        print(f"{task}: lychee is not on PATH", file=sys.stderr)
        return EXIT_REFUSED
    try:
        report = read_report(ran.stdout)
    except NotAReportError as err:
        print(f"{task}: lychee exited {ran.returncode}: {err}", file=sys.stderr)
        return EXIT_REFUSED
    for each in report:
        findings.finding(each.subject, each.message, each.detail)
    print(f"{task}: lychee: {len(report)} broken link(s)", flush=True)
    return ran.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
