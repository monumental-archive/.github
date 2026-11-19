#!/usr/bin/env python3
"""Run `mise/queue_head.py` against scratch repositories (#1422).

Each row builds an `origin` holding `refs/pull/<n>/head` and a checkout
holding the group's base and group commit, the way the gate job's
checkout holds them, and runs the helper there as the step does. Every
row asserts the step's message.

The real case is edtf#265, whose base, merged group and two heads are
the bundle in testdata/queue-head/: the group carries the old head's
tree, and the pull request's head had already moved. The bundle is
fetched from the real repository, not written by hand: a hand-made
fixture would prove only what its author believed. It is kept gzipped,
as release/testdata keeps its .crate: a bundle opens with a text
header, so the text linters would read it as text.

stdlib `unittest`, matching the other tests here. Every git call passes
its own scrubbed environment (#1463).
"""

from __future__ import annotations

import gzip
import pathlib
import re
import shutil

# ruff: ignore[suspicious-subprocess-import]
import subprocess
import sys
import tempfile
import unittest

import hermetic

HERE = pathlib.Path(__file__).resolve().parent
HELPER = HERE / "queue_head.py"
BUNDLE = HERE / "testdata" / "queue-head" / "edtf-265.bundle.gz"
CI = HERE.parent / ".github" / "workflows" / "ci.yml"
GIT = hermetic.tool("git")
# An empty HOME for every fixture, made once for the module.
HOME_DIR = tempfile.mkdtemp()


def tearDownModule() -> None:
    """Remove the fixtures' HOME."""
    shutil.rmtree(HOME_DIR, ignore_errors=True)


# edtf#265, measured 2026-11-17 and 2026-11-19.
EDTF_BASE = "19fa2d6c261e49fe6cecd9e8a0b487ae49df9fd0"
EDTF_GROUP = "34b028e3fe7a37c3b72d9e9354251e9f6a3615c2"
EDTF_OLD_HEAD = "819b929cfec49f947eb6cc37f41a4a8accd5b6bc"
EDTF_HEAD = "be47b318882a05a8a99cc732b4607e57e804b6cd"
EDTF_MERGED_TREE = "7e2034df0f156fe6e3d6093d2cc0aa4e7b4a64cb"
EDTF_HEAD_TREE = "50f1ad6401b66bce3bc69c91a2f1d5428ade77d3"

NUMBER = "7"


def scrubbed() -> dict[str, str]:
    """Return the fixture environment: the allow-list, and no git config.

    HOME is a directory of its own, so no user's git config (signing
    above all) reaches a fixture commit.

    Returns:
        The environment.

    """
    return hermetic.env(HOME_DIR, GIT_CONFIG_NOSYSTEM="1")


def git(repo: pathlib.Path, *args: str) -> str:
    """Run git in a scratch repository.

    Returns:
        Its stripped stdout.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    done = subprocess.run(
        [GIT, "-C", str(repo), *args],
        env=scrubbed(),
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def queue_ref(number: str, base: str) -> str:
    """Return the queue's branch for one entry.

    Returns:
        The ref, as `merge_group.head_ref` carries it.

    """
    return f"refs/heads/gh-readonly-queue/main/pr-{number}-{base}"


class Repos:
    """An `origin` and a checkout cloned from it."""

    def __init__(self, root: pathlib.Path) -> None:
        """Make `origin` with one commit on main, and clone it."""
        self.origin = root / "origin"
        self.checkout = root / "checkout"
        self.origin.mkdir()
        git(self.origin, "init", "-q", "-b", "main")
        self.write("a.txt", "one\ntwo\nthree\n")
        self.write("b.txt", "alpha\n")
        self.base = self.commit("base")

    def write(self, name: str, text: str) -> None:
        """Write a file in `origin`'s work tree."""
        (self.origin / name).write_text(text, encoding="utf-8")

    def commit(self, message: str) -> str:
        """Commit everything in `origin`'s work tree.

        Returns:
            The commit.

        """
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "-q", "-m", message)
        return git(self.origin, "rev-parse", "HEAD")

    def branch(self, name: str, start: str) -> None:
        """Start a branch in `origin` and switch to it."""
        git(self.origin, "checkout", "-q", "-b", name, start)

    def publish(self, number: str, head: str) -> None:
        """Point the pull request's head ref at a commit, as GitHub does."""
        git(self.origin, "update-ref", f"refs/pull/{number}/head", head)

    def clone(self) -> None:
        """Clone `origin` with every branch, as the full-history checkout."""
        git(
            self.origin.parent,
            "clone",
            "-q",
            "--no-local",
            str(self.origin),
            "checkout",
        )
        git(
            self.checkout,
            "fetch",
            "-q",
            "origin",
            "+refs/heads/*:refs/remotes/origin/*",
        )

    def squash(self, base: str, head: str) -> str:
        """Squash a head onto a base in the checkout, as a squash queue does.

        Returns:
            The group commit.

        """
        tree = git(self.checkout, "merge-tree", "--write-tree", base, head)
        return git(self.checkout, "commit-tree", tree, "-p", base, "-m", "squash")


