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

    def enter(self, monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
        """Stand in the tree under `env`, as a step calls the helper (#1697)."""
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(self.dir)


@fixture
def tree(tmp_path: Path, env: support.Env) -> Tree:
    """Plant a tree whose default branch holds one decision.

    Returns:
        The tree.

    """
    planted = Tree(Path(str(tmp_path)), env=env)
    planted.commit(A, README)
    planted.mark_main()
    return planted


def test_one_changed_decision_passes(
    tree: Tree, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new decision alone is green and counted."""
    tree.commit(B)
    tree.enter(monkeypatch, tree.task_env(""))
    status = vex_one_per_merge.main([])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert "1 VEX decision(s) changed ahead of origin/main" in out


def test_two_are_red_naming_both(
    tree: Tree, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two decisions over two commits: red, with the sentence and both files."""
    tree.commit(B)
    tree.commit(A)
    tree.enter(monkeypatch, tree.task_env(""))
    status = vex_one_per_merge.main([])
    out, err = capfd.readouterr()
    assert status == 1, out
    assert f"{TASK}: {SENTENCE}:" in err
    assert f"  {A}\n" in err
    assert f"  {B}\n" in err


def test_a_non_decision_file_is_not_counted(
    tree: Tree, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The README beside one decision changes nothing about the count."""
    tree.commit(B, README)
    tree.enter(monkeypatch, tree.task_env(""))
    status = vex_one_per_merge.main([])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert "1 VEX decision(s) changed" in out


def test_a_deletion_is_not_counted(
    tree: Tree, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing a decision signs nothing, so it does not count."""
    tree.commit(remove=(A,))
    tree.commit(B)
    tree.enter(monkeypatch, tree.task_env(""))
    status = vex_one_per_merge.main([])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert "1 VEX decision(s) changed" in out


def test_a_rename_is_counted_at_its_new_name(
    tree: Tree, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decision moved to a new name, beside another, is two."""
    tree.git("mv", A, C)
    tree.commit(B)
    tree.enter(monkeypatch, tree.task_env(""))
    status = vex_one_per_merge.main([])
    out, err = capfd.readouterr()
    assert status == 1, out
    assert f"  {B}\n" in err
    assert f"  {C}\n" in err


def test_main_moving_on_is_not_counted(
    tree: Tree, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decision main gained after the branch point is not the branch's."""
    tree.commit(B)
    tree.git("switch", "-q", "-c", "main", "origin/main")
    tree.commit(C)
    tree.mark_main()
    tree.git("switch", "-q", "feature")
    tree.enter(monkeypatch, tree.task_env(""))
    status = vex_one_per_merge.main([])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert "1 VEX decision(s) changed" in out


def test_a_merge_commit_reads_its_second_parent(
    tree: Tree, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a pull request's synthetic merge, the branch's commits are judged."""
    tree.commit(B)
    tree.commit(C)
    tree.git("switch", "-q", "--detach", "origin/main")
    tree.git("merge", "-q", "--no-ff", "-m", "merge", "feature")
    tree.enter(monkeypatch, tree.task_env(""))
    status = vex_one_per_merge.main([])
    out, err = capfd.readouterr()
    assert status == 1, out
    assert f"  {B}\n" in err
    assert f"  {C}\n" in err


def test_the_default_branch_itself_skips_by_name(
    tree: Tree, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing ahead of origin/main skips, whatever main holds."""
    tree.commit(B, C)
    tree.mark_main()
    tree.enter(monkeypatch, tree.task_env(""))
    status = vex_one_per_merge.main([])
    out, err = capfd.readouterr()
    assert status == 0, err
    skipped = "nothing ahead of origin/main (the default branch itself), skipped"
    assert f"{TASK}: {skipped}" in out


def test_a_merge_group_skips_by_name(
    tree: Tree, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A group's range holds the entries queued ahead, so it is not judged."""
    tree.commit(B)
    tree.commit(C)
    tree.enter(monkeypatch, tree.task_env("merge_group"))
    status = vex_one_per_merge.main([])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert (
        f"{TASK}: judged on the pull request: a merge group's range also"
        " holds the entries queued ahead of it, skipped"
    ) in out


def test_the_same_tree_is_red_on_a_pull_request(
    tree: Tree, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tree the merge-group row skips is red as a pull request."""
    tree.commit(B)
    tree.commit(C)
    tree.enter(monkeypatch, tree.task_env("pull_request"))
    status = vex_one_per_merge.main([])
    out, err = capfd.readouterr()
    assert status == 1, out
    assert f"{TASK}: {SENTENCE}:" in err
    assert f"  {B}\n" in err
    assert f"  {C}\n" in err


def test_no_origin_main_skips_by_name(
    tree: Tree, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clone with no default branch to diverge from skips."""
    tree.git("update-ref", "-d", "refs/remotes/origin/main")
    tree.enter(monkeypatch, tree.task_env(""))
    status = vex_one_per_merge.main([])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert f"{TASK}: no origin/main, skipped" in out


# A tree that keeps no decisions has nothing to judge.
def test_none_tracked_skips_by_name(
    capfd: pytest.CaptureFixture[str], env: support.Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a README under security/vex/ skips, by name."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Tree(Path(tmp), env=env)
        tree.commit(README)
        tree.mark_main()
        tree.commit(README)
        tree.enter(monkeypatch, tree.task_env(""))
        status = vex_one_per_merge.main([])
        out, err = capfd.readouterr()
        assert status == 0, err
        assert f"{TASK}: no VEX decisions tracked, skipped" in out


# vex-attest.yml's signing list is the lint's own reader (#1615).
LIST = ["--list", "origin/main.."]


@fixture
def listing(tmp_path: Path, env: support.Env) -> Tree:
    """Plant a tree whose default branch holds two decisions.

    Returns:
        The tree.

    """
    planted = Tree(Path(str(tmp_path)), env=env)
    planted.commit(A, B, README)
    planted.mark_main()
    return planted


def test_a_rename_with_an_edit_is_listed_at_its_new_name(
    listing: Tree, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Moved and edited: new bytes at a new name, so it is signed."""
    listing.git("mv", A, C)
    listing.commit(C)
    want: list[str] = [C]
    listing.enter(monkeypatch, listing.env)
    status = vex_one_per_merge.main(LIST)
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out.splitlines() == want


def test_a_pure_rename_is_listed(
    listing: Tree, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Moved alone: listed, and the store decides if its bytes are held."""
    listing.git("mv", A, C)
    listing.commit()
    want: list[str] = [C]
    listing.enter(monkeypatch, listing.env)
    status = vex_one_per_merge.main(LIST)
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out.splitlines() == want


def test_a_deletion_is_not_listed(
    listing: Tree, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removed: nothing to sign."""
    listing.commit(remove=(A,))
    none: list[str] = []
    listing.enter(monkeypatch, listing.env)
    status = vex_one_per_merge.main(LIST)
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out.splitlines() == none


def test_the_count_and_the_listing_agree(
    listing: Tree, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two changed: the gate reds on two, and the listing names the two."""
    listing.commit(A, B)
    want: list[str] = [A, B]
    listing.enter(monkeypatch, listing.env)
    status = vex_one_per_merge.main(LIST)
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out.splitlines() == want
    listing.enter(monkeypatch, listing.task_env(""))
    status = vex_one_per_merge.main([])
    out, err = capfd.readouterr()
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
