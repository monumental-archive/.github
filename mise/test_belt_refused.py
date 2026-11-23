#!/usr/bin/env python3
"""Table tests for a belt that refuses to answer for another pin (#1988).

The belt's `[env]` sets ORG_BELT_REFUSED to one line when the belt
arrived by the clone's link (carrier 3) and is not the cache tree the
invoking tree's own gate stub pins; mise/findings.sh, which BASH_ENV
sources before every task body, prints it and exits 1. So a belt task
refuses in a tree whose link names another pin's belt, and in a nested
worktree with no link of its own whose parent's belt answers.

Each row plants a consumer and the cache trees of two shas, A and B,
each a whole belt (hermetic.belt) whose config.toml is a stub holding
the belt's REAL `ORG_BELT_DIR`, `ORG_BELT_REFUSED` and `BASH_ENV`
lines and its `[task_config]` shell, read out of mise/config.toml, plus
one task. A real mise renders the templates, as it does for every task
(`{{ cwd }}` is mise's own, so nothing else renders it truly); the
value it renders is then handed to the task body as mise hands it,
under hermetic.task_run, and one row runs the task through mise itself.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic

CANON = Path(__file__).resolve().parent.parent
A = "a" * 40
B = "b" * 40
TASK = "lint:probe"
BODY = "echo ran"
OWNER_REPO = "example-org/.github"
REMEDY = "Run: lefthook run post-checkout"


def belt_lines() -> str:
    """Read the belt's own lines this stub carries, verbatim.

    Returns:
        A TOML document: the three `[env]` lines and the task shell.

    Raises:
        AssertionError: a line is not one line in mise/config.toml.

    """
    text = (CANON / "mise" / "config.toml").read_text("utf-8")
    lines = text.splitlines()
    found: dict[str, str] = {}
    for key in ("ORG_BELT_DIR", "ORG_BELT_REFUSED", "BASH_ENV", "shell"):
        hits = [ln for ln in lines if ln.startswith(f"{key} = ")]
        if len(hits) != 1:
            msg = f"mise/config.toml holds {len(hits)} `{key} = ` lines, not one"
            raise AssertionError(msg)
        found[key] = hits[0]
    # The lines must say what the parsed belt says, so a line split over
    # several would be caught here rather than planted half.
    belt = decoded.load_toml(text)
    env = decoded.as_object(belt["env"], "the belt's [env]")
    for key in ("ORG_BELT_DIR", "ORG_BELT_REFUSED", "BASH_ENV"):
        stub = decoded.load_toml(found[key])
        if stub[key] != env[key]:
            msg = f"the `{key} = ` line is not the whole value"
            raise AssertionError(msg)
    return (
        "[env]\n"
        f"{found['ORG_BELT_DIR']}\n"
        f"{found['ORG_BELT_REFUSED']}\n"
        f"{found['BASH_ENV']}\n"
        "[task_config]\n"
        f"{found['shell']}\n"
        f'[tasks."{TASK}"]\n'
        f"run = '{BODY}'\n"
    )


def run(
    argv: list[str], cwd: Path, env: dict[str, str], *, check: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run one command, its output captured as text, within two minutes.

    Returns:
        The run.

    """
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=check,
        timeout=120,
    )


def refusal(answering: str, pinned: str) -> str:
    """Name the one line a refused task prints.

    Returns:
        The line, with its newline.

    """
    return f"the belt answering is {answering}; this tree pins {pinned}. {REMEDY}\n"


def stub(tree: Path, sha: str, name: str = "gate.yml") -> None:
    """Write a workflow in `tree` pinning the canon at `sha`, untracked."""
    wf = tree / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / name).write_text(
        "jobs:\n  ci:\n"
        f"    uses: {OWNER_REPO}/.github/workflows/ci.yml@{sha} # v1.0.0\n",
        encoding="utf-8",
    )


