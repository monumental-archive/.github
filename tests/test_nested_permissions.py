"""Table tests for lint:nested-permissions and its helper (#1911).

Each row plants workflows and asserts the exit status and every line of
the message: a `uses:` job that states its permissions, one that does
not (first, middle and last in the file), a file with no `uses:` job
(not counted), the job keys before `jobs:` (not jobs), a second
`jobs:` line, a key with trailing blanks or a carriage return, a file
that is not UTF-8, a missing file, and a call with no workflows. Two
rows run the task's own body, read from mise/config.toml: the skip when
there are no workflows, and an offender end to end; the rest call the
helper's `main` in this process, in the planted tree.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import decoded, nested_permissions
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:nested-permissions"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
CALL = "    uses: ./.github/workflows/ci.yml\n"
GRANT = "    permissions:\n      contents: read\n"
STEP = "    runs-on: ubuntu-26.04\n    steps:\n      - run: true\n"
TAIL = [
    "  it inherits the workflow-level default, not the caller grant —",
    "  state exactly what the called workflow needs, or the run dies as",
    "  startup_failure with no jobs and no log.",
]


def same[T](got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    assert got == want, msg or None


def body() -> str:
    """Read lint:nested-permissions' run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


def ok(checked: int) -> list[str]:
    """Build the summary line for some checked workflows.

    Returns:
        Its lines.

    """
    return [f"lint:nested-permissions: {checked} workflow(s) with uses: jobs clean"]


def red(*offenders: str) -> list[str]:
    """Build the red message naming some offenders.

    Returns:
        Its lines.

    """
    head = "lint:nested-permissions: nested uses: job with no permissions block:"
    return [head, *(f"  {o}" for o in offenders), *TAIL]


