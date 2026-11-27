#!/usr/bin/env python3
"""Tests for coverage_js_runner.py's readers (#1659, #2105).

The runner line a tree's package.json and lockfile answer, and the
vitest run `measured` reads off its own command line (`--measured-run`).

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import coverage_js_runner as cj
import decoded
import hermetic

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "coverage:check"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


HERE = Path(__file__).resolve().parent


class Readers(unittest.TestCase):
    """Every shape the runner reader decides (#1659)."""

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


# One lockfile of each format the reader answers, and the versions each
# resolves for its package.
PNPM_LOCK = (
    "packages:\n\n  vitest@4.1.12:\n    resolution: {}\n\nsnapshots:\n\n"
    "  vitest@4.1.12(@types/node@22.0.0):\n    dependencies: {}\n"
    "  vitest@3.2.4:\n    dependencies: {}\n"
)
# A nested node_modules entry is another package's copy, not the root's.
NPM_LOCK = (
    '{"packages": {"node_modules/vitest": {"version": "4.1.12"},'
    ' "node_modules/x/node_modules/vitest": {"version": "1.0.0"}}}'
)
YARN_LOCK = 'vitest@^4, vitest@^4.1.0:\n  version "4.1.12"\n  resolved "x"\n'
BERRY_LOCK = '"vitest@npm:^4":\n  version: 4.1.12\n  resolution: "vitest@npm:4.1.12"\n'
BUN_LOCK = '{"packages": {"vitest": ["vitest@4.1.12", "", {}, "sha"]}}'
CARGO_LOCK = (
    '[[package]]\nname = "serde"\nversion = "1.0.154"\n\n'
    '[[package]]\nname = "serde_json"\nversion = "1.0.140"\n'
)
NONE: set[str] = set()
ONE: set[str] = {"4.1.12"}
TWO: set[str] = {"4.1.12", "3.2.4"}
CRATE: set[str] = {"1.0.154"}


class Locked(unittest.TestCase):
    """The version each lockfile resolves an entry to (#2145)."""

    def test_every_format_reads_the_installed_version(self) -> None:
        """The version installed, never the range the entry was asked for."""
        for lockfile, lock, name, want in (
            ("pnpm-lock.yaml", PNPM_LOCK, "vitest", TWO),
            ("package-lock.json", NPM_LOCK, "vitest", ONE),
            ("yarn.lock", YARN_LOCK, "vitest", ONE),
            ("yarn.lock", BERRY_LOCK, "vitest", ONE),
            ("bun.lock", BUN_LOCK, "vitest", ONE),
            ("Cargo.lock", CARGO_LOCK, "serde", CRATE),
        ):
            with self.subTest(lockfile=lockfile, lock=lock[:24]):
                self.assertEqual(want, cj.locked(lockfile, lock, name))

    def test_an_indented_yarn_mention_is_not_an_entry(self) -> None:
        """Berry's `resolution:` line repeats an entry; alone it is none."""
        lock = '"other@npm:^1":\n  version: 1.0.0\n  resolution: "vitest@npm:4.1.12"\n'
        self.assertEqual(NONE, cj.locked("yarn.lock", lock, "vitest"))

    def test_a_name_is_matched_whole_and_literally(self) -> None:
        """`vitest` is not `@vitest/expect`, and a dot in a name is a dot."""
        lock = "packages:\n\n  '@vitest/expect@4.1.12':\n  lodashxget@4.4.2:\n"
        self.assertEqual(NONE, cj.locked("pnpm-lock.yaml", lock, "vitest"))
        self.assertEqual(NONE, cj.locked("pnpm-lock.yaml", lock, "lodash.get"))
        self.assertEqual(ONE, cj.locked("pnpm-lock.yaml", lock, "@vitest/expect"))

    def test_an_entry_without_a_version_reads_none(self) -> None:
        """An entry whose version is not where its format writes one."""
        for lockfile, lock in (
            ("package-lock.json", '{"packages": {"node_modules/vitest": {}}}'),
            ("yarn.lock", 'vitest@^4:\n  resolved "x"\n'),
        ):
            with self.subTest(lockfile=lockfile):
                self.assertEqual(NONE, cj.locked(lockfile, lock, "vitest"))

    def test_a_cargo_lock_of_the_wrong_shape_is_refused(self) -> None:
        """Not TOML, or a package table of another shape, raises by where."""
        for lock in ("[[package]\n", "package = 1\n", '[[package]]\nname = "a"\n'):
            with (
                self.subTest(lock=lock),
                self.assertRaises(decoded.UndecodableError),
            ):
                cj.locked("Cargo.lock", lock, "a")


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
        self.assertTrue(err.startswith(f"{TASK}: package.json: "), err)

    def test_a_package_json_that_is_not_an_object_names_no_runner(self) -> None:
        """A JSON array declares nothing."""
        self.assertEqual(
            (0, "", ""), self.main(["package.json"], {"package.json": "[]"})
        )

    def test_vitest_declared_without_a_lockfile_entry_is_named(self) -> None:
        """Declared but not pinned: nothing the leg may run."""
        declared: dict[str, object] = {"devDependencies": {"vitest": "4.1.12"}}
        manifest = json.dumps(declared)
        self.assertEqual(
            (0, "unsupported vitest-without-a-lockfile-entry\n", ""),
            self.main(["package.json"], {"package.json": manifest}),
        )

    def test_another_runner_is_named(self) -> None:
        """A jest tree is named so the skip says what it skipped."""
        declared: dict[str, object] = {"devDependencies": {"jest": "29"}}
        manifest = json.dumps(declared)
        self.assertEqual(
            (0, "unsupported jest\n", ""),
            self.main(["package.json"], {"package.json": manifest}),
        )


FLAGS = [
    "--coverage.enabled=true",
    "--coverage.provider=v8",
    "--coverage.reporter=json-summary",
    "--coverage.reporter=lcov",
    "--coverage.reportsDirectory=/run/typescript",
]


class MeasuredRun(unittest.TestCase):
    """`--measured-run`: the vitest run on `measured`'s line (#2105)."""

    def call(
        self, *command: str, files: dict[str, str] | None = None
    ) -> tuple[int, str, str]:
        """Call main in a scratch tree of a pnpm vitest tree with v8.

        Returns:
            The status, stdout and stderr.

        """
        lock = (
            "lockfileVersion: '9.0'\npackages:\n"
            "  vitest@4.1.12:\n  '@vitest/coverage-v8@4.1.12':\n"
        )
        planted = {"package.json": "{}", "pnpm-lock.yaml": lock} | (files or {})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, text in planted.items():
            (Path(tmp.name) / name).write_text(text, encoding="utf-8")
        argv = ["--measured-run", "/run/typescript", *planted, "--", *command]
        done = hermetic.call_main(lambda: cj.main(argv), cwd=tmp.name)
        return done.returncode, done.stdout, done.stderr

    def test_a_launched_run_gains_the_flags(self) -> None:
        """`pnpm exec vitest run x`: the five flags, NUL-separated."""
        status, out, err = self.call("pnpm", "exec", "vitest", "run", "x")
        self.assertEqual((0, ""), (status, err))
        want = ["pnpm", "exec", "vitest", "run", "x", *FLAGS]
        self.assertEqual("".join(f"{w}\0" for w in want), out)

    def test_no_subcommand_is_run(self) -> None:
        """A bare vitest is its watch mode; `run` is inserted."""
        got = cj.measured_run(
            "v8", "/run/typescript", ["node_modules/.bin/vitest", "--silent"]
        )
        want: list[str] = ["node_modules/.bin/vitest", "run", "--silent", *FLAGS]
        self.assertEqual(want, got)

    def test_what_is_not_a_run_is_unchanged(self) -> None:
        """Another subcommand, another launcher, or no vitest: nothing."""
        for command in (
            ["vitest", "list"],
            ["bunx", "vitest", "run"],
            ["python3", "-m", "unittest"],
            [],
        ):
            with self.subTest(command=command):
                self.assertEqual((0, "", ""), self.call(*command))

    def test_a_tree_the_leg_reports_on_is_unchanged(self) -> None:
        """No provider pinned: the leg names it; measured adds nothing."""
        lock = "lockfileVersion: '9.0'\npackages:\n  vitest@4.1.12:\n"
        self.assertEqual(
            (0, "", ""), self.call("vitest", "run", files={"pnpm-lock.yaml": lock})
        )

    def test_a_coverage_flag_of_its_own_is_refused(self) -> None:
        """The leg owns the coverage flags: exit 1, named."""
        status, out, err = self.call("vitest", "run", "--coverage.enabled=false")
        self.assertEqual((1, ""), (status, out))
        self.assertTrue(
            err.startswith("measured: --coverage.enabled=false is on the vitest run"),
            err,
        )

    def test_no_separator_is_a_usage_error(self) -> None:
        """The command follows `--`; without it nothing is read."""
        done = hermetic.call_main(lambda: cj.main(["--measured-run", "/d"]))
        self.assertEqual(2, done.returncode)
        self.assertIn("usage", done.stderr)


class Script(unittest.TestCase):
    """The one row that starts the helper as a process: the script contract."""

    def test_the_helper_runs_as_a_script(self) -> None:
        """No tracked package.json: it starts, prints nothing and exits 0."""
        with tempfile.TemporaryDirectory() as tmp:
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
