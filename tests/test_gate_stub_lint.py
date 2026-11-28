"""Table tests for mise/gate_stub.py, lint:gate-stub's helper (#1911).

mise/test_gate_stub.py holds the concurrency requirement through the
task's own body, on the canon's real stub and gate. These rows hold the
rest of the helper, a row per branch, each asserting its message: the
`edited` type (as a list item and on a `types:` line, and named only in
a comment), the `merge_group:` trigger (indented, and at column 0), all
three unmet at once across two callers, what is not a gate caller (no
`ci.yml` call, another repository's, no `pull_request`), a caller in the
canon itself (`./`), a file that is not UTF-8, a missing file, a call
with no canon or no workflow, and the skip through the task's body.
"""

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import decoded, gate_stub, gate_stub_fix
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:gate-stub"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
CANON_DIR = BELT.parent
CANON = "monumental-archive/.github"
OK = ["lint:gate-stub: ok"]
TRIGGERS = (
    "on:\n  pull_request:\n    types: [opened, synchronize, reopened, edited]\n"
    "  merge_group:\n    types:\n      - checks_requested\n"
)
CONCURRENCY = (
    f"concurrency:\n  group: {gate_stub_fix.GROUP}\n"
    f"  cancel-in-progress: {gate_stub_fix.CANCEL}\n"
)
JOB = f"jobs:\n  ci:\n    uses: {CANON}/.github/workflows/ci.yml@{'a' * 40} # v1\n"
GOOD = TRIGGERS + CONCURRENCY + JOB


