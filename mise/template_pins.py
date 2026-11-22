#!/usr/bin/env python3
"""lint:template-pins: workflow templates pin one released canon SHA (#1887).

Found by the #28 pre-close audit: all five templates sat a day behind
the canon, predating three shipped fixes, with nothing anywhere going
red — a repository standing up from the templates would have inherited
known bugs. The deterministic properties:

  every canon pin is a full 40-hex SHA, an ancestor of HEAD (never
  unmerged or foreign history);
  every pinned template agrees on ONE SHA (stub pins move together);
  that SHA names a released tag `v*` (#233): a copier depends on a
  commit that was cut, signed and published, not on any ancestor. A
  clone with no tags says it skipped this, by name, never silently;
  scorecard.yml calls no reusable workflow: it is self-contained by
  design, since Scorecard's publish validation rejects reusables. Its
  one canon pin is the canon action (.github/actions/canon), which its
  `report` job needs for the belt's Python and mise/standing_issue.py
  (#1887, owner's ruling 2026-11-21); that pin is held to the same
  released SHA as every other.

Deliberately NOT required: pin == HEAD, which would couple every canon
commit to a template touch. Staleness within merged history is the
scheduled audit's judgement, not the gate's.

Usage:
  template_pins.py OWNER/REPO TEMPLATE...

OWNER/REPO is the canon (security/identity.toml, read by the task).
Each template is read as UTF-8, strictly. Exit 0 with one summary line,
1 with every violation, 2 on a usage or read error. Stdlib only, and
git on PATH, run in the repository whose templates these are.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import findings
import typed_std

GIT = shutil.which("git") or "git"
EXIT_RED = 1
EXIT_ERROR = 2
ACTION = "/.github/actions/canon@"
SHA = re.compile(r"@([0-9a-f]{40})")


def git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run git in the working directory.

    Returns:
        The finished process; its status is the caller's to judge.

    """
    return subprocess.run([GIT, *args], capture_output=True, text=True, check=False)


def canon_lines(text: str, canon: str) -> list[str]:
    """Name the lines of a template that pin the canon.

    Returns:
        Each `uses:` line naming `<canon>/`, in order.

    """
    pin = re.compile(rf"uses:\s*{re.escape(canon)}/")
    return [line for line in text.splitlines() if pin.search(line)]


def template_pins(path: Path, text: str, canon: str) -> tuple[list[str], list[str]]:
    """Read one template's canon pins.

    Returns:
        The SHAs it pins, in order, and one violation per bad pin.

    """
    lines = canon_lines(text, canon)
    bad: list[str] = []
    if path.name.startswith("scorecard."):
        action = f"{canon}{ACTION}"
        if any(action not in line for line in lines):
            bad.append(f"{path}(pins a reusable workflow: self-contained by design)")
        lines = [line for line in lines if action in line]
    shas: list[str] = []
    for line in lines:
        match = SHA.search(line)
        sha = typed_std.group(match, 1) if match else None
        if sha is None:
            bad.append(f"{path}(pin is not a full 40-hex SHA)")
            continue
        shas.append(sha)
        if git("merge-base", "--is-ancestor", sha, "HEAD").returncode != 0:
            bad.append(f"{path}({sha[:8]} not an ancestor of HEAD)")
    return shas, bad


def first_tag(*args: str) -> str:
    """Name the first release tag git lists for a query.

    Returns:
        The tag, or "" when there is none.

    """
    listed = git("tag", *args, "--list", "v[0-9]*").stdout.splitlines()
    return listed[0] if listed else ""


def released(shas: list[str]) -> tuple[str, list[str]]:
    """Name the release each SHA is, or that it is none.

    Returns:
        The summary's "released as" text, and one violation per SHA
        that names no release.

    """
    if not first_tag():
        return " (no tags in this clone, released-tag check skipped)", []
    names = ""
    bad: list[str] = []
    for sha in shas:
        tag = first_tag("--points-at", sha)
        if tag:
            names += f" {tag}"
        else:
            bad.append(
                f"({sha[:8]} names no released tag — pins must name a release,"
                " not any ancestor)"
            )
    return names, bad


def read(path: Path) -> str:
    """Read a template as UTF-8, strictly.

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


def main(argv: list[str]) -> int:
    """Check every template's canon pins.

    Returns:
        0 when they hold, EXIT_RED on a violation, EXIT_ERROR on a
        usage or read error.

    """
    # identity.sh names a missing field on stderr and prints nothing, so
    # an empty owner or repository arrives here as "/x" or "x/".
    owner, _, repo = argv[0].partition("/") if argv else ("", "", "")
    if not owner or not repo:
        findings.finding("", "usage: template_pins.py OWNER/REPO TEMPLATE...")
        return EXIT_ERROR
    canon, files = argv[0], [Path(f) for f in argv[1:]]
    count = 0
    distinct: list[str] = []
    bad: list[str] = []
    for path in files:
        try:
            text = read(path)
        except ValueError as err:
            findings.finding("", str(err))
            return EXIT_ERROR
        shas, found = template_pins(path, text, canon)
        bad += found
        count += len(shas)
        distinct += [sha for sha in shas if sha not in distinct]
    if len(distinct) > 1:
        listed = "".join(f" {sha}" for sha in distinct)
        bad.append(f"(templates disagree, {len(distinct)} distinct SHAs:{listed})")
    names, unreleased = released(distinct)
    bad += unreleased
    if bad:
        findings.finding(
            "",
            f"template pin violations: {' '.join(bad)}",
            [
                "templates pin one merged, RELEASED canon SHA and move together",
                (
                    "(#111, #233); scorecard.yml pins the canon action,"
                    " never a reusable (#1887)."
                ),
            ],
        )
        return EXIT_RED
    print(
        f"lint:template-pins: {count} pins at one ancestor SHA, scorecard calls no"
        f" reusable, released as{names}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
