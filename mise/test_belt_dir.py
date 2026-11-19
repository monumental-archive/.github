#!/usr/bin/env python3
"""Table tests for where ORG_BELT_DIR finds the belt (#1686).

The belt's `[env]` resolves ORG_BELT_DIR with one shell command inside
a Tera `exec`, from four carriers, first found wins:
MISE_GLOBAL_CONFIG_FILE (CI); the working tree's own mise/config.toml
with clippy.toml beside it (the canon); the working tree's
`.config/mise/conf.d/belt.toml` link (a consumer's pinned belt); the
machine's conf.d (a machine not yet moved). Each row plants all the
carriers it names and asserts the one that answers.

The command under test is the template's own, read out of
mise/config.toml with tomllib and run by `sh -o errexit` as mise runs
it, in a planted git tree. It must stay total: a template that exits
nonzero aborts every mise invocation of the belt (#569), so the rows
with no carrier, and with no git, assert exit 0 and no output.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import tomllib
import unittest
from pathlib import Path

import hermetic

CANON = Path(__file__).resolve().parent.parent
SH = shutil.which("sh")
GIT = shutil.which("git")


def template_command() -> str:
    """Read the ORG_BELT_DIR template's shell command from the belt.

    Returns:
        The command between `exec(command=` and its closing backtick.

    Raises:
        AssertionError: the template is not an exec of one command.

    """
    belt = tomllib.loads((CANON / "mise" / "config.toml").read_text("utf-8"))
    found = re.fullmatch(
        r"\{\{ exec\(command=`(.*)`\) \}\}", belt["env"]["ORG_BELT_DIR"]
    )
    if found is None:
        msg = "ORG_BELT_DIR is not `{{ exec(command=`...`) }}`"
        raise AssertionError(msg)
    return found.group(1)


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
        timeout=120,
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


def plant_belt(tree: Path) -> Path:
    """Make `tree` a canon tree: a belt config with clippy.toml beside it.

    Returns:
        The belt directory, `tree/mise`.

    """
    belt = tree / "mise"
    belt.mkdir(parents=True)
    (belt / "config.toml").write_text("[tools]\n", encoding="utf-8")
    (belt / "clippy.toml").write_text("", encoding="utf-8")
    return belt


class WhereTheBeltIs(unittest.TestCase):
    """Each row: the carriers planted, and which one answers."""

    def setUp(self) -> None:
        """Plant a repository and one tree for each carrier."""
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.xdg = self.tmp / "xdg"
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        run(
            [tool(GIT, "git"), "init", "-q", "."],
            self.repo,
            hermetic.env(self.home),
            check=True,
        )
        self.ci = plant_belt(self.tmp / "ci")
        self.cache = plant_belt(self.tmp / "cache" / "org-belt" / ("a" * 40))
        self.machine = plant_belt(self.tmp / "checkout")
        self.global_file: str | None = None

    def own_tree(self) -> Path:
        """Make the repository itself a canon tree.

        Returns:
            Its belt directory.

        """
        return plant_belt(self.repo)

    def link(self, target: str) -> None:
        """Point the repository's belt link at `target`."""
        conf = self.repo / ".config" / "mise" / "conf.d"
        conf.mkdir(parents=True, exist_ok=True)
        (conf / "belt.toml").symlink_to(target)

    def machine_link(self) -> None:
        """Give the machine a conf.d symlink into its checkout."""
        conf = self.xdg / "mise" / "conf.d"
        conf.mkdir(parents=True)
        (conf / "toolbelt.toml").symlink_to(self.machine / "config.toml")

    def resolve(self, path: str | None = None) -> subprocess.CompletedProcess[str]:
        """Run the template's command in the repository, as mise does.

        Returns:
            The run.

        """
        extra = {"XDG_CONFIG_HOME": str(self.xdg)}
        if self.global_file is not None:
            extra["MISE_GLOBAL_CONFIG_FILE"] = self.global_file
        if path is not None:
            extra["PATH"] = path
        argv = [tool(SH, "sh"), "-o", "errexit", "-c", template_command()]
        return run(argv, self.repo, hermetic.env(self.home, **extra))

    def assert_answers(self, belt: Path | None) -> None:
        """Assert the command exits 0 printing `belt`, or nothing."""
        done = self.resolve()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, f"{belt}\n" if belt else "")

    def test_ci_carrier_wins_over_all_three(self) -> None:
        """1. MISE_GLOBAL_CONFIG_FILE, with every other carrier present."""
        self.own_tree()
        self.link(str(self.cache / "config.toml"))
        self.machine_link()
        self.global_file = str(self.ci / "config.toml")
        self.assert_answers(self.ci)

    def test_the_tree_itself_wins_over_link_and_machine(self) -> None:
        """2. The canon's own tree, with a link and a machine belt present."""
        own = self.own_tree()
        self.link(str(self.cache / "config.toml"))
        self.machine_link()
        self.assert_answers(own)

    def test_the_link_wins_over_the_machine(self) -> None:
        """3. A consumer's link to its pin's cache tree, machine belt present."""
        self.link(str(self.cache / "config.toml"))
        self.machine_link()
        self.assert_answers(self.cache)

    def test_the_machine_belt_answers_last(self) -> None:
        """4. Only the machine's conf.d: it still works."""
        self.machine_link()
        self.assert_answers(self.machine)

    def test_a_relative_link_is_read_from_its_own_directory(self) -> None:
        """A link written relative resolves against conf.d, not the cwd."""
        self.link(
            os.path.relpath(
                self.cache / "config.toml", self.repo / ".config/mise/conf.d"
            )
        )
        self.assert_answers(self.cache)

    def test_a_link_to_a_tree_that_is_no_belt_falls_through(self) -> None:
        """No clippy.toml beside the link's target: the machine answers."""
        (self.cache / "clippy.toml").unlink()
        self.link(str(self.cache / "config.toml"))
        self.machine_link()
        self.assert_answers(self.machine)

    def test_no_carrier_is_empty_and_exit_zero(self) -> None:
        """Nothing anywhere: no output, exit 0 (#569)."""
        self.assert_answers(None)

    def test_no_git_is_still_total(self) -> None:
        """Without git (Renovate's container): falls to the machine, exit 0."""
        self.link(str(self.cache / "config.toml"))
        self.machine_link()
        bare = self.tmp / "bin"
        bare.mkdir()
        for name in ("dirname", "readlink", "cat"):
            found = shutil.which(name)
            if found is not None:
                (bare / name).symlink_to(found)
        done = self.resolve(path=str(bare))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, f"{self.machine}\n")


if __name__ == "__main__":
    unittest.main()
