#!/usr/bin/env python3
"""Tests for coverage_js_runner.py's reading of task run entries.

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
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import coverage_js_runner as cj
import task_walk as tw

HERE = Path(__file__).resolve().parent


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


class Readers(unittest.TestCase):
    """Every shape the runner and line readers decide (#1659)."""

    def test_other_runners_are_named_from_the_manifest(self) -> None:
        """A dependency, a `test` script, npm's placeholder, or nothing."""
        placeholder = 'echo "Error: no test specified" && exit 1'
        for manifest, runner in (
            ({"devDependencies": {"cypress": "13"}}, "cypress"),
            ({"dependencies": "not a table", "scripts": {"test": "ava -v"}}, "ava"),
            ({"scripts": {"test": "node --test src/"}}, "node --test"),
            ({"scripts": {"test": placeholder}}, None),
            ({"scripts": {"test": "   "}}, None),
            ({"scripts": "not a table"}, None),
        ):
            with self.subTest(manifest=manifest):
                self.assertEqual(runner, cj.other_runner(cj.read_manifest(manifest)))

    def test_continued_lines_join_and_comments_drop(self) -> None:
        """A backslash joins the next line; a dangling one ends the script."""
        script = "vitest \\\n  run\n# a comment\n\nfoo \\"
        self.assertEqual(["vitest    run", "foo"], cj.logical_lines(script))

    def test_an_unbalanced_quote_is_refused_only_where_vitest_is(self) -> None:
        """The leg names a line it cannot read only if it runs vitest."""
        self.assertEqual(
            (None, "an unreadable line, `vitest 'run`"), cj.read_line("vitest 'run")
        )
        self.assertEqual((None, None), cj.read_line("echo 'x"))

    def test_a_bare_vitest_runs_as_run(self) -> None:
        """No subcommand is vitest's watch mode; the leg runs `run`."""
        walked = tw.Walk(("test",), (tw.Step("test", "", "vitest --silent"),))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = cj.vitest_command("coverage:check", walked)
        self.assertEqual(0, status)
        self.assertEqual(
            {"dir": ".", "args": ["run", "--silent"]}, json.loads(out.getvalue())
        )


class Main(unittest.TestCase):
    """The runner line for a planted tree's package.json (#1659)."""

    def main(self, tracked: list[str], files: dict[str, str]) -> tuple[int, str, str]:
        """Run main from a scratch tree holding `files`.

        Returns:
            The status, stdout and stderr.

        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, text in files.items():
            (Path(tmp.name) / name).write_text(text, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.chdir(tmp.name),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            status = cj.main(tracked)
        return status, out.getvalue(), err.getvalue()

    def test_no_tracked_package_json_prints_nothing(self) -> None:
        """An untracked package.json is never read."""
        self.assertEqual((0, "", ""), self.main([], {"package.json": "{"}))

    def test_an_unreadable_package_json_is_refused(self) -> None:
        """Broken JSON is exit 1, named, never read as no runner."""
        status, out, err = self.main(["package.json"], {"package.json": "{"})
        self.assertEqual((1, ""), (status, out))
        self.assertTrue(err.startswith("coverage-js-runner: package.json: "), err)

    def test_a_package_json_that_is_not_an_object_names_no_runner(self) -> None:
        """A JSON array declares nothing."""
        self.assertEqual(
            (0, "", ""), self.main(["package.json"], {"package.json": "[]"})
        )

    def test_vitest_declared_without_a_lockfile_entry_is_named(self) -> None:
        """Declared but not pinned: nothing the leg may run."""
        manifest = json.dumps({"devDependencies": {"vitest": "4.1.12"}})
        self.assertEqual(
            (0, "unsupported vitest-without-a-lockfile-entry\n", ""),
            self.main(["package.json"], {"package.json": manifest}),
        )

    def test_another_runner_is_named(self) -> None:
        """A jest tree is named so the skip says what it skipped."""
        manifest = json.dumps({"devDependencies": {"jest": "29"}})
        self.assertEqual(
            (0, "unsupported jest\n", ""),
            self.main(["package.json"], {"package.json": manifest}),
        )


class VitestCommand(unittest.TestCase):
    """`--vitest-command <label> <task>`, through main in this process (#1696)."""

    def call(self, walked: tw.Walk | tw.RefusedError) -> tuple[int, str, str]:
        """Call main with the walk mise would answer replaced by `walked`.

        Returns:
            The status, stdout and stderr.

        """

        def walk_mise(root: str) -> tw.Walk:
            self.assertEqual(root, "test")
            if isinstance(walked, tw.RefusedError):
                raise walked
            return walked

        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(cj.task_walk, "walk_mise", walk_mise),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            status = cj.main(["--vitest-command", "coverage:check", "test"])
        return status, out.getvalue(), err.getvalue()

    def test_the_walked_vitest_command_is_printed(self) -> None:
        """The one vitest run of the walked steps, as JSON."""
        walked = tw.Walk(("test",), (tw.Step("test", "web", "pnpm exec vitest run"),))
        status, out, err = self.call(walked)
        self.assertEqual((0, ""), (status, err))
        self.assertEqual(json.loads(out), {"dir": "web", "args": ["run"]})

    def test_a_walk_refused_is_named_under_the_leg(self) -> None:
        """The walk's refusal, with the leg's label, and exit 1."""
        refused = tw.RefusedError("task 'test' is a file task")
        self.assertEqual(
            (1, "", "coverage:check: typescript: task 'test' is a file task\n"),
            self.call(refused),
        )


class Script(unittest.TestCase):
    """The one row that starts the helper as a process: the script contract."""

    def test_the_helper_runs_as_a_script(self) -> None:
        """No tracked package.json: it starts, prints nothing and exits 0."""
        with tempfile.TemporaryDirectory() as tmp:
            # ruff: ignore[subprocess-without-shell-equals-true]
            done = subprocess.run(
                [sys.executable, str(HERE / "coverage_js_runner.py"), "yarn.lock"],
                cwd=tmp,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual((0, "", ""), (done.returncode, done.stdout, done.stderr))


if __name__ == "__main__":
    unittest.main()
