#!/usr/bin/env python3
r"""lint:runner-pin: every runs-on is a named pin, and no label floats.

Found by the #290 audit: a floating runner label (`ubuntu-latest`)
migrates on GitHub's schedule with no diff, no pull request and no
Renovate roll. The task's comment in mise/config.toml has the history;
this is its logic (#1911). Two checks per file:

  every `runs-on:` line's value, its comment and quotes dropped, is a
  `${{ … matrix. … }}` indirection or a pinned literal, name-version
  optionally suffixed (ubuntu-24.04, ubuntu-24.04-arm, macos-15-intel);
  no line that is not a comment carries `ubuntu-`, `macos-` or
  `windows-latest`, which closes the matrix laundering hole: a floating
  value reaching runs-on through a matrix include or a bash case arm.

Every offender is named in one finding, in the order the files are
given (workflows, then templates), the first floating line of a file
by its line number. Lines are split on "\n" alone, as grep's.

Usage:
  runner_pin.py WORKFLOW... -- TEMPLATE...

Each file is read as UTF-8, strictly. Exit 0 with one summary line, 1
with every offender named, 2 on a usage or read error. Stdlib only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import findings

EXIT_RED = 1
EXIT_ERROR = 2
USAGE = "usage: runner_pin.py WORKFLOW... -- TEMPLATE..."
# POSIX [[:space:]] within one line.
SPACE = r"[ \t\r\f\v]"
RUNS_ON = re.compile(rf"{SPACE}*runs-on:")
PREFIX = re.compile(rf"^{SPACE}*runs-on:{SPACE}*")
COMMENT = re.compile(rf"{SPACE}*#.*$")
PINNED = re.compile(r"[a-z]+-[0-9][a-z0-9.-]*")
FLOAT = re.compile(r"(ubuntu|macos|windows)-latest")
COMMENT_LINE = re.compile(rf"{SPACE}*#")
TAIL = [
    "pin every runs-on to a named image version (ubuntu-24.04, not",
    "ubuntu-latest) — Renovate rolls named pins as visible diffs;",
    "a floating label migrates with no diff at all (#290).",
]


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


def indirect(value: str) -> bool:
    r"""Say whether a runs-on value is a matrix indirection.

    Returns:
        True for `${{`, then `matrix.` somewhere, then `}}` at the end:
        the shell's case pattern `\$\{\{*matrix.*\}\}`.

    """
    head, tail = "${{", "}}"
    if not (value.startswith(head) and value.endswith(tail)):
        return False
    return "matrix." in value[len(head) : -len(tail)]


def offences(name: str, text: str) -> list[str]:
    """Name one file's unpinned runs-on values and its first floating label.

    Returns:
        Each offence as the message carries it.

    """
    lines = text.split("\n")
    found: list[str] = []
    for line in lines:
        if not RUNS_ON.match(line):
            continue
        value = COMMENT.sub("", PREFIX.sub("", line, count=1), count=1)
        value = value.replace('"', "").replace("'", "")
        if not indirect(value) and not PINNED.fullmatch(value):
            found.append(f"{name}(runs-on: {value})")
    for number, line in enumerate(lines, start=1):
        if FLOAT.search(line) and not COMMENT_LINE.match(line):
            found.append(f"{name}(floating label: {number}:{line})")
            break
    return found


def main(argv: list[str]) -> int:
    """Check every runs-on in the workflows and the templates.

    Returns:
        0 when every label is pinned, EXIT_RED with every offender
        named, EXIT_ERROR on a usage or read error.

    """
    if "--" not in argv:
        findings.finding("", USAGE)
        return EXIT_ERROR
    cut = argv.index("--")
    names = argv[:cut] + argv[cut + 1 :]
    bad: list[str] = []
    for name in names:
        try:
            bad.extend(offences(name, read(Path(name))))
        except ValueError as err:
            findings.finding("", str(err))
            return EXIT_ERROR
    if bad:
        listed = "".join(f" {each}" for each in bad)
        findings.finding("", f"floating or unpinned runner labels:{listed}", TAIL)
        return EXIT_RED
    print(
        "lint:runner-pin: every runs-on pinned or matrix-indirected, no floating labels"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
