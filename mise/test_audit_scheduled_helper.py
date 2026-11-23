#!/usr/bin/env python3
"""Table tests for mise/audit_scheduled.py, lint:audit-scheduled's helper (#1911).

mise/test_audit_scheduled.py runs the task's own body for the named
schedulers and the collecting caller. These rows hold the rest of the
helper, a row per branch, each asserting its message: zero belt audit
tasks, a name that is a prefix of another's, no config and no own task,
own tasks with no workflow (alone and beside a red belt half), own tasks
each run by a workflow, collected by the canon itself (`./`), a
scheduler outside the files named (through the body), own tasks with no
canon, a file that is not UTF-8, and no `--own`.

stdlib `unittest`, run through the gate as `mise run test`.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import audit_scheduled
import decoded
import hermetic

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:audit-scheduled"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


BELT = Path(__file__).resolve().parent
CANON = "monumental-archive/.github"
OWN_AUDITS_STUB = "own_audit_tasks() { :; }\n"
CONFIG = '[tasks."audit:fuzz"]\nrun = "x"\n\n[tasks."audit:fuzz-long"]\nrun = "x"\n'
NONE_OWN = "lint:audit-scheduled: this repository defines no audit:* of its own"
NO_FLOWS = [
    "lint:audit-scheduled: repo-defined audit task(s) and NO workflows at all:",
    "    audit:custom",
    "    audit:other",
    "  wire the own-audits stub (workflow-templates/own-audits.yml) —",
    "  it calls repo-own-audits.yml, whose one leg runs every audit:*",
    "  this repository defines (#704, #1858).",
]


def same[T](case: unittest.TestCase, got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    case.assertEqual(got, want, msg or None)


def body() -> str:
    """Read lint:audit-scheduled's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


def unscheduled(names: str) -> list[str]:
    """Build the belt half's red message.

    Returns:
        Its lines.

    """
    return [
        f"lint:audit-scheduled: belt audit task(s) no workflow ever runs:{names}",
        "  schedule each in .github/workflows/ (slsa.yml or a sibling, publish.yml, a",
        (
            "  repo-*.yml subject workflow or repo-mutants.yml) or delete the"
            " task — a check"
        ),
        "  nobody runs is a claim, not a check (#358 seam 1).",
    ]


