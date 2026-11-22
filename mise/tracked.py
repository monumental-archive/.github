#!/usr/bin/env python3
"""The one producer of the tracked-file list, for Python (#1986).

mise/tracked.sh's `org_tracked` is the same producer for the shell; the
two run the same git command, so a task body and a helper cannot name
different files.

- `tracked(*pathspecs, root=None)`: the tracked files the pathspecs
  match, git's default pathspec meaning, in git's order. No pathspec is
  the whole index. Read NUL-separated and unquoted, so a name with a
  space, a newline or a non-ASCII letter is one entry, spelled as on
  disk (`os.fsdecode`). With `root`, the listing is that tree's, and no
  `GIT_*` variable reaches git: git exports GIT_DIR to hooks in a
  linked worktree, and `-C` does not outrank it (#857).
- `stdin_paths()`: the list a task body piped in from `org_tracked`,
  NUL-separated on standard input, decoded the same way. A stream that
  carries text only (a test's `io.StringIO`) is split on NUL as it is.

A linter reads exactly these files and never walks the tree itself, so
an untracked file (a tool's output, a scratch note) never reaches a
verdict.

stdlib only.
"""

import io
import os
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import typed_std

if TYPE_CHECKING:
    from pathlib import Path

GIT = shutil.which("git") or "git"


def split(listing: bytes) -> list[str]:
    """Read a NUL-separated listing.

    Returns:
        Each entry, decoded as a file name, in order.

    """
    return [os.fsdecode(entry) for entry in listing.split(b"\0") if entry]


def tracked(*pathspecs: str, root: Path | None = None) -> list[str]:
    """List the tracked files the pathspecs match.

    Returns:
        The paths, relative to the tree's top, in git's order.

    """
    where: list[str] = []
    env: dict[str, str] | None = None
    if root is not None:
        where = ["-C", str(root)]
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    out = subprocess.run(
        [GIT, *where, "-c", "core.quotePath=false", "ls-files", "-z", "--", *pathspecs],
        capture_output=True,
        check=True,
        env=env,
    ).stdout
    return split(out)


def stdin_paths() -> list[str]:
    """Read the tracked-file list piped in on standard input.

    Returns:
        The paths, in the order given.

    Raises:
        NoStdinError: the process has no standard input.

    """
    stream: object = sys.stdin
    if isinstance(stream, io.TextIOWrapper):
        # The bytes, from the stream's own descriptor: a name need not be
        # UTF-8, and typeshed types the wrapper's buffer as `Any`.
        with open(stream.fileno(), "rb", closefd=False) as raw:
            return split(raw.read())
    if isinstance(stream, io.TextIOBase):
        return [entry for entry in stream.read().split("\0") if entry]
    msg = "no standard input: the process was started without one"
    raise typed_std.NoStdinError(msg)
