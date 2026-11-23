#!/usr/bin/env python3
"""lint:citation: CITATION.cff agrees with its sources (#316 finding 6).

The task's comment in mise/config.toml has the history; this is its
logic (#1911). In order:

  no CITATION.cff: red when a workflow requests `mint-doi: true` on a
  non-comment line, each such workflow named; else a named skip;
  CITATION.cff with no REUSE.toml, its source: red;
  the render mise/citation.sh writes from the sources, compared to the
  committed file with `diff -u`: a difference is red, the diff on
  stdout. A failed render is its own status, its message its own.

Usage:
  citation_check.py [WORKFLOW...]

The workflows are the tracked ones the task lists, possibly none. Each
is read as UTF-8, strictly, when there is no CITATION.cff to compare.
Exit 0 with one line, 1 with the finding, 2 on a read error, or the
render's own status. Stdlib only, with bash and diff on PATH.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import findings
import tracked

EXIT_RED = 1
EXIT_ERROR = 2
BASH = shutil.which("bash") or "bash"
DIFF = shutil.which("diff") or "diff"
RENDER = Path(__file__).resolve().with_name("citation.sh")
# grep's [[:space:]] within one line: lines split on "\n" alone, as grep's.
SPACE = r"[ \t\r\f\v]"
MINTING = re.compile(rf"^[^#]*mint-doi:{SPACE}*true")


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


def minting(names: list[str]) -> list[str]:
    """Name the workflows that request DOI minting.

    Returns:
        The names, in the order given; a workflow that cannot be read
        raises read's ValueError.

    """
    return [
        name
        for name in names
        if any(MINTING.search(line) for line in read(Path(name)).split("\n"))
    ]


def uncited(names: list[str]) -> int:
    """Judge a tree with no CITATION.cff.

    Returns:
        0 when nothing requests minting, EXIT_RED when something does,
        EXIT_ERROR on a read error.

    """
    try:
        found = minting(names)
    except ValueError as err:
        findings.finding("", str(err))
        return EXIT_ERROR
    if found:
        findings.finding(
            "",
            "mint-doi: true with no CITATION.cff — a citable repo carries its"
            " citation (#316 finding 6):",
            [*found, "run fix:citation (REUSE.toml is the source), commit the render"],
        )
        return EXIT_RED
    print("lint:citation: no CITATION.cff and no mint-doi request, skipped")
    return 0


def compare() -> int:
    """Render the citation from its sources and compare it to the file.

    The render's stdout is dropped, its stderr passes through; the
    diff goes to stdout.

    Returns:
        0 when they agree, EXIT_RED when they differ, or the render's
        own status when it fails.

    """
    handle, name = tempfile.mkstemp()
    os.close(handle)
    try:
        render = subprocess.run(
            [BASH, str(RENDER)],
            env=os.environ | {"CITATION_OUT": name},
            stdout=subprocess.DEVNULL,
            check=False,
        )
        if render.returncode:
            return render.returncode
        sys.stdout.flush()
        if subprocess.run([DIFF, "-u", "CITATION.cff", name], check=False).returncode:
            findings.finding(
                "",
                "CITATION.cff drifted from its sources — edit REUSE.toml (or the"
                " human fields) and run fix:citation",
            )
            return EXIT_RED
    finally:
        with contextlib.suppress(FileNotFoundError):
            Path(name).unlink()
    print("lint:citation: CITATION.cff agrees with REUSE.toml")
    return 0


def main(argv: list[str]) -> int:
    """Check the tree's citation against its sources.

    Returns:
        0 when it holds or does not apply, EXIT_RED on a violation,
        EXIT_ERROR on a read error, or a failed render's status.

    """
    # Asked of git, never the directory: an untracked file is not the
    # tree's (#2013).
    here = Path.cwd()
    if not tracked.has("CITATION.cff", root=here):
        return uncited(argv)
    if not tracked.has("REUSE.toml", root=here):
        findings.finding(
            "", "CITATION.cff exists but REUSE.toml (its source of truth) does not"
        )
        return EXIT_RED
    return compare()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
