#!/usr/bin/env python3
"""Table tests for lint:installer-pinned and its helper (#1911).

Each row plants workflows and asserts the exit status and every line of
the message: each way a workflow holds (no mise-action step, as many
pins as steps, more pins than steps, a step only in a comment), each way
it falls short (no pin, fewer pins than steps, a pin only in a comment),
a file that is not UTF-8, and a call with no workflows. Two rows run the
task's own body, read from mise/config.toml: the skip when there are no
tracked workflows, and a shortfall end to end; the rest call the
helper's `main` in this process, in the planted tree.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom. stdlib
`unittest`, run through the gate as `mise run test`.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic
import installer_pinned

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:installer-pinned"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


BELT = Path(__file__).resolve().parent
GIT = hermetic.tool("git")
OK = ["lint:installer-pinned: ok"]
STEP = "      - uses: jdx/mise-action@0123456789abcdef0123456789abcdef01234567\n"
PIN = "        with:\n          version: 2026.11.3\n"


def same[T](case: unittest.TestCase, got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    case.assertEqual(got, want, msg or None)


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


class Pinned(unittest.TestCase):
    """Planted workflows; the installer pin's verdict on them."""

    @override
    def setUp(self) -> None:
        """Plant an empty workflows directory."""
        self.tree = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tree)
        (self.tree / ".github" / "workflows").mkdir(parents=True)
        self.env = hermetic.env(
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

    def helper(self, *files: str) -> hermetic.Called:
        """Call the helper's main in the planted tree.

        Returns:
            The status and what it printed.

        """
        argv = list(files)
        return hermetic.call_main(
            lambda: installer_pinned.main(argv), env=self.env, cwd=self.tree
        )

    def task(self, *tracked: str) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree, git tracking some files.

        Returns:
            The finished process.

        """
        subprocess.run([GIT, "init", "-q"], cwd=self.tree, env=self.env, check=True)
        if tracked:
            add = [GIT, "add", "--", *tracked]
            subprocess.run(add, cwd=self.tree, env=self.env, check=True)
        as_mise = hermetic.task_run(TASK, body())
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_no_tracked_workflows_is_skipped_by_name(self) -> None:
        """An untracked workflow only: the task says it skipped."""
        self.plant("loose.yml", STEP)
        done = self.task()
        same(self, done.returncode, 0, done.stderr)
        skipped = ["lint:installer-pinned: no workflows, skipped"]
        same(self, done.stdout.splitlines(), skipped)

    def test_shortfall_is_red_through_the_task(self) -> None:
        """The task body hands the helper the tracked workflows only."""
        good = self.plant("good.yml", STEP + PIN)
        bad = self.plant("bad.yaml", STEP)
        self.plant("loose.yml", STEP)
        done = self.task(good, bad)
        same(self, done.returncode, installer_pinned.EXIT_RED)
        same(self, done.stderr.splitlines(), red(f"{bad}(1 steps, 0 version pins)"))

    def test_holding_workflows_are_ok(self) -> None:
        """No step, a pin per step, spare pins, a step in a comment: ok."""
        rows = {
            "no step": "on: push\n",
            "pinned": STEP + PIN,
            "tabs, no final newline": "uses:\tjdx/mise-action@v3\nversion:\t2026.1.1",
            "spare pins": STEP + PIN + PIN,
            "step in a comment": "# uses: jdx/mise-action@v3\n",
            "step after a hash": "x: 1 # uses: jdx/mise-action@v3\n",
        }
        for row, text in rows.items():
            with self.subTest(row=row):
                rel = self.plant("wf.yml", text)
                done = self.helper(rel)
                same(self, done.returncode, 0, done.stderr)
                same(self, done.stdout.splitlines(), OK)
                same(self, done.stderr, "")

    def test_shortfalls_are_red_with_counts(self) -> None:
        """No pin, too few, a pin in prose or short: each named, in order."""
        bare = self.plant("bare.yml", STEP)
        few = self.plant("few.yml", STEP + PIN + STEP)
        prose = self.plant("prose.yml", STEP + "  # version: 2026.11.3\n")
        short = self.plant("short.yml", STEP + "version: 2026.1\n")
        clean = self.plant("clean.yml", STEP + PIN)
        done = self.helper(bare, clean, few, prose, short)
        same(self, done.returncode, installer_pinned.EXIT_RED)
        want = red(
            f"{bare}(1 steps, 0 version pins)",
            f"{few}(2 steps, 1 version pins)",
            f"{prose}(1 steps, 0 version pins)",
            f"{short}(1 steps, 0 version pins)",
        )
        same(self, done.stderr.splitlines(), want)
        same(self, done.stdout, "")

    def test_not_utf8_is_an_error(self) -> None:
        """A workflow that is not UTF-8 is refused by name, exit 2."""
        rel = self.plant("bad.yml", b"on: \xff\n")
        done = self.helper(rel)
        same(self, done.returncode, installer_pinned.EXIT_ERROR)
        self.assertTrue(
            done.stderr.startswith(f"{TASK}: {rel}: cannot be read as UTF-8:"),
            done.stderr,
        )

    def test_no_workflows_is_a_usage_error(self) -> None:
        """No workflow named: exit 2 with the usage line."""
        done = self.helper()
        same(self, done.returncode, installer_pinned.EXIT_ERROR)
        usage = f"{TASK}: usage: installer_pinned.py WORKFLOW..."
        same(self, done.stderr.splitlines(), [usage])


if __name__ == "__main__":
    unittest.main()
