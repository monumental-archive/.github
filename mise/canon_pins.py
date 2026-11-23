#!/usr/bin/env python3
r"""lint:canon-pins: every canon reference is a version Renovate can bump.

Renovate is the bump mechanism (#133); a reference nothing can bump
silently rots, the v0.16.1 failure class. The task's comment in
mise/config.toml has the history; this is its logic (#1911). Four
surfaces, each counted as a canon reference:

  YAML: each line that is not commented before its `uses:` and uses
  `<canon>/…` carries `@<40-hex> # vX.Y.Z`;
  lefthook configs naming `github.com/<canon>`: some line is
  `ref: v<digit>…`, a tag, never a branch;
  Renovate configs naming `github><canon>`: the reference carries
  `#v<digit>…`;
  workflows: no line that is not commented before it checks the canon
  out (`repository: <canon>` ending the line). Canon content reaches a
  job only through `uses: $/.github/actions/canon` (#165).

No reference on any surface: skipped, by name. Otherwise every
offender is named in one finding, surface by surface in the order
above, files in the order given; else one line with the count.

Usage:
  canon_pins.py OWNER/REPO YAML... -- LEFTHOOK... -- RENOVATE... -- WORKFLOW...

Each file is read as bytes, as grep reads it; lines split on "\n"
alone, as grep's. Exit 0 with one line, 1 with every offender named, 2 on a usage
or read error. Stdlib only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import findings

EXIT_RED = 1
EXIT_ERROR = 2
# The four lists, split by three separators.
SURFACES = 4
USAGE = (
    "usage: canon_pins.py OWNER/REPO YAML... -- LEFTHOOK... -- RENOVATE..."
    " -- WORKFLOW..."
)
# POSIX [[:space:]] within one line.
SPACE = r"[ \t\r\f\v]"
VERSIONED = re.compile(r"@[0-9a-f]{40} # v[0-9]+\.[0-9]+\.[0-9]+")
TAG_REF = re.compile(rf"^{SPACE}*ref:{SPACE}*v[0-9]", re.MULTILINE)


def read(path: Path) -> str:
    """Read a file's bytes as grep does, a byte that is not UTF-8 kept as is.

    No message carries a file's content, only its name, so a file that
    is not UTF-8 is judged on the bytes it has.

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


class Lint:
    """The references counted and the offenders named, surface by surface."""

    def __init__(self, canon: str) -> None:
        """Start with nothing counted, for one canon's `owner/repo`."""
        self.canon = canon
        literal = re.escape(canon)
        self.pin = re.compile(rf"[^#\n]*uses:{SPACE}*{literal}/")
        self.preset = re.compile(rf"github>{literal}#v[0-9]")
        self.checkout = re.compile(
            rf"^[^#\n]*repository:{SPACE}*{literal}{SPACE}*$", re.MULTILINE
        )
        self.found = 0
        self.bad: list[str] = []

    def yaml(self, name: str, text: str) -> None:
        """Count a file's canon pins; name each one with no version."""
        for line in text.split("\n"):
            if not self.pin.match(line):
                continue
            self.found += 1
            if not VERSIONED.search(line):
                self.bad.append(f"{name}(canon pin without '@<full-sha> # vX.Y.Z')")

    def lefthook(self, name: str, text: str) -> None:
        """Count a lefthook config naming the canon; name it when no tag is ref'd."""
        if f"github.com/{self.canon}" not in text:
            return
        self.found += 1
        if not TAG_REF.search(text):
            self.bad.append(
                f"{name}(lefthook canon remote must ref a v* tag, not a branch)"
            )

    def renovate(self, name: str, text: str) -> None:
        """Count a Renovate config naming the canon; name it when untagged."""
        if f"github>{self.canon}" not in text:
            return
        self.found += 1
        if not self.preset.search(text):
            self.bad.append(f"{name}(preset reference must carry #vX.Y.Z)")

    def workflow(self, name: str, text: str) -> None:
        """Count a workflow's canon checkouts; name it when it has any."""
        checkouts = sum(1 for _ in self.checkout.finditer(text))
        if not checkouts:
            return
        self.found += checkouts
        self.bad.append(
            f"{name}({checkouts} canon checkout(s) — use $/.github/actions/canon)"
        )

    def report(self) -> int:
        """Print the verdict.

        Returns:
            0 when every reference is versioned or there is none,
            EXIT_RED otherwise.

        """
        if not self.found:
            print("lint:canon-pins: no canon references, skipped")
            return 0
        if self.bad:
            listed = "".join(f" {each}" for each in self.bad)
            findings.finding(
                "",
                f"unversioned canon references:{listed}",
                [
                    "every canon reference pins a tag Renovate can bump (#133);",
                    "workflow pins: '@<sha> # vX.Y.Z', lefthook: 'ref: vX.Y.Z',",
                    f"preset: 'github>{self.canon}#vX.Y.Z'; canon",
                    "content in a job comes only via $/.github/actions/canon (#165).",
                ],
            )
            return EXIT_RED
        print(f"lint:canon-pins: {self.found} canon references, all versioned")
        return 0


def split(argv: list[str]) -> list[list[str]] | None:
    """Split the file arguments into the four surfaces' lists.

    Returns:
        The lists, or None when there are not exactly three separators.

    """
    lists: list[list[str]] = [[]]
    for arg in argv:
        if arg == "--":
            lists.append([])
        else:
            lists[-1].append(arg)
    return lists if len(lists) == SURFACES else None


def main(argv: list[str]) -> int:
    """Check every canon reference on the four surfaces.

    Returns:
        0 when every reference is versioned or there is none, EXIT_RED
        with every offender named, EXIT_ERROR on a usage or read error.

    """
    owner, _, repo = argv[0].partition("/") if argv else ("", "", "")
    lists = split(argv[1:])
    if not owner or not repo or lists is None:
        findings.finding("", USAGE)
        return EXIT_ERROR
    lint = Lint(argv[0])
    checks = (lint.yaml, lint.lefthook, lint.renovate, lint.workflow)
    for check, names in zip(checks, lists, strict=True):
        for name in names:
            try:
                check(name, read(Path(name)))
            except ValueError as err:
                findings.finding("", str(err))
                return EXIT_ERROR
    return lint.report()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
