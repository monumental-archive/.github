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

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

import subprocess
import sys
from typing import TYPE_CHECKING

from belt import decoded
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from pathlib import Path

BELT = support.MISE
BASH = support.tool("bash")
GIT = support.tool("git")
OWN_AUDITS_STUB = "own_audit_tasks() { :; }\n"
OWN_AUDIT_STUB = "own_audit_tasks() { echo audit:custom; }\n"
CANON = "monumental-archive/.github/.github/workflows"
PIN = "6d6916fcdaa4e632bd12af51e8240af6b597d75e # v1.71.1"
# The retired single audit workflow (#1858), spelled in two parts so the
# tree-wide search for its name stays empty outside CHANGELOG.md.
RETIRED = "repo-" + "audit.yml"
CONFIG = '[tasks."audit:fuzz"]\nrun = "x"\n\n[tasks."audit:mutants"]\nrun = "x"\n'


def body() -> str:
    """Read lint:audit-scheduled's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks["lint:audit-scheduled"], "lint:audit-scheduled")
    return decoded.as_str(task["run"], "lint:audit-scheduled.run")


class Scheduled:
    """A planted belt config and workflows; the lint's verdict on them."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        belt: support.Belt,
        env: support.Env,
        track: support.Track,
    ) -> None:
        """Plant a tree with two belt audit tasks and a stub belt."""
        self.plant_belt = belt
        self.make_env = env
        self.track_tree = track
        self.tmp = tmp_path
        self.tree = self.tmp / "tree"
        (self.tree / "mise").mkdir(parents=True)
        (self.tree / "mise" / "config.toml").write_text(CONFIG, encoding="utf-8")
        (self.tree / ".github" / "workflows").mkdir(parents=True)
        stubs = {"own-audits.sh": OWN_AUDITS_STUB}
        belt = self.plant_belt(self.tmp / "belt", stubs=stubs)
        self.env = self.make_env() | {
            "ORG_BELT_DIR": str(belt),
            "ORG_PYTHON": sys.executable,
        }

    def workflow(self, name: str, task: str) -> None:
        """Plant a workflow whose one step runs one audit task."""
        (self.tree / ".github" / "workflows" / name).write_text(
            f"jobs:\n  a:\n    steps:\n      - run: mise run audit:{task}\n",
            encoding="utf-8",
        )

    def run_lint(
        self, untracked: tuple[str, ...] = ()
    ) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree, tracking all but `untracked`.

        Returns:
            The finished process.

        """
        self.track_tree(self.tree)
        if untracked:
            rm = [GIT, "rm", "-q", "--cached", "--", *untracked]
            subprocess.run(rm, cwd=self.tree, env=self.env, check=True)
        as_mise = support.task_run("lint:audit-scheduled", body())
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


@fixture
def scheduled(
    tmp_path: Path, belt: support.Belt, env: support.Env, track: support.Track
) -> Scheduled:
    """Give each row its own Scheduled.

    Returns:
        The row's Scheduled.

    """
    return Scheduled(tmp_path, belt=belt, env=env, track=track)


def test_mutants_scheduled_by_its_own_workflow(scheduled: Scheduled) -> None:
    """repo-mutants.yml counts as the scheduler of audit:mutants."""
    scheduled.workflow("repo-dependencies.yml", "fuzz")
    scheduled.workflow("repo-mutants.yml", "mutants")
    done = scheduled.run_lint()
    assert done.returncode == 0, done.stderr
    expected = [
        "lint:audit-scheduled: 2 belt audit task(s), every one scheduled",
        "lint:audit-scheduled: this repository defines no audit:* of its own",
    ]
    assert done.stdout.splitlines() == expected


def test_an_untracked_belt_config_holds_nothing_to_schedule(
    scheduled: Scheduled,
) -> None:
    """mise/config.toml on disk, not in git: no belt half (#2013)."""
    scheduled.workflow("repo-dependencies.yml", "fuzz")
    done = scheduled.run_lint(untracked=("mise/config.toml",))
    assert done.returncode == 0, done.stderr
    expected = [
        "lint:audit-scheduled: this repository defines no audit:* of its own",
    ]
    assert done.stdout.splitlines() == expected


def test_unscheduled_task_is_red_and_names_the_schedulers(scheduled: Scheduled) -> None:
    """No workflow runs audit:mutants: red, and repo-mutants.yml named."""
    scheduled.workflow("repo-dependencies.yml", "fuzz")
    done = scheduled.run_lint()
    assert done.returncode == 1
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
    assert done.stderr.splitlines() == expected


class OwnTasks:
    """The repository half: its own audit tasks need a collecting caller."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        belt: support.Belt,
        canon: support.Canon,
        env: support.Env,
        track: support.Track,
    ) -> None:
        """Plant a consumer tree (no belt config) defining one audit task."""
        self.plant_belt = belt
        self.plant_canon = canon
        self.make_env = env
        self.track_tree = track
        self.tmp = tmp_path
        self.tree = self.tmp / "tree"
        (self.tree / ".github" / "workflows").mkdir(parents=True)
        belt = self.plant_belt(
            self.tmp / "belt", stubs={"own-audits.sh": OWN_AUDIT_STUB}
        )
        canon = self.plant_canon(self.tmp / "canon", "security/identity.toml")
        self.env = self.make_env() | {
            "ORG_BELT_DIR": str(belt),
            "ORG_CANON_DIR": str(canon),
            "ORG_PYTHON": sys.executable,
        }
        subprocess.run([GIT, "init", "-q"], cwd=self.tree, env=self.env, check=True)

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
        self.track_tree(self.tree)
        as_mise = support.task_run("lint:audit-scheduled", body())
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


@fixture
def own_tasks(
    tmp_path: Path,
    belt: support.Belt,
    canon: support.Canon,
    env: support.Env,
    track: support.Track,
) -> OwnTasks:
    """Give each row its own OwnTasks.

    Returns:
        The row's OwnTasks.

    """
    return OwnTasks(tmp_path, belt=belt, canon=canon, env=env, track=track)


def test_the_own_audits_caller_collects_them(own_tasks: OwnTasks) -> None:
    """A tree whose only caller is repo-own-audits.yml passes."""
    own_tasks.caller("own-audits.yml", "repo-own-audits.yml")
    done = own_tasks.run_lint()
    assert done.returncode == 0, done.stderr
    expected = (
        "lint:audit-scheduled: 1 repo-defined audit task(s),"
        " collected by repo-own-audits.yml\n"
    )
    assert (done.stdout, done.stderr) == (expected, "")


def test_the_retired_caller_collects_nothing(own_tasks: OwnTasks) -> None:
    """A tree whose only caller is the retired workflow is red, by name."""
    own_tasks.caller("audit.yml", RETIRED)
    done = own_tasks.run_lint()
    assert done.returncode == 1, done.stdout
    expected = [
        (
            "lint:audit-scheduled: repo-defined audit task(s) no workflow"
            " runs: audit:custom"
        ),
        ("  wire the own-audits stub (workflow-templates/own-audits.yml), which"),
        ("  calls repo-own-audits.yml and runs every audit:* this repository defines,"),
        "  or run each from a workflow here — or delete the task. A check",
        "  nobody runs is a claim, not a check (#358 seam 1, #704).",
    ]
    assert done.stderr.splitlines() == expected


def test_another_subject_caller_collects_nothing(own_tasks: OwnTasks) -> None:
    """A caller of a belt-only subject workflow runs no repository task."""
    own_tasks.caller("records.yml", "repo-records.yml")
    done = own_tasks.run_lint()
    assert done.returncode == 1, done.stdout
    assert done.stderr.startswith(
        "lint:audit-scheduled: repo-defined audit task(s) no workflow"
        " runs: audit:custom\n"
    ), done.stderr
