"""Table tests for belt/audit_scheduled.py, lint:audit-scheduled's helper (#1911).

tests/test_audit_scheduled.py runs the task's own body for the named
schedulers and the collecting caller. These rows hold the rest of the
helper, a row per branch, each asserting its message: zero belt audit
tasks, a name that is a prefix of another's, no config and no own task,
own tasks with no workflow (alone and beside a red belt half), own tasks
each run by a workflow, collected by the canon itself (`./`), a
scheduler in a file the selection does not name (through the body), own tasks with no
canon, a file that is not UTF-8, and no `--own`.
"""

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import audit_scheduled, decoded
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:audit-scheduled"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
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


def same[T](got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    assert got == want, msg or None


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


class Helper:
    """A planted tree; the helper's verdict on it."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant an empty repository with a workflows directory.

        The helper asks git whether the belt config is tracked (#2013).
        """
        self.make_env = env
        self.tmp = tmp_path
        self.tree = self.tmp / "tree"
        (self.tree / ".github" / "workflows").mkdir(parents=True)
        self.env = self.make_env(MISE_TASK_NAME=TASK, ORG_FINDINGS="")
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        """Run git in the planted tree."""
        git = support.tool("git")
        subprocess.run([git, *args], cwd=self.tree, env=self.env, check=True)

    def config(self, text: str) -> None:
        """Plant a belt config in the tree, as the canon carries one."""
        (self.tree / "mise").mkdir(exist_ok=True)
        (self.tree / "mise" / "config.toml").write_text(text, encoding="utf-8")
        self.git("add", "--", "mise/config.toml")

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

    def enter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stand in the planted tree as the task's main meets it."""
        for name, value in self.env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(self.tree)


@fixture
def helper_state(tmp_path: Path, env: support.Env) -> Helper:
    """Give each row its own Helper.

    Returns:
        The row's Helper.

    """
    return Helper(tmp_path, env=env)


def test_zero_belt_audits_is_red(
    helper_state: Helper,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A belt config naming no audit task: the enumeration is broken."""
    helper_state.config('[tasks."lint:x"]\nrun = "x"\n')
    helper_state.enter(monkeypatch)
    status = audit_scheduled.main(["--own", ""])
    out, err = capfd.readouterr()
    same(status, audit_scheduled.EXIT_RED)
    broken = (
        "lint:audit-scheduled: zero belt audit tasks enumerated — the grep is broken"
    )
    same(err.splitlines(), [broken])
    same(out.splitlines(), [NONE_OWN])


def test_a_prefix_name_is_not_its_longer_sibling(
    helper_state: Helper,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running audit:fuzz-long does not schedule audit:fuzz."""
    helper_state.config(CONFIG)
    rel = helper_state.plant("x.yml", "      - run: mise run audit:fuzz-long\n")
    helper_state.enter(monkeypatch)
    status = audit_scheduled.main(["--own", "", rel])
    _out, err = capfd.readouterr()
    same(status, audit_scheduled.EXIT_RED)
    same(err.splitlines(), unscheduled(" audit:fuzz"))


def test_both_scheduled_at_a_line_end_and_before_a_blank(
    helper_state: Helper,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name at the line's end, and one followed by a blank, both count."""
    helper_state.config(CONFIG)
    rel = helper_state.plant(
        "x.yml", "run: mise run audit:fuzz\nrun: mise run audit:fuzz-long --x\n"
    )
    helper_state.enter(monkeypatch)
    status = audit_scheduled.main(["--own", "", rel])
    out, err = capfd.readouterr()
    same(status, 0, err)
    expected = [
        "lint:audit-scheduled: 2 belt audit task(s), every one scheduled",
        NONE_OWN,
    ]
    same(out.splitlines(), expected)


def test_no_config_and_no_own_task(
    helper_state: Helper,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A consumer with no audit task of its own: one line, ok."""
    helper_state.enter(monkeypatch)
    status = audit_scheduled.main(["--own", ""])
    out, err = capfd.readouterr()
    same(status, 0, err)
    same(out.splitlines(), [NONE_OWN])


def test_own_tasks_and_no_workflow_are_red(
    helper_state: Helper,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Own tasks with no workflow at all: red, each task listed."""
    helper_state.enter(monkeypatch)
    status = audit_scheduled.main(["--own", "audit:custom\naudit:other\n"])
    out, err = capfd.readouterr()
    same(status, audit_scheduled.EXIT_RED)
    same(err.splitlines(), NO_FLOWS)
    same(out, "")


def test_a_red_belt_half_stays_red(
    helper_state: Helper,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The belt half red and own tasks collected: still red."""
    helper_state.config(CONFIG)
    caller = f"    uses: {CANON}/.github/workflows/repo-own-audits.yml@x\n"
    rel = helper_state.plant("x.yml", caller)
    helper_state.enter(monkeypatch)
    status = audit_scheduled.main(["--own", "audit:custom", "--canon", CANON, rel])
    out, err = capfd.readouterr()
    same(status, audit_scheduled.EXIT_RED)
    same(err.splitlines(), unscheduled(" audit:fuzz audit:fuzz-long"))
    collected = (
        "lint:audit-scheduled: 1 repo-defined audit task(s), collected by"
        " repo-own-audits.yml"
    )
    same(out.splitlines(), [collected])


def test_own_tasks_each_run_by_a_workflow(
    helper_state: Helper,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each own task run directly: ok, counted."""
    one = helper_state.plant("a.yml", "run: mise run audit:custom\n")
    two = helper_state.plant("b.yaml", "run: mise run audit:other # weekly\n")
    own = "audit:custom\naudit:other"
    helper_state.enter(monkeypatch)
    status = audit_scheduled.main(["--own", own, "--canon", CANON, one, two])
    out, err = capfd.readouterr()
    same(status, 0, err)
    each = (
        "lint:audit-scheduled: 2 repo-defined audit task(s), each run by a"
        " workflow here"
    )
    same(out.splitlines(), [each])


def test_the_canon_collects_its_own_by_path(
    helper_state: Helper,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`./.github/workflows/repo-own-audits.yaml` collects in the canon."""
    caller = "    uses:\t./.github/workflows/repo-own-audits.yaml\n"
    rel = helper_state.plant("own.yml", caller)
    helper_state.enter(monkeypatch)
    status = audit_scheduled.main(["--own", "audit:custom", "--canon", CANON, rel])
    out, err = capfd.readouterr()
    same(status, 0, err)
    collected = (
        "lint:audit-scheduled: 1 repo-defined audit task(s), collected by"
        " repo-own-audits.yml"
    )
    same(out.splitlines(), [collected])


def test_a_file_outside_the_workflows_schedules_nothing(
    helper_state: Helper, belt: support.Belt, track: support.Track
) -> None:
    """Through the body: a file the selection does not name schedules nothing."""
    helper_state.config('[tasks."audit:fuzz"]\nrun = "x"\n')
    helper_state.plant("top.yml", "run: true\n")
    helper_state.plant("notes.md", "run: mise run audit:fuzz\n")
    stubs = {"own-audits.sh": OWN_AUDITS_STUB}
    belt_dir = belt(helper_state.tmp / "belt", stubs=stubs)
    track(helper_state.tree)
    as_mise = support.task_run(TASK, body())
    env = helper_state.env | {
        "ORG_BELT_DIR": str(belt_dir),
        "ORG_PYTHON": sys.executable,
    }
    done = subprocess.run(
        as_mise.argv,
        cwd=helper_state.tree,
        env=env | as_mise.env,
        capture_output=True,
        text=True,
        check=False,
    )
    same(done.returncode, audit_scheduled.EXIT_RED)
    same(done.stderr.splitlines(), unscheduled(" audit:fuzz"))


@pytest.mark.parametrize("canon", ["", "/", "owner/", "/repo"])
def test_own_tasks_need_a_canon(
    helper_state: Helper,
    canon: str,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Own tasks and workflows with no canon to judge a caller by: exit 2."""
    rel = helper_state.plant("a.yml", "run: mise run audit:custom\n")
    helper_state.enter(monkeypatch)
    with pytest.raises(SystemExit) as ended:
        audit_scheduled.main(["--own", "audit:custom", "--canon", canon, rel])
    out, err = capfd.readouterr()
    same((ended.value.code, out), (audit_scheduled.EXIT_ERROR, ""))
    assert err.startswith("usage: belt audit-scheduled "), err
    same(
        err.splitlines()[-1],
        "belt audit-scheduled: error: --canon OWNER/REPO is needed to judge own tasks",
    )


def test_not_utf8_is_an_error(
    helper_state: Helper,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workflow that is not UTF-8 is refused by name, exit 2."""
    rel = helper_state.plant("bad.yml", b"run: \xff\n")
    helper_state.enter(monkeypatch)
    status = audit_scheduled.main(["--own", "", rel])
    _out, err = capfd.readouterr()
    same(status, audit_scheduled.EXIT_ERROR)
    assert err.startswith(f"lint:audit-scheduled: {rel}: cannot be read as UTF-8:"), err


def test_no_own_flag_is_a_usage_error(
    helper_state: Helper,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `--own`: argparse refuses, exit 2."""
    helper_state.enter(monkeypatch)
    with pytest.raises(SystemExit) as ended:
        audit_scheduled.main([])
    out, err = capfd.readouterr()
    same((ended.value.code, out), (audit_scheduled.EXIT_ERROR, ""))
    assert err.startswith("usage: belt audit-scheduled "), err
    same(
        err.splitlines()[-1],
        "belt audit-scheduled: error: the following arguments are required: --own",
    )
