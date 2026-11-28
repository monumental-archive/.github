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
"""

import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import decoded, vex_one_per_merge
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
CANON = BELT.parent
BASH = support.tool("bash")
GIT = support.tool("git")
TASK = "lint:vex-one-per-merge"
SENTENCE = (BELT / "vex-one-per-merge.txt").read_text(encoding="utf-8").strip()
A = "security/vex/RUSTSEC-0000-0001.openvex.json"
B = "security/vex/RUSTSEC-0000-0002.openvex.json"
C = "security/vex/RUSTSEC-0000-0003.openvex.json"
README = "security/vex/README.md"


def body() -> str:
    """Read the task's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    task = decoded.as_object(decoded.as_object(config["tasks"], "tasks")[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


class Tree:
    """A planted repository with a default branch to diverge from."""

    def __init__(self, root: Path, *, env: support.Env) -> None:
        """Make the repository under `root`."""
        self.make_env = env
        self.dir = root / "repo"
        self.dir.mkdir()
        self.env = self.make_env(root / "home", MISE_TASK_NAME=TASK)
        self.git("init", "-q", "-b", "feature")

    def git(self, *args: str) -> str:
        """Run git in the tree.

        Returns:
            git's standard output.

        """
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

    def listing(self, *, capfd: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
        """List the decisions ahead of origin/main, as vex-attest.yml asks.

        Returns:
            The status and both streams, from an in-process call (#1697).

        """
        with pytest.MonkeyPatch.context() as patched:
            for name, value in self.env.items():
                patched.setenv(name, value)
            patched.chdir(self.dir)
            status = vex_one_per_merge.main(["--list", "origin/main.."])
        out, err = capfd.readouterr()
        return status, out, err

    def task_env(self, event: str) -> dict[str, str]:
        """Build the environment the task hands the helper.

        Returns:
            The environment, with the CI event when there is one.

        """
        return {
            **self.env,
            "ORG_BELT_DIR": str(BELT),
            **({"GITHUB_EVENT_NAME": event} if event else {}),
        }

    def run(
        self, event: str = "", *, capfd: pytest.CaptureFixture[str]
    ) -> tuple[int, str, str]:
        """Call the task's helper in the tree, as the CI event `event`.

        The task's body is the one line that starts the helper with no
        arguments, so the row calls `main([])` with the body's
        environment (#1697).

        Returns:
            The status and both streams.

        """
        with pytest.MonkeyPatch.context() as patched:
            for name, value in (self.task_env(event)).items():
                patched.setenv(name, value)
            patched.chdir(self.dir)
            status = vex_one_per_merge.main([])
        out, err = capfd.readouterr()
        return status, out, err

    def _set_up_test_one_per_merge(self, tmp_path: Path) -> None:
        """Plant a tree whose default branch holds one decision."""
        self.tree = Tree(Path(str(tmp_path)), env=self.make_env)
        self.tree.commit(A, README)
        self.tree.mark_main()

    def test_one_changed_decision_passes(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A new decision alone is green and counted."""
        self.tree.commit(B)
        status, out, err = self.tree.run(capfd=capfd)
        assert status == 0, err
        assert "1 VEX decision(s) changed ahead of origin/main" in out

    def test_two_are_red_naming_both(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Two decisions over two commits: red, with the sentence and both files."""
        self.tree.commit(B)
        self.tree.commit(A)
        status, out, err = self.tree.run(capfd=capfd)
        assert status == 1, out
        assert f"{TASK}: {SENTENCE}:" in err
        assert f"  {A}\n" in err
        assert f"  {B}\n" in err

    def test_a_non_decision_file_is_not_counted(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The README beside one decision changes nothing about the count."""
        self.tree.commit(B, README)
        status, out, err = self.tree.run(capfd=capfd)
        assert status == 0, err
        assert "1 VEX decision(s) changed" in out

    def test_a_deletion_is_not_counted(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Removing a decision signs nothing, so it does not count."""
        self.tree.commit(remove=(A,))
        self.tree.commit(B)
        status, out, err = self.tree.run(capfd=capfd)
        assert status == 0, err
        assert "1 VEX decision(s) changed" in out

    def test_a_rename_is_counted_at_its_new_name(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A decision moved to a new name, beside another, is two."""
        self.tree.git("mv", A, C)
        self.tree.commit(B)
        status, out, err = self.tree.run(capfd=capfd)
        assert status == 1, out
        assert f"  {B}\n" in err
        assert f"  {C}\n" in err

    def test_main_moving_on_is_not_counted(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A decision main gained after the branch point is not the branch's."""
        self.tree.commit(B)
        self.tree.git("switch", "-q", "-c", "main", "origin/main")
        self.tree.commit(C)
        self.tree.mark_main()
        self.tree.git("switch", "-q", "feature")
        status, out, err = self.tree.run(capfd=capfd)
        assert status == 0, err
        assert "1 VEX decision(s) changed" in out

    def test_a_merge_commit_reads_its_second_parent(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """On a pull request's synthetic merge, the branch's commits are judged."""
        self.tree.commit(B)
        self.tree.commit(C)
        self.tree.git("switch", "-q", "--detach", "origin/main")
        self.tree.git("merge", "-q", "--no-ff", "-m", "merge", "feature")
        status, out, err = self.tree.run(capfd=capfd)
        assert status == 1, out
        assert f"  {B}\n" in err
        assert f"  {C}\n" in err

    def test_the_default_branch_itself_skips_by_name(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Nothing ahead of origin/main skips, whatever main holds."""
        self.tree.commit(B, C)
        self.tree.mark_main()
        status, out, err = self.tree.run(capfd=capfd)
        assert status == 0, err
        skipped = "nothing ahead of origin/main (the default branch itself), skipped"
        assert f"{TASK}: {skipped}" in out

    def test_a_merge_group_skips_by_name(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A group's range holds the entries queued ahead, so it is not judged."""
        self.tree.commit(B)
        self.tree.commit(C)
        status, out, err = self.tree.run("merge_group", capfd=capfd)
        assert status == 0, err
        assert (
            f"{TASK}: judged on the pull request: a merge group's range also"
            " holds the entries queued ahead of it, skipped"
        ) in out

    def test_the_same_tree_is_red_on_a_pull_request(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The tree the merge-group row skips is red as a pull request."""
        self.tree.commit(B)
        self.tree.commit(C)
        status, out, err = self.tree.run("pull_request", capfd=capfd)
        assert status == 1, out
        assert f"{TASK}: {SENTENCE}:" in err
        assert f"  {B}\n" in err
        assert f"  {C}\n" in err

    def test_no_origin_main_skips_by_name(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A clone with no default branch to diverge from skips."""
        self.tree.git("update-ref", "-d", "refs/remotes/origin/main")
        status, out, err = self.tree.run(capfd=capfd)
        assert status == 0, err
        assert f"{TASK}: no origin/main, skipped" in out


# A tree that keeps no decisions has nothing to judge.
def test_none_tracked_skips_by_name(
    capfd: pytest.CaptureFixture[str], env: support.Env
) -> None:
    """Only a README under security/vex/ skips, by name."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Tree(Path(tmp), env=env)
        tree.commit(README)
        tree.mark_main()
        tree.commit(README)
        status, out, err = tree.run(capfd=capfd)
        assert status == 0, err
        assert f"{TASK}: no VEX decisions tracked, skipped" in out


class Listing:
    """vex-attest.yml's signing list is the lint's own reader (#1615)."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant a tree whose default branch holds two decisions."""
        self.make_env = env
        self.tree = Tree(Path(str(tmp_path)), env=self.make_env)
        self.tree.commit(A, B, README)
        self.tree.mark_main()

    def listed(self, *, capfd: pytest.CaptureFixture[str]) -> list[str]:
        """Run the listing and require it to succeed.

        Returns:
            The decisions it printed, one per line.

        """
        status, out, err = self.tree.listing(capfd=capfd)
        assert status == 0, err
        return out.splitlines()


@fixture
def listing_state(tmp_path: Path, env: support.Env) -> Listing:
    """Give each row its own Listing."""
    return Listing(tmp_path, env=env)


def test_a_rename_with_an_edit_is_listed_at_its_new_name(
    listing_state: Listing, capfd: pytest.CaptureFixture[str]
) -> None:
    """Moved and edited: new bytes at a new name, so it is signed."""
    listing_state.tree.git("mv", A, C)
    listing_state.tree.commit(C)
    want: list[str] = [C]
    assert listing_state.listed(capfd=capfd) == want


def test_a_pure_rename_is_listed(
    listing_state: Listing, capfd: pytest.CaptureFixture[str]
) -> None:
    """Moved alone: listed, and the store decides if its bytes are held."""
    listing_state.tree.git("mv", A, C)
    listing_state.tree.commit()
    want: list[str] = [C]
    assert listing_state.listed(capfd=capfd) == want


def test_a_deletion_is_not_listed(
    listing_state: Listing, capfd: pytest.CaptureFixture[str]
) -> None:
    """Removed: nothing to sign."""
    listing_state.tree.commit(remove=(A,))
    none: list[str] = []
    assert listing_state.listed(capfd=capfd) == none


def test_the_count_and_the_listing_agree(
    listing_state: Listing, capfd: pytest.CaptureFixture[str]
) -> None:
    """Two changed: the gate reds on two, and the listing names the two."""
    listing_state.tree.commit(A, B)
    want: list[str] = [A, B]
    assert listing_state.listed(capfd=capfd) == want
    status, out, err = listing_state.tree.run(capfd=capfd)
    assert status == 1, out
    assert f"  {A}\n  {B}\n" in err


# The push branch asks the helper and holds no diff of its own.
def test_the_step_calls_the_helper_and_holds_no_git_diff() -> None:
    """The collect step lists through the helper; `git diff` is gone."""
    workflow = (CANON / ".github/workflows/vex-attest.yml").read_text(encoding="utf-8")
    start = workflow.index("        id: collect\n")
    step = workflow[start : workflow.index("\n  sign-vex:", start)]
    # The listing moved into the step's script (#1660).
    script = (CANON / "mise/vex-decisions.sh").read_text(encoding="utf-8")
    assert ".org-canon/mise/vex-decisions.sh" in step
    assert (
        '"${ORG_PYTHON}" -I "${here}/../belt" vex-one-per-merge --list "${BEFORE}.."'
        in script
    )
    assert "git diff" not in step
    assert "git diff" not in script


# The lint and vex-attest.yml print one sentence, from one file.
def test_the_workflow_reads_the_same_file() -> None:
    """The workflow names the file; its script prints it (#1660).

    Neither restates the sentence of its own.
    """
    workflow = (CANON / ".github/workflows/vex-attest.yml").read_text(encoding="utf-8")
    script = (CANON / "mise/vex-decisions.sh").read_text(encoding="utf-8")
    assert "ONE_PER_MERGE: .org-canon/mise/vex-one-per-merge.txt" in workflow
    assert ".org-canon/mise/vex-decisions.sh" in workflow
    assert 'org_error "$(< "${ONE_PER_MERGE}")"' in script
    assert SENTENCE not in workflow
    assert SENTENCE not in script
