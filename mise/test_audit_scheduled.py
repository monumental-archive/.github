#!/usr/bin/env python3
"""Table tests for lint:audit-scheduled's belt half (#358 seam 1, #1355).

The task's own body, read from mise/config.toml, runs in a planted tree
that carries a belt config and workflows. Its second half (the
repository's OWN audit tasks) asks mise for the task list; that is
own-audits.sh's to prove, so the belt directory here holds a stub
`own_audit_tasks` that reports none, and the rows are about the belt
half: every belt audit:* task needs a workflow that runs it.

The row that matters for #1355: `audit:mutants` runs from
repo-mutants.yml, its own workflow, and never from repo-audit.yml. A
scheduler counts wherever it lives under .github/workflows/, and the red
message names repo-mutants.yml among the places a task may be run from.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom. stdlib `unittest`,
run through the gate as `mise run test`, which `ci` collects.
"""

import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import tomllib
import unittest
from pathlib import Path

import hermetic

BELT = Path(__file__).resolve().parent
BASH = hermetic.tool("bash")
OWN_AUDITS_STUB = "own_audit_tasks() { :; }\n"
CONFIG = '[tasks."audit:fuzz"]\nrun = "x"\n\n[tasks."audit:mutants"]\nrun = "x"\n'


def body() -> str:
    """Read lint:audit-scheduled's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    with (BELT / "config.toml").open("rb") as handle:
        return tomllib.load(handle)["tasks"]["lint:audit-scheduled"]["run"]


class Scheduled(unittest.TestCase):
    """A planted belt config and workflows; the lint's verdict on them."""

    def setUp(self) -> None:
        """Plant a tree with two belt audit tasks and a stub belt."""
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.tree = self.tmp / "tree"
        (self.tree / "mise").mkdir(parents=True)
        (self.tree / "mise" / "config.toml").write_text(CONFIG, encoding="utf-8")
        (self.tree / ".github" / "workflows").mkdir(parents=True)
        belt = self.tmp / "belt"
        belt.mkdir()
        (belt / "own-audits.sh").write_text(OWN_AUDITS_STUB, encoding="utf-8")
        self.env = hermetic.env() | {"ORG_BELT_DIR": str(belt)}

    def workflow(self, name: str, task: str) -> None:
        """Plant a workflow whose one step runs one audit task."""
        (self.tree / ".github" / "workflows" / name).write_text(
            f"jobs:\n  a:\n    steps:\n      - run: mise run audit:{task}\n",
            encoding="utf-8",
        )

    def run_lint(self) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree.

        Returns:
            The finished process.

        """
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, "-euo", "pipefail", "-c", body()],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_mutants_scheduled_by_its_own_workflow(self) -> None:
        """repo-mutants.yml counts as the scheduler of audit:mutants."""
        self.workflow("repo-audit.yml", "fuzz")
        self.workflow("repo-mutants.yml", "mutants")
        done = self.run_lint()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.splitlines(),
            [
                "lint:audit-scheduled: 2 belt audit task(s), every one scheduled",
                "lint:audit-scheduled: this repository defines no audit:* of its own",
            ],
        )

    def test_unscheduled_task_is_red_and_names_the_schedulers(self) -> None:
        """No workflow runs audit:mutants: red, and repo-mutants.yml named."""
        self.workflow("repo-audit.yml", "fuzz")
        done = self.run_lint()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    "lint:audit-scheduled: belt audit task(s) no workflow ever runs:"
                    " audit:mutants"
                ),
                "  schedule each in .github/workflows/ (audit.yml, publish.yml,",
                "  repo-audit.yml or repo-mutants.yml) or delete the task — a check",
                "  nobody runs is a claim, not a check (#358 seam 1).",
            ],
        )


if __name__ == "__main__":
    unittest.main()
