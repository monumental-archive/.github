#!/usr/bin/env python3
"""lint:cold-attested: every cache declaration carries its own marker.

A cache on an attested path is state injected into the build that no
provenance captures, so a cache must say why its path is unattested
with an `unattested-path:` marker. The task's comment in
mise/config.toml has the rule's history and its four marker rules
(#676, #732); this is its logic (#1911).

The workflows are read line by line, by indentation, as the canon's
two-space style writes every workflow; actionlint, earlier in the same
gate, holds the YAML well-formed. State runs across the files in the
order named: a file's first line closes the step the last one left
open, and an empty file has no first line.

- A comment block is a run of comment lines with no blank line in it.
  It is a marker when its FIRST line's first token is
  `unattested-path:`, and the marker authorises the step that ends the
  block at the marker's own indentation, that step alone. A marker
  ended by a blank line, or by any other line, introduces no step.
- A step is a `- <key>:` line at or left of the open step's indent.
- `uses: actions/cache…` declares a cache, and is red without a marker.
- `uses: jdx/mise-action@` and `uses: actions/setup-…` cache by
  default: the step must say `cache: false` or carry a marker.
- A marker on a step that declares no cache is red.

Usage:
  cold_attested.py WORKFLOW...

Each workflow is read as UTF-8, strictly. Exit 0
with one summary line, 1 with every finding named `<file>:<line>`, 2 on
a usage or read error. Stdlib only.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import findings

EXIT_RED = 1
EXIT_ERROR = 2
# awk's [[:space:]] within one line: lines split on "\n" alone, as awk's.
SPACE = r"[ \t\r\f\v]"
BLANK = re.compile(rf"{SPACE}*")
COMMENT = re.compile(rf"{SPACE}*#")
MARKER = re.compile(rf"{SPACE}*#{SPACE}*unattested-path:")
STEP = re.compile(rf"{SPACE}*-{SPACE}+[A-Za-z_][A-Za-z0-9_-]*:")
CACHE = re.compile(rf"[^#]*uses:{SPACE}*actions/cache")
MISE = re.compile(rf"[^#]*uses:{SPACE}*jdx/mise-action@")
SETUP = re.compile(rf"[^#]*uses:{SPACE}*actions/setup-")
CACHE_FALSE = re.compile(rf"[^#]*cache:{SPACE}*false")

NO_CACHE = "unattested-path marker on a step that declares no cache"
NO_STEP = "unattested-path marker introduces no step"
NO_MARKER = "actions/cache step carries no unattested-path marker"
DEFAULT = "{} step does not say `cache: false` and carries no unattested-path marker"
CLEAN = (
    "lint:cold-attested: every cache declaration carries its own unattested-path marker"
)


def indent(line: str) -> int:
    """Count a line's leading spaces, as awk's `match($0, /[^ ]/) - 1`.

    Returns:
        The column of the first character that is not a space, or -1
        for a line of spaces only.

    """
    stripped = line.lstrip(" ")
    return len(line) - len(stripped) if stripped else -1


@dataclass
class Reader:
    """The awk program's state, carried across every file it reads."""

    out: list[tuple[str, str]] = field(default_factory=list)
    instep: bool = False
    stepind: int = -1
    stepline: int = 0
    stepfile: str = ""
    auth: bool = False
    has_cache: bool = False
    has_dflt: bool = False
    has_false: bool = False
    dfltkind: str = ""
    blkstart: int = 0
    blkmark: bool = False
    blkind: int = -1

    def say(self, where: str, message: str) -> None:
        """Record one finding."""
        self.out.append((where, message))

    def endstep(self) -> None:
        """Judge the open step, then forget it."""
        default = self.has_dflt and not self.has_false
        declares = self.has_cache or default
        where = f"{self.stepfile}:{self.stepline}"
        if self.instep and self.auth and not declares:
            self.say(where, NO_CACHE)
        if self.instep and default and not self.auth:
            self.say(where, DEFAULT.format(self.dfltkind))
        self.instep = False
        self.has_dflt = False
        self.dfltkind = ""
        self.has_false = False
        self.has_cache = False
        self.auth = False

    def start(self) -> None:
        """Begin a file: close the last file's step, forget its block."""
        self.endstep()
        self.blkstart = 0
        self.blkmark = False
        self.blkind = -1
        self.stepind = -1

    def line(self, name: str, number: int, text: str) -> None:
        """Read one line of the file `name`."""
        ind = indent(text)
        if BLANK.fullmatch(text):
            if self.blkmark:
                self.say(f"{name}:{self.blkstart}", NO_STEP)
            self.blkstart = 0
            self.blkmark = False
            return
        if COMMENT.match(text):
            if not self.blkstart:
                self.blkstart = number
                self.blkind = ind
                self.blkmark = MARKER.match(text) is not None
            return
        isstep = STEP.match(text) is not None and (
            not self.instep or ind <= self.stepind
        )
        newauth = False
        if self.blkstart:
            if self.blkmark:
                if isstep and ind == self.blkind:
                    newauth = True
                else:
                    self.say(f"{name}:{self.blkstart}", NO_STEP)
            self.blkstart = 0
            self.blkmark = False
        if isstep:
            self.endstep()
            self.instep = True
            self.stepind = ind
            self.stepline = number
            self.stepfile = name
            self.auth = newauth
        elif self.instep and ind <= self.stepind:
            self.endstep()
        self.uses(name, number, text)

    def uses(self, name: str, number: int, text: str) -> None:
        """Note what one line of a step declares."""
        if CACHE.match(text):
            self.has_cache = True
            if not self.auth:
                self.say(f"{name}:{number}", NO_MARKER)
        if MISE.match(text):
            self.has_dflt = True
            self.dfltkind = "mise-action"
        if SETUP.match(text):
            self.has_dflt = True
            self.dfltkind = "actions/setup-*"
        if CACHE_FALSE.match(text):
            self.has_false = True


def lines(path: Path) -> list[str]:
    """Read a workflow as UTF-8, its records split on newline, as awk's.

    Returns:
        Its lines; no final empty record.

    Raises:
        ValueError: the file cannot be read or is not UTF-8.

    """
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as err:
        msg = f"{path}: cannot be read as UTF-8: {err}"
        raise ValueError(msg) from err
    records = text.split("\n")
    if not records[-1]:
        records.pop()
    return records


def main(argv: list[str]) -> int:
    """Check every workflow named.

    Returns:
        0 when every cache declaration carries its own marker, EXIT_RED
        when one does not, EXIT_ERROR on a usage or read error.

    """
    if not argv:
        findings.finding("", "usage: cold_attested.py WORKFLOW...")
        return EXIT_ERROR
    reader = Reader()
    for name in argv:
        try:
            records = lines(Path(name))
        except ValueError as err:
            findings.finding("", str(err))
            return EXIT_ERROR
        for number, text in enumerate(records, start=1):
            if number == 1:
                reader.start()
            reader.line(name, number, text)
    reader.endstep()
    for where, message in reader.out:
        findings.finding(where, message)
    if reader.out:
        return EXIT_RED
    print(CLEAN)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