def same[T](got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    assert got == want, msg or None


def body() -> str:
    """Read lint:gate-stub's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


def unedited(files: str) -> list[str]:
    """Build the `edited` message for some callers.

    Returns:
        Its lines.

    """
    return [
        "lint:gate-stub: gate caller's pull_request trigger lacks the edited type,",
        "  so title/base edits keep a stale verdict (#427). Set",
        f"  types: [opened, synchronize, reopened, edited] in:{files}",
    ]


def unqueued(files: str) -> list[str]:
    """Build the merge_group message for some callers.

    Returns:
        Its lines.

    """
    return [
        "lint:gate-stub: gate caller has no merge_group trigger, so the merge",
        "  queue's own run never starts and a queued pull request waits for a",
        '  check that cannot report (#1117). Under "on": add',
        "    merge_group:",
        "      types:",
        "        - checks_requested",
        f"  in:{files}",
    ]


def ungrouped(files: str) -> list[str]:
    """Build the concurrency message for some callers.

    Returns:
        Its lines.

    """
    return [
        "lint:gate-stub: gate caller's concurrency group is shared: a merged pull",
        "  request's event cancels the push gate (#1195), a push replaces the one",
        "  pending (#1617). Run mise run fix:gate-stub, or set under concurrency:",
        f"    group: {gate_stub_fix.GROUP}",
        f"    cancel-in-progress: {gate_stub_fix.CANCEL}",
        f"  in:{files}",
    ]


class Lint:
    """Planted callers; the helper's verdict on them."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant an empty workflows directory."""
        self.make_env = env
        self.tree = tmp_path
        (self.tree / ".github" / "workflows").mkdir(parents=True)
        self.env = self.make_env(
            MISE_TASK_NAME=TASK,
            ORG_BELT_DIR=str(BELT),
            ORG_CANON_DIR=str(CANON_DIR),
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

    def enter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stand in the planted tree as the task's main meets it."""
        for name, value in self.env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(self.tree)


@fixture
def lint(tmp_path: Path, env: support.Env) -> Lint:
    """Give each row its own Lint."""
    return Lint(tmp_path, env=env)


def test_no_workflows_is_skipped_by_name(lint: Lint, track: support.Track) -> None:
    """No workflow file: the task says it skipped."""
    track(lint.tree)
    as_mise = support.task_run(TASK, body())
    done = subprocess.run(
        as_mise.argv,
        cwd=lint.tree,
        env=lint.env | as_mise.env,
        capture_output=True,
        text=True,
        check=False,
    )
    same(done.returncode, 0, done.stderr)
    same(done.stdout.splitlines(), ["lint:gate-stub: no workflows, skipped"])


def test_a_conforming_caller_is_ok(
    lint: Lint, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every requirement met, `edited` as a list item too: ok."""
    listed = GOOD.replace(
        "    types: [opened, synchronize, reopened, edited]\n",
        "    types:\n      - opened\n      -   edited\n",
    )
    one = lint.plant("gate.yml", GOOD)
    two = lint.plant("listed.yaml", listed)
    lint.enter(monkeypatch)
    status = gate_stub.main([CANON, one, two])
    out, err = capfd.readouterr()
    same(status, 0, err)
    same(out.splitlines(), OK)
    same(err, "")


def test_not_a_gate_caller_is_not_held(
    lint: Lint, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ci.yml call, another repository's, or no pull_request: not held."""
    rows = {
        "no call": "on:\n  pull_request:\njobs:\n  a:\n    runs-on: x\n",
        "foreign": "on:\n  pull_request:\n" + JOB.replace(CANON, "other/repo"),
        "no pull_request": "on:\n  push:\n" + JOB,
    }
    names = [lint.plant(f"{i}.yml", text) for i, text in enumerate(rows.values())]
    lint.enter(monkeypatch)
    status = gate_stub.main([CANON, *names])
    out, err = capfd.readouterr()
    same(status, 0, err)
    same(out.splitlines(), OK)


def test_edited_only_in_a_comment_is_red(
    lint: Lint, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`edited` after a `#` on the types line is not the type."""
    rel = lint.plant(
        "gate.yml",
        GOOD.replace("reopened, edited]", "reopened] # edited"),
    )
    lint.enter(monkeypatch)
    status = gate_stub.main([CANON, rel])
    out, err = capfd.readouterr()
    same(status, gate_stub.EXIT_RED)
    same(err.splitlines(), unedited(f" {rel}"))
    same(out, "")


def test_an_unindented_merge_group_is_red(
    lint: Lint, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`merge_group:` at column 0 is not a trigger under `on:`."""
    rel = lint.plant("gate.yml", GOOD.replace("  merge_group:", "merge_group:"))
    lint.enter(monkeypatch)
    status = gate_stub.main([CANON, rel])
    _out, err = capfd.readouterr()
    same(status, gate_stub.EXIT_RED)
    same(err.splitlines(), unqueued(f" {rel}"))


def test_all_three_unmet_name_every_caller(
    lint: Lint, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare caller in the canon (`./`) and one elsewhere: three messages."""
    own = "jobs:\n  ci:\n    uses: ./.github/workflows/ci.yaml\n"
    local = lint.plant("self.yml", "on:\n  pull_request:\n" + own)
    bare = lint.plant("bare.yml", "on:\n  pull_request:\n" + JOB)
    clean = lint.plant("clean.yml", GOOD)
    lint.enter(monkeypatch)
    status = gate_stub.main([CANON, local, clean, bare])
    _out, err = capfd.readouterr()
    same(status, gate_stub.EXIT_RED)
    files = f" {local} {bare}"
    expected = [*unedited(files), *unqueued(files), *ungrouped(files)]
    same(err.splitlines(), expected)


def test_a_cancel_line_compares_without_its_comment(
    lint: Lint, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Indent, a trailing comment and trailing blanks are dropped first."""
    commented = GOOD.replace(
        f"  cancel-in-progress: {gate_stub_fix.CANCEL}\n",
        f"\t cancel-in-progress: {gate_stub_fix.CANCEL} \t# why  \n",
    )
    rel = lint.plant("gate.yml", commented)
    lint.enter(monkeypatch)
    status = gate_stub.main([CANON, rel])
    out, err = capfd.readouterr()
    same(status, 0, err)
    same(out.splitlines(), OK)


def test_not_utf8_is_an_error(
    lint: Lint, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workflow that is not UTF-8 is refused by name, exit 2."""
    rel = lint.plant("bad.yml", b"on: \xff\n")
    lint.enter(monkeypatch)
    status = gate_stub.main([CANON, rel])
    _out, err = capfd.readouterr()
    same(status, gate_stub.EXIT_ERROR)
    assert err.startswith(f"lint:gate-stub: {rel}: cannot be read as UTF-8:"), err


def test_missing_file_is_an_error(
    lint: Lint, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A named workflow that does not exist is refused by name, exit 2."""
    rel = ".github/workflows/gone.yml"
    lint.enter(monkeypatch)
    status = gate_stub.main([CANON, rel])
    _out, err = capfd.readouterr()
    same(status, gate_stub.EXIT_ERROR)
    assert err.startswith(f"lint:gate-stub: {rel}: cannot be read as UTF-8:"), err


def test_a_bad_call_is_a_usage_error(
    lint: Lint, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No canon, half a canon, or no workflow: exit 2 with the usage line."""
    usage = "lint:gate-stub: usage: belt gate-stub OWNER/REPO WORKFLOW..."
    calls: list[list[str]] = [[], [CANON], ["/repo", "x"], ["owner/", "x"]]
    for argv in calls:
        lint.enter(monkeypatch)
        status = gate_stub.main([*argv])
        _out, err = capfd.readouterr()
        same(status, gate_stub.EXIT_ERROR)
        same(err.splitlines(), [usage])
