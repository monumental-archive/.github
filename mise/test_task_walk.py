#!/usr/bin/env python3
"""Tests for task_walk.py, the one walk of a mise task (#1608).

The rows read `testdata/task-references`: mise's own `tasks info
--json` for a probe holding every way mise 2026.11.7's documentation
lists for one task to run another, captured at the belt's pinned mise.
A row names the probe task it walks from.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import decoded
import hermetic
import task_walk as tw

HERE = Path(__file__).resolve().parent
CAPTURE: object = json.loads(
    (HERE / "testdata" / "task-references" / "tasks-info.json").read_text("utf-8")
)


def captured(key: str) -> object:
    """Read one field of the capture.

    Returns:
        Its value.

    Raises:
        ValueError: the capture is not the object recorded.

    """
    if not decoded.is_object(CAPTURE) or key not in CAPTURE:
        msg = f"tasks-info.json holds no {key!r}"
        raise ValueError(msg)
    return CAPTURE[key]


def info_map() -> dict[str, object]:
    """Read the capture's `tasks info` answers, by task name.

    Returns:
        Each task's answer.

    Raises:
        ValueError: they are not an object.

    """
    answers = captured("tasks info --json")
    if not decoded.is_object(answers):
        msg = "tasks-info.json: its answers are not an object"
        raise ValueError(msg)
    return answers


def name_list() -> list[str]:
    """Read the capture's `tasks ls` names.

    Returns:
        The names.

    Raises:
        ValueError: they are not strings.

    """
    names = captured("tasks ls --hidden --json")
    if not decoded.is_strings(names):
        msg = "tasks-info.json: its names are not strings"
        raise ValueError(msg)
    return names


INFO = info_map()
NAMES = name_list()


def answer(name: str, **fields: object) -> dict[str, object]:
    """Read one captured answer, with fields replaced.

    Returns:
        The answer, as a new object.

    Raises:
        ValueError: the capture holds no such task.

    """
    found = INFO.get(name)
    if not decoded.is_object(found):
        msg = f"tasks-info.json holds no task {name!r}"
        raise ValueError(msg)
    return found | fields


def walk(root: str) -> tw.Walk:
    """Walk the capture from one of its tasks.

    Returns:
        The walk.

    """
    return tw.walk(root, INFO.get, lambda: NAMES)


def scripts(root: str) -> list[tuple[str, str]]:
    """Walk the capture and name each step by its task and script.

    Returns:
        (task, script) per step, in order.

    """
    return [(s.task, s.script) for s in walk(root).steps]


UNIT = ("unit", "pnpm exec vitest run tests/unit")


class Order(unittest.TestCase):
    """`mise run`'s order: depends, the task's run in place, depends_post."""

    def expect_steps(self, root: str, want: list[tuple[str, str]]) -> None:
        """Assert the steps walked from a probe task, by task and script."""
        self.assertEqual(scripts(root), want)

    def test_depends_run_first(self) -> None:
        """A depends name, as an array or a lone string."""
        for root in ("depends-name", "depends-string"):
            with self.subTest(root=root):
                self.expect_steps(root, [UNIT, (root, "echo test")])

    def test_a_run_reference_runs_in_place(self) -> None:
        """`run = [{ task = "unit" }, "echo after"]`."""
        self.expect_steps("run-task", [UNIT, ("run-task", "echo after")])

    def test_a_run_tasks_entry_runs_each(self) -> None:
        """`run = [{ tasks = ["lint", "unit"] }]`."""
        self.expect_steps("run-tasks", [("lint", "echo lint"), UNIT])

    def test_depends_post_runs_after(self) -> None:
        """A depends_post name, and its table form."""
        for root in ("post-name", "post-table"):
            with self.subTest(root=root):
                self.expect_steps(root, [(root, "echo test"), UNIT])

    def test_extends_is_already_resolved(self) -> None:
        """A template's `run` is reported as the task's own."""
        self.expect_steps("extends", [UNIT])

    def test_wait_for_is_not_a_run(self) -> None:
        """`wait_for` adds no task to the run."""
        self.assertEqual(walk("wait-for").tasks, ("wait-for",))

    def test_each_task_once_and_a_cycle_ends(self) -> None:
        """cycle-a depends on cycle-b, whose run names cycle-a."""
        found = walk("cycle-a")
        self.assertEqual(found.tasks, ("cycle-a", "cycle-b"))
        self.expect_steps("cycle-a", [("cycle-a", "echo a")])

    def test_every_task_reached_is_named(self) -> None:
        """A depends and a `{ task }` entry, in the order reached."""
        self.assertEqual(walk("none").tasks, ("none", "lint", "run-string"))

    def test_a_step_runs_in_its_own_tasks_dir(self) -> None:
        """Each step carries its task's `dir`, as mise reports it."""
        dirs: set[str] = {s.dir for s in walk("run-task").steps}
        want: set[str] = {"<probe>"}
        self.assertEqual(dirs, want)


