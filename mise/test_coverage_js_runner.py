#!/usr/bin/env python3
"""Tests for coverage-js-runner.py's reading of task run entries.

The regression row of the defect `prop_coverage_js_runner.py` found
(#1555): `mise tasks info --json` reports a `{ task = "build" }` run
entry as an object, and `--vitest-command` raised AttributeError on it,
where the contract is the vitest command or a refusal by name.

The walk's rows (#1608) read `testdata/task-references`, mise's own
`tasks info --json` for a probe holding every documented way one task
runs another, captured at the belt's pinned mise.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import contextlib
import io
import json
import re
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
            status = cj.vitest_command("coverage:check", [task])
        self.assertEqual(status, 0, err.getvalue())
        self.assertEqual(json.loads(out.getvalue()), {"dir": ".", "args": ["run"]})


CAPTURE = json.loads(
    (HERE / "testdata" / "task-references" / "tasks-info.json").read_text("utf-8")
)
INFO = CAPTURE["tasks info --json"]
NAMES = CAPTURE["tasks ls --hidden --json"]


def walked(root: str) -> list[str]:
    """Walk the capture from a task.

    Returns:
        The names of the tasks read, in order, and a marker naming a
        task the capture cannot answer for.

    """
    tasks, unread = cj.walk(root, INFO.get, lambda: NAMES)
    return [t["name"] for t in tasks] + ([f"<unread {unread}>"] if unread else [])


class Walk(unittest.TestCase):
    """The walk follows every reference form mise reports (#1608)."""

    def test_run_entries_are_followed(self) -> None:
        """`{ task }` and `{ tasks }`, as captured."""
        self.assertEqual(walked("run-task"), ["run-task", "unit"])
        self.assertEqual(walked("run-tasks"), ["run-tasks", "lint", "unit"])

    def test_depends_forms_are_followed(self) -> None:
        """A name, an array, a table, depends_post, as captured."""
        for root in ("depends-args", "depends-env", "depends-table", "post-name"):
            with self.subTest(root=root):
                self.assertEqual(walked(root), [root, "unit"])

    def test_a_pattern_runs_every_match(self) -> None:
        """`tasks info <pattern>` answers one match; a run runs them all."""
        self.assertEqual(walked("depends-pattern"), ["depends-pattern", "unit:extra"])
        self.assertEqual(
            walked("run-task-pattern"),
            ["run-task-pattern", "unit:extra", "unit:more:deep"],
        )

    def test_wait_for_is_not_a_run(self) -> None:
        """`wait_for` adds no task to the run."""
        self.assertEqual(walked("wait-for"), ["wait-for"])

    def test_a_cycle_ends(self) -> None:
        """Each task is read once."""
        self.assertEqual(walked("cycle-a"), ["cycle-a", "cycle-b"])

    def test_an_unreadable_task_is_named(self) -> None:
        """A name mise cannot answer for stops the walk, by name."""
        _, unread = cj.walk("missing", INFO.get, lambda: NAMES)
        self.assertEqual(unread, "missing")


class Patterns(unittest.TestCase):
    """mise's wildcards, as docs/tasks/running-tasks.md lists them."""

    def test_each_wildcard(self) -> None:
        """`*` within a group, `**` across, `?`, braces and sets."""
        rows = (
            ("lint:*", "lint:a", True),
            ("lint:*", "lint:a:b", False),
            ("test:**", "test:a:b", True),
            ("t?st", "test", True),
            ("gen:{a,docs:*}", "gen:docs:x", True),
            ("gen:{a,docs:*}", "gen:b", False),
            ("[ab]x", "bx", True),
            ("[!ab]x", "bx", False),
            ("a.b", "axb", False),
        )
        for pattern, name, hit in rows:
            with self.subTest(pattern=pattern, name=name):
                matched = re.fullmatch(cj.glob_regex(pattern), name) is not None
                self.assertEqual(matched, hit)


if __name__ == "__main__":
    unittest.main()
