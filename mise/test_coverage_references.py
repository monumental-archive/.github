#!/usr/bin/env python3
"""The coverage tasks follow every way one task runs another (#1608).

Split out of test_coverage_tasks.py so the gate's test shards can run
it beside the rest (#2049); the planted trees, stubs and the task
runner are that file's, imported from it.

TaskReferenceRows: the captured `mise tasks info` answers for each
reference form mise documents.

stdlib `unittest`, run by `mise run test`.
"""

import unittest
from typing import TYPE_CHECKING

from test_coverage_tasks import (
    HEAD_MSG,
    CoverageTaskCase,
)

if TYPE_CHECKING:
    import subprocess


class TaskReferenceRows(CoverageTaskCase):
    """The leg follows every task the test task runs to vitest (#1608).

    mise answers from the task-references capture: its own `tasks info
    --json` for a probe holding each documented way one task runs
    another.
    """

    def captured(self, root: str) -> subprocess.CompletedProcess[str]:
        """Run the typescript leg with the captured task as `test`.

        Returns:
            The finished process.

        """
        done, _ = self.run_task(
            "coverage:check", python=False, typescript="vitest", captured_test=root
        )
        return done

    def found_through(self, root: str) -> None:
        """Assert the captured task's vitest is reached and measured."""
        done = self.captured(root)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn(
            "vitest args: run tests/unit --coverage.enabled=true", done.stderr
        )

    def test_vitest_through_a_run_task_entry_is_found(self) -> None:
        """`run = [{ task = "unit" }, …]` runs unit, where vitest is (#1608)."""
        self.found_through("run-task")

    def test_vitest_through_a_run_tasks_entry_is_found(self) -> None:
        """`run = [{ tasks = ["lint", "unit"] }]` runs unit too (#1608)."""
        self.found_through("run-tasks")

    def test_vitest_through_depends_post_is_found(self) -> None:
        """`depends_post = ["unit"]` runs unit after the task (#1608)."""
        self.found_through("post-name")

    def test_every_captured_reference_form_is_followed(self) -> None:
        """Each form that runs a task as written reaches unit (#1608).

        A depends name and lone string, a table depends_post, and a run
        inherited through `extends`. Patterns are test_task_walk.py's:
        the probe's matches run no vitest.
        """
        for root in ("depends-name", "depends-string", "post-table", "extends"):
            with self.subTest(root=root):
                self.found_through(root)

    def test_a_depends_with_arguments_is_refused_by_name(self) -> None:
        """A depends with arguments: mise reports an array (#1608).

        On main the jq walk died on it with jq's own error, naming no
        task. The measured run cannot pass the arguments, so the walk
        refuses the task that asks for them.
        """
        done = self.captured("depends-args")
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertIn(
            "coverage:check: typescript: task 'test' runs 'unit' with arguments,"
            " which the measured run cannot pass\n",
            done.stderr,
        )
        self.assertNotIn("vitest suite ran", done.stderr)

    def test_every_form_that_passes_something_is_refused(self) -> None:
        """An env prefix, a table with args, a `{ task }` with args."""
        for root, what in (
            ("depends-array", "arguments"),
            ("depends-env", "environment"),
            ("depends-table", "arguments"),
            ("run-task-args", "arguments"),
        ):
            with self.subTest(root=root):
                done = self.captured(root)
                self.assertEqual(1, done.returncode, done.stderr)
                self.assertIn(f"runs 'unit' with {what},", done.stderr)

    def test_a_cycle_ends_and_refuses_by_name(self) -> None:
        """cycle-a depends on cycle-b, which runs cycle-a: no vitest."""
        done = self.captured("cycle-a")
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertIn(
            HEAD_MSG + ", and the test task and its depends run none —\n"
            "  tasks walked: test, cycle-b, cycle-a\n",
            done.stderr,
        )
        self.assertNotIn("vitest suite ran", done.stderr)

    def test_no_vitest_anywhere_names_the_tasks_walked(self) -> None:
        """A depends and a `{ task }` entry are walked, and named."""
        done = self.captured("none")
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertIn(
            HEAD_MSG + ", and the test task and its depends run none —\n"
            "  tasks walked: test, lint, run-string\n",
            done.stderr,
        )
        self.assertNotIn("vitest suite ran", done.stderr)

    def test_wait_for_is_not_followed(self) -> None:
        """`wait_for` adds no task to the run, so its vitest is not the suite."""
        done = self.captured("wait-for")
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertIn("  tasks walked: test\n", done.stderr)


if __name__ == "__main__":
    unittest.main()