class Planted(unittest.TestCase):
    """The cache trees of A and B, and a consumer with no pin yet."""

    @override
    def setUp(self) -> None:
        """Plant both cache trees and an empty consumer repository."""
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        stub = belt_lines()
        self.trees = {}
        for sha in (A, B):
            tree = self.tmp / "cache" / "org-belt" / sha
            hermetic.belt(tree / "mise", stubs={"config.toml": stub})
            self.trees[sha] = tree
        self.repo = self.tmp / "consumer"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q", "-b", "main", ".")

    def env(self, **extra: str) -> dict[str, str]:
        """Build the row's environment, every mise directory its own.

        Returns:
            The environment.

        """
        return hermetic.env(
            self.home,
            MISE_CONFIG_DIR=str(self.tmp / "mise-config"),
            MISE_DATA_DIR=str(self.tmp / "mise-data"),
            MISE_CACHE_DIR=str(self.tmp / "mise-cache"),
            MISE_STATE_DIR=str(self.tmp / "mise-state"),
            MISE_TRUSTED_CONFIG_PATHS=str(self.tmp),
            MISE_OFFLINE="1",
            MISE_YES="1",
            MISE_AUTO_INSTALL="0",
            MISE_TASK_RUN_AUTO_INSTALL="0",
            **extra,
        )

    def git(self, cwd: Path, *args: str) -> None:
        """Run git in `cwd`, checked."""
        run([hermetic.tool("git"), *args], cwd, self.env(), check=True)

    def pin(self, tree: Path, sha: str) -> None:
        """Write `tree`'s gate stub pinning `sha`, and track it.

        The pin is read from tracked workflows only (#2018), so a stub
        is staged as a commit would have it.
        """
        stub(tree, sha)
        self.git(tree, "add", ".github/workflows/gate.yml")

    def link(self, tree: Path, sha: str) -> None:
        """Point `tree`'s belt link into the cache tree of `sha`."""
        conf = tree / ".config" / "mise" / "conf.d"
        conf.mkdir(parents=True, exist_ok=True)
        (conf / "belt.toml").symlink_to(self.trees[sha] / "mise" / "config.toml")

    def mise(
        self, where: Path, *args: str, **extra: str
    ) -> subprocess.CompletedProcess[str]:
        """Run the real mise in `where`.

        Returns:
            The run.

        """
        return run([hermetic.tool("mise"), *args], where, self.env(**extra))

    def rendered(self, where: Path, **extra: str) -> dict[str, str]:
        """Ask mise for the environment it renders in `where`.

        Returns:
            `mise env --json`, decoded.

        """
        done = self.mise(where, "env", "--json", **extra)
        self.assertEqual(done.returncode, 0, done.stderr)
        loaded = decoded.as_object(decoded.load_json(done.stdout), "mise env")
        return {k: decoded.as_str(v, k) for k, v in loaded.items()}

    def task(self, where: Path, **extra: str) -> subprocess.CompletedProcess[str]:
        """Run the probe's body as mise runs a task body, in `where`.

        ORG_BELT_REFUSED and BASH_ENV are what mise rendered there, so
        the body meets the refusal exactly as a belt task does.

        Returns:
            The run.

        """
        rendered = self.rendered(where, **extra)
        as_mise = hermetic.task_run(TASK, BODY)
        given = {k: rendered[k] for k in ("ORG_BELT_REFUSED", "BASH_ENV")}
        return run(as_mise.argv, where, self.env(**(extra | as_mise.env | given)))

    def assert_refused(self, where: Path, answering: str, pinned: str) -> None:
        """Assert the task refuses in `where`, naming both shas, and runs nothing."""
        done = self.task(where)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual((done.stdout, done.stderr), ("", refusal(answering, pinned)))

    def assert_runs(self, where: Path, **extra: str) -> None:
        """Assert the task runs in `where`, with nothing refused."""
        self.assertEqual(self.rendered(where, **extra)["ORG_BELT_REFUSED"], "")
        done = self.task(where, **extra)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(done.stdout, "ran\n")