class Helper(unittest.TestCase):
    """A planted tree; the helper's verdict on it."""

    @override
    def setUp(self) -> None:
        """Plant an empty tree with a workflows directory."""
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.tree = self.tmp / "tree"
        (self.tree / ".github" / "workflows").mkdir(parents=True)
        self.env = hermetic.env(MISE_TASK_NAME=TASK, ORG_FINDINGS="")

    def config(self, text: str) -> None:
        """Plant a belt config in the tree, as the canon carries one."""
        (self.tree / "mise").mkdir(exist_ok=True)
        (self.tree / "mise" / "config.toml").write_text(text, encoding="utf-8")

    def plant(self, name: str, text: str | bytes) -> str:
        """Write one workflow.

        Returns:
            Its path from the tree's top.

        """
        rel = f".github/workflows/{name}"
        path = self.tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        return rel

    def helper(self, *argv: str) -> hermetic.Called:
        """Call the helper's main in the planted tree.

        Returns:
            The status and what it printed.

        """
        args = list(argv)
        return hermetic.call_main(
            lambda: audit_scheduled.main(args), env=self.env, cwd=self.tree
        )

    def test_zero_belt_audits_is_red(self) -> None:
        """A belt config naming no audit task: the enumeration is broken."""
        self.config('[tasks."lint:x"]\nrun = "x"\n')
        done = self.helper("--own", "")
        same(self, done.returncode, audit_scheduled.EXIT_RED)
        broken = (
            "lint:audit-scheduled: zero belt audit tasks enumerated — the grep"
            " is broken"
        )
        same(self, done.stderr.splitlines(), [broken])
        same(self, done.stdout.splitlines(), [NONE_OWN])

    def test_a_prefix_name_is_not_its_longer_sibling(self) -> None:
        """Running audit:fuzz-long does not schedule audit:fuzz."""
        self.config(CONFIG)
        rel = self.plant("x.yml", "      - run: mise run audit:fuzz-long\n")
        done = self.helper("--own", "", rel)
        same(self, done.returncode, audit_scheduled.EXIT_RED)
        same(self, done.stderr.splitlines(), unscheduled(" audit:fuzz"))

    def test_both_scheduled_at_a_line_end_and_before_a_blank(self) -> None:
        """A name at the line's end, and one followed by a blank, both count."""
        self.config(CONFIG)
        rel = self.plant(
            "x.yml", "run: mise run audit:fuzz\nrun: mise run audit:fuzz-long --x\n"
        )
        done = self.helper("--own", "", rel)
        same(self, done.returncode, 0, done.stderr)
        expected = [
            "lint:audit-scheduled: 2 belt audit task(s), every one scheduled",
            NONE_OWN,
        ]
        same(self, done.stdout.splitlines(), expected)

    def test_no_config_and_no_own_task(self) -> None:
        """A consumer with no audit task of its own: one line, ok."""
        done = self.helper("--own", "")
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), [NONE_OWN])

    def test_own_tasks_and_no_workflow_are_red(self) -> None:
        """Own tasks with no workflow at all: red, each task listed."""
        done = self.helper("--own", "audit:custom\naudit:other\n")
        same(self, done.returncode, audit_scheduled.EXIT_RED)
        same(self, done.stderr.splitlines(), NO_FLOWS)
        same(self, done.stdout, "")

    def test_a_red_belt_half_stays_red(self) -> None:
        """The belt half red and own tasks collected: still red."""
        self.config(CONFIG)
        caller = f"    uses: {CANON}/.github/workflows/repo-own-audits.yml@x\n"
        rel = self.plant("x.yml", caller)
        done = self.helper("--own", "audit:custom", "--canon", CANON, rel)
        same(self, done.returncode, audit_scheduled.EXIT_RED)
        same(self, done.stderr.splitlines(), unscheduled(" audit:fuzz audit:fuzz-long"))
        collected = (
            "lint:audit-scheduled: 1 repo-defined audit task(s), collected by"
            " repo-own-audits.yml"
        )
        same(self, done.stdout.splitlines(), [collected])

    def test_own_tasks_each_run_by_a_workflow(self) -> None:
        """Each own task run directly: ok, counted."""
        one = self.plant("a.yml", "run: mise run audit:custom\n")
        two = self.plant("b.yaml", "run: mise run audit:other # weekly\n")
        own = "audit:custom\naudit:other"
        done = self.helper("--own", own, "--canon", CANON, one, two)
        same(self, done.returncode, 0, done.stderr)
        each = (
            "lint:audit-scheduled: 2 repo-defined audit task(s), each run by a"
            " workflow here"
        )
        same(self, done.stdout.splitlines(), [each])

    def test_the_canon_collects_its_own_by_path(self) -> None:
        """`./.github/workflows/repo-own-audits.yaml` collects in the canon."""
        caller = "    uses:\t./.github/workflows/repo-own-audits.yaml\n"
        rel = self.plant("own.yml", caller)
        done = self.helper("--own", "audit:custom", "--canon", CANON, rel)
        same(self, done.returncode, 0, done.stderr)
        collected = (
            "lint:audit-scheduled: 1 repo-defined audit task(s), collected by"
            " repo-own-audits.yml"
        )
        same(self, done.stdout.splitlines(), [collected])

    def test_a_file_outside_the_workflows_schedules_nothing(self) -> None:
        """Through the body: a subdirectory's file is not a workflow GitHub runs."""
        self.config('[tasks."audit:fuzz"]\nrun = "x"\n')
        self.plant("top.yml", "run: true\n")
        self.plant("sub/x.yml", "run: mise run audit:fuzz\n")
        stubs = {"own-audits.sh": OWN_AUDITS_STUB}
        belt = hermetic.belt(self.tmp / "belt", stubs=stubs)
        as_mise = hermetic.task_run(TASK, body())
        env = self.env | {"ORG_BELT_DIR": str(belt), "ORG_PYTHON": sys.executable}
        done = subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )
        same(self, done.returncode, audit_scheduled.EXIT_RED)
        same(self, done.stderr.splitlines(), unscheduled(" audit:fuzz"))

    def test_own_tasks_need_a_canon(self) -> None:
        """Own tasks and workflows with no canon to judge a caller by: exit 2."""
        rel = self.plant("a.yml", "run: mise run audit:custom\n")
        usage = (
            "lint:audit-scheduled: usage: --canon OWNER/REPO is needed to judge"
            " own tasks"
        )
        for canon in ("", "/", "owner/", "/repo"):
            with self.subTest(canon=canon):
                done = self.helper("--own", "audit:custom", "--canon", canon, rel)
                same(self, done.returncode, audit_scheduled.EXIT_ERROR)
                same(self, done.stderr.splitlines(), [usage])

    def test_not_utf8_is_an_error(self) -> None:
        """A workflow that is not UTF-8 is refused by name, exit 2."""
        rel = self.plant("bad.yml", b"run: \xff\n")
        done = self.helper("--own", "", rel)
        same(self, done.returncode, audit_scheduled.EXIT_ERROR)
        self.assertTrue(
            done.stderr.startswith(
                f"lint:audit-scheduled: {rel}: cannot be read as UTF-8:"
            ),
            done.stderr,
        )

    def test_no_own_flag_is_a_usage_error(self) -> None:
        """No `--own`: argparse refuses, exit 2."""
        done = self.helper()
        same(self, done.returncode, audit_scheduled.EXIT_ERROR)
        self.assertIn("--own", done.stderr)


if __name__ == "__main__":
    unittest.main()
