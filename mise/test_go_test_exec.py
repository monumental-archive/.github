#!/usr/bin/env python3
"""Rows for go-test-exec, the program `go` runs binaries through (#1651).

Under a measured run `go test` starts every test binary as
`go-test-exec <pkg>.test -test.<flag>… [the test's own arguments]`.
For a `*.test` binary the program inserts
`-test.gocoverdir=$ORG_COVERAGE_RUN/go` after go's leading `-test.*`
arguments and runs it; any other binary (`go run` also goes through
`-exec`) runs unchanged. The binaries here are planted scripts that
print the arguments they receive, one per line, so a row reads exactly
what the program handed on.
"""

import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from pathlib import Path

import hermetic

BELT = Path(__file__).resolve().parent
EXEC = BELT / "go-test-exec"
# A binary that prints each argument it was given on a line of its own.
ECHO = '#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done\n'
# The leading arguments go 1.27.4 hands a test binary (measured).
LEAD = [
    "-test.paniconexit0",
    "-test.gocoverdir=/go-build/b001/gocoverdir",
    "-test.timeout=10m0s",
]


def plant(base: Path, name: str) -> Path:
    """Plant a binary that echoes its arguments.

    Returns:
        Its path.

    """
    binary = base / name
    binary.write_text(ECHO, encoding="utf-8")
    binary.chmod(0o755)
    return binary


def run(argv: list[str], **env: str) -> subprocess.CompletedProcess[str]:
    """Run go-test-exec as go would.

    Returns:
        The finished process, text mode.

    """
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [str(EXEC), *argv],
        env=hermetic.env(**env),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


class TestBinaries(unittest.TestCase):
    """A `*.test` binary is handed the run's directory, after go's own."""

    def test_the_run_directory_follows_the_leading_flags(self) -> None:
        """Inserted after go's -test.* arguments, where the later one wins."""
        with tempfile.TemporaryDirectory() as tmp:
            binary = plant(Path(tmp), "a.test")
            done = run([str(binary), *LEAD], ORG_COVERAGE_RUN="/run")
            self.assertEqual(0, done.returncode, done.stderr)
            want: list[str] = [*LEAD, "-test.gocoverdir=/run/go"]
            self.assertEqual(want, done.stdout.split())

    def test_the_tests_own_arguments_follow_unchanged(self) -> None:
        """An `-args` tail reaches the test as given, after the inserted flag."""
        with tempfile.TemporaryDirectory() as tmp:
            binary = plant(Path(tmp), "b.test")
            own = ["-mode=x", "two words", "-test.not-from-go"]
            done = run([str(binary), *LEAD, *own], ORG_COVERAGE_RUN="/run")
            self.assertEqual(0, done.returncode, done.stderr)
            want: list[str] = [
                *LEAD,
                "-test.gocoverdir=/run/go",
                "-mode=x",
                "two words",
            ]
            self.assertEqual(want, done.stdout.splitlines()[:6])
            self.assertEqual("-test.not-from-go", done.stdout.splitlines()[6])

    def test_no_run_directory_is_refused(self) -> None:
        """Without the belt's run directory there is nowhere to measure into."""
        with tempfile.TemporaryDirectory() as tmp:
            binary = plant(Path(tmp), "a.test")
            done = run([str(binary), *LEAD], ORG_COVERAGE_RUN="")
            self.assertEqual(1, done.returncode)
            self.assertIn(
                "ORG_COVERAGE_RUN: unset — the belt names the run directory (#1667)",
                done.stderr,
            )
            self.assertEqual("", done.stdout)


class OtherBinaries(unittest.TestCase):
    """`go run` goes through -exec too: anything else runs unchanged."""

    def test_a_program_is_handed_no_test_flag(self) -> None:
        """Its own arguments, and nothing added."""
        with tempfile.TemporaryDirectory() as tmp:
            binary = plant(Path(tmp), "echoargs")
            done = run([str(binary), "one", "-test.v"], ORG_COVERAGE_RUN="/run")
            self.assertEqual(0, done.returncode, done.stderr)
            want: list[str] = ["one", "-test.v"]
            self.assertEqual(want, done.stdout.split())

    def test_no_binary_is_a_usage_error(self) -> None:
        """Called with nothing, it says how go calls it."""
        done = run([])
        self.assertEqual(2, done.returncode)
        self.assertIn("go-test-exec: no binary", done.stderr)


if __name__ == "__main__":
    unittest.main()
