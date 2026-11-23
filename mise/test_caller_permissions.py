#!/usr/bin/env python3
"""Table tests for lint:caller-permissions and its helper (#1911).

Each row plants a git tree and a stub `stele` on PATH that logs its
arguments, says a line on stderr, prints the row's report and exits
with the row's status, then asserts the exit status and every line
printed: the skip when the tree tracks no workflow, an untracked
workflow (still a skip), a passing join end to end, stele missing,
stele's FAIL with its findings and the fix, and a call with no canon or
no workflow. Three rows run the task's own body, read from
mise/config.toml; the rest call the helper's `main` in this process.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom. stdlib
`unittest`, run through the gate as `mise run test`.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import caller_permissions
import decoded
import hermetic

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:caller-permissions"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


BELT = Path(__file__).resolve().parent
GIT = hermetic.tool("git")
CANON = "/canon"
STUB = """\
import os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:
    log.write("\\t".join(sys.argv[1:]) + "\\n")
print("stub stele: walking", file=sys.stderr)
with open(os.environ["STUB_DOC"], encoding="utf-8") as doc:
    sys.stdout.write(doc.read())
sys.exit(int(os.environ["STUB_RC"]))
"""
PASS: dict[str, object] = {
    "schema": 9,
    "target": "assert permissions",
    "subject": ".",
    "verdict": "PASS",
    "findings": [],
}
FAIL: dict[str, object] = {
    "schema": 9,
    "target": "assert permissions",
    "subject": ".",
    "verdict": "FAIL",
    "findings": [
        {
            "subject": ".github/workflows/release.yml",
            "assertion": "caller-grant",
            "expected": "id-token: write",
            "actual": "absent",
        }
    ],
}
FIX = [
    "lint:caller-permissions: see above — grant what the pinned canon's",
    "  callee jobs ask for, at the caller's uses: job (#358 seam 2).",
]


def same[T](case: unittest.TestCase, got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    case.assertEqual(got, want, msg or None)


def body() -> str:
    """Read lint:caller-permissions' run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


class Planted(unittest.TestCase):
    """A planted git tree, and a stub stele first on PATH."""

    @override
    def setUp(self) -> None:
        """Plant an empty tree and the stub, which answers PASS."""
        self.tree = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tree)
        self.bin = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.bin)
        stele = self.bin / "stele"
        stele.write_text(f"#!{sys.executable}\n{STUB}", encoding="utf-8")
        stele.chmod(0o755)
        self.log = self.bin / "log"
        self.doc = self.bin / "doc.json"
        base = hermetic.env(
            MISE_TASK_NAME=TASK,
            ORG_BELT_DIR=str(BELT),
            ORG_CANON_DIR=CANON,
            ORG_PYTHON=sys.executable,
            STUB_LOG=str(self.log),
            STUB_DOC=str(self.doc),
        )
        self.env = base | {"PATH": f"{self.bin}:{base.get('PATH', '')}"}
        self.answer(PASS, 0)
        self.git("init", "-q")

    def answer(self, doc: dict[str, object], status: int) -> None:
        """Set what the stub prints and exits with."""
        self.doc.write_text(json.dumps(doc), encoding="utf-8")
        self.env["STUB_RC"] = str(status)

    def git(self, *args: str) -> None:
        """Run git in the planted tree."""
        subprocess.run(
            [GIT, *args],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            check=True,
        )

    def plant(self, name: str, *, track: bool = True) -> str:
        """Write one workflow, tracked unless asked otherwise.

        Returns:
            Its path from the tree's top.

        """
        rel = f".github/workflows/{name}"
        path = self.tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("on: push\n", encoding="utf-8")
        if track:
            self.git("add", "--", rel)
        return rel

    def logged(self) -> list[str]:
        """Read the stub's argument log.

        Returns:
            One line per call, its arguments tab-joined.

        """
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    def helper(self, *argv: str) -> hermetic.Called:
        """Call the helper's main in the planted tree.

        Returns:
            The status and what it printed.

        """
        args = list(argv)
        return hermetic.call_main(
            lambda: caller_permissions.main(args), env=self.env, cwd=self.tree
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


CALL = "\t".join([
    "assert",
    "permissions",
    "--json",
    "--policy",
    f"{CANON}/slsa/assert-policy.json",
    "--tree",
    CANON,
    "--callers",
    ".",
])
SKIPPED = ["lint:caller-permissions: no workflows, skipped"]


class Task(Planted):
    """The task's own body: its guard, and the call."""

    def test_no_workflows_is_skipped_by_name(self) -> None:
        """No tracked workflow: the task says it skipped, stele never runs."""
        done = self.task()
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), SKIPPED)
        same(self, self.logged(), [])

    def test_an_untracked_workflow_is_no_workflow(self) -> None:
        """A workflow git does not track: still skipped."""
        self.plant("ci.yml", track=False)
        done = self.task()
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), SKIPPED)

    def test_a_pass_through_the_task(self) -> None:
        """A tracked workflow: the body calls stele on the canon tree, PASS."""
        self.plant("ci.yml")
        done = self.task()
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), ["lint:caller-permissions: stele: PASS"])
        same(self, done.stderr.splitlines(), ["stub stele: walking"])
        same(self, self.logged(), [CALL])


class Helper(Planted):
    """The helper's branches, called in this process."""

    def test_stele_missing_is_red(self) -> None:
        """No stele on PATH: red by name, nothing run."""
        rel = self.plant("ci.yml")
        self.env["PATH"] = str(self.tree)
        done = self.helper(CANON, rel)
        same(self, done.returncode, caller_permissions.EXIT_RED)
        same(self, done.stdout, "")
        expected = ["lint:caller-permissions: stele missing from the belt"]
        same(self, done.stderr.splitlines(), expected)
        same(self, self.logged(), [])

    def test_a_short_grant_is_red_with_the_fix(self) -> None:
        """A FAIL from stele: its findings, its verdict, then the fix."""
        rel = self.plant("ci.yml")
        self.answer(FAIL, 1)
        done = self.helper(CANON, rel)
        same(self, done.returncode, caller_permissions.EXIT_RED)
        same(self, done.stdout.splitlines(), ["lint:caller-permissions: stele: FAIL"])
        expected = [
            "stub stele: walking",
            "lint:caller-permissions: .github/workflows/release.yml: caller-grant",
            "  expected: id-token: write",
            "  actual: absent",
            *FIX,
        ]
        same(self, done.stderr.splitlines(), expected)
        same(self, self.logged(), [CALL])

    def test_a_pass_is_quiet(self) -> None:
        """A PASS from stele: its report alone, exit 0."""
        rel = self.plant("ci.yml")
        done = self.helper(CANON, rel)
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), ["lint:caller-permissions: stele: PASS"])

    def test_a_bad_call_is_a_usage_error(self) -> None:
        """No canon, an empty canon, or no workflow: exit 2 with the usage line."""
        usage = f"{TASK}: {caller_permissions.USAGE}"
        calls: list[list[str]] = [[], [CANON], ["", "ci.yml"]]
        for argv in calls:
            with self.subTest(argv=argv):
                done = self.helper(*argv)
                same(self, done.returncode, caller_permissions.EXIT_ERROR)
                same(self, done.stderr.splitlines(), [usage])
        same(self, self.logged(), [])


if __name__ == "__main__":
    unittest.main()
