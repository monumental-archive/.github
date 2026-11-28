"""Table tests for mise/actions_lint.py, lint:actions' call (#2007).

A row per branch, each asserting its message: a clean workflow passes
with nothing written in the tree, a finding is actionlint's report and
then the one finding naming the fix, both `-ignore` patterns reach
actionlint (the `$/` self-reference and the ubuntu-26.04 label pass), no
workflow named, no ORG_CANON_DIR, and no actionlint on PATH. The
`.org-canon` link and the shellcheck it serves are held end to end by
mise/test_action_lints.py; the untracked-file rows by
mise/test_workflow_files.py.

The rows that run actionlint use the belt's, so this file runs under the
belt's tools (`mise run test`, or `mise x --`).
"""

from typing import TYPE_CHECKING

import pytest

from belt import actions_lint
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:actions"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
CANON_DIR = BELT.parent
CLEAN = (
    "name: clean\non: push\npermissions: {}\njobs:\n  a:\n"
    "    runs-on: ubuntu-26.04\n    steps:\n"
    "      - uses: $/.github/actions/canon\n      - run: echo ok\n"
)
FIX = (
    "lint:actions: actionlint findings above — fixed by hand; fix:actions only"
    " re-pins uses: (pinact)"
)


def names(tree: Path) -> list[str]:
    """List every path under a tree, sorted.

    Returns:
        Each path's name.

    """
    found: list[str] = [p.name for p in tree.rglob("*")]
    return sorted(found)


class ActionsLint:
    """One planted workflow; actionlint's verdict through the helper."""

    def __init__(self, tmp_path: Path) -> None:
        """Plant an empty workflows directory."""
        self.tree = tmp_path
        (self.tree / ".github" / "workflows").mkdir(parents=True)

    def plant(self, text: str) -> str:
        """Write the one workflow.

        Returns:
            Its path from the tree's top.

        """
        rel = ".github/workflows/w.yml"
        (self.tree / rel).write_text(text, encoding="utf-8")
        return rel

    def enter(self, monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
        """Set the task's world in the planted tree."""
        for name, value in ({"ORG_CANON_DIR": str(CANON_DIR)} | env).items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(self.tree)


@fixture
def actions_lint_state(tmp_path: Path) -> ActionsLint:
    """Give each row its own ActionsLint."""
    return ActionsLint(tmp_path)


def test_a_clean_workflow_passes_and_writes_nothing(
    actions_lint_state: ActionsLint,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """`$/` and ubuntu-26.04 pass under the two ignores; the tree is untouched."""
    rel = actions_lint_state.plant(CLEAN)
    before = names(actions_lint_state.tree)
    actions_lint_state.enter(monkeypatch)
    status = actions_lint.main([rel])
    out, err = capfd.readouterr()
    assert status == 0, out + err
    assert (out, err) == ("", "")
    assert names(actions_lint_state.tree) == before


def test_a_finding_names_the_fix(
    actions_lint_state: ActionsLint,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Actionlint's report, then the one finding, exit 1."""
    rel = actions_lint_state.plant(CLEAN.replace("echo ok", 'echo "${{ nope.x }}"'))
    actions_lint_state.enter(monkeypatch)
    status = actions_lint.main([rel])
    out, err = capfd.readouterr()
    assert status == actions_lint.EXIT_RED
    assert "nope" in out
    fix: list[str] = [FIX]
    assert err.splitlines() == fix


@pytest.mark.usefixtures("actions_lint_state")
def test_the_two_ignores_are_anchored() -> None:
    """Each pattern names its exception and no other."""
    assert actions_lint.SELF_REF == 'specifying action "\\$/'
    runner = 'label "ubuntu-26\\.04(-arm)?" is unknown'
    assert runner == actions_lint.RUNNER


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param(
            [],
            "belt actions-lint: error: the following arguments are required: WORKFLOW",
            id="nothing",
        ),
    ],
)
def test_no_workflow_is_a_usage_error(
    argv: list[str], error: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """No file named: exit 2, never a walk of the directory: argparse's exit 2, its usage and its error."""
    with pytest.raises(SystemExit) as ended:
        actions_lint.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt actions-lint "), err
    assert err.splitlines()[-1] == error


def test_no_canon_tree_is_refused(
    actions_lint_state: ActionsLint,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """No ORG_CANON_DIR: exit 2, named."""
    rel = actions_lint_state.plant(CLEAN)
    actions_lint_state.enter(monkeypatch, ORG_CANON_DIR="")
    status = actions_lint.main([rel])
    out, err = capfd.readouterr()
    assert status == actions_lint.EXIT_ERROR
    refused: list[str] = [
        (
            "lint:actions: ORG_CANON_DIR is unset — no canon tree for"
            " shellcheck to follow"
        )
    ]
    assert err.splitlines() == refused


def test_no_actionlint_is_refused(
    actions_lint_state: ActionsLint,
    capfd: pytest.CaptureFixture[str],
    env: support.Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Actionlint not found: exit 2, named, never a pass."""
    rel = actions_lint_state.plant(CLEAN)
    missing = str(actions_lint_state.tree / "no-such-actionlint")
    environ = env(MISE_TASK_NAME=TASK, ORG_FINDINGS="", ORG_CANON_DIR=str(CANON_DIR))
    monkeypatch.setattr(actions_lint, "ACTIONLINT", missing)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(actions_lint_state.tree)
    status = actions_lint.main([rel])
    _out, err = capfd.readouterr()
    assert status == actions_lint.EXIT_ERROR
    missing_tool: list[str] = ["lint:actions: actionlint is not on PATH"]
    assert err.splitlines() == missing_tool
