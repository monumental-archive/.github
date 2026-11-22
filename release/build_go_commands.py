#!/usr/bin/env python3
"""Build every command of a Go module into staged/.

The go-binary class's build step (#1384 moved it here from the
workflow's shell, refusals unchanged). Run from the module root on the
leg's own hardware, the toolchain on PATH and the build environment
(CGO_ENABLED and the rest) inherited from the job.

The command list comes from the module's own package graph, never from
a directory convention: a main package is what `go list` says is a
main package, wherever it lives. Each builds to staged/<last path
element>. Two refusals: a module with no main package is the wrong
class, and two main packages ending in the same element would build
one binary name, the second over the first.

Exit status: 0 built, 1 refused, else go's own failing status.
"""

import subprocess
import sys
from pathlib import Path

# The canon's one writer of workflow commands, in mise/ (#1660).
# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import findings

MAINS = '{{if eq .Name "main"}}{{.ImportPath}}{{end}}'


class RefusedError(Exception):
    """The build is refused; the message says why."""


class GoFailedError(Exception):
    """go failed; its own output already said why."""

    def __init__(self, status: int) -> None:
        """Keep go's exit status, which the step exits with."""
        super().__init__(status)
        self.status = status


def go(*args: str) -> str:
    """Run the go tool.

    Returns:
        Its standard output.

    Raises:
        GoFailedError: go exited non-zero.

    """
    done = subprocess.run(
        # ruff: ignore[start-process-with-partial-path] - the caller's pinned go, on PATH
        ["go", *args],
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise GoFailedError(done.returncode)
    return done.stdout


def build() -> None:
    """List the main packages and build each.

    Raises:
        RefusedError: no main package, or two building one name.

    """
    staged = Path("staged")
    staged.mkdir(parents=True, exist_ok=True)
    pkgs = [p for p in go("list", "-f", MAINS, "./...").splitlines() if p]
    if not pkgs:
        msg = "no main packages in this module — go-binary is the wrong class"
        raise RefusedError(msg)
    for p in pkgs:
        b = p.rpartition("/")[2]
        if (staged / b).exists():
            msg = f"two main packages both build a binary named {b}"
            raise RefusedError(msg)
        go("build", "-o", str(staged / b), p)


def main() -> int:
    """Run the step.

    Returns:
        The exit status: 0 built, 1 refused, else go's.

    """
    try:
        build()
    except RefusedError as e:
        findings.finding("", str(e), annotate=True)
        return 1
    except GoFailedError as e:
        return e.status
    return 0


if __name__ == "__main__":
    sys.exit(main())
