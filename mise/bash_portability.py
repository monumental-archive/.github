#!/usr/bin/env python3
r"""lint:bash-portability: no bash 4+ syntax, and every EXIT trap guarded.

macOS ships bash 3.2.57 and always will; runners ship bash 5, so a
bash-4-ism passes CI and fails on the machine the code is written on.
The task's comment in mise/config.toml has the history; this is its
logic (#1911). Two halves:

  bash 4+ syntax in a workflow or a shell file: `${v,,}` `${v^^}`
  `${v,}` `${v^}` (case modification), `declare -A` / `typeset -A`
  (associative arrays), `mapfile` / `readarray`, and `&>>`. Comment
  lines are dropped first, so documenting a forbidden form is not a
  violation, and each hit's number is its line's in what is left, as
  `grep -v | grep -n` numbered it. A named file that is not a regular
  file is counted and not read.
  EXIT traps in the shell files and mise configs: mise/exit_trap.py,
  its findings its own; a regular file it cannot decode as UTF-8 is
  exit 2, by name.

The bash 4+ finding names every hit, each file's first as
`  <file>: <n>:<line>` and the rest as `<n>:<line>`; the text went
through bash's `printf %b` and a newline-split read, so it is expanded
and split here the same way (bash 5's escapes, a NUL ending the text,
empty lines dropped). Either half red is exit 1; else one line with
the number of workflows and shell files.

Usage:
  bash_portability.py WORKFLOW... -- SCRIPT... -- CONFIG...

Each file is read as bytes, as grep reads it. Exit 0 when clean, 1 on
a finding, 2 on a usage or read error. Stdlib only.
"""

from __future__ import annotations

import re
import string
import sys
from pathlib import Path

import exit_trap
import findings

EXIT_RED = 1
EXIT_ERROR = 2
# The three lists, split by two separators.
LISTS = 3
USAGE = "usage: bash_portability.py WORKFLOW... -- SCRIPT... -- CONFIG..."
# POSIX [[:space:]] within one line.
SPACE = r"[ \t\r\f\v]"
COMMENT = re.compile(rf"{SPACE}*#")
BAD4 = re.compile(
    r"\$\{[A-Za-z_][A-Za-z0-9_]*(\[[^]\n]*\])?(,,|\^\^|,|\^)\}"
    rf"|(declare|typeset){SPACE}+-[A-Za-z]*A"
    rf"|(^|[^A-Za-z_])(mapfile|readarray){SPACE}"
    r"|&>>"
)
TAIL = [
    "these pass in CI and fail locally, which is how a script reaches",
    "a release untested. Use tr, case, or an explicit loop instead.",
]
SIMPLE = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
}
OCTAL = string.octdigits
HEX = string.hexdigits
BYTE = 0xFF
ASCII = 0x80
# \x takes two hex digits, \u four, \U eight; an octal escape three.
WIDTH = {"x": 2, "u": 4, "U": 8}
OCTAL_WIDTH = 3


def read(path: Path) -> str:
    """Read a file's bytes as grep does, a byte that is not UTF-8 kept as is.

    Returns:
        Its text, each undecodable byte a lone surrogate.

    Raises:
        ValueError: the file cannot be read.

    """
    try:
        return path.read_bytes().decode("utf-8", errors="surrogateescape")
    except OSError as err:
        msg = f"{path}: cannot be read: {err}"
        raise ValueError(msg) from err


def byte(value: int) -> str:
    """Spell one byte as text: ASCII as itself, any other as its surrogate.

    Returns:
        The character.

    """
    value &= BYTE
    return chr(value) if value < ASCII else chr(0xDC00 + value)


def digits(text: str, at: int, allowed: str, most: int) -> str:
    """Read up to `most` characters from `allowed` at an offset.

    Returns:
        The run read.

    """
    end = at
    while end < len(text) and end - at < most and text[end] in allowed:
        end += 1
    return text[at:end]


