#!/usr/bin/env python3
"""Table tests for startup_hook.py: a belt hook never ends the chain (#1542).

Each row writes stand-in hooks whose work logs its name, puts their
directories on PYTHONPATH in the row's order, starts a bare `python3`,
and asserts which hooks ran, in order. Measured before the chain: a
coverage measurement nested inside the mutated suite put its hook first,
Python imported only that one, and the mutated helper could not import
mutmut (the whole-collection reproduction on #1674).

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest import mock

import hermetic
import startup_hook


def logs(name: str) -> str:
    """Write a hook's work: append its name to the row's log.

    Returns:
        The work's source.

    """
    return (
        "import os\n"
        "with open(os.environ['HOOK_LOG'], 'a') as f:\n"
        f"    f.write({name!r} + '\\n')\n"
    )


class Chain(unittest.TestCase):
    """Every hook on the path runs, whatever the order and depth."""

    @override
    def setUp(self) -> None:
        """Make the row's scratch and its log."""
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.log = self.tmp / "log"

    def belt(self, name: str) -> Path:
        """Write a belt hook through the writer.

        Returns:
            Its directory.

        """
        directory = self.tmp / name
        startup_hook.write(directory, logs(name))
        return directory

    def own(self, name: str) -> Path:
        """Write a repository's own sitecustomize, which chains nothing.

        Returns:
            Its directory.

        """
        directory = self.tmp / name
        directory.mkdir()
        (directory / "sitecustomize.py").write_text(logs(name), encoding="utf-8")
        return directory

    def ran(self, *path: Path) -> list[str]:
        """Start a bare python3 with the hooks on PYTHONPATH in this order.

        Returns:
            The names of the hooks that ran, in order.

        """
        env = hermetic.env(
            PYTHONPATH=":".join(str(p) for p in path), HOOK_LOG=str(self.log)
        )
        done = subprocess.run(
            [sys.executable, "-c", "pass"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        return self.log.read_text(encoding="utf-8").split() if self.log.exists() else []

    def assert_ran(self, expected: list[str], *path: Path) -> None:
        """Assert which hooks ran, in order, for this PYTHONPATH."""
        self.assertEqual(expected, self.ran(*path))

    def test_coverage_first_then_the_leg_both_run(self) -> None:
        """The nested measurement: coverage's hook ahead of the leg's."""
        self.assert_ran(
            ["coverage", "mutation"],
            self.belt("coverage"),
            self.belt("mutation"),
        )

    def test_the_reverse_order_both_run(self) -> None:
        """The leg's hook ahead of coverage's."""
        self.assert_ran(
            ["mutation", "coverage"],
            self.belt("mutation"),
            self.belt("coverage"),
        )

    def test_a_repository_hook_runs_behind_either(self) -> None:
        """A repository's own sitecustomize, behind a belt hook, still runs."""
        for first in ("coverage", "mutation"):
            with self.subTest(first):
                self.log.unlink(missing_ok=True)
                hook = self.tmp / first
                shutil.rmtree(hook, ignore_errors=True)
                shutil.rmtree(self.tmp / "own", ignore_errors=True)
                self.assert_ran([first, "own"], self.belt(first), self.own("own"))

    def test_none_behind_is_nothing(self) -> None:
        """One hook alone: its work, and no error."""
        self.assert_ran(["coverage"], self.belt("coverage"))

    def test_three_deep_all_run(self) -> None:
        """Two belt hooks and a repository's own: all three, in order."""
        self.assert_ran(
            ["coverage", "mutation", "own"],
            self.belt("coverage"),
            self.belt("mutation"),
            self.own("own"),
        )

    def test_an_error_in_the_next_hook_is_reported_as_if_it_were_first(self) -> None:
        """The next hook's error reaches `site`, which names it."""
        broken = self.tmp / "broken"
        broken.mkdir()
        (broken / "sitecustomize.py").write_text("raise ValueError('planted')\n")
        env = hermetic.env(
            PYTHONPATH=f"{self.belt('coverage')}:{broken}", HOOK_LOG=str(self.log)
        )
        done = subprocess.run(
            [sys.executable, "-c", "pass"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn("Error in sitecustomize", done.stderr)
        self.assertIn("ValueError: planted", done.stderr)

    def test_the_command_line_writes_the_hook_from_stdin(self) -> None:
        """The shell's form: work on stdin, directory named."""
        directory = self.tmp / "cli"
        done = subprocess.run(
            [sys.executable, str(Path(startup_hook.__file__)), str(directory)],
            input=logs("cli"),
            env=hermetic.env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assert_ran(["cli"], directory)

    def test_main_writes_the_hook_in_process(self) -> None:
        """`main([dir])` with the work on stdin; no directory is a usage error."""
        directory = self.tmp / "main"
        with mock.patch("sys.stdin", io.StringIO(logs("main"))):
            self.assertEqual(0, startup_hook.main([str(directory)]))
        self.assert_ran(["main"], directory)
        with mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(2, startup_hook.main([]))
        self.assertEqual("usage: startup_hook.py DIR < WORK\n", err.getvalue())


if __name__ == "__main__":
    unittest.main()
