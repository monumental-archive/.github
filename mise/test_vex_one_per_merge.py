#!/usr/bin/env python3
"""Table tests for `lint:vex-one-per-merge` (#1603).

Two VEX decisions in one pull request went green through the gate and
reddened main on the push, when `vex-attest.yml` refused them (#1551).
Each row plants a git tree with an `origin/main` and a branch ahead of
it, then runs the task's own `run` body, read out of mise/config.toml,
under bash with ORG_BELT_DIR at this belt, so the real helper and the
real message file answer. Before #1603 the task does not exist and
every row fails on the lookup.

Each row asserts the message, not just the exit status: a planted row
scored on exit alone can pass for the wrong reason.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace

hermetic = SimpleNamespace(
    **runpy.run_path(str(Path(__file__).resolve().with_name("hermetic.py")))
)

BELT = Path(__file__).resolve().parent
CANON = BELT.parent
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"
TASK = "lint:vex-one-per-merge"
SENTENCE = (BELT / "vex-one-per-merge.txt").read_text(encoding="utf-8").strip()
A = "security/vex/RUSTSEC-0000-0001.openvex.json"
B = "security/vex/RUSTSEC-0000-0002.openvex.json"
C = "security/vex/RUSTSEC-0000-0003.openvex.json"
README = "security/vex/README.md"
HELPER = BELT / "vex-one-per-merge.py"


def body() -> str:
    """Read the task's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    with (BELT / "config.toml").open("rb") as handle:
        return tomllib.load(handle)["tasks"][TASK]["run"]


class Tree:
    """A planted repository with a default branch to diverge from."""

    def __init__(self, root: Path) -> None:
        """Make the repository under `root`."""
        self.dir = root / "repo"
        self.dir.mkdir()
        self.env = hermetic.env(root / "home")
        self.git("init", "-q", "-b", "feature")

    def git(self, *args: str) -> str:
        """Run git in the tree.

        Returns:
            git's standard output.

        """
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [GIT, *args],
            cwd=self.dir,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def commit(self, *names: str, remove: tuple[str, ...] = ()) -> None:
        """Write each named file with fresh content, then commit."""
        for name in names:
            path = self.dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            old = path.read_text(encoding="utf-8") if path.exists() else ""
            path.write_text(old + "x\n", encoding="utf-8")
        self.git("add", "-A", "--", *names)
        if remove:
            self.git("rm", "-q", "--", *remove)
        self.git("commit", "-q", "--allow-empty", "-m", "change")

    def mark_main(self) -> None:
        """Point origin/main at HEAD: everything so far is the default branch."""
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def listing(self) -> subprocess.CompletedProcess[str]:
        """List the decisions ahead of origin/main, as vex-attest.yml asks.

        Returns:
            The finished process, output captured.

        """
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [sys.executable, str(HELPER), "--list", "origin/main.."],
            cwd=self.dir,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def run(self, event: str = "") -> subprocess.CompletedProcess[str]:
        """Run the task's body in the tree, as the CI event `event`.

        Returns:
            The finished process, output captured.

        """
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [BASH, "-euo", "pipefail", "-c", body()],
            cwd=self.dir,
            env={
                **self.env,
                "ORG_BELT_DIR": str(BELT),
                **({"GITHUB_EVENT_NAME": event} if event else {}),
            },
            capture_output=True,
            text=True,
            check=False,
        )


