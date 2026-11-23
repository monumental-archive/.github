#!/usr/bin/env python3
"""The coverage tasks over a TypeScript tree (#1357).

Split out of test_coverage_tasks.py so the gate's test shards can run
it beside the rest (#2049); the planted trees, stubs and the task
runner are that file's, imported from it.

TypeScriptRows: vitest found through the tree's own test task, its
coverage provider, and every leg printed beside Python's.

stdlib `unittest`, run by `mise run test`.
"""

import unittest
from typing import Unpack

from test_coverage_tasks import (
    HEAD_MSG,
    CoverageTaskCase,
    VitestPlant,
    upload_line,
)


class TypeScriptRows(CoverageTaskCase):
    """`coverage:check` over the TypeScript leg (#1357)."""

    def test_a_vitest_tree_measures_and_holds(self) -> None:
        """Both TypeScript legs print, and each holds the floor by name."""
        done, tree = self.run_task("coverage:check", python=False, typescript="vitest")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:check: typescript leg holds the floor 80.0 (88.0%)\n"
            "coverage:check: typescript-branches leg holds the floor 80.0 (81.0%)\n"
            + upload_line(tree, "lcov-typescript.info"),
            done.stdout,
        )
        self.assertIn("vitest suite ran", done.stderr)

    def test_branches_below_the_floor_red_by_name(self) -> None:
        """Lines holding does not carry the branch leg."""
        done, _ = self.run_task(
            "coverage:check", python=False, typescript="vitest", ts_branches="79.5"
        )
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "coverage:check: typescript-branches: 79.5% is below the derived"
            " floor 80.0%\n",
            done.stderr,
        )

    def test_lines_below_the_floor_red_by_name(self) -> None:
        """The line leg reds before the branch leg is read."""
        done, _ = self.run_task(
            "coverage:check", python=False, typescript="vitest", ts="60.0"
        )
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "coverage:check: typescript: 60.0% is below the derived floor 80.0%\n",
            done.stderr,
        )

    def test_no_floor_skips_clean(self) -> None:
        """A vitest tree with no floor owes nothing, and runs nothing."""
        done, _ = self.run_task(
            "coverage:check", python=False, typescript="vitest", floor=False
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("coverage:check: no .coverage-floor, skipped\n", done.stdout)
        self.assertNotIn("vitest suite ran", done.stderr)

    def test_an_unsupported_runner_is_named_and_skipped(self) -> None:
        """A jest tree is named, never guessed at or measured as nothing."""
        done, _ = self.run_task("coverage:check", python=False, typescript="jest")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn(
            "coverage:check: typescript: jest is not a runner the leg supports"
            " (vitest), skipped\n",
            done.stderr,
        )
        self.assertEqual(
            "coverage:check: no measurable language (go.mod, Cargo.toml,"
            " tracked *.py with a test task, package.json with vitest, or"
            " a floor recording its legs),"
            " skipped\n",
            done.stdout,
        )

    def test_vitest_without_a_provider_reds_with_the_remedy(self) -> None:
        """The belt pins no provider; the lockfile must."""
        done, _ = self.run_task(
            "coverage:check", python=False, typescript="vitest-bare"
        )
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "coverage:check: vitest runs the suite but the lockfile pins no"
            " coverage provider —\n"
            "  add @vitest/coverage-v8 to devDependencies at vitest's own version\n",
            done.stderr,
        )

    def test_mixed_python_and_typescript_prints_every_leg(self) -> None:
        """A mixed tree measures each language it tracks."""
        done, tree = self.run_task("coverage:check", typescript="vitest")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:check: python leg holds the floor 80.0 (90.0%)\n"
            "coverage:check: python-branches leg holds the floor 80.0 (85.0%)\n"
            "coverage:check: typescript leg holds the floor 80.0 (88.0%)\n"
            "coverage:check: typescript-branches leg holds the floor 80.0 (81.0%)\n"
            + upload_line(tree, "lcov-typescript.info", "coverage.xml"),
            done.stdout,
        )

    def test_the_test_task_selection_is_the_measured_selection(self) -> None:
        """A `test` task narrower than vitest's config is measured as written.

        #1357 item 2: the leg runs the repository's own test command, so a
        task running `vitest run tests/unit` is measured over tests/unit,
        not over every file the vitest config would select.
        """
        done, _ = self.run_task(
            "coverage:check",
            python=False,
            typescript="vitest",
            test_run="vitest run tests/unit",
        )
        self.assertEqual(0, done.returncode, done.stderr)
        runs = [
            ln.removeprefix("vitest args: ").split()
            for ln in done.stderr.splitlines()
            if ln.startswith("vitest args: ")
        ]
        self.assertEqual(1, len(runs), done.stderr)
        self.assertIn("tests/unit", runs[0])
        self.assertIn("--coverage.enabled=true", runs[0])

    def test_a_depended_on_vitest_task_is_followed(self) -> None:
        """The test task's `depends` are read: vitest may live in one."""
        done, _ = self.run_task(
            "coverage:check",
            python=False,
            typescript="vitest",
            test_run="echo test: unit tests green",
            depends={"test:unit": "pnpm exec vitest run tests/unit"},
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn(
            "vitest args: run tests/unit --coverage.enabled=true", done.stderr
        )

    def test_a_vitest_tree_without_a_test_task_skips_clean(self) -> None:
        """No test task, no suite the gate runs: nothing to measure."""
        done, _ = self.run_task(
            "coverage:check", python=False, typescript="vitest", test_task=False
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("no measurable language", done.stdout)
        self.assertNotIn("vitest suite ran", done.stderr)

    def refused(self, run: str, message: str, **plant: Unpack[VitestPlant]) -> None:
        """Assert a test task whose vitest command is refused, by message."""
        done, _ = self.run_task(
            "coverage:check",
            python=False,
            typescript="vitest",
            test_run=run,
            **plant,
        )
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertIn(message, done.stderr)
        self.assertNotIn("vitest suite ran", done.stderr)

    def test_a_test_task_without_vitest_is_refused(self) -> None:
        """A `pnpm test` behind a package.json script is not read through."""
        self.refused(
            "pnpm test", HEAD_MSG + ", and the test task and its depends run none —\n"
        )

    def test_two_vitest_commands_are_refused(self) -> None:
        """The leg measures one suite."""
        self.refused(
            "vitest run tests/a",
            HEAD_MSG + ", and the test task and its depends run 2 —\n",
            depends={"test:more": "vitest run tests/b"},
        )

    def test_a_shell_expansion_is_refused(self) -> None:
        """Arguments only the shell can resolve are not guessed at."""
        self.refused(
            "vitest run ${FILES}",
            "  test: a shell expansion in `vitest run ${FILES}`\n",
        )

    def test_a_compound_line_is_refused(self) -> None:
        """A `cd` before vitest is a directory the leg would not honour."""
        self.refused(
            "cd web && vitest run",
            "  test: vitest inside a compound line, `cd web && vitest run`\n",
        )

    def test_an_unknown_launcher_is_refused(self) -> None:
        """Only the package managers' plain launchers are read."""
        self.refused(
            "bunx vitest run",
            "  test: vitest behind a command the leg does not read,"
            " `bunx vitest run`\n",
        )

    def test_a_subcommand_other_than_run_is_refused(self) -> None:
        """`vitest watch` is not a suite run."""
        self.refused("vitest watch", HEAD_MSG + ", and `vitest watch` is not a run —\n")

    def test_a_coverage_flag_in_the_task_is_refused(self) -> None:
        """The leg owns the coverage flags."""
        self.refused(
            "vitest run --coverage.enabled=false",
            HEAD_MSG + ", and it sets coverage itself —\n",
        )

    def test_check_writes_the_typescript_lcov(self) -> None:
        """The upload is the measured run's own lcov, off lcov.info."""
        done, tree = self.run_task("coverage:check", python=False, typescript="vitest")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(
            done.stdout.endswith(upload_line(tree, "lcov-typescript.info")),
            done.stdout,
        )
        reports = tree.parent / "reports"
        self.assertEqual("TN:\n", (reports / "lcov-typescript.info").read_text("utf-8"))
        self.assertFalse((reports / "lcov.info").exists())


if __name__ == "__main__":
    unittest.main()