class Patterns(unittest.TestCase):
    """`tasks info <pattern>` answers one match; a run runs them all."""

    def test_a_depends_pattern_runs_every_match(self) -> None:
        """`depends = ["unit:*"]`: `*` stays within one group."""
        self.assertEqual(
            walk("depends-pattern").tasks, ("depends-pattern", "unit:extra")
        )

    def test_a_run_pattern_runs_every_match(self) -> None:
        """`run = [{ task = "unit:**" }]`: `**` crosses groups."""
        self.assertEqual(
            walk("run-task-pattern").tasks,
            ("run-task-pattern", "unit:extra", "unit:more:deep"),
        )

    def test_a_pattern_matching_nothing_is_refused(self) -> None:
        """Such a run fails in mise; the walk says so by name."""
        with self.assertRaisesRegex(
            tw.RefusedError, "'run-task-pattern' runs the pattern"
        ):
            tw.walk("run-task-pattern", INFO.get, lambda: ["unit"])

    def test_each_wildcard(self) -> None:
        """The wildcards docs/tasks/running-tasks.md lists."""
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
                matched = re.fullmatch(tw.pattern_regex(pattern), name) is not None
                self.assertEqual(matched, hit)


class Refusals(unittest.TestCase):
    """A form the walk cannot run is refused by name, never skipped."""

    def test_a_reference_that_passes_arguments_or_env(self) -> None:
        """The script runs as written, so what it is passed is lost."""
        rows = (
            ("depends-args", "arguments"),
            ("depends-array", "arguments"),
            ("depends-env", "environment"),
            ("depends-table", "arguments"),
            ("run-task-args", "arguments"),
        )
        for root, what in rows:
            with self.subTest(root=root):
                with self.assertRaises(tw.RefusedError) as caught:
                    walk(root)
                self.assertEqual(
                    str(caught.exception),
                    f"task '{root}' runs 'unit' with {what},"
                    " which the measured run cannot pass",
                )

    def test_a_task_mise_cannot_answer_for(self) -> None:
        """The name is the message."""
        with self.assertRaisesRegex(
            tw.RefusedError, "^cannot read task 'missing' with mise tasks info$"
        ):
            walk("missing")

    def test_a_file_task(self) -> None:
        """Its script is a file the walk does not read."""
        planted = answer("unit", run=[], file="<probe>/unit.sh")
        with self.assertRaisesRegex(tw.RefusedError, "'unit' is a file task"):
            tw.walk("unit", {"unit": planted}.get, list)

    def test_an_entry_of_no_documented_shape(self) -> None:
        """A table naming no task, or a task that is not a name."""
        entries: tuple[object, ...] = ({"args": ["x"]}, {"task": 3}, 7)
        for entry in entries:
            with self.subTest(entry=entry):
                planted = answer("unit", depends=[entry])
                with self.assertRaisesRegex(tw.RefusedError, "a depends entry"):
                    tw.walk("unit", {"unit": planted}.get, list)

    def test_a_task_with_no_fields_reads_as_empty(self) -> None:
        """A field mise leaves out is no entry, not a refusal."""
        empty: dict[str, object] = {"x": {}}
        self.assertEqual(tw.walk("x", empty.get, list), tw.Walk(("x",), ()))


# A `mise` answering the read-only questions the walk asks of it.
MISE = """\
#!/usr/bin/env bash
case "$*" in
  "tasks ls --hidden --json") echo '[{"name": "a"}, {"nope": 1}, "b", {"name": 2}]' ;;
  "tasks info x --json") echo '{"run": ["echo hi"]}' ;;
  "tasks info bad --json") echo 'not json' ;;
  *) exit 1 ;;
esac
"""


class Mise(unittest.TestCase):
    """The walk's questions to mise, against a stub on PATH (#1659)."""

    def setUp(self) -> None:
        """Put the stub, and nothing else, on PATH."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        stub = Path(tmp.name) / "mise"
        stub.write_text(MISE, encoding="utf-8")
        stub.chmod(0o755)
        bash = hermetic.tool("bash")
        (Path(tmp.name) / "bash").symlink_to(bash)
        patcher = mock.patch.dict(os.environ, {"PATH": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_task_names_keeps_only_named_rows(self) -> None:
        """A row with no string name is not a task."""
        self.assertEqual(["a"], tw.task_names())

    def test_an_answer_mise_cannot_give_is_none(self) -> None:
        """A failed question and an unreadable answer both read as none."""
        self.assertIsNone(tw.mise_json("tasks", "info", "gone"))
        self.assertIsNone(tw.mise_json("tasks", "info", "bad"))

    def test_walk_mise_walks_from_a_named_root(self) -> None:
        """`walk_mise` asks mise for each task it reaches, from the root named."""
        walked = tw.walk_mise("x")
        self.assertEqual(("echo hi",), tuple(step.script for step in walked.steps))

    def test_no_mise_on_path_is_none(self) -> None:
        """Without mise there is nothing to ask."""
        with mock.patch.dict(os.environ, {"PATH": ""}):
            self.assertIsNone(tw.mise_json("tasks", "ls"))


class Fields(unittest.TestCase):
    """A field mise reports as one entry, or in no documented form."""

    def test_a_single_script_is_one_entry(self) -> None:
        """`run` as a string is that one script."""
        walked = tw.walk("x", {"x": {"run": "echo hi"}}.get, list)
        self.assertEqual(("echo hi",), tuple(step.script for step in walked.steps))

    def test_a_field_of_no_documented_form_is_refused(self) -> None:
        """A number where entries belong is named, never read as none."""
        with self.assertRaisesRegex(
            tw.RefusedError, "task 'x': its depends is not a form mise documents"
        ):
            tw.walk("x", {"x": {"depends": 5}}.get, list)


if __name__ == "__main__":
    unittest.main()
