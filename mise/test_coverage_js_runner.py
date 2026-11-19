#!/usr/bin/env python3
"""Tests for coverage-js-runner.py's reading of task run entries (#1555).

The regression row of the defect `prop_coverage_js_runner.py` found:
`mise tasks info --json` reports a `{ task = "build" }` run entry as an
object, and `--vitest-command` raised AttributeError on it, where the
contract is the vitest command or a refusal by name.

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

HERE = Path(__file__).resolve().parent
cj = SimpleNamespace(**runpy.run_path(str(HERE / "coverage-js-runner.py")))


class TaskReferences(unittest.TestCase):
    """A run entry that names another task holds no script to read."""

    def test_a_task_reference_beside_the_script(self) -> None:
        """The shrunk example, as mise 2026.11 reports it (measured)."""
        task = {"name": "test", "run": [{"task": "build"}, "vitest run"]}
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cj.vitest_command("coverage:check", json.dumps(task) + "\n")
        self.assertEqual(status, 0, err.getvalue())
        self.assertEqual(json.loads(out.getvalue()), {"dir": ".", "args": ["run"]})


if __name__ == "__main__":
    unittest.main()
