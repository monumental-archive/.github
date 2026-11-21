#!/usr/bin/env python3
"""Table tests for lint:audit-scheduled (#358 seam 1, #1355, #1858).

The task's own body, read from mise/config.toml, runs in a planted tree
that carries a belt config and workflows. Its second half (the
repository's OWN audit tasks) asks mise for the task list; that is
own-audits.sh's to prove, so the belt directory here holds a stub
`own_audit_tasks`: it reports none for the belt-half rows, and one task
for the repository-half rows.

The row that matters for #1355: `audit:mutants` runs from
repo-mutants.yml, its own workflow. A scheduler counts wherever it lives
under .github/workflows/, and the red message names repo-mutants.yml
among the places a task may be run from.

The rows that matter for #1858: a repository's own audit tasks are
collected by repo-own-audits.yml and by nothing else. A tree whose only
caller is that workflow passes; a tree whose only caller is the retired
single audit workflow is red, by name.

The rows that matter for #1884: slsa.yml is the one canon workflow whose
legs span both hours of the Monday API budget (#1169). Every leg but
`plan` needs `plan` and names its group, and the second group is the
three source walks and nothing else. A leg with no group would run in
both hours, so a planted one is red, by name.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom. stdlib `unittest`,
run through the gate as `mise run test`, which `ci` collects.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic
import workflow_yaml

BELT = Path(__file__).resolve().parent
BASH = hermetic.tool("bash")
OWN_AUDITS_STUB = "own_audit_tasks() { :; }\n"
OWN_AUDIT_STUB = "own_audit_tasks() { echo audit:custom; }\n"
CANON = "monumental-archive/.github/.github/workflows"
PIN = "6d6916fcdaa4e632bd12af51e8240af6b597d75e # v1.71.1"
# The retired single audit workflow (#1858), spelled in two parts so the
# tree-wide search for its name stays empty outside CHANGELOG.md.
RETIRED = "repo-" + "audit.yml"
CONFIG = '[tasks."audit:fuzz"]\nrun = "x"\n\n[tasks."audit:mutants"]\nrun = "x"\n'
SLSA = BELT.parent / ".github" / "workflows" / "slsa.yml"
# The source walks: about 3,500 of the Monday's 5,950 requests (#1169).
SECOND = ["source-vsa", "release-tags", "blast-radius"]
GROUPS = {f"needs.plan.outputs.legs == '{g}'": g for g in ("first", "second")}


def body() -> str:
    """Read lint:audit-scheduled's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks["lint:audit-scheduled"], "lint:audit-scheduled")
    return decoded.as_str(task["run"], "lint:audit-scheduled.run")


def groups(document: dict[str, object]) -> tuple[dict[str, str], list[str]]:
    """Read each leg's group, and name each leg that has none.

    Returns:
        Leg to group, and one finding per leg with no `needs: plan` or
        no group `if:`.

    """
    found: dict[str, str] = {}
    ungrouped: list[str] = []
    for job_id, job in workflow_yaml.jobs(document).items():
        # `plan` opens the run and `report` (#1887) ends it; neither is a
        # leg, and the report reads only this run's own job list.
        if job_id in {"plan", "report"}:
            continue
        group = GROUPS.get(str(job.get("if")))
        if job.get("needs") != "plan" or group is None:
            ungrouped.append(
                f"slsa.yml: {job_id} names no group, so it runs in both hours"
            )
            continue
        found[job_id] = group
    return found, ungrouped


