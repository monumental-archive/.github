#!/usr/bin/env python3
"""Planted rows for the delivered ruff config's subprocess rules (#1838).

The config turns off S404 and S603: both are bandit's audit prompts, and
neither can be passed by code that hands a process an argument it was
given, so each was a suppression comment at every site. What stays on
is what code CAN pass: `shell=True` and a partial executable path.

Each row runs the pinned ruff on a planted file with the DELIVERED
config, and a red row asserts the rule's NAME in the output, not only
the exit status: a file can be red for a reason that is not the row's,
and then the row would pass for the wrong one.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

import hermetic

CONFIG = Path(__file__).parent / "ruff.toml"
RUFF = hermetic.tool("ruff")

# A helper the config must leave alone: it imports subprocess and
# passes a module constant and an argument it was given, the shape
# S404 and S603 flagged at every site and no code can avoid.
CLEAN = '''"""A planted helper."""

import subprocess

TOOL = "/usr/bin/git"


def run(path: str) -> None:
    """Run the tool on a path it was given."""
    subprocess.run([TOOL, str(path)], check=True)
'''

SHELL = '''"""A planted helper."""

import subprocess


def run(cmd: str) -> None:
    """Run a command line through a shell."""
    subprocess.run(cmd, shell=True, check=True)
'''

PARTIAL = '''"""A planted helper."""

import subprocess


def run() -> None:
    """Run a tool found by name on PATH."""
    subprocess.run(["git", "status"], check=True)
'''


def check(source: str) -> subprocess.CompletedProcess[str]:
    """Run ruff with the delivered config on one planted file.

    Returns:
        The finished ruff process, both streams captured.

    """
    with tempfile.TemporaryDirectory() as tmp:
        # A package, or the clean row is red on implicit-namespace-package.
        (Path(tmp) / "__init__.py").write_text("", encoding="utf-8")
        planted = Path(tmp) / "helper.py"
        planted.write_text(source, encoding="utf-8")
        return subprocess.run(
            [RUFF, "check", "--config", str(CONFIG), "--no-cache", str(planted)],
            cwd=tmp,
            env=hermetic.env(),
            capture_output=True,
            text=True,
            check=False,
        )


class SubprocessRules(unittest.TestCase):
    """The rules code can pass stay on; the two it cannot are off."""

    def test_shell_true_is_red_by_name(self) -> None:
        """S602: a command line through a shell is a finding."""
        done = check(SHELL)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("subprocess-popen-with-shell-equals-true", done.stdout)

    def test_a_partial_path_is_red_by_name(self) -> None:
        """S607: an executable found by name on PATH is a finding."""
        done = check(PARTIAL)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("start-process-with-partial-path", done.stdout)

    def test_a_given_argument_needs_no_comment(self) -> None:
        """S404 and S603 are off: the import and the call are clean."""
        done = check(CLEAN)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("suspicious-subprocess-import", done.stdout)
        self.assertNotIn("subprocess-without-shell-equals-true", done.stdout)


if __name__ == "__main__":
    unittest.main()