class Nested:
    """Planted workflows; the lint's verdict on them."""

    def __init__(self, tmp_path: Path) -> None:
        """Plant an empty workflows directory."""
        self.tree = tmp_path
        (self.tree / ".github" / "workflows").mkdir(parents=True)
        self.env = support.env(
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

    def helper(
        self, *files: str, capfd: pytest.CaptureFixture[str]
    ) -> tuple[int, str, str]:
        """Call the helper's main in the planted tree.

        Returns:
            The status and what it printed.

        """
        argv = list(files)
        with pytest.MonkeyPatch.context() as patched:
            for name, value in self.env.items():
                patched.setenv(name, value)
            patched.chdir(self.tree)
            status = nested_permissions.main(argv)
        out, err = capfd.readouterr()
        return status, out, err

    def task(self) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree.

        Returns:
            The finished process.

        """
        support.track(self.tree)
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
def nested(tmp_path: Path) -> Nested:
    """Give each row its own Nested."""
    return Nested(tmp_path)


def test_no_workflows_is_skipped_by_name(nested: Nested) -> None:
    """No workflow file: the task says it skipped."""
    done = nested.task()
    same(done.returncode, 0, done.stderr)
    skipped = ["lint:nested-permissions: no workflows, skipped"]
    same(done.stdout.splitlines(), skipped)


def test_offender_is_red_through_the_task(nested: Nested) -> None:
    """The task body hands the helper every workflow; an offender is red."""
    nested.plant("clean.yml", "jobs:\n  ci:\n" + CALL + GRANT)
    bad = nested.plant("stub.yaml", "jobs:\n  ci:\n" + CALL)
    done = nested.task()
    same(done.returncode, nested_permissions.EXIT_RED)
    same(done.stderr.splitlines(), red(f"{bad}:ci"))


def test_stated_permissions_are_clean(
    nested: Nested, capfd: pytest.CaptureFixture[str]
) -> None:
    """Every uses: job states permissions; only files with one count."""
    one = nested.plant("one.yml", "jobs:\n  ci:\n" + CALL + GRANT)
    two = nested.plant(
        "two.yml",
        "jobs:\n  a:\n" + GRANT + CALL + "  b:\n" + STEP + "  c:\n" + CALL + GRANT,
    )
    none = nested.plant("none.yml", "jobs:\n  build:\n" + STEP)
    status, out, err = nested.helper(one, two, none, capfd=capfd)
    same(status, 0, err)
    same(out.splitlines(), ok(2))
    same(err, "")


def test_offenders_are_named_in_file_order(
    nested: Nested, capfd: pytest.CaptureFixture[str]
) -> None:
    """First, middle and last jobs offend: each named, file by file."""
    first = nested.plant(
        "first.yml",
        "jobs:\n  a:\n" + CALL + "  b:\n" + CALL + GRANT + "  c:\n" + CALL,
    )
    second = nested.plant("second.yml", "jobs:\n  m:\n" + STEP + "  n:\n" + CALL)
    status, out, err = nested.helper(first, second, capfd=capfd)
    same(status, nested_permissions.EXIT_RED)
    same(
        err.splitlines(),
        red(f"{first}:a", f"{first}:c", f"{second}:n"),
    )
    same(out, "")


def test_keys_before_jobs_are_not_jobs(
    nested: Nested, capfd: pytest.CaptureFixture[str]
) -> None:
    """A two-space key above `jobs:` opens no job."""
    text = "on:\n  workflow_call:\n" + CALL + "jobs:\n  ci:\n" + CALL + GRANT
    rel = nested.plant("wf.yml", text)
    status, out, err = nested.helper(rel, capfd=capfd)
    same(status, 0, err)
    same(out.splitlines(), ok(1))


def test_a_second_jobs_line_closes_nothing(
    nested: Nested, capfd: pytest.CaptureFixture[str]
) -> None:
    """A repeated `jobs:` line leaves the open job open."""
    text = "jobs:\n  ci:\n" + CALL + "jobs:\n" + GRANT
    rel = nested.plant("wf.yml", text)
    status, out, err = nested.helper(rel, capfd=capfd)
    same(status, 0, err)
    same(out.splitlines(), ok(1))


def test_trailing_blanks_and_carriage_returns(
    nested: Nested, capfd: pytest.CaptureFixture[str]
) -> None:
    """Trailing blanks still open a job; a carriage return stays in its name.

    awk's first field keeps the carriage return, so its `:` is not
    the last character and stays; the writer prints it escaped.
    """
    blanks = nested.plant("blanks.yml", "jobs: \t\n  ci:  \n" + CALL)
    crlf = nested.plant("crlf.yml", "jobs:\r\n  ci:\r\n" + CALL)
    status, _out, err = nested.helper(blanks, crlf, capfd=capfd)
    same(status, nested_permissions.EXIT_RED)
    same(err.splitlines(), red(f"{blanks}:ci", f"{crlf}:ci:\\r"))


def test_not_a_job_key(nested: Nested, capfd: pytest.CaptureFixture[str]) -> None:
    """Deeper, shallower or valued keys open no job."""
    text = "jobs:\n  ci:\n" + GRANT + "  x: y\n   deep:\n" + CALL
    rel = nested.plant("wf.yml", text)
    status, out, err = nested.helper(rel, capfd=capfd)
    same(status, 0, err)
    same(out.splitlines(), ok(1))


def test_not_utf8_is_an_error(
    nested: Nested, capfd: pytest.CaptureFixture[str]
) -> None:
    """A workflow that is not UTF-8 is refused by name, exit 2."""
    rel = nested.plant("bad.yml", b"jobs: \xff\n")
    status, _out, err = nested.helper(rel, capfd=capfd)
    same(status, nested_permissions.EXIT_ERROR)
    assert err.startswith(
        f"lint:nested-permissions: {rel}: cannot be read as UTF-8:"
    ), err


def test_missing_file_is_an_error(
    nested: Nested, capfd: pytest.CaptureFixture[str]
) -> None:
    """A named workflow that does not exist is refused by name, exit 2."""
    status, _out, err = nested.helper(".github/workflows/gone.yml", capfd=capfd)
    same(status, nested_permissions.EXIT_ERROR)
    assert err.startswith(
        "lint:nested-permissions: .github/workflows/gone.yml: cannot be read as UTF-8:"
    ), err


def test_no_workflows_is_a_usage_error(
    nested: Nested, capfd: pytest.CaptureFixture[str]
) -> None:
    """No workflow named: exit 2 with the usage line."""
    status, _out, err = nested.helper(capfd=capfd)
    same(status, nested_permissions.EXIT_ERROR)
    usage = "lint:nested-permissions: usage: belt nested-permissions WORKFLOW..."
    same(err.splitlines(), [usage])
