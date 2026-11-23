#!/usr/bin/env python3
"""lint:capability-boundary: no caller-code workflow holds a signing identity.

The org's SLSA Build L3 boundary, made mechanical. A workflow that
declares `workflow_call` (the machine-checkable proxy for "runs caller
code") and grants `id-token: write` is red unless it carries a
`capability-boundary:` marker saying why it runs none. The task's
comment in mise/config.toml has the reasons; this is its logic (#1911).

Three line tests, each on one line of the file:

  `workflow_call:` as a key, anywhere in the file;
  a real grant: `id-token:` then `write`, with no `#` before it on its
  line, so prose about the grant is not one;
  a marker: a comment line whose FIRST token is `capability-boundary:`
  (#676), so prose about the marker is not one.

Scope is per FILE: one marker covers every grant in it.

Usage:
  capability_boundary.py WORKFLOW...

Each workflow is read as UTF-8, strictly. Exit 0 with one line, 1 with
every unmarked file named, 2 on a usage or read error. Stdlib only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import findings

EXIT_RED = 1
EXIT_ERROR = 2
# grep's [[:space:]] within one line: lines split on "\n" alone, as grep's.
SPACE = r"[ \t\r\f\v]"
CALLED = re.compile(rf"^{SPACE}*workflow_call:")
GRANT = re.compile(rf"^[^#]*id-token:{SPACE}*write")
MARKER = re.compile(rf"^{SPACE}*#{SPACE}*capability-boundary:")


def read(path: Path) -> str:
    """Read a workflow as UTF-8, strictly.

    Returns:
        Its text.

    Raises:
        ValueError: the file cannot be read or is not UTF-8.

    """
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as err:
        msg = f"{path}: cannot be read as UTF-8: {err}"
        raise ValueError(msg) from err


def any_line(pattern: re.Pattern[str], lines: list[str]) -> bool:
    """Say whether one line of a file matches a pattern from its start.

    Returns:
        True when some line matches.

    """
    return any(pattern.search(line) for line in lines)


def unmarked(text: str) -> bool:
    """Judge one workflow against the boundary.

    Returns:
        True when it is called, grants id-token and carries no marker.

    """
    lines = text.split("\n")
    return (
        any_line(CALLED, lines)
        and any_line(GRANT, lines)
        and not any_line(MARKER, lines)
    )


def main(argv: list[str]) -> int:
    """Judge every workflow named.

    Returns:
        0 when none breaches the boundary, EXIT_RED when one does,
        EXIT_ERROR on a usage or read error.

    """
    if not argv:
        findings.finding("", "usage: capability_boundary.py WORKFLOW...")
        return EXIT_ERROR
    bad = ""
    for name in argv:
        try:
            text = read(Path(name))
        except ValueError as err:
            findings.finding("", str(err))
            return EXIT_ERROR
        if unmarked(text):
            bad += f" {name}"
    if bad:
        findings.finding(
            "",
            "workflow_call workflow holds id-token",
            [f"without a capability-boundary marker:{bad}"],
        )
        return EXIT_RED
    print("lint:capability-boundary: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
