#!/usr/bin/env python3
"""Run `mise/rename_helpers.py` against a scratch tree (#1634).

The tree holds one hyphenated helper, referenced from a task body, a
workflow and a test, plus a task name and a longer file name that share
its stem and must not move. One run renames the helper and rewrites the
three references; a second run changes nothing.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`. Every git call passes a scrubbed environment.
"""

import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "rename_helpers.py"
GIT = shutil.which("git") or "git"
hermetic = SimpleNamespace(**runpy.run_path(str(HERE / "hermetic.py")))

TASKS = """\
[tasks."lint:caller-pins"]
run = '"${ORG_PYTHON}" "${ORG_BELT_DIR}/caller-pins.py" --check'
"""
WORKFLOW = """\
jobs:
  caller-pins:
    steps:
      - run: python3 .org-canon/release/caller-pins.py
      - run: python3 .org-canon/release/old-caller-pins.py
"""
TEST = """\
HELPER = Path(__file__).with_name("caller-pins.py")
CALLS = {r"caller-pins\\.py": ["jq"]}
"""


class RenameHelpers(unittest.TestCase):
    """One run renames and rewrites; a second run changes nothing."""

    def setUp(self) -> None:
        """Plant the scratch tree and commit it."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tree = root / "repo"
        self.env = hermetic.env(root / "home")
        files = {
            "release/caller-pins.py": "print('pins')\n",
            "release/old-caller-pins.py": "print('old')\n",
            "mise/config.toml": TASKS,
            ".github/workflows/ci.yml": WORKFLOW,
            "release/test_caller_pins.py": TEST,
        }
        for name, text in files.items():
            path = self.tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "plant")

    def git(self, *args: str) -> str:
        """Run git in the tree.

        Returns:
            Its standard output.

        """
        # ruff: ignore[subprocess-without-shell-equals-true]
        done = subprocess.run(
            [GIT, *args],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout

    def rename(self) -> str:
        """Run the script in the tree.

        Returns:
            Its standard output.

        """
        # ruff: ignore[subprocess-without-shell-equals-true]
        done = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, done.returncode, done.stderr)
        return done.stdout

    def read(self, name: str) -> str:
        """Read a file of the tree.

        Returns:
            Its text.

        """
        return (self.tree / name).read_text(encoding="utf-8")

    def test_one_run_renames_and_rewrites_every_reference(self) -> None:
        """The helpers move and the task, workflow and test follow."""
        out = self.rename()
        self.assertEqual("rename_helpers: renamed 2 files, rewrote 3 files\n", out)
        self.assertEqual(
            sorted(self.git("ls-files").split()),
            sorted([
                ".github/workflows/ci.yml",
                "mise/config.toml",
                "release/caller_pins.py",
                "release/old_caller_pins.py",
                "release/test_caller_pins.py",
            ]),
        )
        self.assertEqual(
            TASKS.replace("/caller-pins.py", "/caller_pins.py"),
            self.read("mise/config.toml"),
        )
        self.assertEqual(
            WORKFLOW.replace("/caller-pins.py", "/caller_pins.py").replace(
                "/old-caller-pins.py", "/old_caller_pins.py"
            ),
            self.read(".github/workflows/ci.yml"),
        )
        self.assertEqual(
            TEST.replace('"caller-pins.py"', '"caller_pins.py"').replace(
                r'r"caller-pins\.py"', r'r"caller_pins\.py"'
            ),
            self.read("release/test_caller_pins.py"),
        )
        # The task and job names share the stem and are not file names.
        self.assertIn('[tasks."lint:caller-pins"]', self.read("mise/config.toml"))
        self.assertIn("  caller-pins:\n", self.read(".github/workflows/ci.yml"))

    def test_a_second_run_changes_nothing(self) -> None:
        """Run twice; the second finds nothing and the tree is unchanged."""
        self.rename()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "renamed")
        out = self.rename()
        self.assertEqual("rename_helpers: renamed 0 files, rewrote 0 files\n", out)
        self.assertEqual("", self.git("status", "--porcelain"))

    def test_a_taken_name_is_refused_by_name(self) -> None:
        """A module name already tracked stops the run before any move."""
        (self.tree / "release/caller_pins.py").write_text("x\n", encoding="utf-8")
        self.git("add", ".")
        # ruff: ignore[subprocess-without-shell-equals-true]
        done = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            "rename_helpers: release/caller-pins.py cannot become "
            "release/caller_pins.py: it exists\n",
            done.stderr,
        )
        self.assertIn("release/caller-pins.py", self.git("ls-files"))


if __name__ == "__main__":
    unittest.main()
