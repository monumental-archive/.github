#!/usr/bin/env python3
"""Table tests for lint:belt-link (#1686).

The org hooks put a clone's belt link and its lock under `.config/mise/`,
and mise reads one lock per config directory, so the belt claims that
directory: a tracked file there is red by name.

The body under test is the task's own `run` string, read out of
mise/config.toml with tomllib and run by bash in a planted git tree, not
a copy of it. Red rows assert the whole message.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

from __future__ import annotations

import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import tomllib
import unittest
from pathlib import Path

import hermetic

CANON = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
GIT = shutil.which("git")
NAME = "lint:belt-link"
CLAIM = (
    "lint:belt-link: the belt's link and lock live in .config/mise/; a repository's\n"
    "  own config is mise.toml at its root. Tracked there:\n"
)


def run(
    argv: list[str], cwd: Path, env: dict[str, str], *, check: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run one command, its output captured as text, within a minute.

    Returns:
        The run.

    """
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=check,
        timeout=60,
    )


def tool(found: str | None, name: str) -> str:
    """Return a tool's path, or fail by name.

    Returns:
        The path.

    Raises:
        RuntimeError: the tool is not on PATH.

    """
    if found is None:
        msg = f"{name} is not on PATH"
        raise RuntimeError(msg)
    return found


def body() -> str:
    """Read the lint's run string from the belt.

    Returns:
        The task body.

    """
    belt = tomllib.loads((CANON / "mise" / "config.toml").read_text("utf-8"))
    return belt["tasks"][NAME]["run"]


class TheBeltClaimsItsDirectory(unittest.TestCase):
    """Each row: what the repository tracks under .config/mise/."""

    def setUp(self) -> None:
        """Plant an empty repository."""
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = hermetic.env(self.tmp)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q", ".")
        (self.repo / "mise.toml").write_text("[tools]\n", encoding="utf-8")

    def git(self, *args: str) -> None:
        """Run git in the repository, checked."""
        run([tool(GIT, "git"), *args], self.repo, self.env, check=True)

    def track(self, rel: str) -> None:
        """Write and stage a file at `rel`."""
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[tools]\n", encoding="utf-8")
        self.git("add", "--", rel)

    def lint(self) -> subprocess.CompletedProcess[str]:
        """Run the lint's body in the repository.

        Returns:
            The run.

        """
        argv = [tool(BASH, "bash"), "-euo", "pipefail", "-c", body()]
        return run(argv, self.repo, self.env)

    def test_a_tracked_config_toml_is_red(self) -> None:
        """A repository's own `.config/mise/config.toml`: red by name."""
        self.track(".config/mise/config.toml")
        done = self.lint()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(done.stderr, CLAIM + "  .config/mise/config.toml\n")

    def test_a_tracked_conf_d_file_is_red(self) -> None:
        """A tracked conf.d file, beside where the belt link goes: red."""
        self.track(".config/mise/conf.d/x.toml")
        self.track("mise.toml")
        done = self.lint()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(done.stderr, CLAIM + "  .config/mise/conf.d/x.toml\n")

    def test_nothing_tracked_there_is_green(self) -> None:
        """The untracked links the hooks write are not the repository's."""
        self.track("mise.toml")
        conf = self.repo / ".config" / "mise" / "conf.d"
        conf.mkdir(parents=True)
        (conf / "belt.toml").symlink_to(self.tmp / "belt.toml")
        (conf.parent / "mise.lock").symlink_to(self.tmp / "mise.lock")
        done = self.lint()
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(
            done.stdout, "lint:belt-link: nothing tracked under .config/mise/\n"
        )


if __name__ == "__main__":
    unittest.main()
