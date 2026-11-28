"""The workflow tasks read the tracked workflows only (#2007).

A glob over `.github/workflows/` matches an untracked file as readily as
a tracked one, so a task that chose its files that way linted, rewrote
or skipped on a file CI never sees. Every such task now takes its list
from mise/action-files.sh (`workflow_files`, `workflow_template_files`).

Each row runs a task's own body, read from mise/config.toml, through
`hermetic.task_run` in a planted git repository, and was red on the
glob: an untracked `stray.yml` that breaks the task's rule, beside one
clean tracked workflow, is not read (exit 0, never named); the same
file tracked is the task's finding, naming it. `fix:gate-stub` leaves an
untracked stub it would rewrite byte for byte. Each of the nine tasks
that skipped on the glob, on a tree whose only workflow (or template)
is untracked, takes its skip branch. derive-badges.sh's coverage check
is not satisfied by an untracked workflow.

lint:actions runs the belt's real actionlint, so this file runs under
the belt's tools (`mise run test`, or `mise x --`).

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom. stdlib
`unittest`, run through the gate as `mise run test`.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from belt import decoded
from tests import hermetic

BELT = hermetic.MISE
CANON_DIR = BELT.parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
STRAY = ".github/workflows/stray.yml"
CLEAN = (
    "name: clean\non: push\npermissions: {}\njobs:\n  a:\n"
    "    runs-on: ubuntu-26.04\n    steps:\n      - run: echo ok\n"
)
# One file per task that breaks only that task's rule.
BREAKS: dict[str, str] = {
    "lint:actions": CLEAN.replace("echo ok", 'echo "${{ nope.x }}"'),
    "lint:gate-stub": (
        "on:\n  pull_request:\njobs:\n  ci:\n    uses: ./.github/workflows/ci.yml\n"
    ),
    "lint:capability-boundary": (
        "on:\n  workflow_call:\npermissions:\n  id-token: write\n"
        "jobs:\n  a:\n    runs-on: x\n    steps:\n      - run: true\n"
    ),
    "lint:nested-permissions": (
        "on: push\njobs:\n  ci:\n    uses: ./.github/workflows/clean.yml\n"
    ),
    "lint:cold-attested": (
        "on: push\njobs:\n  a:\n    runs-on: x\n    steps:\n"
        "      - uses: actions/cache@" + "a" * 40 + " # v4\n"
    ),
}
SKIPPED = {
    "lint:actions": "lint:actions: no workflows, skipped",
    "lint:gate-stub": "lint:gate-stub: no workflows, skipped",
    "fix:gate-stub": "fix:gate-stub: no workflows, skipped",
    "lint:capability-boundary": "lint:capability-boundary: no workflows, skipped",
    "lint:nested-permissions": "lint:nested-permissions: no workflows, skipped",
    "lint:cold-attested": "lint:cold-attested: no workflows, skipped",
    "audit:actions": "audit:actions: no workflows, skipped",
}
WANT_GROUP = (
    "${{ github.workflow }}-${{ github.event.pull_request.number"
    " || format('{0}-{1}', github.ref, github.sha) }}"
)
STALE_STUB = (
    "on:\n  pull_request:\n    types: [opened, synchronize, reopened, edited]\n"
    "  merge_group:\n    types:\n      - checks_requested\n"
    "concurrency:\n  group: ${{ github.workflow }}-${{ github.ref }}\n"
    "  cancel-in-progress: true\n"
    "jobs:\n  ci:\n    uses: ./.github/workflows/ci.yml\n"
)
OWN_AUDIT_STUB = "own_audit_tasks() { echo audit:custom; }\n"


def task_table(task: str) -> dict[str, object]:
    """Read one task's table from the belt's config.

    Returns:
        The table.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    return decoded.as_object(tasks[task], task)


def body(task: str) -> str:
    """Read a task's run body as mise renders it (raw markers dropped).

    Returns:
        The bash the task runs.

    """
    run = decoded.as_str(task_table(task)["run"], f"{task}.run")
    return "".join(
        line
        for line in run.splitlines(keepends=True)
        if line.strip() not in {"{% raw %}", "{% endraw %}"}
    )


def task_env(task: str) -> dict[str, str]:
    """Read a task's own env table.

    Returns:
        The variables `mise run` would set for it.

    """
    env = decoded.as_object(task_table(task).get("env", {}), f"{task}.env")
    return {name: decoded.as_str(value, name) for name, value in env.items()}


