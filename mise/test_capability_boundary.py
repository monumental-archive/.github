#!/usr/bin/env python3
"""Table tests for lint:capability-boundary and its helper (#1911).

Each row plants workflows and asserts the exit status and every line of
the message: each way a workflow stays inside the boundary (not
called, no grant, a grant only in prose, a first-token marker), each
way it breaches it (no marker, a marker only in prose), a file that is
not UTF-8, and a call with no workflows. Two rows run the task's own
body, read from mise/config.toml: the skip when there are no
workflows, and a breach end to end; the rest call the helper's `main`
in this process, in the planted tree.

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

import capability_boundary
import decoded
import hermetic

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:capability-boundary"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


BELT = Path(__file__).resolve().parent
OK = ["lint:capability-boundary: ok"]
CALLED = "on:\n  workflow_call:\n"
GRANT = "permissions:\n  id-token: write\n"
MARKER = "# capability-boundary: signs only; runs no caller step\n"
JOBS = "jobs:\n  a:\n    runs-on: ubuntu-26.04\n    steps:\n      - run: true\n"


def same[T](case: unittest.TestCase, got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    case.assertEqual(got, want, msg or None)


def body() -> str:
    """Read lint:capability-boundary's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


def red(*files: str) -> list[str]:
    """Build the red message naming some workflows.

    Returns:
        Its lines.

    """
    named = "".join(f" {f}" for f in files)
    return [
        "lint:capability-boundary: workflow_call workflow holds id-token",
        f"  without a capability-boundary marker:{named}",
    ]


class Boundary(unittest.TestCase):
    """Planted workflows; the boundary's verdict on them."""

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
            lambda: capability_boundary.main(argv), env=self.env, cwd=self.tree
        )

    def task(self) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree.

        Returns:
            The finished process.

        """
        as_mise = hermetic.task_run(TASK, body())
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_no_workflows_is_skipped_by_name(self) -> None:
        """No workflow file: the task says it skipped."""
        done = self.task()
        same(self, done.returncode, 0, done.stderr)
        skipped = ["lint:capability-boundary: no workflows, skipped"]
        same(self, done.stdout.splitlines(), skipped)

    def test_breach_is_red_through_the_task(self) -> None:
        """The task body hands the helper every workflow; a breach is red."""
        self.plant("clean.yml", CALLED + JOBS)
        bad = self.plant("sign.yaml", CALLED + GRANT + JOBS)
        done = self.task()
        same(self, done.returncode, capability_boundary.EXIT_RED)
        same(self, done.stderr.splitlines(), red(bad))

    def test_inside_the_boundary_is_ok(self) -> None:
        """Not called, no grant, a grant in prose, a marked signer: ok."""
        rows = {
            "not called": "on: push\n" + GRANT + JOBS,
            "no grant": CALLED + JOBS,
            "grant in a comment": CALLED + "# no id-token: write, ever\n" + JOBS,
            "grant after a hash": CALLED + "x: 1 # id-token: write\n" + JOBS,
            "marked": CALLED + GRANT + MARKER + JOBS,
            "marked, indented": CALLED + GRANT + JOBS + "  #  " + MARKER[2:],
        }
        for row, text in rows.items():
            with self.subTest(row=row):
                rel = self.plant("wf.yml", text)
                done = self.helper(rel)
                same(self, done.returncode, 0, done.stderr)
                same(self, done.stdout.splitlines(), OK)
                same(self, done.stderr, "")

    def test_breaches_are_red_and_named(self) -> None:
        """No marker, or a marker only in prose: red, every file named."""
        bare = self.plant("bare.yml", CALLED + GRANT + JOBS)
        prose = self.plant(
            "prose.yml",
            CALLED + GRANT + "# see the capability-boundary: rule\n" + JOBS,
        )
        tabbed = self.plant("tabbed.yml", "on:\n\tworkflow_call:\n\tid-token:\twrite\n")
        clean = self.plant("clean.yml", CALLED + JOBS)
        done = self.helper(bare, clean, prose, tabbed)
        same(self, done.returncode, capability_boundary.EXIT_RED)
        same(self, done.stderr.splitlines(), red(bare, prose, tabbed))
        same(self, done.stdout, "")

    def test_a_line_is_split_on_newline_alone(self) -> None:
        """A carriage return or form feed inside a line does not end it."""
        text = "on:\n  workflow_call:\nx: '#'\rid-token: write\n"
        rel = self.plant("cr.yml", text)
        done = self.helper(rel)
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), OK)

    def test_not_utf8_is_an_error(self) -> None:
        """A workflow that is not UTF-8 is refused by name, exit 2."""
        rel = self.plant("bad.yml", b"on: \xff\n")
        done = self.helper(rel)
        same(self, done.returncode, capability_boundary.EXIT_ERROR)
        self.assertTrue(
            done.stderr.startswith(
                f"lint:capability-boundary: {rel}: cannot be read as UTF-8:"
            ),
            done.stderr,
        )

    def test_missing_file_is_an_error(self) -> None:
        """A named workflow that does not exist is refused by name, exit 2."""
        done = self.helper(".github/workflows/gone.yml")
        same(self, done.returncode, capability_boundary.EXIT_ERROR)
        self.assertTrue(
            done.stderr.startswith(
                "lint:capability-boundary: .github/workflows/gone.yml:"
                " cannot be read as UTF-8:"
            ),
            done.stderr,
        )

    def test_no_workflows_is_a_usage_error(self) -> None:
        """No workflow named: exit 2 with the usage line."""
        done = self.helper()
        same(self, done.returncode, capability_boundary.EXIT_ERROR)
        usage = "lint:capability-boundary: usage: capability_boundary.py WORKFLOW..."
        same(self, done.stderr.splitlines(), [usage])


if __name__ == "__main__":
    unittest.main()
