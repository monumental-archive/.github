#!/usr/bin/env python3
"""Planted rows for the runner that names a test moving the process (#1491).

Each row writes a module into a scratch tree and runs it through
`mise/unittest-guarded.py`, from a starting directory of its own, as the
canon's `test` task does. The leak rows assert the MESSAGE and the test
it names; the clean rows assert that the runner is otherwise
`python3 -m unittest`: the same output and the same exit status.
"""

import os
import pathlib
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
RUNNER = _HERE / "unittest-guarded.py"
# unittest prints its own timing; two runs never agree on it.
TIMING = re.compile(r"Ran (\d+ tests?) in \d+\.\d+s")

LEAKS = """\
import os
import tempfile
import unittest


class T(unittest.TestCase):
    def test_a_leaves_a_deleted_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)

    def test_b_runs_where_the_run_started(self):
        self.assertEqual(os.getcwd(), os.environ["START"])
"""

MOVES = """\
import os
import unittest


class T(unittest.TestCase):
    def test_moves(self):
        os.chdir(os.environ["ELSEWHERE"])
"""

CLEAN = """\
import unittest


class T(unittest.TestCase):
    def test_one(self):
        self.assertEqual(1, 1)

    def test_two(self):
        self.assertTrue(True)
"""

FAILING = """\
import unittest


class T(unittest.TestCase):
    def test_wrong(self):
        self.assertEqual(1, 2)
"""


class Runner(unittest.TestCase):
    """The runner over planted modules."""

    def tree(self, **modules: str) -> tuple[pathlib.Path, pathlib.Path]:
        """Write modules into a scratch tree beside a starting directory.

        Returns:
            The starting directory and the tree's discovery root.

        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = pathlib.Path(tmp.name).resolve()
        start = base / "start"
        root = start / "suite"
        root.mkdir(parents=True)
        (base / "elsewhere").mkdir()
        for name, body in modules.items():
            (root / f"{name}.py").write_text(body, encoding="utf-8")
        return start, root

    @staticmethod
    def run_with(
        start: pathlib.Path,
        command: list[str],
        *flags: str,
    ) -> subprocess.CompletedProcess[str]:
        """Run a discovery command from `start`.

        Returns:
            The finished process.

        """
        env = {
            **os.environ,
            "START": str(start),
            "ELSEWHERE": str(start.parent / "elsewhere"),
        }
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [
                *command,
                "discover",
                *flags,
                *("--start-directory", "suite", "--pattern", "test_*.py"),
            ],
            cwd=start,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def guarded(self, start: pathlib.Path) -> subprocess.CompletedProcess[str]:
        """Run the planted tree through the guarded runner.

        Returns:
            The finished process.

        """
        return self.run_with(start, [sys.executable, str(RUNNER)])

    def test_a_deleted_directory_is_an_error_on_the_test_that_left_it(self) -> None:
        """The leaking test is the ERROR, by name; the next one passes."""
        start, _ = self.tree(test_leaks=LEAKS)
        r = self.guarded(start)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn(
            "ERROR: test_a_leaves_a_deleted_directory (test_leaks.T"
            ".test_a_leaves_a_deleted_directory)",
            r.stderr,
        )
        self.assertRegex(
            r.stderr,
            r"LeftTheProcessError: left the process in \S+ \(deleted\)",
        )
        self.assertNotIn("test_b_runs_where_the_run_started (", r.stderr)
        self.assertIn("FAILED (errors=1)", r.stderr)

    def test_a_moved_directory_names_where_it_started(self) -> None:
        """A directory that still exists is named, with the start beside it."""
        start, _ = self.tree(test_moves=MOVES)
        r = self.guarded(start)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("ERROR: test_moves (test_moves.T.test_moves)", r.stderr)
        self.assertIn(
            f"left the process in {start.parent / 'elsewhere'} (moved from {start})",
            r.stderr,
        )

    def test_otherwise_it_is_unittest(self) -> None:
        """A clean or failing module: unittest's output and status, exactly."""
        for name, body in (("test_clean", CLEAN), ("test_failing", FAILING)):
            with self.subTest(module=name):
                start, _ = self.tree(**{name: body})
                plain = self.run_with(start, [sys.executable, "-m", "unittest"])
                ours = self.guarded(start)
                self.assertEqual(ours.returncode, plain.returncode)
                self.assertEqual(ours.stdout, plain.stdout)
                self.assertEqual(
                    TIMING.sub(r"Ran \1", ours.stderr),
                    TIMING.sub(r"Ran \1", plain.stderr),
                )

    def test_verbosity_reaches_the_runner(self) -> None:
        """`-v` still prints one line per test: unittest builds the runner."""
        start, _ = self.tree(test_clean=CLEAN)
        r = self.run_with(start, [sys.executable, str(RUNNER)], "-v")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("test_one (test_clean.T.test_one) ... ok", r.stderr)


if __name__ == "__main__":
    unittest.main()