def run_helper(
    cwd: pathlib.Path, ref: str, base: str, group: str
) -> subprocess.CompletedProcess[str]:
    """Run the helper in a checkout as the step does.

    Returns:
        The run.

    """
    env = scrubbed()
    env.update({
        "ORG_QUEUE_REF": ref,
        "ORG_QUEUE_BASE": base,
        "ORG_QUEUE_GROUP": group,
        "ORG_QUEUE_TOKEN": "not-a-real-token",
    })
    # ruff: ignore[subprocess-without-shell-equals-true]
    return subprocess.run(
        [sys.executable, str(HELPER)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class QueueHead(unittest.TestCase):
    """Each row: a merge group, and the pull request it was queued from."""

    def setUp(self) -> None:
        """Give each row its own scratch directory."""
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def refused(self, done: subprocess.CompletedProcess[str], message: str) -> None:
        """Assert the step is red with exactly this message."""
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout, f"::error::queue-head: {message}\n")

    def passed(
        self, done: subprocess.CompletedProcess[str], group: str, head: str, base: str
    ) -> None:
        """Assert the step is green and names the group, head and base."""
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertRegex(
            done.stdout,
            rf"^queue-head: merge group {group} is pull request #{NUMBER}'s current"
            rf" head {head} merged onto {base} \(tree [0-9a-f]{{40}}\)\n$",
        )

    def moved(self) -> tuple[Repos, str, str]:
        """Queue a pull request at one head, then push another.

        Returns:
            The repositories, the head the group was built from, and the
            head the pull request holds now.

        """
        repos = Repos(self.tmp)
        repos.branch("feature", repos.base)
        repos.write("a.txt", "one\nTWO\nthree\n")
        older = repos.commit("older head")
        repos.write("a.txt", "one\nTWO\nTHREE\n")
        current = repos.commit("current head")
        repos.publish(NUMBER, current)
        return repos, older, current

    def test_a_group_built_from_an_older_head_is_refused(self) -> None:
        """A squash of the older head, checked against a moved head, is red."""
        repos, older, current = self.moved()
        repos.clone()
        group = repos.squash(repos.base, older)
        done = run_helper(
            repos.checkout, queue_ref(NUMBER, repos.base), repos.base, group
        )
        have = git(repos.checkout, "rev-parse", f"{group}^{{tree}}")
        want = git(repos.checkout, "merge-tree", "--write-tree", repos.base, current)
        self.refused(
            done,
            f"the merge group {group} was not built from pull request #{NUMBER}'s"
            f" current head {current}: the pull request moved after it was queued."
            f" The group's tree is {have}; {current} merged onto the base"
            f" {repos.base} is {want}. Re-queue it, and the group is built from the"
            " real head",
        )

    def test_a_group_built_from_the_current_head_passes(self) -> None:
        """A squash of the head the pull request holds is green."""
        repos, _, current = self.moved()
        repos.clone()
        group = repos.squash(repos.base, current)
        done = run_helper(
            repos.checkout, queue_ref(NUMBER, repos.base), repos.base, group
        )
        self.passed(done, group, current, repos.base)

    def test_edtf_265_is_refused(self) -> None:
        """The real case: edtf#265's group carries its old head's tree."""
        checkout = self.tmp / "checkout"
        origin = self.tmp / "origin"
        bundle = self.tmp / "edtf-265.bundle"
        bundle.write_bytes(gzip.decompress(BUNDLE.read_bytes()))
        for repo in (checkout, origin):
            git(self.tmp, "init", "-q", str(repo))
            # The bundle holds the four commits and no history behind
            # the base, so both repositories treat the base as a root.
            (repo / ".git" / "shallow").write_text(f"{EDTF_BASE}\n", encoding="utf-8")
        git(
            checkout,
            "fetch",
            "-q",
            str(bundle),
            "refs/heads/base:refs/heads/main",
            "refs/heads/group:refs/heads/group",
            "refs/heads/old-head:refs/heads/old-head",
        )
        git(
            origin,
            "fetch",
            "-q",
            str(bundle),
            "refs/heads/current-head:refs/pull/265/head",
        )
        git(checkout, "remote", "add", "origin", str(origin))
        # What the queue did: the old head merged onto the base is the
        # tree that merged, and the current head's is not.
        self.assertEqual(
            git(checkout, "rev-parse", f"{EDTF_GROUP}^{{tree}}"), EDTF_MERGED_TREE
        )
        self.assertEqual(
            git(checkout, "merge-tree", "--write-tree", EDTF_BASE, EDTF_OLD_HEAD),
            EDTF_MERGED_TREE,
        )
        done = run_helper(
            checkout,
            queue_ref("265", EDTF_BASE),
            EDTF_BASE,
            EDTF_GROUP,
        )
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(
            done.stdout,
            f"::error::queue-head: the merge group {EDTF_GROUP} was not built from"
            f" pull request #265's current head {EDTF_HEAD}: the pull request moved"
            f" after it was queued. The group's tree is {EDTF_MERGED_TREE};"
            f" {EDTF_HEAD} merged onto the base {EDTF_BASE} is {EDTF_HEAD_TREE}."
            " Re-queue it, and the group is built from the real head\n",
        )

    def test_a_ref_that_is_not_a_queue_ref_is_refused(self) -> None:
        """A merge group whose head_ref names no queue entry is red."""
        repos, _, current = self.moved()
        repos.clone()
        group = repos.squash(repos.base, current)
        done = run_helper(repos.checkout, "refs/heads/main", repos.base, group)
        self.refused(
            done,
            "the merge group's head_ref 'refs/heads/main' is not a merge-queue ref"
            " (refs/heads/gh-readonly-queue/<base>/pr-<n>-<sha>), so the pull"
            " request it came from is unknown and the group is unverified",
        )

    def test_a_missing_base_is_refused(self) -> None:
        """A base the checkout does not hold is red."""
        repos, _, current = self.moved()
        repos.clone()
        group = repos.squash(repos.base, current)
        absent = "0" * 40
        done = run_helper(repos.checkout, queue_ref(NUMBER, absent), absent, group)
        self.refused(
            done,
            f"the merge group's base commit '{absent}' is not in the checkout,"
            " so the group is unverified",
        )

    def test_a_missing_group_is_refused(self) -> None:
        """A group commit the checkout does not hold is red."""
        repos, _, _ = self.moved()
        repos.clone()
        absent = "f" * 40
        done = run_helper(
            repos.checkout, queue_ref(NUMBER, repos.base), repos.base, absent
        )
        self.refused(
            done,
            f"the merge group's group commit '{absent}' is not in the checkout,"
            " so the group is unverified",
        )

    def test_a_fetch_that_fails_is_refused(self) -> None:
        """No refs/pull/<n>/head on origin: the fetch fails, and it is red."""
        repos, _, current = self.moved()
        git(repos.origin, "update-ref", "-d", f"refs/pull/{NUMBER}/head")
        repos.clone()
        group = repos.squash(repos.base, current)
        done = run_helper(
            repos.checkout, queue_ref(NUMBER, repos.base), repos.base, group
        )
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertRegex(
            done.stdout,
            rf"^::error::queue-head: fetching pull request #{NUMBER}'s head"
            rf" \(refs/pull/{NUMBER}/head\) from origin failed, so the group is"
            r" unverified: .*couldn't find remote ref refs/pull/7/head\n$",
        )

    def test_a_merge_that_conflicts_is_refused(self) -> None:
        """A head that conflicts with the base is red, not compared."""
        repos, _, current = self.moved()
        git(repos.origin, "checkout", "-q", "main")
        repos.write("a.txt", "one\nZWEI\nthree\n")
        moved_base = repos.commit("main moved")
        repos.clone()
        done = run_helper(
            repos.checkout, queue_ref(NUMBER, moved_base), moved_base, moved_base
        )
        self.refused(
            done,
            f"pull request #{NUMBER}'s current head {current} conflicts with the"
            f" group's base {moved_base}, so the group is unverified",
        )

    def test_the_second_entry_of_a_batch_passes(self) -> None:
        """Entry two's base is entry one's group commit, and it is green."""
        repos = Repos(self.tmp)
        repos.branch("first", repos.base)
        repos.write("a.txt", "one\nTWO\nthree\n")
        first = repos.commit("first")
        repos.branch("second", repos.base)
        repos.write("b.txt", "ALPHA\n")
        second = repos.commit("second")
        repos.publish(NUMBER, second)
        repos.clone()
        group_one = repos.squash(repos.base, first)
        group_two = repos.squash(group_one, second)
        done = run_helper(
            repos.checkout, queue_ref(NUMBER, group_one), group_one, group_two
        )
        self.passed(done, group_two, second, group_one)

    def test_a_merge_commit_group_passes(self) -> None:
        """A merge-commit queue's group is green."""
        repos, _, current = self.moved()
        git(repos.origin, "checkout", "-q", "main")
        repos.write("b.txt", "ALPHA\n")
        moved_base = repos.commit("main moved")
        repos.clone()
        tree = git(repos.checkout, "merge-tree", "--write-tree", moved_base, current)
        group = git(
            repos.checkout,
            "commit-tree",
            tree,
            "-p",
            moved_base,
            "-p",
            current,
            "-m",
            "merge",
        )
        done = run_helper(
            repos.checkout, queue_ref(NUMBER, moved_base), moved_base, group
        )
        self.passed(done, group, current, moved_base)

    def test_a_rebased_group_passes(self) -> None:
        """A rebase queue's group, the head's commits replayed, is green."""
        repos, _, current = self.moved()
        git(repos.origin, "checkout", "-q", "main")
        repos.write("b.txt", "ALPHA\n")
        moved_base = repos.commit("main moved")
        repos.clone()
        git(repos.checkout, "checkout", "-q", "-b", "rebased", current)
        git(repos.checkout, "rebase", "-q", moved_base)
        group = git(repos.checkout, "rev-parse", "HEAD")
        self.assertNotEqual(group, current)
        done = run_helper(
            repos.checkout, queue_ref(NUMBER, moved_base), moved_base, group
        )
        self.passed(done, group, current, moved_base)


def gate_steps() -> list[str]:
    """Return the gate job's steps in ci.yml, each as its text.

    Returns:
        The steps, in order.

    """
    lines = CI.read_text(encoding="utf-8").splitlines()
    start = lines.index("  gate:")
    end = next(
        i for i in range(start + 1, len(lines)) if re.match(r"^  [\w-]+:$", lines[i])
    )
    steps: list[list[str]] = []
    for line in lines[start:end]:
        if line.startswith("      - "):
            steps.append([line])
        elif steps and (line.startswith("        ") or not line.strip()):
            steps[-1].append(line)
    return ["\n".join(step).rstrip() for step in steps]


class Workflow(unittest.TestCase):
    """The step exists in the gate job, on merge_group only, as one line."""

    def test_the_step_runs_on_merge_group_only_as_one_line(self) -> None:
        """One gate step calls the helper; its `if:` and `run:` are held."""
        steps = [s for s in gate_steps() if "queue_head.py" in s]
        self.assertEqual(len(steps), 1, steps)
        step = steps[0]
        self.assertIn(
            "        if: ${{ github.event_name == 'merge_group' }}\n", step + "\n"
        )
        runs = [line for line in step.splitlines() if line.startswith("        run:")]
        self.assertEqual(
            runs,
            # Under the belt's Python (#1543).
            ["        run: '\"${ORG_PYTHON}\" .org-canon/mise/queue_head.py'"],
            step,
        )
        for key, value in (
            ("ORG_QUEUE_BASE", "github.event.merge_group.base_sha"),
            ("ORG_QUEUE_GROUP", "github.event.merge_group.head_sha"),
            ("ORG_QUEUE_REF", "github.event.merge_group.head_ref"),
            ("ORG_QUEUE_TOKEN", "github.token"),
        ):
            self.assertIn(f"          {key}: ${{{{ {value} }}}}\n", step + "\n")


if __name__ == "__main__":
    unittest.main()