class SlsaGroups(unittest.TestCase):
    """slsa.yml's legs keep their hours (#1169, #1884)."""

    def test_every_leg_needs_the_plan_and_names_its_group(self) -> None:
        """The tree: no leg of slsa.yml runs in both hours."""
        found, ungrouped = groups(workflow_yaml.document(SLSA))
        none: list[str] = []
        self.assertEqual(none, ungrouped)
        self.assertEqual(8, len(found))

    def test_the_second_hour_is_the_source_walks_alone(self) -> None:
        """Exactly the three source walks are in group `second`."""
        found, _ = groups(workflow_yaml.document(SLSA))
        second: list[str] = sorted(j for j, group in found.items() if group == "second")
        want: list[str] = sorted(SECOND)
        self.assertEqual(want, second)

    def test_a_leg_with_no_group_is_red_by_name(self) -> None:
        """A planted leg with no `needs`/`if` pair is named."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        planted = tmp / "slsa.yml"
        leg = "  stray:\n    runs-on: ubuntu-26.04\n    steps:\n      - run: x\n"
        text = SLSA.read_text(encoding="utf-8")
        planted.write_text(text.replace("\njobs:\n", "\njobs:\n" + leg, 1), "utf-8")
        _, ungrouped = groups(workflow_yaml.document(planted))
        want: list[str] = ["slsa.yml: stray names no group, so it runs in both hours"]
        self.assertEqual(want, ungrouped)


class Scheduled(unittest.TestCase):
    """A planted belt config and workflows; the lint's verdict on them."""

    @override
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
        return subprocess.run(
            [BASH, "-euo", "pipefail", "-c", body()],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_mutants_scheduled_by_its_own_workflow(self) -> None:
        """repo-mutants.yml counts as the scheduler of audit:mutants."""
        self.workflow("repo-dependencies.yml", "fuzz")
        self.workflow("repo-mutants.yml", "mutants")
        done = self.run_lint()
        self.assertEqual(done.returncode, 0, done.stderr)
        expected = [
            "lint:audit-scheduled: 2 belt audit task(s), every one scheduled",
            "lint:audit-scheduled: this repository defines no audit:* of its own",
        ]
        self.assertEqual(done.stdout.splitlines(), expected)

    def test_unscheduled_task_is_red_and_names_the_schedulers(self) -> None:
        """No workflow runs audit:mutants: red, and repo-mutants.yml named."""
        self.workflow("repo-dependencies.yml", "fuzz")
        done = self.run_lint()
        self.assertEqual(done.returncode, 1)
        expected = [
            (
                "lint:audit-scheduled: belt audit task(s) no workflow ever runs:"
                " audit:mutants"
            ),
            (
                "  schedule each in .github/workflows/ (slsa.yml or a sibling,"
                " publish.yml, a"
            ),
            (
                "  repo-*.yml subject workflow or repo-mutants.yml) or delete the"
                " task — a check"
            ),
            "  nobody runs is a claim, not a check (#358 seam 1).",
        ]
        self.assertEqual(done.stderr.splitlines(), expected)


class OwnTasks(unittest.TestCase):
    """The repository half: its own audit tasks need a collecting caller."""

    @override
    def setUp(self) -> None:
        """Plant a consumer tree (no belt config) defining one audit task."""
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.tree = self.tmp / "tree"
        (self.tree / ".github" / "workflows").mkdir(parents=True)
        belt = hermetic.belt(self.tmp / "belt", stubs={"own-audits.sh": OWN_AUDIT_STUB})
        canon = hermetic.canon(self.tmp / "canon", "security/identity.toml")
        self.env = hermetic.env() | {
            "ORG_BELT_DIR": str(belt),
            "ORG_CANON_DIR": str(canon),
        }

    def caller(self, name: str, callee: str) -> None:
        """Plant a stub whose one job calls a canon workflow by pin."""
        (self.tree / ".github" / "workflows" / name).write_text(
            f"jobs:\n  a:\n    uses: {CANON}/{callee}@{PIN}\n",
            encoding="utf-8",
        )

    def run_lint(self) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree.

        Returns:
            The finished process.

        """
        return subprocess.run(
            [BASH, "-euo", "pipefail", "-c", body()],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_own_audits_caller_collects_them(self) -> None:
        """A tree whose only caller is repo-own-audits.yml passes."""
        self.caller("own-audits.yml", "repo-own-audits.yml")
        done = self.run_lint()
        self.assertEqual(done.returncode, 0, done.stderr)
        expected = (
            "lint:audit-scheduled: 1 repo-defined audit task(s),"
            " collected by repo-own-audits.yml\n"
        )
        self.assertEqual((done.stdout, done.stderr), (expected, ""))

    def test_the_retired_caller_collects_nothing(self) -> None:
        """A tree whose only caller is the retired workflow is red, by name."""
        self.caller("audit.yml", RETIRED)
        done = self.run_lint()
        self.assertEqual(done.returncode, 1, done.stdout)
        expected = [
            (
                "lint:audit-scheduled: repo-defined audit task(s) no workflow"
                " runs: audit:custom"
            ),
            ("  wire the own-audits stub (workflow-templates/own-audits.yml), which"),
            (
                "  calls repo-own-audits.yml and runs every audit:* this"
                " repository defines,"
            ),
            "  or run each from a workflow here — or delete the task. A check",
            "  nobody runs is a claim, not a check (#358 seam 1, #704).",
        ]
        self.assertEqual(done.stderr.splitlines(), expected)

    def test_another_subject_caller_collects_nothing(self) -> None:
        """A caller of a belt-only subject workflow runs no repository task."""
        self.caller("records.yml", "repo-records.yml")
        done = self.run_lint()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertTrue(
            done.stderr.startswith(
                "lint:audit-scheduled: repo-defined audit task(s) no workflow"
                " runs: audit:custom\n"
            ),
            done.stderr,
        )


if __name__ == "__main__":
    unittest.main()