def escape(text: str, at: int) -> tuple[str, int, bool]:
    r"""Expand the escape whose letter is at `at`, just past a backslash.

    Returns:
        The expansion, the offset after it, and whether `\c` ended the
        text there. An escape bash does not know stays as written.

    """
    letter = text[at]
    if letter == "c":
        return "", at, True
    spelled, end = "\\" + letter, at + 1
    if letter in SIMPLE:
        spelled = SIMPLE[letter]
    elif letter in OCTAL:
        # \0 takes up to three digits after it; \1 to \7 begin their own.
        start = at + 1 if letter == "0" else at
        run = digits(text, start, OCTAL, OCTAL_WIDTH)
        spelled, end = byte(int(run or "0", 8)), start + len(run)
    elif letter in WIDTH:
        run = digits(text, at + 1, HEX, WIDTH[letter])
        value = int(run or "0", 16)
        if run and letter == "x":
            spelled, end = byte(value), at + 1 + len(run)
        elif run and value <= sys.maxunicode:
            spelled, end = chr(value), at + 1 + len(run)
    return spelled, end, False


def expand(text: str) -> str:
    r"""Expand backslash escapes as bash 5's `printf %b` does.

    Returns:
        The text expanded, ending where a `\c` stops the output.

    """
    out: list[str] = []
    at = 0
    while at < len(text):
        char = text[at]
        if char != "\\" or at + 1 == len(text):
            out.append(char)
            at += 1
            continue
        spelled, at, stop = escape(text, at + 1)
        out.append(spelled)
        if stop:
            break
    return "".join(out)


def detail(found: str) -> list[str]:
    r"""Split the hits as `printf "%b\n" | IFS=$'\n' read -d '' -a` did.

    Returns:
        The non-empty lines of the expanded text before any NUL.

    """
    shown = expand(found).partition("\0")[0]
    return [line for line in shown.split("\n") if line]


def hits(name: str, text: str) -> str:
    r"""Find a file's bash 4+ syntax outside comment lines.

    Returns:
        `\n  <file>: ` and the numbered hits, newline-joined; "" for none.

    """
    lines = text.split("\n")
    if lines and not lines[-1]:
        lines.pop()
    kept = [line for line in lines if not COMMENT.match(line)]
    numbered = [
        f"{number}:{line}"
        for number, line in enumerate(kept, start=1)
        if BAD4.search(line)
    ]
    return f"\n  {name}: " + "\n".join(numbered) if numbered else ""


def traps(names: list[str]) -> int:
    """Hold the EXIT traps to the guard, through mise/exit_trap.py.

    exit_trap.py reads each file as UTF-8, strictly, and its error does
    not name the file, so a regular file it could not decode is named
    here first.

    Returns:
        exit_trap.py's status, after its report.

    Raises:
        ValueError: a file cannot be read or decoded.

    """
    for name in names:
        path = Path(name)
        if not path.is_file():
            continue
        try:
            path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as err:
            msg = f"{name}: cannot be read as UTF-8: {err}"
            raise ValueError(msg) from err
    try:
        return exit_trap.main(names)
    except (OSError, UnicodeDecodeError) as err:
        msg = f"mise/exit_trap.py cannot read a file: {err}"
        raise ValueError(msg) from err


def split(argv: list[str]) -> list[list[str]] | None:
    """Split the arguments into the three lists.

    Returns:
        The lists, or None when there are not exactly two separators.

    """
    lists: list[list[str]] = [[]]
    for arg in argv:
        if arg == "--":
            lists.append([])
        else:
            lists[-1].append(arg)
    return lists if len(lists) == LISTS else None


def main(argv: list[str]) -> int:
    """Check the workflows and shell files, then their EXIT traps.

    Returns:
        0 when clean, EXIT_RED on a finding, EXIT_ERROR on a usage or
        read error.

    """
    lists = split(argv)
    if lists is None:
        findings.finding("", USAGE)
        return EXIT_ERROR
    flows, scripts, configs = lists
    files = flows + scripts
    try:
        found = "".join(hits(n, read(Path(n))) for n in files if Path(n).is_file())
        trapped = traps([*scripts, *configs])
    except ValueError as err:
        findings.finding("", str(err))
        return EXIT_ERROR
    if found:
        findings.finding(
            "", "bash 4+ syntax found (macOS ships 3.2):", [*detail(found), *TAIL]
        )
        return EXIT_RED
    if trapped:
        return EXIT_RED
    print(f"lint:bash-portability: {len(files)} files clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
