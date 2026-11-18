#!/usr/bin/env python3
"""Tests for hermetic.py and the law it holds the suite to (#1514).

The helper: a variable the suite's process carries reaches a fixture
only if INHERITED or a named tool extension names it; a row's override
always does. The law: no test file in the four discovery roots builds
a fixture's environment by copying the caller's.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import os
import re
import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
hermetic = SimpleNamespace(**runpy.run_path(str(HERE / "hermetic.py")))

CANON = HERE.parent
GIT = shutil.which("git") or "git"
ROOTS = ("mise", "release", ".github/rekor-monitor", "settings")

# The release job's step inputs, the runner's ambient, and the
# provisioning the gate exports: each has reached, or could reach, a
# script under test by inheritance (#1508).
PLANTED = {
    "VERSION": "1.69.4",
    "PLAN": "{}",
    "APP_SLUG": "canon-release",
    "GITHUB_REF": "refs/heads/main",
    "RUNNER_TEMP": "/runner-temp",
    "ORG_PGRX_MAJORS": "17,18",
    "GIT_DIR": "/nowhere/.git",
    "GIT_INDEX_FILE": "/nowhere/.git/index",
}

# The five ways a test file copies the caller's environment. `dict(` is
# anchored so `mock.patch.dict(os.environ, env, clear=True)` is not
# counted: that REPLACES the process environment with `env`, it does not
# copy the caller's into a fixture.
COPIES = re.compile(
    r"os\.environ\.items\(\)|\{\*\*os\.environ|(?<![\w.])dict\(os\.environ"
    r"|os\.environ\.copy\(\)|os\.environ \|"
)

# Excepted by name: the helper itself, and this file, which plants and
# names the spellings it looks for.
EXCEPTED = frozenset({"mise/hermetic.py", "mise/test_hermetic.py"})


class Env(unittest.TestCase):
    """What hermetic.env lets through."""

    def test_planted_variables_do_not_reach_a_fixture(self) -> None:
        """The job's inputs and git's own variables stay with the suite."""
        with mock.patch.dict(os.environ, PLANTED):
            env = hermetic.env("/fixture-home")
        self.assertEqual([], sorted(set(PLANTED) & set(env)))

    def test_only_the_allow_list_is_inherited(self) -> None:
        """Every inherited name is INHERITED's, or one the helper sets."""
        set_here = {
            "HOME",
            "GIT_EDITOR",
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
        }
        with mock.patch.dict(os.environ, PLANTED):
            env = hermetic.env()
        self.assertEqual(set(), set(env) - set(hermetic.INHERITED) - set_here)

    def test_a_rows_override_reaches_the_fixture(self) -> None:
        """A row states its own input, and that wins over the suite's."""
        with mock.patch.dict(os.environ, PLANTED):
            env = hermetic.env("/fixture-home", VERSION="2.0.0")
        self.assertEqual("2.0.0", env["VERSION"])

    def test_home_is_the_fixtures_or_the_suites(self) -> None:
        """A given home wins; without one the suite's HOME is kept."""
        with mock.patch.dict(os.environ, {"HOME": "/suite-home"}):
            self.assertEqual("/fixture-home", hermetic.env("/fixture-home")["HOME"])
            self.assertEqual("/suite-home", hermetic.env()["HOME"])

    def test_each_tool_adds_exactly_its_names(self) -> None:
        """A tool extension adds its variables and nothing else."""
        every = {n for names in hermetic.TOOLS.values() for n in names}
        planted = dict.fromkeys(every | set(hermetic.INHERITED), "x")
        with mock.patch.dict(os.environ, planted):
            base = set(hermetic.env())
            for tool, names in hermetic.TOOLS.items():
                with self.subTest(tool=tool):
                    self.assertEqual(
                        set(names) - base, set(hermetic.env(tools=(tool,))) - base
                    )

    def test_an_unknown_tool_is_refused(self) -> None:
        """A tool nobody measured has no extension to borrow."""
        with self.assertRaises(KeyError):
            hermetic.env(tools=("no-such-tool",))


class TheLaw(unittest.TestCase):
    """No test file copies the caller's environment (#1514 item 3)."""

    def test_no_test_file_copies_the_callers_environment(self) -> None:
        """Every fixture environment is built through hermetic.env."""
        # ruff: ignore[subprocess-without-shell-equals-true]
        listed = subprocess.run(
            [
                GIT,
                "-C",
                str(CANON),
                "ls-files",
                "--",
                *(f"{r}/test_*.py" for r in ROOTS),
                *(f"{r}/prop_*.py" for r in ROOTS),
            ],
            capture_output=True,
            text=True,
            check=True,
            env=hermetic.env(),
        ).stdout.split()
        self.assertTrue(listed, "git listed no test files")
        offenders = [
            path
            for path in listed
            if path not in EXCEPTED
            and COPIES.search((CANON / path).read_text(encoding="utf-8"))
        ]
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
