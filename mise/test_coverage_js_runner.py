#!/usr/bin/env python3
"""Tests for coverage-js-runner.py's reading of task run entries.

The regression row of the defect `prop_coverage_js_runner.py` found
(#1555): `mise tasks info --json` reports a `{ task = "build" }` run
entry as an object, and `--vitest-command` raised AttributeError on it,
where the contract is the vitest command or a refusal by name.

The walk itself is task_walk.py's, with its rows in test_task_walk.py
(#1608).

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import contextlib
import io
import json
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace

import task_walk as tw

HERE = Path(__file__).resolve().parent
cj = SimpleNamespace(**runpy.run_path(str(HERE / "coverage-js-runner.py")))


class TaskReferences(unittest.TestCase):
    """A run entry that names another task holds no script to read."""

    def test_a_task_reference_beside_the_script(self) -> None:
        """The shrunk example, as mise 2026.11 reports it (measured).

        The reference is the walk's to follow (#1608); the script beside
        it is read.
        """
        answers = {"test": {"run": [{"task": "build"}, "vitest run"]}, "build": {}}
        walked = tw.walk("test", answers.get, list)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cj.vitest_command("coverage:check", walked)
        self.assertEqual(status, 0, err.getvalue())
        self.assertEqual(json.loads(out.getvalue()), {"dir": ".", "args": ["run"]})

    def test_no_vitest_names_the_tasks_walked(self) -> None:
        """The refusal says where the leg looked (#1608 item 4)."""
        walked = tw.Walk(("test", "lint"), (tw.Step("lint", "", "echo lint"),))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status = cj.vitest_command("coverage:check", walked)
        self.assertEqual(status, 1)
        self.assertIn("  tasks walked: test, lint\n", err.getvalue())


if __name__ == "__main__":
    unittest.main()
