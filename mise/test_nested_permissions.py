#!/usr/bin/env python3
"""Table tests for lint:nested-permissions and its helper (#1911).

Each row plants workflows and asserts the exit status and every line of
the message: a `uses:` job that states its permissions, one that does
not (first, middle and last in the file), a file with no `uses:` job
(not counted), the job keys before `jobs:` (not jobs), a second
`jobs:` line, a key with trailing blanks or a carriage return, a file
that is not UTF-8, a missing file, and a call with no workflows. Two
rows run the task's own body, read from mise/config.toml: the skip when
there are no workflows, and an offender end to end; the rest call the
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
import nested_permissions

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:nested-permissions"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


BELT = Path(__file__).resolve().parent
CALL = "    uses: ./.github/workflows/ci.yml\n"
GRANT = "    permissions:\n      contents: read\n"
STEP = "    runs-on: ubuntu-26.04\n    steps:\n      - run: true\n"
TAIL = [
    "  it inherits the workflow-level default, not the caller grant —",
    "  state exactly what the called workflow needs, or the run dies as",
    "  startup_failure with no jobs and no log.",
]


def same[T](case: unittest.TestCase, got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    case.assertEqual(got, want, msg or None)


def body() -> str:
    """Read lint:nested-permissions' run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


def ok(checked: int) -> list[str]:
    """Build the summary line for some checked workflows.

    Returns:
        Its lines.

    """
    return [f"lint:nested-permissions: {checked} workflow(s) with uses: jobs clean"]


def red(*offenders: str) -> list[str]:
    """Build the red message naming some offenders.

    Returns:
        Its lines.

    """
    head = "lint:nested-permissions: nested uses: job with no permissions block:"
    return [head, *(f"  {o}" for o in offenders), *TAIL]


class Nested(unittest.TestCase):
    """Planted workflows; the lint's verdict on them."""

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
            lambda: nested_permissions.main(argv), env=self.env, cwd=self.tree
        )

    def task(self) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree.

        Returns:
            The finished process.

        """
        hermetic.track(self.tree)
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
        skipped = ["lint:nested-permissions: no workflows, skipped"]
        same(self, done.stdout.splitlines(), skipped)

    def test_offender_is_red_through_the_task(self) -> None:
        """The task body hands the helper every workflow; an offender is red."""
        self.plant("clean.yml", "jobs:\n  ci:\n" + CALL + GRANT)
        bad = self.plant("stub.yaml", "jobs:\n  ci:\n" + CALL)
        done = self.task()
        same(self, done.returncode, nested_permissions.EXIT_RED)
        same(self, done.stderr.splitlines(), red(f"{bad}:ci"))

    def test_stated_permissions_are_clean(self) -> None:
        """Every uses: job states permissions; only files with one count."""
        one = self.plant("one.yml", "jobs:\n  ci:\n" + CALL + GRANT)
        two = self.plant(
            "two.yml",
            "jobs:\n  a:\n" + GRANT + CALL + "  b:\n" + STEP + "  c:\n" + CALL + GRANT,
        )
        none = self.plant("none.yml", "jobs:\n  build:\n" + STEP)
        done = self.helper(one, two, none)
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), ok(2))
        same(self, done.stderr, "")

    def test_offenders_are_named_in_file_order(self) -> None:
        """First, middle and last jobs offend: each named, file by file."""
        first = self.plant(
            "first.yml",
            "jobs:\n  a:\n" + CALL + "  b:\n" + CALL + GRANT + "  c:\n" + CALL,
        )
        second = self.plant("second.yml", "jobs:\n  m:\n" + STEP + "  n:\n" + CALL)
        done = self.helper(first, second)
        same(self, done.returncode, nested_permissions.EXIT_RED)
        same(
            self,
            done.stderr.splitlines(),
            red(f"{first}:a", f"{first}:c", f"{second}:n"),
        )
        same(self, done.stdout, "")

    def test_keys_before_jobs_are_not_jobs(self) -> None:
        """A two-space key above `jobs:` opens no job."""
        text = "on:\n  workflow_call:\n" + CALL + "jobs:\n  ci:\n" + CALL + GRANT
        rel = self.plant("wf.yml", text)
        done = self.helper(rel)
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), ok(1))

    def test_a_second_jobs_line_closes_nothing(self) -> None:
        """A repeated `jobs:` line leaves the open job open."""
        text = "jobs:\n  ci:\n" + CALL + "jobs:\n" + GRANT
        rel = self.plant("wf.yml", text)
        done = self.helper(rel)
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), ok(1))

    def test_trailing_blanks_and_carriage_returns(self) -> None:
        """Trailing blanks still open a job; a carriage return stays in its name.

        awk's first field keeps the carriage return, so its `:` is not
        the last character and stays; the writer prints it escaped.
        """
        blanks = self.plant("blanks.yml", "jobs: \t\n  ci:  \n" + CALL)
        crlf = self.plant("crlf.yml", "jobs:\r\n  ci:\r\n" + CALL)
        done = self.helper(blanks, crlf)
        same(self, done.returncode, nested_permissions.EXIT_RED)
        same(self, done.stderr.splitlines(), red(f"{blanks}:ci", f"{crlf}:ci:\\r"))

    def test_not_a_job_key(self) -> None:
        """Deeper, shallower or valued keys open no job."""
        text = "jobs:\n  ci:\n" + GRANT + "  x: y\n   deep:\n" + CALL
        rel = self.plant("wf.yml", text)
        done = self.helper(rel)
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), ok(1))

    def test_not_utf8_is_an_error(self) -> None:
        """A workflow that is not UTF-8 is refused by name, exit 2."""
        rel = self.plant("bad.yml", b"jobs: \xff\n")
        done = self.helper(rel)
        same(self, done.returncode, nested_permissions.EXIT_ERROR)
        self.assertTrue(
            done.stderr.startswith(
                f"lint:nested-permissions: {rel}: cannot be read as UTF-8:"
            ),
            done.stderr,
        )

    def test_missing_file_is_an_error(self) -> None:
        """A named workflow that does not exist is refused by name, exit 2."""
        done = self.helper(".github/workflows/gone.yml")
        same(self, done.returncode, nested_permissions.EXIT_ERROR)
        self.assertTrue(
            done.stderr.startswith(
                "lint:nested-permissions: .github/workflows/gone.yml:"
                " cannot be read as UTF-8:"
            ),
            done.stderr,
        )

    def test_no_workflows_is_a_usage_error(self) -> None:
        """No workflow named: exit 2 with the usage line."""
        done = self.helper()
        same(self, done.returncode, nested_permissions.EXIT_ERROR)
        usage = "lint:nested-permissions: usage: nested_permissions.py WORKFLOW..."
        same(self, done.stderr.splitlines(), [usage])


if __name__ == "__main__":
    unittest.main()
