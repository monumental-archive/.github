#!/usr/bin/env python3
"""lint:installer-pinned: every mise-action step pins mise's own version.

Found the expensive way (v0.18.2 darwin leg): every tool was pinned and
every `uses:` SHA-pinned, but the tool that installs them floated. The
task's comment in mise/config.toml has the history (#117); this is its
logic (#1911).

Per file, two counts of non-comment lines: the `jdx/mise-action@` steps
and the `version: YYYY.N.N` pins. A file with more steps than pins is
red, named with both counts; a file with no step is not judged.

Usage:
  installer_pinned.py WORKFLOW...

Each workflow is read as UTF-8, strictly. Exit 0 with one line, 1 with
every short file named, 2 on a usage or read error. Stdlib only.
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
STEP = re.compile(rf"^[^#]*uses:{SPACE}*jdx/mise-action@")
PIN = re.compile(rf"^[^#]*version:{SPACE}*[0-9]{{4}}\.[0-9]+\.[0-9]+")


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


def shortfall(text: str) -> tuple[int, int] | None:
    """Count one workflow's mise-action steps and its version pins.

    Returns:
        (steps, pins) when it has a step and fewer pins than steps;
        None when it is not judged or holds.

    """
    lines = text.split("\n")
    steps = sum(1 for line in lines if STEP.search(line))
    pins = sum(1 for line in lines if PIN.search(line))
    if steps == 0 or steps <= pins:
        return None
    return steps, pins


def main(argv: list[str]) -> int:
    """Judge every workflow named.

    Returns:
        0 when every step is pinned, EXIT_RED when one is not,
        EXIT_ERROR on a usage or read error.

    """
    if not argv:
        findings.finding("", "usage: installer_pinned.py WORKFLOW...")
        return EXIT_ERROR
    bad = ""
    for name in argv:
        try:
            text = read(Path(name))
        except ValueError as err:
            findings.finding("", str(err))
            return EXIT_ERROR
        short = shortfall(text)
        if short is not None:
            bad += f" {name}({short[0]} steps, {short[1]} version pins)"
    if bad:
        findings.finding(
            "",
            f"mise-action without a mise version pin:{bad}",
            [
                "the installer is a build input — an unpinned mise delivers",
                "same-day releases straight into attested builds (v0.18.2 darwin leg)",
            ],
        )
        return EXIT_RED
    print("lint:installer-pinned: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