class OnePerMerge(unittest.TestCase):
    """The commits ahead of origin/main change at most one decision."""

    def setUp(self) -> None:
        """Plant a tree whose default branch holds one decision."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tree = Tree(Path(tmp.name))
        self.tree.commit(A, README)
        self.tree.mark_main()

    def test_one_changed_decision_passes(self) -> None:
        """A new decision alone is green and counted."""
        self.tree.commit(B)
        done = self.tree.run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("1 VEX decision(s) changed ahead of origin/main", done.stdout)

    def test_two_are_red_naming_both(self) -> None:
        """Two decisions over two commits: red, with the sentence and both files."""
        self.tree.commit(B)
        self.tree.commit(A)
        done = self.tree.run()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn(f"{TASK}: {SENTENCE}:", done.stderr)
        self.assertIn(f"  {A}\n", done.stderr)
        self.assertIn(f"  {B}\n", done.stderr)

    def test_a_non_decision_file_is_not_counted(self) -> None:
        """The README beside one decision changes nothing about the count."""
        self.tree.commit(B, README)
        done = self.tree.run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("1 VEX decision(s) changed", done.stdout)

    def test_a_deletion_is_not_counted(self) -> None:
        """Removing a decision signs nothing, so it does not count."""
        self.tree.commit(remove=(A,))
        self.tree.commit(B)
        done = self.tree.run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("1 VEX decision(s) changed", done.stdout)

    def test_a_rename_is_counted_at_its_new_name(self) -> None:
        """A decision moved to a new name, beside another, is two."""
        self.tree.git("mv", A, C)
        self.tree.commit(B)
        done = self.tree.run()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn(f"  {B}\n", done.stderr)
        self.assertIn(f"  {C}\n", done.stderr)

    def test_main_moving_on_is_not_counted(self) -> None:
        """A decision main gained after the branch point is not the branch's."""
        self.tree.commit(B)
        self.tree.git("switch", "-q", "-c", "main", "origin/main")
        self.tree.commit(C)
        self.tree.mark_main()
        self.tree.git("switch", "-q", "feature")
        done = self.tree.run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("1 VEX decision(s) changed", done.stdout)

    def test_a_merge_commit_reads_its_second_parent(self) -> None:
        """On a pull request's synthetic merge, the branch's commits are judged."""
        self.tree.commit(B)
        self.tree.commit(C)
        self.tree.git("switch", "-q", "--detach", "origin/main")
        self.tree.git("merge", "-q", "--no-ff", "-m", "merge", "feature")
        done = self.tree.run()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn(f"  {B}\n", done.stderr)
        self.assertIn(f"  {C}\n", done.stderr)

    def test_the_default_branch_itself_skips_by_name(self) -> None:
        """Nothing ahead of origin/main skips, whatever main holds."""
        self.tree.commit(B, C)
        self.tree.mark_main()
        done = self.tree.run()
        self.assertEqual(done.returncode, 0, done.stderr)
        skipped = "nothing ahead of origin/main (the default branch itself), skipped"
        self.assertIn(f"{TASK}: {skipped}", done.stdout)

    def test_a_merge_group_skips_by_name(self) -> None:
        """A group's range holds the entries queued ahead, so it is not judged."""
        self.tree.commit(B)
        self.tree.commit(C)
        done = self.tree.run("merge_group")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(
            f"{TASK}: judged on the pull request: a merge group's range also"
            " holds the entries queued ahead of it, skipped",
            done.stdout,
        )

    def test_the_same_tree_is_red_on_a_pull_request(self) -> None:
        """The tree the merge-group row skips is red as a pull request."""
        self.tree.commit(B)
        self.tree.commit(C)
        done = self.tree.run("pull_request")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn(f"{TASK}: {SENTENCE}:", done.stderr)
        self.assertIn(f"  {B}\n", done.stderr)
        self.assertIn(f"  {C}\n", done.stderr)

    def test_no_origin_main_skips_by_name(self) -> None:
        """A clone with no default branch to diverge from skips."""
        self.tree.git("update-ref", "-d", "refs/remotes/origin/main")
        done = self.tree.run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"{TASK}: no origin/main, skipped", done.stdout)


class NoneTracked(unittest.TestCase):
    """A tree that keeps no decisions has nothing to judge."""

    def test_none_tracked_skips_by_name(self) -> None:
        """Only a README under security/vex/ skips, by name."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Tree(Path(tmp))
            tree.commit(README)
            tree.mark_main()
            tree.commit(README)
            done = tree.run()
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn(f"{TASK}: no VEX decisions tracked, skipped", done.stdout)


class Listing(unittest.TestCase):
    """vex-attest.yml's signing list is the lint's own reader (#1615)."""

    def setUp(self) -> None:
        """Plant a tree whose default branch holds two decisions."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tree = Tree(Path(tmp.name))
        self.tree.commit(A, B, README)
        self.tree.mark_main()

    def listed(self) -> list[str]:
        """Run the listing and require it to succeed.

        Returns:
            The decisions it printed, one per line.

        """
        done = self.tree.listing()
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.splitlines()

    def test_a_rename_with_an_edit_is_listed_at_its_new_name(self) -> None:
        """Moved and edited: new bytes at a new name, so it is signed."""
        self.tree.git("mv", A, C)
        self.tree.commit(C)
        self.assertEqual(self.listed(), [C])

    def test_a_pure_rename_is_listed(self) -> None:
        """Moved alone: listed, and the store decides if its bytes are held."""
        self.tree.git("mv", A, C)
        self.tree.commit()
        self.assertEqual(self.listed(), [C])

    def test_a_deletion_is_not_listed(self) -> None:
        """Removed: nothing to sign."""
        self.tree.commit(remove=(A,))
        self.assertEqual(self.listed(), [])

    def test_the_count_and_the_listing_agree(self) -> None:
        """Two changed: the gate reds on two, and the listing names the two."""
        self.tree.commit(A, B)
        self.assertEqual(self.listed(), [A, B])
        done = self.tree.run()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn(f"  {A}\n  {B}\n", done.stderr)


class Workflow(unittest.TestCase):
    """The push branch asks the helper and holds no diff of its own."""

    def test_the_step_calls_the_helper_and_holds_no_git_diff(self) -> None:
        """The collect step lists through the helper; `git diff` is gone."""
        workflow = (CANON / ".github/workflows/vex-attest.yml").read_text(
            encoding="utf-8"
        )
        start = workflow.index("        id: collect\n")
        step = workflow[start : workflow.index("\n  sign-vex:", start)]
        self.assertIn(
            '"${ORG_PYTHON}" .org-canon/mise/vex-one-per-merge.py --list "${BEFORE}.."',
            step,
        )
        self.assertNotIn("git diff", step)


class OneWording(unittest.TestCase):
    """The lint and vex-attest.yml print one sentence, from one file."""

    def test_the_workflow_reads_the_same_file(self) -> None:
        """The workflow names the file and restates no sentence of its own."""
        workflow = (CANON / ".github/workflows/vex-attest.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("ONE_PER_MERGE: .org-canon/mise/vex-one-per-merge.txt", workflow)
        self.assertIn('echo "::error::$(< "${ONE_PER_MERGE}")"', workflow)
        self.assertNotIn(SENTENCE, workflow)


if __name__ == "__main__":
    unittest.main()
