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

stdlib `unittest`, run through the gate as `mise run test`.
"""

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import actions_lint
from tests import hermetic

if TYPE_CHECKING:
    from collections.abc import Callable

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:actions"


@pytest.fixture(autouse=True)
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


BELT = hermetic.MISE
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


class TestActionsLint:
    """One planted workflow; actionlint's verdict through the helper."""

    @pytest.fixture(autouse=True)
    def _set_up_test_actions_lint(self, tmp_path: Path) -> None:
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

    def run_helper(self, *argv: str, **env: str) -> subprocess.CompletedProcess[str]:
        """Start the helper as the task does, in the planted tree.

        Returns:
            The finished process.

        """
        base = hermetic.env(
            MISE_TASK_NAME=TASK, ORG_FINDINGS="", ORG_CANON_DIR=str(CANON_DIR)
        )
        return subprocess.run(
            [sys.executable, str(BELT / "actions_lint.py"), *argv],
            cwd=self.tree,
            env=base | env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_a_clean_workflow_passes_and_writes_nothing(self) -> None:
        """`$/` and ubuntu-26.04 pass under the two ignores; the tree is untouched."""
        rel = self.plant(CLEAN)
        before = names(self.tree)
        done = self.run_helper(rel)
        assert done.returncode == 0, done.stdout + done.stderr
        assert (done.stdout, done.stderr) == ("", "")
        assert names(self.tree) == before

    def test_a_finding_names_the_fix(self) -> None:
        """Actionlint's report, then the one finding, exit 1."""
        rel = self.plant(CLEAN.replace("echo ok", 'echo "${{ nope.x }}"'))
        done = self.run_helper(rel)
        assert done.returncode == actions_lint.EXIT_RED
        assert "nope" in done.stdout
        fix: list[str] = [FIX]
        assert done.stderr.splitlines() == fix

    def test_the_two_ignores_are_anchored(self) -> None:
        """Each pattern names its exception and no other."""
        assert actions_lint.SELF_REF == 'specifying action "\\$/'
        runner = 'label "ubuntu-26\\.04(-arm)?" is unknown'
        assert runner == actions_lint.RUNNER

    def test_no_workflow_is_a_usage_error(self) -> None:
        """No file named: exit 2, never a walk of the directory."""
        done = hermetic.call(lambda: actions_lint.main([]), cwd=self.tree)
        assert done.returncode == actions_lint.EXIT_ERROR
        usage: list[str] = ["lint:actions: usage: actions_lint.py WORKFLOW..."]
        assert done.stderr.splitlines() == usage

    def test_no_canon_tree_is_refused(self) -> None:
        """No ORG_CANON_DIR: exit 2, named."""
        rel = self.plant(CLEAN)
        done = self.run_helper(rel, ORG_CANON_DIR="")
        assert done.returncode == actions_lint.EXIT_ERROR
        refused: list[str] = [
            (
                "lint:actions: ORG_CANON_DIR is unset — no canon tree for"
                " shellcheck to follow"
            )
        ]
        assert done.stderr.splitlines() == refused

    def test_no_actionlint_is_refused(self) -> None:
        """Actionlint not found: exit 2, named, never a pass."""
        rel = self.plant(CLEAN)
        missing = str(self.tree / "no-such-actionlint")
        env = hermetic.env(
            MISE_TASK_NAME=TASK, ORG_FINDINGS="", ORG_CANON_DIR=str(CANON_DIR)
        )
        with pytest.MonkeyPatch.context() as patched:
            patched.setattr(actions_lint, "ACTIONLINT", missing)
            done = hermetic.call(
                lambda: actions_lint.main([rel]), env=env, cwd=self.tree
            )
        assert done.returncode == actions_lint.EXIT_ERROR
        missing_tool: list[str] = ["lint:actions: actionlint is not on PATH"]
        assert done.stderr.splitlines() == missing_tool
