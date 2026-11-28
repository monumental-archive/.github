"""The workflow tasks read the tracked workflows only (#2007).

A glob over `.github/workflows/` matches an untracked file as readily as
a tracked one, so a task that chose its files that way linted, rewrote
or skipped on a file CI never sees. Every such task now takes its list
from mise/action-files.sh (`workflow_files`, `workflow_template_files`).

Each row runs a task's own body, read from mise/config.toml, through
`support.task_run` in a planted git repository, and was red on the
glob: an untracked `stray.yml` that breaks the task's rule, beside one
clean tracked workflow, is not read (exit 0, never named); the same
file tracked is the task's finding, naming it. `fix:gate-stub` leaves an
untracked stub it would rewrite byte for byte. Each of the nine tasks
that skipped on the glob, on a tree whose only workflow (or template)
is untracked, takes its skip branch. derive-badges.sh's coverage check
is not satisfied by an untracked workflow.

lint:actions runs the belt's real actionlint, so this file runs under
the belt's tools (`mise run test`, or `mise x --`).

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import decoded
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from pathlib import Path

BELT = support.MISE
CANON_DIR = BELT.parent
BASH = support.tool("bash")
GIT = support.tool("git")
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


class Planted:
    """A planted git repository and the belt's tasks run in it."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant an empty repository."""
        self.make_env = env
        self.tmp = tmp_path
        self.tree = self.tmp / "tree"
        self.tree.mkdir()
        self.env = self.make_env(
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
        as_mise = support.task_run(task, body(task))
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | task_env(task) | extra | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


@fixture
def planted(tmp_path: Path, env: support.Env) -> Planted:
    """Give each row its own Planted."""
    return Planted(tmp_path, env=env)


class Untracked(Planted):
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


@fixture
def untracked(tmp_path: Path, env: support.Env) -> Untracked:
    """Give each row its own Untracked."""
    return Untracked(tmp_path, env=env)


def test_lint_actions(untracked: Untracked) -> None:
    """Actionlint reads the list, so an untracked expression error is not read."""
    untracked.assert_read_only_when_tracked("lint:actions")


def test_lint_gate_stub(untracked: Untracked) -> None:
    """A gate caller missing all three requirements, untracked."""
    untracked.assert_read_only_when_tracked("lint:gate-stub")


def test_lint_capability_boundary(untracked: Untracked) -> None:
    """A called workflow granting id-token with no marker, untracked."""
    untracked.assert_read_only_when_tracked("lint:capability-boundary")


def test_lint_nested_permissions(untracked: Untracked) -> None:
    """A `uses:` job with no permissions block, untracked."""
    untracked.assert_read_only_when_tracked("lint:nested-permissions")


def test_lint_cold_attested(untracked: Untracked) -> None:
    """An actions/cache step with no unattested-path marker, untracked."""
    untracked.assert_read_only_when_tracked("lint:cold-attested")


def test_fix_gate_stub_leaves_an_untracked_stub_alone(untracked: Untracked) -> None:
    """A stale stub fix:gate-stub would rewrite is left byte for byte."""
    untracked.plant(".github/workflows/clean.yml", CLEAN)
    untracked.plant(STRAY, STALE_STUB, track=False)
    done = untracked.task("fix:gate-stub")
    assert done.returncode == 0, done.stdout + done.stderr
    assert (untracked.tree / STRAY).read_text(encoding="utf-8") == STALE_STUB
    untracked.git("add", "--", STRAY)
    done = untracked.task("fix:gate-stub")
    assert done.returncode == 0, done.stdout + done.stderr
    assert WANT_GROUP in (untracked.tree / STRAY).read_text(encoding="utf-8")


@pytest.mark.parametrize(("task", "line"), SKIPPED.items())
def test_each_task_skips(planted: Planted, task: object, line: object) -> None:
    """Seven tasks print their skip line and pass."""
    planted.plant(STRAY, CLEAN, track=False)
    done = planted.task(task)
    assert done.returncode == 0, done.stdout + done.stderr
    want: list[str] = [line]
    assert done.stdout.splitlines() == want


def test_audit_template_pins_skips(planted: Planted) -> None:
    """An untracked template holding an org pin is not read."""
    pin = (
        "jobs:\n  a:\n    uses: monumental-archive/.github/.github/workflows/"
        "ci.yml@" + "b" * 40 + " # v1.0.0\n"
    )
    planted.plant("workflow-templates/ci.yml", pin, track=False)
    done = planted.task("audit:template-pins")
    assert done.returncode == 0, done.stdout + done.stderr
    skipped = ["audit:template-pins: no workflow-templates/, skipped"]
    assert done.stdout.splitlines() == skipped


def test_audit_scheduled_has_no_workflows(planted: Planted, belt: support.Belt) -> None:
    """Own audit tasks and only an untracked runner: the no-workflows branch."""
    runner = "jobs:\n  a:\n    steps:\n      - run: mise run audit:custom\n"
    planted.plant(STRAY, runner, track=False)
    stubs = {"own-audits.sh": OWN_AUDIT_STUB}
    belt_dir = belt(planted.tmp / "belt", stubs=stubs)
    done = planted.task("lint:audit-scheduled", ORG_BELT_DIR=str(belt_dir))
    assert done.returncode == 1, done.stdout + done.stderr
    expected = [
        "lint:audit-scheduled: repo-defined audit task(s) and NO workflows at all:",
        "    audit:custom",
        "  wire the own-audits stub (workflow-templates/own-audits.yml) —",
        "  it calls repo-own-audits.yml, whose one leg runs every audit:*",
        "  this repository defines (#704, #1858).",
    ]
    assert done.stderr.splitlines() == expected


def test_an_untracked_token_feeds_no_shield(planted: Planted) -> None:
    """`.coverage-floor` with codecov-token only in an untracked workflow: red."""
    reuse = (
        "version = 1\n"
        'SPDX-PackageDownloadLocation = "https://github.com/'
        'monumental-archive/demo"\n\n'
        '[[annotations]]\npath = "**"\nSPDX-License-Identifier = "0BSD"\n'
    )
    planted.plant("REUSE.toml", reuse)
    planted.plant(".coverage-floor", "80\n")
    planted.plant(
        STRAY,
        "jobs:\n  ci:\n    secrets:\n      codecov-token: ${{ secrets.X }}\n",
        track=False,
    )
    out = planted.tmp / "block.md"
    done = subprocess.run(
        [BASH, str(BELT / "derive-badges.sh")],
        cwd=planted.tree,
        env=planted.env | {"BADGES_OUT": str(out)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 1, done.stdout + done.stderr
    assert done.stderr.startswith(
        "fix:badges: .coverage-floor is tracked and no workflow passes codecov-token"
    ), done.stderr