class TheBeltAnswersOnlyForItsPin(Planted):
    """Each row: the tree's pin, the belt that answers, and the verdict."""

    def test_a_link_to_another_pin_refuses(self) -> None:
        """Pin B, link A: exit 1, one line naming A, B and the remedy."""
        self.pin(self.repo, B)
        self.link(self.repo, A)
        self.assert_refused(self.repo, A, B)

    def test_a_link_to_the_pin_runs(self) -> None:
        """Pin B, link B: the task runs."""
        self.pin(self.repo, B)
        self.link(self.repo, B)
        self.assert_runs(self.repo)

    def test_the_refusal_holds_from_a_subdirectory(self) -> None:
        """Invoked below the top: the tree is still the one judged."""
        self.pin(self.repo, B)
        self.link(self.repo, A)
        below = self.repo / "src" / "it's here"
        below.mkdir(parents=True)
        self.assert_refused(below, A, B)

    def test_mise_run_itself_refuses(self) -> None:
        """Through `mise run`: the task's body never runs."""
        self.pin(self.repo, B)
        self.link(self.repo, A)
        done = self.mise(self.repo, "run", TASK)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertNotIn("ran", done.stdout)
        self.assertIn(refusal(A, B), done.stderr)

    def test_a_nested_worktree_with_no_link_refuses(self) -> None:
        """Parent pinned and linked to A; a worktree inside it pins B, unlinked."""
        self.pin(self.repo, A)
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "-m", "pin A")
        self.link(self.repo, A)
        nested = self.repo / ".claude" / "worktrees" / "lane"
        self.git(self.repo, "worktree", "add", "-q", "-b", "lane", str(nested))
        self.pin(nested, B)
        self.assertFalse((nested / ".config").exists())
        self.assert_refused(nested, A, B)

    def test_a_nested_worktree_on_its_parents_pin_runs(self) -> None:
        """The same nesting, the same pin: the belt answering is the pinned."""
        self.pin(self.repo, A)
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "-m", "pin A")
        self.link(self.repo, A)
        nested = self.repo / ".claude" / "worktrees" / "lane"
        self.git(self.repo, "worktree", "add", "-q", "-b", "lane", str(nested))
        self.assert_runs(nested)

    def test_a_belt_named_by_the_global_carrier_runs(self) -> None:
        """Carrier 1, MISE_GLOBAL_CONFIG_FILE, named on purpose: runs."""
        self.pin(self.repo, B)
        named = str(self.trees[A] / "mise" / "config.toml")
        self.assert_runs(self.repo, MISE_GLOBAL_CONFIG_FILE=named)

    def test_the_canons_own_tree_runs(self) -> None:
        """Carrier 2: the tree is its own belt, whatever stub or link it holds."""
        hermetic.belt(self.repo / "mise", stubs={"config.toml": belt_lines()})
        self.pin(self.repo, B)
        self.link(self.repo, A)
        self.assert_runs(self.repo)

    def test_an_untracked_stray_does_not_choose_the_pin(self) -> None:
        """Tracked stub pins B, linked B; an untracked stray pinning A sorts first.

        Fails before #2018: the glob read the stray, so the right belt
        was refused.
        """
        self.pin(self.repo, B)
        stub(self.repo, A, "a-stray.yml")
        self.link(self.repo, B)
        self.assert_runs(self.repo)

    def test_an_untracked_stray_does_not_excuse_a_wrong_link(self) -> None:
        """Tracked stub pins B, linked A; a stray pinning A: still refused."""
        self.pin(self.repo, B)
        stub(self.repo, A, "a-stray.yml")
        self.link(self.repo, A)
        self.assert_refused(self.repo, A, B)

    def test_a_tree_with_no_pin_runs(self) -> None:
        """No gate stub: nothing to compare, nothing refused."""
        self.link(self.repo, A)
        self.assert_runs(self.repo)


class OnlyTasksRefuse(Planted):
    """A refused tree: mise's own commands still work, so the remedy can run."""

    @override
    def setUp(self) -> None:
        """Plant the refused tree: pin B, link A."""
        super().setUp()
        self.pin(self.repo, B)
        self.link(self.repo, A)

    def test_mise_env_exits_0(self) -> None:
        """`mise env` renders the refusal and exits 0."""
        self.assertEqual(
            self.rendered(self.repo)["ORG_BELT_REFUSED"], refusal(A, B).rstrip("\n")
        )

    def test_mise_install_exits_0(self) -> None:
        """`mise install` runs no task body, and exits 0."""
        done = self.mise(self.repo, "install")
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
