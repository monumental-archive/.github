"""The canon action's link script, every branch (#1386).

`.github/actions/canon/link.sh` is the canon action's one step: it links
the tree `$/.github/actions/canon` resolved from to `.org-canon` and
names the resolved ref. It finds that tree from its own location, so
each row plants a tree, copies the shipped script into the tree's
`.github/actions/canon/`, and runs it there under bash.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING

from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    import pathlib
    from pathlib import Path

CANON = "example-org/.github"
SHA = "0123456789abcdef0123456789abcdef01234567"


def plant(
    root: pathlib.Path, *, config: bool = True, canon: support.Canon
) -> pathlib.Path:
    """Plant a tree with the shipped script where the action keeps it.

    Returns:
        The script's path inside the planted tree.

    """
    canon(root, ".github/actions/canon", stubs={"mise/config.toml": "[tools]\n"})
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


class LinkTheCanonTree:
    """Each row: where the tree resolved, and what the step did."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Give each row a scratch root and a workspace."""
        self.make_env = env
        self.tmp = tmp_path
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
        bash = support.tool("bash")
        return subprocess.run(
            [bash, str(script)],
            env=self.make_env(
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

    def git(self, *args: str, cwd: pathlib.Path | None = None) -> str:
        """Run git as a row's caller would, in the workspace by default.

        Returns:
            Its standard output.

        """
        return subprocess.run(
            [support.tool("git"), "-c", "commit.gpgsign=false", *args],
            cwd=cwd or self.workspace,
            env=self.make_env(self.tmp / "home", PATH=os.environ["PATH"]),
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
        assert self.git("status", "--porcelain", cwd=root) == ""


@fixture
def link_the_canon_tree(tmp_path: Path, env: support.Env) -> LinkTheCanonTree:
    """Give each row its own LinkTheCanonTree."""
    return LinkTheCanonTree(tmp_path, env=env)


def test_an_actions_path_names_the_pinned_ref(
    link_the_canon_tree: LinkTheCanonTree, canon: support.Canon
) -> None:
    """A caller's pin: the ref is the path segment under _actions."""
    root = link_the_canon_tree.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
    done = link_the_canon_tree.link(plant(root, canon=canon), "someone/consumer")
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout == f"canon resolved: v1.69.0 at {root}\n"
    assert link_the_canon_tree.output.read_text(encoding="utf-8") == "ref=v1.69.0\n"
    assert link_the_canon_tree.linked() == root


def test_the_canon_calling_itself_names_the_running_commit(
    link_the_canon_tree: LinkTheCanonTree, canon: support.Canon
) -> None:
    """A local `$/` resolution in the canon is the running commit."""
    root = link_the_canon_tree.tmp / "work" / ".github"
    done = link_the_canon_tree.link(plant(root, canon=canon), CANON)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout == f"canon resolved: {SHA} at {root}\n"
    assert link_the_canon_tree.output.read_text(encoding="utf-8") == f"ref={SHA}\n"
    assert link_the_canon_tree.linked() == root


def test_a_checkout_does_not_see_the_link_as_a_change(
    link_the_canon_tree: LinkTheCanonTree, canon: support.Canon
) -> None:
    """The link is in the clone's info/exclude, so the tree is clean.

    #1944: the go-binary build unlinked `.org-canon` mid-job because an
    untracked path stamps every binary vcs.modified=true, and two later
    steps still sourced through the link that was gone.
    """
    link_the_canon_tree.checkout(link_the_canon_tree.workspace)
    root = link_the_canon_tree.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
    done = link_the_canon_tree.link(plant(root, canon=canon), "someone/consumer")
    assert done.returncode == 0, done.stdout + done.stderr
    assert link_the_canon_tree.linked() == root
    assert excluded(link_the_canon_tree.workspace / ".git") == "/.org-canon"
    link_the_canon_tree.assert_clean(link_the_canon_tree.workspace)


def test_a_second_link_writes_no_second_line(
    link_the_canon_tree: LinkTheCanonTree, canon: support.Canon
) -> None:
    """The action can run twice in a job; the exclude holds one line."""
    link_the_canon_tree.checkout(link_the_canon_tree.workspace)
    root = link_the_canon_tree.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
    script = plant(root, canon=canon)
    for _ in range(2):
        done = link_the_canon_tree.link(script, "someone/consumer")
        assert done.returncode == 0, done.stdout + done.stderr
    assert excluded(link_the_canon_tree.workspace / ".git") == "/.org-canon"
    link_the_canon_tree.assert_clean(link_the_canon_tree.workspace)


def test_a_workspace_inside_a_repository_is_anchored_where_it_sits(
    link_the_canon_tree: LinkTheCanonTree, canon: support.Canon
) -> None:
    """A checkout one level up: the entry names the workspace's path."""
    clone = link_the_canon_tree.tmp / "clone"
    link_the_canon_tree.checkout(clone)
    link_the_canon_tree.workspace = clone / "sub"
    link_the_canon_tree.workspace.mkdir()
    root = link_the_canon_tree.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
    done = link_the_canon_tree.link(plant(root, canon=canon), "someone/consumer")
    assert done.returncode == 0, done.stdout + done.stderr
    assert excluded(clone / ".git") == "/sub/.org-canon"
    link_the_canon_tree.assert_clean(clone)


def test_a_worktree_is_excluded_through_the_common_dir(
    link_the_canon_tree: LinkTheCanonTree, canon: support.Canon
) -> None:
    """Git reads info/exclude from the common dir, not the worktree's."""
    clone = link_the_canon_tree.tmp / "clone"
    link_the_canon_tree.checkout(clone)
    link_the_canon_tree.git(
        "worktree", "add", "-q", str(link_the_canon_tree.workspace / "wt"), cwd=clone
    )
    link_the_canon_tree.workspace /= "wt"
    root = link_the_canon_tree.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
    done = link_the_canon_tree.link(plant(root, canon=canon), "someone/consumer")
    assert done.returncode == 0, done.stdout + done.stderr
    assert excluded(clone / ".git") == "/.org-canon"
    link_the_canon_tree.assert_clean(link_the_canon_tree.workspace)


def test_a_workspace_in_no_repository_is_linked_and_nothing_written(
    link_the_canon_tree: LinkTheCanonTree, canon: support.Canon
) -> None:
    """No clone: the link is made and there is no exclude to write."""
    root = link_the_canon_tree.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
    done = link_the_canon_tree.link(plant(root, canon=canon), "someone/consumer")
    assert done.returncode == 0, done.stdout + done.stderr
    assert link_the_canon_tree.linked() == root
    assert not (link_the_canon_tree.workspace / ".git").exists()


def test_another_repository_off_an_actions_path_is_refused(
    link_the_canon_tree: LinkTheCanonTree, canon: support.Canon
) -> None:
    """A shape the action does not understand is refused, not guessed."""
    root = link_the_canon_tree.tmp / "work" / "consumer"
    done = link_the_canon_tree.link(plant(root, canon=canon), "someone/consumer")
    assert done.returncode == 1, done.stdout + done.stderr
    assert done.stderr == (
        f"::error::link.sh: canon action resolved to '{root}' in"
        " someone/consumer — not an _actions path and not a canon self-call\n"
    )
    assert link_the_canon_tree.output.read_text(encoding="utf-8") == ""
    assert link_the_canon_tree.linked() is None


def test_an_actions_path_of_another_repository_is_refused(
    link_the_canon_tree: LinkTheCanonTree, canon: support.Canon
) -> None:
    """Only the canon's own _actions segment names a pinned ref."""
    root = link_the_canon_tree.tmp / "_actions" / "someone" / "fork" / "v1.0.0"
    done = link_the_canon_tree.link(plant(root, canon=canon), "someone/consumer")
    assert done.returncode == 1, done.stdout + done.stderr
    assert "not an _actions path and not a canon self-call" in done.stderr
    assert link_the_canon_tree.linked() is None


def test_a_tree_with_no_belt_config_is_refused(
    link_the_canon_tree: LinkTheCanonTree, canon: support.Canon
) -> None:
    """A tree with no mise/config.toml is not a canon tree."""
    root = link_the_canon_tree.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
    done = link_the_canon_tree.link(
        plant(root, config=False, canon=canon), "someone/consumer"
    )
    assert done.returncode == 1, done.stdout + done.stderr
    assert done.stdout == (
        "::error::the resolved canon tree has no mise/config.toml or"
        " mise/findings.sh — not a canon tree\n"
        f"  resolved: {root}\n"
    )
    assert link_the_canon_tree.output.read_text(encoding="utf-8") == ""
    assert link_the_canon_tree.linked() is None


def test_no_canon_repository_is_refused(
    link_the_canon_tree: LinkTheCanonTree, canon: support.Canon
) -> None:
    """The canon's name is an input: absent, nothing is guessed."""
    root = link_the_canon_tree.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
    done = link_the_canon_tree.link(
        plant(root, canon=canon), "someone/consumer", canon=None
    )
    assert done.returncode != 0
    assert (
        "CANON_REPOSITORY must be set (action.yml passes the canon repository)"
        in done.stderr
    )
    assert link_the_canon_tree.linked() is None
