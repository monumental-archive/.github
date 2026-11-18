#!/usr/bin/env python3
"""Table tests for lint:gate-stub's concurrency requirement (#1195).

An event on a pull request that has already merged has no merge ref, so
its `github.ref` is the base branch. A stub grouping on the ref alone put
that run in the same group as the push gate on main, and
`cancel-in-progress: true` let it cancel the gate: main went red with
nothing wrong in it (edtf, run 43620265494). The stub now groups by the
pull request's number and cancels only `pull_request` runs, and the lint
holds every gate caller to both lines.

The body under test is the task's own `run` string, read out of
mise/config.toml with tomllib and run by bash in a planted tree — not a
copy of it — so a change to the lint has to face these rows. The Tera
raw markers are dropped the way mise's render drops them.

Each red row is red-when-planted and asserts the message, not just the
exit status: a planted row scored on exit alone can pass for the wrong
reason (one of the other two requirements tripping instead). The green
rows plant the canon's real stub and its real gate.yml, so the template
and the lint cannot drift apart.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import importlib.util
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

_HERMETIC = importlib.util.spec_from_file_location(
    "hermetic", Path(__file__).resolve().with_name("hermetic.py")
)
if _HERMETIC is None or _HERMETIC.loader is None:  # pragma: no cover
    msg = "cannot load mise/hermetic.py"
    raise RuntimeError(msg)
hermetic = importlib.util.module_from_spec(_HERMETIC)
sys.modules.setdefault("hermetic", hermetic)
_HERMETIC.loader.exec_module(hermetic)


CANON = Path(__file__).resolve().parent.parent
BELT = CANON / "mise"
BASH = shutil.which("bash")

STUB = (CANON / "workflow-templates" / "ci.yml").read_text(encoding="utf-8")
GATE = (CANON / ".github" / "workflows" / "gate.yml").read_text(encoding="utf-8")

GROUP = (
    "  group: ${{ github.workflow }}-"
    "${{ github.event.pull_request.number || github.ref }}\n"
)
CANCEL = "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n"
OLD_GROUP = "  group: ${{ github.workflow }}-${{ github.ref }}\n"
OLD_CANCEL = "  cancel-in-progress: true\n"

# The message, line for line: the defect, then both wanted lines verbatim.
MESSAGE = (
    "lint:gate-stub: gate caller's concurrency group is shared with events\n"
    "  on merged pull requests, which then cancel the push gate on the\n"
    "  default branch (#1195). Under concurrency: set exactly\n"
    "    group: ${{ github.workflow }}-"
    "${{ github.event.pull_request.number || github.ref }}\n"
    "    cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n"
    "  in: .github/workflows/gate.yml\n"
)


def body() -> str:
    """Read the lint's run string as mise would render it.

    Returns:
        The task body with the `{% raw %}` / `{% endraw %}` lines removed.

    """
    config = tomllib.loads((BELT / "config.toml").read_text(encoding="utf-8"))
    run = config["tasks"]["lint:gate-stub"]["run"]
    return "".join(
        line
        for line in run.splitlines(keepends=True)
        if line.strip() not in {"{% raw %}", "{% endraw %}"}
    )


BODY = body()


def lint(workflow: str) -> subprocess.CompletedProcess[str]:
    """Run the lint against one planted gate caller.

    Returns:
        The finished process, stdout and stderr captured as text.

    """
    with tempfile.TemporaryDirectory() as tmp:
        flows = Path(tmp) / ".github" / "workflows"
        flows.mkdir(parents=True)
        (flows / "gate.yml").write_text(workflow, encoding="utf-8")
        env = hermetic.env()
        env |= {"ORG_BELT_DIR": str(BELT), "ORG_CANON_DIR": str(CANON)}
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, "-euo", "pipefail", "-c", BODY],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


def planted(old: str, new: str) -> str:
    """Return the canon's stub with one line swapped.

    Returns:
        The stub text with the swap made.

    Raises:
        ValueError: the line to swap is not in the stub exactly once, so
            the row would plant nothing.

    """
    if STUB.count(old) != 1:
        message = f"the stub no longer carries exactly one {old!r}"
        raise ValueError(message)
    return STUB.replace(old, new)


class Green(unittest.TestCase):
    """The shapes the lint must pass."""

    def assert_ok(self, workflow: str) -> None:
        """Assert a clean pass with the ok line and nothing on stderr."""
        done = lint(workflow)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "lint:gate-stub: ok\n")
        self.assertEqual(done.stderr, "")

    def test_the_canons_stub_passes(self) -> None:
        """The template every repository copies is clean."""
        self.assert_ok(STUB)

    def test_the_canons_own_gate_passes(self) -> None:
        """The canon's self-applied gate is clean."""
        self.assert_ok(GATE)

    def test_a_trailing_comment_on_cancel_passes(self) -> None:
        """A comment after the wanted line does not change it."""
        self.assert_ok(planted(CANCEL, CANCEL.rstrip("\n") + " # see #1195\n"))

    def test_a_workflow_that_is_not_a_gate_caller_is_not_held(self) -> None:
        """Only a caller of ci.yml owes the group."""
        other = planted(
            "uses: monumental-archive/.github/.github/workflows/ci.yml@",
            "uses: monumental-archive/.github/.github/workflows/audit.yml@",
        )
        self.assert_ok(other.replace(GROUP, OLD_GROUP).replace(CANCEL, OLD_CANCEL))


class Red(unittest.TestCase):
    """Each planted defect reds with the stated message."""

    def assert_red(self, workflow: str) -> None:
        """Assert exit 1 and exactly the concurrency message on stderr."""
        done = lint(workflow)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(done.stderr, MESSAGE)
        self.assertEqual(done.stdout, "")

    def test_the_pre_1195_stub_is_red(self) -> None:
        """The stub every repository carried before #1195."""
        self.assert_red(planted(GROUP, OLD_GROUP).replace(CANCEL, OLD_CANCEL))

    def test_a_group_on_the_ref_alone_is_red(self) -> None:
        """The shared group alone, cancel already right."""
        self.assert_red(planted(GROUP, OLD_GROUP))

    def test_cancel_true_is_red(self) -> None:
        """A push to main may still be cancelled."""
        self.assert_red(planted(CANCEL, OLD_CANCEL))

    def test_cancel_false_is_red(self) -> None:
        """Five pushes to a pull request would run five gates."""
        self.assert_red(planted(CANCEL, "  cancel-in-progress: false\n"))

    def test_a_missing_cancel_line_is_red(self) -> None:
        """Absent is not the expression."""
        self.assert_red(planted(CANCEL, ""))

    def test_a_missing_concurrency_block_is_red(self) -> None:
        """No group at all is not the group."""
        self.assert_red(
            planted("concurrency:\n" + GROUP + CANCEL, ""),
        )

    def test_a_second_cancel_line_that_disagrees_is_red(self) -> None:
        """One right line does not excuse a wrong one."""
        self.assert_red(
            STUB.replace(
                "    uses: monumental-archive",
                "    concurrency:\n"
                "      group: x\n"
                "      cancel-in-progress: true\n"
                "    uses: monumental-archive",
            ),
        )


if __name__ == "__main__":
    unittest.main()
