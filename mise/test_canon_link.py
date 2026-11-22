#!/usr/bin/env python3
"""The canon action's link script, every branch (#1386).

`.github/actions/canon/link.sh` is the canon action's one step: it links
the tree `$/.github/actions/canon` resolved from to `.org-canon` and
names the resolved ref. It finds that tree from its own location, so
each row plants a tree, copies the shipped script into the tree's
`.github/actions/canon/`, and runs it there under bash.

stdlib `unittest`, matching the other tests here.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from typing import override

import hermetic

CANON = "example-org/.github"
SHA = "0123456789abcdef0123456789abcdef01234567"


def plant(root: pathlib.Path, *, config: bool = True) -> pathlib.Path:
    """Plant a tree with the shipped script where the action keeps it.

    Returns:
        The script's path inside the planted tree.

    """
    hermetic.canon(
        root, ".github/actions/canon", stubs={"mise/config.toml": "[tools]\n"}
    )
    if not config:
        shutil.rmtree(root / "mise")
    return root / ".github" / "actions" / "canon" / "link.sh"


def excluded(gitdir: pathlib.Path) -> str:
    """Read the clone's info/exclude lines that name the link.

    Returns:
        Those lines, in order, one per line.

    """
    lines = (gitdir / "info" / "exclude").read_text(encoding="utf-8")
    return "\n".join(line for line in lines.splitlines() if "org-canon" in line)


class LinkTheCanonTree(unittest.TestCase):
    """Each row: where the tree resolved, and what the step did."""

    @override
    def setUp(self) -> None:
        """Give each row a scratch root and a workspace."""
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        self.output = self.tmp / "github-output"
        self.output.write_text("", encoding="utf-8")

    def link(
        self, script: pathlib.Path, repository: str, canon: str | None = CANON
    ) -> subprocess.CompletedProcess[str]:
        """Run the planted script as the action does.

        Returns:
            The run.

        """
        bash = hermetic.tool("bash")
        return subprocess.run(
            [bash, str(script)],
            env=hermetic.env(
                PATH=os.environ["PATH"],
                **{"CANON_REPOSITORY": canon} if canon is not None else {},
                GITHUB_REPOSITORY=repository,
                GITHUB_SHA=SHA,
                GITHUB_WORKSPACE=str(self.workspace),
                GITHUB_OUTPUT=str(self.output),
            ),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def linked(self) -> pathlib.Path | None:
        """Read where `.org-canon` points, if it was made.

        Returns:
            The link's target, or None.

        """
        link = self.workspace / ".org-canon"
        return link.readlink() if link.is_symlink() else None

    def test_an_actions_path_names_the_pinned_ref(self) -> None:
        """A caller's pin: the ref is the path segment under _actions."""
        root = self.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
        done = self.link(plant(root), "someone/consumer")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, f"canon resolved: v1.69.0 at {root}\n")
        self.assertEqual(self.output.read_text(encoding="utf-8"), "ref=v1.69.0\n")
        self.assertEqual(self.linked(), root)

    def test_the_canon_calling_itself_names_the_running_commit(self) -> None:
        """A local `$/` resolution in the canon is the running commit."""
        root = self.tmp / "work" / ".github"
        done = self.link(plant(root), CANON)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, f"canon resolved: {SHA} at {root}\n")
        self.assertEqual(self.output.read_text(encoding="utf-8"), f"ref={SHA}\n")
        self.assertEqual(self.linked(), root)

    def git(self, *args: str, cwd: pathlib.Path | None = None) -> str:
        """Run git as a row's caller would, in the workspace by default.

        Returns:
            Its standard output.

        """
        return subprocess.run(
            [hermetic.tool("git"), "-c", "commit.gpgsign=false", *args],
            cwd=cwd or self.workspace,
            env=hermetic.env(self.tmp / "home", PATH=os.environ["PATH"]),
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout

    def checkout(self, repo: pathlib.Path) -> None:
        """Make `repo` a clone with one commit, as actions/checkout leaves it."""
        repo.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", cwd=repo)
        (repo / "main.go").write_text("package main\n", encoding="utf-8")
        self.git("add", "main.go", cwd=repo)
        self.git("commit", "-q", "-m", "init", cwd=repo)

    def assert_clean(self, root: pathlib.Path) -> None:
        """Git reports nothing changed: go would stamp vcs.modified=false."""
        self.assertEqual(self.git("status", "--porcelain", cwd=root), "")

    def test_a_checkout_does_not_see_the_link_as_a_change(self) -> None:
        """The link is in the clone's info/exclude, so the tree is clean.

        #1944: the go-binary build unlinked `.org-canon` mid-job because an
        untracked path stamps every binary vcs.modified=true, and two later
        steps still sourced through the link that was gone.
        """
        self.checkout(self.workspace)
        root = self.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
        done = self.link(plant(root), "someone/consumer")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.linked(), root)
        self.assertEqual(excluded(self.workspace / ".git"), "/.org-canon")
        self.assert_clean(self.workspace)

    def test_a_second_link_writes_no_second_line(self) -> None:
        """The action can run twice in a job; the exclude holds one line."""
        self.checkout(self.workspace)
        root = self.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
        script = plant(root)
        for _ in range(2):
            done = self.link(script, "someone/consumer")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(excluded(self.workspace / ".git"), "/.org-canon")
        self.assert_clean(self.workspace)

    def test_a_workspace_inside_a_repository_is_anchored_where_it_sits(self) -> None:
        """A checkout one level up: the entry names the workspace's path."""
        clone = self.tmp / "clone"
        self.checkout(clone)
        self.workspace = clone / "sub"
        self.workspace.mkdir()
        root = self.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
        done = self.link(plant(root), "someone/consumer")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(excluded(clone / ".git"), "/sub/.org-canon")
        self.assert_clean(clone)

    def test_a_worktree_is_excluded_through_the_common_dir(self) -> None:
        """Git reads info/exclude from the common dir, not the worktree's."""
        clone = self.tmp / "clone"
        self.checkout(clone)
        self.git("worktree", "add", "-q", str(self.workspace / "wt"), cwd=clone)
        self.workspace /= "wt"
        root = self.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
        done = self.link(plant(root), "someone/consumer")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(excluded(clone / ".git"), "/.org-canon")
        self.assert_clean(self.workspace)

    def test_a_workspace_in_no_repository_is_linked_and_nothing_written(self) -> None:
        """No clone: the link is made and there is no exclude to write."""
        root = self.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
        done = self.link(plant(root), "someone/consumer")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.linked(), root)
        self.assertFalse((self.workspace / ".git").exists())

    def test_another_repository_off_an_actions_path_is_refused(self) -> None:
        """A shape the action does not understand is refused, not guessed."""
        root = self.tmp / "work" / "consumer"
        done = self.link(plant(root), "someone/consumer")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(
            done.stderr,
            f"::error::link.sh: canon action resolved to '{root}' in"
            " someone/consumer — not an _actions path and not a canon self-call\n",
        )
        self.assertEqual(self.output.read_text(encoding="utf-8"), "")
        self.assertIsNone(self.linked())

    def test_an_actions_path_of_another_repository_is_refused(self) -> None:
        """Only the canon's own _actions segment names a pinned ref."""
        root = self.tmp / "_actions" / "someone" / "fork" / "v1.0.0"
        done = self.link(plant(root), "someone/consumer")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("not an _actions path and not a canon self-call", done.stderr)
        self.assertIsNone(self.linked())

    def test_a_tree_with_no_belt_config_is_refused(self) -> None:
        """A tree with no mise/config.toml is not a canon tree."""
        root = self.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
        done = self.link(plant(root, config=False), "someone/consumer")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(
            done.stdout,
            "::error::the resolved canon tree has no mise/config.toml or"
            " mise/findings.sh — not a canon tree\n"
            f"  resolved: {root}\n",
        )
        self.assertEqual(self.output.read_text(encoding="utf-8"), "")
        self.assertIsNone(self.linked())

    def test_no_canon_repository_is_refused(self) -> None:
        """The canon's name is an input: absent, nothing is guessed."""
        root = self.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
        done = self.link(plant(root), "someone/consumer", canon=None)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(
            "CANON_REPOSITORY must be set (action.yml passes the canon repository)",
            done.stderr,
        )
        self.assertIsNone(self.linked())


if __name__ == "__main__":
    unittest.main()
