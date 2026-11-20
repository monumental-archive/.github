#!/usr/bin/env python3
"""Table tests for where ORG_BELT_DIR finds the belt (#1686, #1739).

The belt's `[env]` resolves ORG_BELT_DIR with one shell command inside
a Tera `exec`, from three carriers, first found wins:
MISE_GLOBAL_CONFIG_FILE (CI); the working tree's own mise/config.toml
with clippy.toml beside it (the canon); the working tree's
`.config/mise/conf.d/belt.toml` link (a consumer's pinned belt). Each
row plants the carriers it names and asserts the one that answers.

Every row also plants a machine belt, a belt linked from the machine's
own mise conf.d, and it never answers (#1739): it was the fourth
source until then, and a clone with no link was judged by it, an
unpinned belt (the fail-before row: on 64b2f99e it answered).

The command under test is the template's own, read out of
mise/config.toml through decoded.py and run by `sh -o errexit` as mise runs
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
import unittest
from pathlib import Path

import decoded
import hermetic

CANON = Path(__file__).resolve().parent.parent


def template_command() -> str:
    """Read the ORG_BELT_DIR template's shell command from the belt.

    Returns:
        The command between `exec(command=` and its closing backtick.

    Raises:
        AssertionError: the template is not an exec of one command.

    """
    belt = decoded.load_toml((CANON / "mise" / "config.toml").read_text("utf-8"))
    env = decoded.as_object(belt["env"], "the belt's [env]")
    template = decoded.as_str(env["ORG_BELT_DIR"], "ORG_BELT_DIR")
    found = re.fullmatch(r"\{\{ exec\(command=`(.*)`\) \}\}", template)
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


class Planted(unittest.TestCase):
    """A repository and one tree for each carrier; the rows are below."""

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
            [hermetic.tool("git"), "init", "-q", "."],
            self.repo,
            hermetic.env(self.home),
            check=True,
        )
        self.ci = plant_belt(self.tmp / "ci")
        self.cache = plant_belt(self.tmp / "cache" / "org-belt" / ("a" * 40))
        self.machine = plant_belt(self.tmp / "checkout")
        self.global_file: str | None = None
        self.git_vars: dict[str, str] = {}

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
        """Plant a machine belt: a link from the machine's own conf.d."""
        conf = self.xdg / "mise" / "conf.d"
        conf.mkdir(parents=True)
        (conf / "toolbelt.toml").symlink_to(self.machine / "config.toml")

    def resolve(
        self, where: Path, path: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the template's command in `where`, as mise does.

        mise runs an `[env]` exec in the directory of the config file that
        defines it, whatever the shell's or the task's working directory
        (measured on #1686, mise 2026.11.7): the link's own directory in a
        clone, `mise/` in the canon.

        Returns:
            The run.

        """
        extra = {"XDG_CONFIG_HOME": str(self.xdg)} | self.git_vars
        if self.global_file is not None:
            extra["MISE_GLOBAL_CONFIG_FILE"] = self.global_file
        if path is not None:
            extra["PATH"] = path
        argv = [hermetic.tool("sh"), "-o", "errexit", "-c", template_command()]
        return run(argv, where, hermetic.env(self.home, **extra))

    def link_dir(self) -> Path:
        """Name the directory mise runs the linked belt's exec in.

        Returns:
            The clone's `.config/mise/conf.d`.

        """
        return self.repo / ".config" / "mise" / "conf.d"

    def assert_answers(self, belt: Path | None, where: Path) -> None:
        """Assert the command, run in `where`, prints `belt` or nothing."""
        done = self.resolve(where)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, f"{belt}\n" if belt else "")


class WhereTheBeltIs(Planted):
    """Each row: the carriers planted, and which one answers."""

    def test_ci_carrier_wins_over_all_three(self) -> None:
        """1. MISE_GLOBAL_CONFIG_FILE, with every other belt present."""
        self.own_tree()
        self.link(str(self.cache / "config.toml"))
        self.machine_link()
        self.global_file = str(self.ci / "config.toml")
        self.assert_answers(self.ci, self.link_dir())

    def test_the_tree_itself_wins_over_link_and_machine(self) -> None:
        """2. The canon's own tree, with a link and a machine belt present."""
        own = self.own_tree()
        self.link(str(self.cache / "config.toml"))
        self.machine_link()
        self.assert_answers(own, own)

    def test_the_link_answers_with_a_machine_belt_present(self) -> None:
        """3. A consumer's link to its pin's cache tree, machine belt present."""
        self.link(str(self.cache / "config.toml"))
        self.machine_link()
        self.assert_answers(self.cache, self.link_dir())

    def test_a_machine_belt_is_not_followed(self) -> None:
        """No fourth source: an unlinked clone has no belt (#1739)."""
        self.machine_link()
        self.assert_answers(None, self.repo)

    def test_a_machine_belt_is_not_followed_from_its_own_conf_d(self) -> None:
        """Run where mise runs a machine conf.d exec: still nothing."""
        self.machine_link()
        self.assert_answers(None, self.xdg / "mise" / "conf.d")

    def test_a_relative_link_is_read_from_its_own_directory(self) -> None:
        """A link written relative resolves against conf.d, not the cwd."""
        self.link(
            os.path.relpath(
                self.cache / "config.toml", self.repo / ".config/mise/conf.d"
            )
        )
        self.assert_answers(self.cache, self.link_dir())

    def test_a_link_to_a_tree_that_is_no_belt_answers_nothing(self) -> None:
        """No clippy.toml beside the link's target: no belt, machine unread."""
        (self.cache / "clippy.toml").unlink()
        self.link(str(self.cache / "config.toml"))
        self.machine_link()
        self.assert_answers(None, self.link_dir())

    def test_no_carrier_is_empty_and_exit_zero(self) -> None:
        """Nothing anywhere: no output, exit 0 (#569)."""
        self.assert_answers(None, self.repo)

    def test_no_git_is_still_total(self) -> None:
        """Without git (Renovate's container): no belt, exit 0."""
        self.link(str(self.cache / "config.toml"))
        self.machine_link()
        bare = self.tmp / "bin"
        bare.mkdir()
        for name in ("dirname", "readlink", "cat"):
            found = hermetic.optional_tool(name)
            if found is not None:
                (bare / name).symlink_to(found)
        done = self.resolve(self.link_dir(), path=str(bare))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "")


class UnderAHook(Planted):
    """The same question with git's own variables exported (#1744).

    git hands a hook GIT_DIR, and in a linked worktree no GIT_WORK_TREE
    beside it; the answer must not depend on either.
    """

    def worktree(self) -> Path:
        """Add a linked worktree with its own belt link, as a lane has.

        Returns:
            The worktree's `.config/mise/conf.d`, where mise runs the exec.

        """
        env = hermetic.env(self.home)
        git = hermetic.tool("git")
        run(
            [git, "commit", "-q", "--allow-empty", "-m", "c"],
            self.repo,
            env,
            check=True,
        )
        tree = self.tmp / "lane"
        run([git, "worktree", "add", "-q", str(tree)], self.repo, env, check=True)
        conf = tree / ".config" / "mise" / "conf.d"
        conf.mkdir(parents=True)
        (conf / "belt.toml").symlink_to(self.cache / "config.toml")
        return conf

    def hook_git_dir(self, conf: Path) -> str:
        """Name the git directory a hook in that worktree is handed.

        Returns:
            The worktree's own git directory, absolute.

        """
        done = run(
            [hermetic.tool("git"), "rev-parse", "--absolute-git-dir"],
            conf,
            hermetic.env(self.home),
            check=True,
        )
        return done.stdout.strip()

    def test_a_hook_in_a_linked_worktree_reads_its_link(self) -> None:
        """GIT_DIR with no work tree, as git hands a hook there (#1744)."""
        conf = self.worktree()
        self.machine_link()
        self.git_vars = {"GIT_DIR": self.hook_git_dir(conf)}
        self.assert_answers(self.cache, conf)

    def test_a_hook_that_names_its_work_tree_reads_its_link(self) -> None:
        """GIT_DIR and GIT_WORK_TREE both exported: the same answer."""
        conf = self.worktree()
        self.machine_link()
        self.git_vars = {
            "GIT_DIR": self.hook_git_dir(conf),
            "GIT_WORK_TREE": str(conf.parents[2]),
        }
        self.assert_answers(self.cache, conf)

    def test_a_hook_with_its_own_index_reads_its_link(self) -> None:
        """GIT_INDEX_FILE beside GIT_DIR, as a commit hook is handed."""
        conf = self.worktree()
        self.machine_link()
        git_dir = self.hook_git_dir(conf)
        self.git_vars = {"GIT_DIR": git_dir, "GIT_INDEX_FILE": f"{git_dir}/index"}
        self.assert_answers(self.cache, conf)

    def test_a_foreign_git_dir_does_not_move_the_canon_tree(self) -> None:
        """2 under a hook: the canon's own tree still answers from mise/."""
        own = self.own_tree()
        self.machine_link()
        self.git_vars = {"GIT_DIR": str(self.repo / ".git")}
        self.assert_answers(own, own)


if __name__ == "__main__":
    unittest.main()
