"""Table tests for lint:installer-pinned and its helper (#1911).

Each row plants workflows and asserts the exit status and every line of
the message: each way a workflow holds (no mise-action step, as many
pins as steps, more pins than steps, a step only in a comment), each way
it falls short (no pin, fewer pins than steps, a pin only in a comment),
a file that is not UTF-8, and a call with no workflows. Two rows run the
task's own body, read from mise/config.toml: the skip when there are no
tracked workflows, and a shortfall end to end; the rest call the
helper's `main` in this process, in the planted tree.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import decoded, installer_pinned
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:installer-pinned"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
GIT = support.tool("git")
OK = ["lint:installer-pinned: ok"]
STEP = "      - uses: jdx/mise-action@0123456789abcdef0123456789abcdef01234567\n"
PIN = "        with:\n          version: 2026.11.3\n"


def same[T](got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    assert got == want, msg or None


def body() -> str:
    """Read lint:installer-pinned's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


def red(*short: str) -> list[str]:
    """Build the red message naming each short workflow with its counts.

    Returns:
        Its lines.

    """
    named = "".join(f" {s}" for s in short)
    return [
        f"lint:installer-pinned: mise-action without a mise version pin:{named}",
        "  the installer is a build input — an unpinned mise delivers",
        "  same-day releases straight into attested builds (v0.18.2 darwin leg)",
    ]


class Pinned:
    """Planted workflows; the installer pin's verdict on them."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant an empty workflows directory."""
        self.make_env = env
        self.tree = tmp_path
        (self.tree / ".github" / "workflows").mkdir(parents=True)
        self.env = self.make_env(
            MISE_TASK_NAME=TASK,
            ORG_BELT_DIR=str(BELT),
            ORG_PYTHON=sys.executable,
        )

    def plant(self, name: str, text: str | bytes) -> str:
        """Write one workflow.

        Returns:
            Its path from the tree's top.

        """
        rel = f".github/workflows/{name}"
        path = self.tree / rel
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        return rel

    def helper(
        self, *files: str, capfd: pytest.CaptureFixture[str]
    ) -> tuple[int, str, str]:
        """Call the helper's main in the planted tree.

        Returns:
            The status and what it printed.

        """
        argv = list(files)
        with pytest.MonkeyPatch.context() as patched:
            for name, value in self.env.items():
                patched.setenv(name, value)
            patched.chdir(self.tree)
            status = installer_pinned.main(argv)
        out, err = capfd.readouterr()
        return status, out, err

    def task(self, *tracked: str) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree, git tracking some files.

        Returns:
            The finished process.

        """
        subprocess.run([GIT, "init", "-q"], cwd=self.tree, env=self.env, check=True)
        if tracked:
            add = [GIT, "add", "--", *tracked]
            subprocess.run(add, cwd=self.tree, env=self.env, check=True)
        as_mise = support.task_run(TASK, body())
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


@fixture
def pinned(tmp_path: Path, env: support.Env) -> Pinned:
    """Give each row its own Pinned."""
    return Pinned(tmp_path, env=env)


def test_no_tracked_workflows_is_skipped_by_name(pinned: Pinned) -> None:
    """An untracked workflow only: the task says it skipped."""
    pinned.plant("loose.yml", STEP)
    done = pinned.task()
    same(done.returncode, 0, done.stderr)
    skipped = ["lint:installer-pinned: no workflows, skipped"]
    same(done.stdout.splitlines(), skipped)


def test_shortfall_is_red_through_the_task(pinned: Pinned) -> None:
    """The task body hands the helper the tracked workflows only."""
    good = pinned.plant("good.yml", STEP + PIN)
    bad = pinned.plant("bad.yaml", STEP)
    pinned.plant("loose.yml", STEP)
    done = pinned.task(good, bad)
    same(done.returncode, installer_pinned.EXIT_RED)
    same(done.stderr.splitlines(), red(f"{bad}(1 steps, 0 version pins)"))


def test_holding_workflows_are_ok(
    pinned: Pinned, capfd: pytest.CaptureFixture[str]
) -> None:
    """No step, a pin per step, spare pins, a step in a comment: ok."""
    rows = {
        "no step": "on: push\n",
        "pinned": STEP + PIN,
        "tabs, no final newline": "uses:\tjdx/mise-action@v3\nversion:\t2026.1.1",
        "spare pins": STEP + PIN + PIN,
        "step in a comment": "# uses: jdx/mise-action@v3\n",
        "step after a hash": "x: 1 # uses: jdx/mise-action@v3\n",
    }
    for text in rows.values():
        rel = pinned.plant("wf.yml", text)
        status, out, err = pinned.helper(rel, capfd=capfd)
        same(status, 0, err)
        same(out.splitlines(), OK)
        same(err, "")


def test_shortfalls_are_red_with_counts(
    pinned: Pinned, capfd: pytest.CaptureFixture[str]
) -> None:
    """No pin, too few, a pin in prose or short: each named, in order."""
    bare = pinned.plant("bare.yml", STEP)
    few = pinned.plant("few.yml", STEP + PIN + STEP)
    prose = pinned.plant("prose.yml", STEP + "  # version: 2026.11.3\n")
    short = pinned.plant("short.yml", STEP + "version: 2026.1\n")
    clean = pinned.plant("clean.yml", STEP + PIN)
    status, out, err = pinned.helper(bare, clean, few, prose, short, capfd=capfd)
    same(status, installer_pinned.EXIT_RED)
    want = red(
        f"{bare}(1 steps, 0 version pins)",
        f"{few}(2 steps, 1 version pins)",
        f"{prose}(1 steps, 0 version pins)",
        f"{short}(1 steps, 0 version pins)",
    )
    same(err.splitlines(), want)
    same(out, "")


def test_not_utf8_is_an_error(
    pinned: Pinned, capfd: pytest.CaptureFixture[str]
) -> None:
    """A workflow that is not UTF-8 is refused by name, exit 2."""
    rel = pinned.plant("bad.yml", b"on: \xff\n")
    status, _out, err = pinned.helper(rel, capfd=capfd)
    same(status, installer_pinned.EXIT_ERROR)
    assert err.startswith(f"{TASK}: {rel}: cannot be read as UTF-8:"), err


def test_no_workflows_is_a_usage_error(
    pinned: Pinned, capfd: pytest.CaptureFixture[str]
) -> None:
    """No workflow named: exit 2 with the usage line."""
    status, _out, err = pinned.helper(capfd=capfd)
    same(status, installer_pinned.EXIT_ERROR)
    usage = f"{TASK}: usage: installer_pinned.py WORKFLOW..."
    same(err.splitlines(), [usage])
