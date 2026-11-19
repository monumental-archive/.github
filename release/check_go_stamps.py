#!/usr/bin/env python3
"""Refuse a built binary whose stamp is not the released commit.

The go-binary class's stamp check (#1371 moved it here from the
workflow's shell, refusals unchanged). Go embeds the module list and the
VCS facts in every binary, and `go version -m` reads them back out of
the built bytes — the artifact-carries-its-inventory property this class
rests on (stele#7). Asserted, not hoped: the stamped revision must equal
the commit being released and the tree must be clean, which binds the
bytes to the commit more strongly than any manifest-version comparison
could; and the bytes must carry a module stamp, or they carry no
inventory at all.

Run from the module root after the build step, the caller's pinned go
on PATH: every file in staged/ is checked against `git rev-parse HEAD`.
A build leg carries no belt, so this is stdlib Python run from the canon
tree the leg recorded.

Exit status: 0 every stamp holds, 1 refused, else go's or git's own.
"""

import re

# ruff: ignore[suspicious-subprocess-import]
import subprocess
import sys
from pathlib import Path

# The canon's one writer of workflow commands, in mise/ (#1660).
# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import typed_std
import workflow_command

# A VCS setting's own line, `\tbuild\tvcs.revision=<sha>`, and nothing
# else: anchored at the line's `build` field, because another setting's
# VALUE is the builder's text and may spell `vcs.revision=` itself —
# `-ldflags=-X=main.v=vcs.revision=<HEAD>` printed before the real
# setting passed a binary built from any commit (#1556).
REVISION = re.compile(r"^[ \t]*build[ \t]+vcs\.revision=(?P<value>.*)$", re.MULTILINE)
MODIFIED = re.compile(r"^[ \t]*build[ \t]+vcs\.modified=(?P<value>.*)$", re.MULTILINE)
# The main module's own line: `go version -m` prints it indented, as
# `\tmod\t<path>\t<version>`.
MODULE = re.compile(r"^\s+mod\s", re.MULTILINE)


class RefusedError(Exception):
    """A binary is refused; the message says why."""


class ToolFailedError(Exception):
    """go or git failed; its own output already said why."""

    def __init__(self, status: int) -> None:
        """Keep the tool's exit status, which the step exits with."""
        super().__init__(status)
        self.status = status


def finished(done: subprocess.CompletedProcess[str]) -> str:
    """Pass a tool's output on, or its failure.

    Returns:
        Its standard output.

    Raises:
        ToolFailedError: the tool exited non-zero.

    """
    if done.returncode != 0:
        raise ToolFailedError(done.returncode)
    return done.stdout


def go(*args: str) -> str:
    """Run the caller's pinned go, on PATH.

    Returns:
        Its standard output.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    return finished(
        subprocess.run(
            # ruff: ignore[start-process-with-partial-path] - the caller's pinned go, on PATH
            ["go", *args],
            stdout=subprocess.PIPE,
            text=True,
            check=False,
        )
    )


def git(*args: str) -> str:
    """Run the runner's git.

    Returns:
        Its standard output.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    return finished(
        subprocess.run(
            # ruff: ignore[start-process-with-partial-path] - the runner's git
            ["git", *args],
            stdout=subprocess.PIPE,
            text=True,
            check=False,
        )
    )


def stamp(info: str, pattern: re.Pattern[str]) -> str:
    """Read one stamped setting, or "" when the binary carries none.

    Returns:
        The setting's value.

    """
    found = pattern.search(info)
    value = typed_std.group(found, "value") if found else None
    return value.strip() if value is not None else ""


def check(binary: Path, head: str) -> None:
    """Hold one binary's stamp to the released commit.

    Raises:
        RefusedError: another revision or none, a modified tree, or no
            module stamp.

    """
    info = go("version", "-m", str(binary))
    rev = stamp(info, REVISION)
    modified = stamp(info, MODIFIED)
    name = binary.name
    if rev != head:
        stamped = rev or "<none>"
        msg = f"{name}: stamped revision {stamped} is not the released commit {head}"
        raise RefusedError(msg)
    if modified != "false":
        state = modified or "<none>"
        msg = f"{name}: vcs.modified={state} — a release builds a clean tree"
        raise RefusedError(msg)
    if not MODULE.search(info):
        msg = f"{name}: no module stamp — the shipped bytes must carry their inventory"
        raise RefusedError(msg)


def check_staged() -> None:
    """Hold every staged binary, in name order, to the released commit.

    Raises:
        RefusedError: nothing staged, or a binary refused.

    """
    head = git("rev-parse", "HEAD").strip()
    binaries = sorted(p for p in Path("staged").glob("*") if p.is_file())
    if not binaries:
        msg = "staged/ holds no binary — nothing was built to check"
        raise RefusedError(msg)
    for b in binaries:
        check(b, head)


def main() -> int:
    """Run the step.

    Returns:
        The exit status: 0 every stamp holds, 1 refused, else the tool's.

    """
    try:
        check_staged()
    except RefusedError as e:
        workflow_command.error(str(e))
        return 1
    except ToolFailedError as e:
        return e.status
    return 0


if __name__ == "__main__":
    sys.exit(main())
