"""Table tests for lint:capability-boundary and its helper (#1911).

Each row plants workflows and asserts the exit status and every line of
the message: each way a workflow stays inside the boundary (not
called, no grant, a grant only in prose, a first-token marker), each
way it breaches it (no marker, a marker only in prose), a file that is
not UTF-8, and a call with no workflows. Two rows run the task's own
body, read from mise/config.toml: the skip when there are no
workflows, and a breach end to end; the rest call the helper's `main`
in this process, in the planted tree.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import capability_boundary, decoded
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:capability-boundary"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
OK = ["lint:capability-boundary: ok"]
CALLED = "on:\n  workflow_call:\n"
GRANT = "permissions:\n  id-token: write\n"
MARKER = "# capability-boundary: signs only; runs no caller step\n"
JOBS = "jobs:\n  a:\n    runs-on: ubuntu-26.04\n    steps:\n      - run: true\n"


def same[T](got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    assert got == want, msg or None


def body() -> str:
    """Read lint:capability-boundary's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


def red(*files: str) -> list[str]:
    """Build the red message naming some workflows.

    Returns:
        Its lines.

    """
    named = "".join(f" {f}" for f in files)
    return [
        "lint:capability-boundary: workflow_call workflow holds id-token",
        f"  without a capability-boundary marker:{named}",
    ]


class Boundary:
    """Planted workflows; the boundary's verdict on them."""

    def __init__(
        self, tmp_path: Path, *, env: support.Env, track: support.Track
    ) -> None:
        """Plant an empty workflows directory."""
        self.make_env = env
        self.track_tree = track
        self.tree = tmp_path
        (self.tree / ".github" / "workflows").mkdir(parents=True)
        self.env = self.make_env(
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

    def enter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stand in the planted tree as the task's main meets it."""
        for name, value in self.env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(self.tree)

    def task(self) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree.

        Returns:
            The finished process.

        """
        self.track_tree(self.tree)
        as_mise = support.task_run(TASK, body())
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


@fixture
def boundary(tmp_path: Path, env: support.Env, track: support.Track) -> Boundary:
    """Give each row its own Boundary.

    Returns:
        The row's Boundary.

    """
    return Boundary(tmp_path, env=env, track=track)


def test_no_workflows_is_skipped_by_name(boundary: Boundary) -> None:
    """No workflow file: the task says it skipped."""
    done = boundary.task()
    same(done.returncode, 0, done.stderr)
    skipped = ["lint:capability-boundary: no workflows, skipped"]
    same(done.stdout.splitlines(), skipped)


def test_breach_is_red_through_the_task(boundary: Boundary) -> None:
    """The task body hands the helper every workflow; a breach is red."""
    boundary.plant("clean.yml", CALLED + JOBS)
    bad = boundary.plant("sign.yaml", CALLED + GRANT + JOBS)
    done = boundary.task()
    same(done.returncode, capability_boundary.EXIT_RED)
    same(done.stderr.splitlines(), red(bad))


def test_inside_the_boundary_is_ok(
    boundary: Boundary,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not called, no grant, a grant in prose, a marked signer: ok."""
    rows = {
        "not called": "on: push\n" + GRANT + JOBS,
        "no grant": CALLED + JOBS,
        "grant in a comment": CALLED + "# no id-token: write, ever\n" + JOBS,
        "grant after a hash": CALLED + "x: 1 # id-token: write\n" + JOBS,
        "marked": CALLED + GRANT + MARKER + JOBS,
        "marked, indented": CALLED + GRANT + JOBS + "  #  " + MARKER[2:],
    }
    for text in rows.values():
        rel = boundary.plant("wf.yml", text)
        boundary.enter(monkeypatch)
        status = capability_boundary.main([rel])
        out, err = capfd.readouterr()
        same(status, 0, err)
        same(out.splitlines(), OK)
        same(err, "")


def test_breaches_are_red_and_named(
    boundary: Boundary,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No marker, or a marker only in prose: red, every file named."""
    bare = boundary.plant("bare.yml", CALLED + GRANT + JOBS)
    prose = boundary.plant(
        "prose.yml",
        CALLED + GRANT + "# see the capability-boundary: rule\n" + JOBS,
    )
    tabbed = boundary.plant("tabbed.yml", "on:\n\tworkflow_call:\n\tid-token:\twrite\n")
    clean = boundary.plant("clean.yml", CALLED + JOBS)
    boundary.enter(monkeypatch)
    status = capability_boundary.main([bare, clean, prose, tabbed])
    out, err = capfd.readouterr()
    same(status, capability_boundary.EXIT_RED)
    same(err.splitlines(), red(bare, prose, tabbed))
    same(out, "")


def test_a_line_is_split_on_newline_alone(
    boundary: Boundary,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A carriage return or form feed inside a line does not end it."""
    text = "on:\n  workflow_call:\nx: '#'\rid-token: write\n"
    rel = boundary.plant("cr.yml", text)
    boundary.enter(monkeypatch)
    status = capability_boundary.main([rel])
    out, err = capfd.readouterr()
    same(status, 0, err)
    same(out.splitlines(), OK)


def test_not_utf8_is_an_error(
    boundary: Boundary,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workflow that is not UTF-8 is refused by name, exit 2."""
    rel = boundary.plant("bad.yml", b"on: \xff\n")
    boundary.enter(monkeypatch)
    status = capability_boundary.main([rel])
    _out, err = capfd.readouterr()
    same(status, capability_boundary.EXIT_ERROR)
    assert err.startswith(
        f"lint:capability-boundary: {rel}: cannot be read as UTF-8:"
    ), err


def test_missing_file_is_an_error(
    boundary: Boundary,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A named workflow that does not exist is refused by name, exit 2."""
    boundary.enter(monkeypatch)
    status = capability_boundary.main([".github/workflows/gone.yml"])
    _out, err = capfd.readouterr()
    same(status, capability_boundary.EXIT_ERROR)
    assert err.startswith(
        "lint:capability-boundary: .github/workflows/gone.yml: cannot be read as UTF-8:"
    ), err


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param(
            [],
            (
                "belt capability-boundary: error: the following arguments are "
                "required: WORKFLOW"
            ),
            id="nothing",
        ),
    ],
)
def test_no_workflows_is_a_usage_error(
    argv: list[str], error: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """No workflow named: argparse's exit 2 and its error."""
    with pytest.raises(SystemExit) as ended:
        capability_boundary.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt capability-boundary "), err
    assert err.splitlines()[-1] == error