@pytest.mark.usefixtures("_set_up_test_planted")
class TestPlanted:
    """A planted git repository and the belt's tasks run in it."""

    @pytest.fixture
    def _set_up_test_planted(self, tmp_path: Path) -> None:
        """Plant an empty repository."""
        self.tmp = tmp_path
        self.tree = self.tmp / "tree"
        self.tree.mkdir()
        self.env = hermetic.env(
            ORG_BELT_DIR=str(BELT),
            ORG_CANON_DIR=str(CANON_DIR),
            ORG_PYTHON=sys.executable,
            ORG_PYTHON_UNSET="unset",
            ORG_FINDINGS="",
        )
        self.git("init", "-q")

    def git(self, *args: str) -> str:
        """Run git in the planted tree.

        Returns:
            Its standard output.

        """
        return subprocess.run(
            [GIT, *args],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def plant(self, name: str, text: str, *, track: bool = True) -> str:
        """Write one file, tracked unless told otherwise.

        Returns:
            Its path from the tree's top.

        """
        path = self.tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if track:
            self.git("add", "--", name)
        return name

    def task(self, task: str, **extra: str) -> subprocess.CompletedProcess[str]:
        """Run a task's body in the planted tree, as mise does.

        Returns:
            The finished process.

        """
        as_mise = hermetic.task_run(task, body(task))
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | task_env(task) | extra | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


class TestUntracked(TestPlanted):
    """An untracked workflow that breaks a rule is never read; tracked, it is."""

    def assert_read_only_when_tracked(self, task: str) -> None:
        """Untracked: exit 0, never named. Tracked: red, named."""
        self.plant(".github/workflows/clean.yml", CLEAN)
        self.plant(STRAY, BREAKS[task], track=False)
        done = self.task(task)
        assert done.returncode == 0, done.stdout + done.stderr
        assert "stray.yml" not in done.stdout + done.stderr
        self.git("add", "--", STRAY)
        done = self.task(task)
        assert done.returncode != 0, done.stdout + done.stderr
        assert "stray.yml" in done.stdout + done.stderr

    def test_lint_actions(self) -> None:
        """Actionlint reads the list, so an untracked expression error is not read."""
        self.assert_read_only_when_tracked("lint:actions")

    def test_lint_gate_stub(self) -> None:
        """A gate caller missing all three requirements, untracked."""
        self.assert_read_only_when_tracked("lint:gate-stub")

    def test_lint_capability_boundary(self) -> None:
        """A called workflow granting id-token with no marker, untracked."""
        self.assert_read_only_when_tracked("lint:capability-boundary")

    def test_lint_nested_permissions(self) -> None:
        """A `uses:` job with no permissions block, untracked."""
        self.assert_read_only_when_tracked("lint:nested-permissions")

    def test_lint_cold_attested(self) -> None:
        """An actions/cache step with no unattested-path marker, untracked."""
        self.assert_read_only_when_tracked("lint:cold-attested")

    def test_fix_gate_stub_leaves_an_untracked_stub_alone(self) -> None:
        """A stale stub fix:gate-stub would rewrite is left byte for byte."""
        self.plant(".github/workflows/clean.yml", CLEAN)
        self.plant(STRAY, STALE_STUB, track=False)
        done = self.task("fix:gate-stub")
        assert done.returncode == 0, done.stdout + done.stderr
        assert (self.tree / STRAY).read_text(encoding="utf-8") == STALE_STUB
        self.git("add", "--", STRAY)
        done = self.task("fix:gate-stub")
        assert done.returncode == 0, done.stdout + done.stderr
        assert WANT_GROUP in (self.tree / STRAY).read_text(encoding="utf-8")


class TestOnlyUntracked(TestPlanted):
    """A tree whose only workflow or template is untracked has none."""

    @pytest.mark.parametrize("task,line", SKIPPED.items())
    def test_each_task_skips(self, task: object, line: object) -> None:
        """Seven tasks print their skip line and pass."""
        self.plant(STRAY, CLEAN, track=False)
        done = self.task(task)
        assert done.returncode == 0, done.stdout + done.stderr
        want: list[str] = [line]
        assert done.stdout.splitlines() == want

    def test_audit_template_pins_skips(self) -> None:
        """An untracked template holding an org pin is not read."""
        pin = (
            "jobs:\n  a:\n    uses: monumental-archive/.github/.github/workflows/"
            "ci.yml@" + "b" * 40 + " # v1.0.0\n"
        )
        self.plant("workflow-templates/ci.yml", pin, track=False)
        done = self.task("audit:template-pins")
        assert done.returncode == 0, done.stdout + done.stderr
        skipped = ["audit:template-pins: no workflow-templates/, skipped"]
        assert done.stdout.splitlines() == skipped

    def test_audit_scheduled_has_no_workflows(self) -> None:
        """Own audit tasks and only an untracked runner: the no-workflows branch."""
        runner = "jobs:\n  a:\n    steps:\n      - run: mise run audit:custom\n"
        self.plant(STRAY, runner, track=False)
        stubs = {"own-audits.sh": OWN_AUDIT_STUB}
        belt = hermetic.belt(self.tmp / "belt", stubs=stubs)
        done = self.task("lint:audit-scheduled", ORG_BELT_DIR=str(belt))
        assert done.returncode == 1, done.stdout + done.stderr
        expected = [
            "lint:audit-scheduled: repo-defined audit task(s) and NO workflows at all:",
            "    audit:custom",
            "  wire the own-audits stub (workflow-templates/own-audits.yml) —",
            "  it calls repo-own-audits.yml, whose one leg runs every audit:*",
            "  this repository defines (#704, #1858).",
        ]
        assert done.stderr.splitlines() == expected


class TestDeriveBadges(TestPlanted):
    """derive-badges.sh's coverage check reads tracked workflows only."""

    def test_an_untracked_token_feeds_no_shield(self) -> None:
        """`.coverage-floor` with codecov-token only in an untracked workflow: red."""
        reuse = (
            "version = 1\n"
            'SPDX-PackageDownloadLocation = "https://github.com/'
            'monumental-archive/demo"\n\n'
            '[[annotations]]\npath = "**"\nSPDX-License-Identifier = "0BSD"\n'
        )
        self.plant("REUSE.toml", reuse)
        self.plant(".coverage-floor", "80\n")
        self.plant(
            STRAY,
            "jobs:\n  ci:\n    secrets:\n      codecov-token: ${{ secrets.X }}\n",
            track=False,
        )
        out = self.tmp / "block.md"
        done = subprocess.run(
            [BASH, str(BELT / "derive-badges.sh")],
            cwd=self.tree,
            env=self.env | {"BADGES_OUT": str(out)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 1, done.stdout + done.stderr
        assert done.stderr.startswith(
            "fix:badges: .coverage-floor is tracked and no workflow passes"
            " codecov-token"
        ), done.stderr
