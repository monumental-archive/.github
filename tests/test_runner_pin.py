"""Table tests for lint:runner-pin and mise/runner_pin.py (#1911).

Each row plants workflows and templates and asserts the exit status
and every line of the message: pinned labels (plain, suffixed, quoted,
commented), a matrix indirection, an expression that is not one, an
unpinned literal, an empty value, a floating label reaching runs-on
through a matrix include, a floating label in a comment (not counted),
two floating lines (the first named), a carriage return, the order
across workflows and templates, a file that is not UTF-8, a missing
file, and a call with no separator. Three rows run the task's own
body, read from mise/config.toml: the skip with nothing tracked, an
untracked workflow (still a skip), and offenders in both lists end to
end; the rest call the helper's `main` in this process.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import decoded, runner_pin
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:runner-pin"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
GIT = support.tool("git")
OK = [
    ("lint:runner-pin: every runs-on pinned or matrix-indirected, no floating labels")
]
SKIPPED = ["lint:runner-pin: no workflows, skipped"]
TAIL = [
    "  pin every runs-on to a named image version (ubuntu-24.04, not",
    "  ubuntu-latest) — Renovate rolls named pins as visible diffs;",
    "  a floating label migrates with no diff at all (#290).",
]


def same[T](got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    assert got == want, msg or None


def body() -> str:
    """Read lint:runner-pin's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


def job(runs_on: str) -> str:
    """Write a workflow with one job on a runner.

    Returns:
        Its text.

    """
    return f"jobs:\n  a:\n    runs-on: {runs_on}\n    steps:\n      - run: true\n"


def red(*offences: str) -> list[str]:
    """Build the red message naming some offences.

    Returns:
        Its lines.

    """
    listed = "".join(f" {o}" for o in offences)
    return [f"{TASK}: floating or unpinned runner labels:{listed}", *TAIL]


class Planted:
    """A planted git tree of workflows and templates."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant an empty tree."""
        self.make_env = env
        self.tree = tmp_path
        self.env = self.make_env(
            MISE_TASK_NAME=TASK,
            ORG_BELT_DIR=str(BELT),
            ORG_PYTHON=sys.executable,
        )
        subprocess.run([GIT, "init", "-q"], cwd=self.tree, env=self.env, check=True)

    def plant(self, rel: str, text: str | bytes, *, track: bool = True) -> str:
        """Write one file, tracked unless asked otherwise.

        Returns:
            Its path from the tree's top.

        """
        path = self.tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        if track:
            subprocess.run(
                [GIT, "add", "--", rel], cwd=self.tree, env=self.env, check=True
            )
        return rel

    def flow(self, name: str, text: str | bytes) -> str:
        """Write one workflow.

        Returns:
            Its path from the tree's top.

        """
        return self.plant(f".github/workflows/{name}", text)

    def helper(
        self, *argv: str, capfd: pytest.CaptureFixture[str]
    ) -> tuple[int, str, str]:
        """Call the helper's main in the planted tree.

        Returns:
            The status and what it printed.

        """
        args = list(argv)
        with pytest.MonkeyPatch.context() as patched:
            for name, value in self.env.items():
                patched.setenv(name, value)
            patched.chdir(self.tree)
            status = runner_pin.main(args)
        out, err = capfd.readouterr()
        return status, out, err

    def check(
        self, text: str, *, capfd: pytest.CaptureFixture[str]
    ) -> tuple[int, str, str]:
        """Plant one workflow and check it alone.

        Returns:
            The status and what it printed.

        """
        return self.helper(self.flow("ci.yml", text), "--", capfd=capfd)

    def task(self) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree.

        Returns:
            The finished process.

        """
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
def planted(tmp_path: Path, env: support.Env) -> Planted:
    """Give each row its own Planted."""
    return Planted(tmp_path, env=env)


def test_nothing_tracked_is_skipped_by_name(planted: Planted) -> None:
    """No workflow and no template: the task says it skipped."""
    done = planted.task()
    same(done.returncode, 0, done.stderr)
    same(done.stdout.splitlines(), SKIPPED)


def test_an_untracked_workflow_is_no_workflow(planted: Planted) -> None:
    """A workflow git does not track: still skipped, never read."""
    planted.plant(".github/workflows/ci.yml", job("ubuntu-latest"), track=False)
    done = planted.task()
    same(done.returncode, 0, done.stderr)
    same(done.stdout.splitlines(), SKIPPED)


def test_both_lists_through_the_task(planted: Planted) -> None:
    """Offenders in a workflow and a template: one finding, workflows first."""
    planted.plant("workflow-templates/a.yaml", job("macos-latest"))
    planted.flow("z.yml", job("self-hosted"))
    planted.flow("ok.yaml", job("ubuntu-26.04"))
    done = planted.task()
    same(done.returncode, runner_pin.EXIT_RED)
    expected = red(
        ".github/workflows/z.yml(runs-on: self-hosted)",
        "workflow-templates/a.yaml(runs-on: macos-latest)",
        "workflow-templates/a.yaml(floating label: 3:    runs-on: macos-latest)",
    )
    same(done.stderr.splitlines(), expected)


def test_a_template_alone_is_checked(planted: Planted) -> None:
    """Templates and no workflow: checked, not skipped."""
    planted.plant("workflow-templates/a.yml", job("ubuntu-24.04-arm"))
    done = planted.task()
    same(done.returncode, 0, done.stderr)
    same(done.stdout.splitlines(), OK)


@pytest.mark.parametrize(
    "value",
    [
        "ubuntu-24.04",
        "ubuntu-24.04-arm",
        "macos-15-intel",
        '"ubuntu-26.04"',
        "'windows-2025'",
        "ubuntu-24.04 # the named pin",
        "ubuntu-24.04# no blank",
        "${{ matrix.os }}",
        "${{matrix.runner}}",
        "${{ fromJSON(matrix.runner) }}",
    ],
)
def test_pinned_values_are_clean(
    planted: Planted, value: object, capfd: pytest.CaptureFixture[str]
) -> None:
    """Pinned literals, suffixed, quoted or commented: clean."""
    status, out, err = planted.check(job(value), capfd=capfd)
    same(status, 0, err)
    same(out.splitlines(), OK)


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("self-hosted", "self-hosted"),
        ("Ubuntu-24.04", "Ubuntu-24.04"),
        ("ubuntu-24.04 extra", "ubuntu-24.04 extra"),
        ("${{ inputs.runner }}", "${{ inputs.runner }}"),
        ("${{ matrix }}", "${{ matrix }}"),
        ("[ubuntu-24.04]", "[ubuntu-24.04]"),
        ("", ""),
        ("# just a comment", ""),
    ],
)
def test_unpinned_values_are_red(
    planted: Planted, value: object, shown: object, capfd: pytest.CaptureFixture[str]
) -> None:
    """A literal with no version, or an expression that is no matrix one."""
    rel = ".github/workflows/ci.yml"
    status, _out, err = planted.check(job(value), capfd=capfd)
    same(status, runner_pin.EXIT_RED)
    same(err.splitlines(), red(f"{rel}(runs-on: {shown})"))


def test_a_carriage_return_is_part_of_the_value(
    planted: Planted, capfd: pytest.CaptureFixture[str]
) -> None:
    """Lines split on newline alone: a trailing CR leaves the value unpinned.

    The finding writer prints the CR as its escape, as the shell's did.
    """
    status, _out, err = planted.check(
        "jobs:\n  a:\n    runs-on: ubuntu-24.04\r\n", capfd=capfd
    )
    same(status, runner_pin.EXIT_RED)
    expected = red(".github/workflows/ci.yml(runs-on: ubuntu-24.04\\r)")
    same(err.splitlines(), expected)


def test_a_matrix_include_is_red(
    planted: Planted, capfd: pytest.CaptureFixture[str]
) -> None:
    """ubuntu-latest reaching runs-on through a matrix: the first line named."""
    text = (
        "jobs:\n  a:\n    strategy:\n      matrix:\n"
        "        os: [ubuntu-latest, windows-latest]\n"
        "        also: macos-latest\n"
        "    runs-on: ${{ matrix.os }}\n"
    )
    status, _out, err = planted.check(text, capfd=capfd)
    same(status, runner_pin.EXIT_RED)
    named = "        os: [ubuntu-latest, windows-latest]"
    expected = red(f".github/workflows/ci.yml(floating label: 5:{named})")
    same(err.splitlines(), expected)


def test_a_comment_is_not_a_label(
    planted: Planted, capfd: pytest.CaptureFixture[str]
) -> None:
    """A comment line naming ubuntu-latest: not counted."""
    text = "# never ubuntu-latest\n   # nor macos-latest\n" + job("ubuntu-24.04")
    status, out, err = planted.check(text, capfd=capfd)
    same(status, 0, err)
    same(out.splitlines(), OK)


def test_a_trailing_comment_still_counts(
    planted: Planted, capfd: pytest.CaptureFixture[str]
) -> None:
    """A label before a comment on the same line is a float."""
    text = job("ubuntu-24.04") + "      - run: echo ubuntu-latest # no\n"
    status, _out, err = planted.check(text, capfd=capfd)
    same(status, runner_pin.EXIT_RED)
    line = "      - run: echo ubuntu-latest # no"
    expected = red(f".github/workflows/ci.yml(floating label: 6:{line})")
    same(err.splitlines(), expected)


def test_the_order_is_the_arguments(
    planted: Planted, capfd: pytest.CaptureFixture[str]
) -> None:
    """Workflows then templates, each file's runs-on before its float."""
    one = planted.flow("b.yml", job("ubuntu-latest"))
    two = planted.flow("a.yml", job("x"))
    three = planted.plant("workflow-templates/c.yml", job("y"))
    status, _out, err = planted.helper(one, two, "--", three, capfd=capfd)
    same(status, runner_pin.EXIT_RED)
    expected = red(
        f"{one}(runs-on: ubuntu-latest)",
        f"{one}(floating label: 3:    runs-on: ubuntu-latest)",
        f"{two}(runs-on: x)",
        f"{three}(runs-on: y)",
    )
    same(err.splitlines(), expected)


def test_nothing_given_is_clean(
    planted: Planted, capfd: pytest.CaptureFixture[str]
) -> None:
    """A separator and no file: nothing to name."""
    status, out, err = planted.helper("--", capfd=capfd)
    same(status, 0, err)
    same(out.splitlines(), OK)


def test_not_utf8_is_an_error(
    planted: Planted, capfd: pytest.CaptureFixture[str]
) -> None:
    """A workflow that is not UTF-8 is refused by name, exit 2."""
    rel = planted.flow("ci.yml", b"jobs: \xff\n")
    status, _out, err = planted.helper(rel, "--", capfd=capfd)
    same(status, runner_pin.EXIT_ERROR)
    assert err.startswith(f"{TASK}: {rel}: cannot be read as UTF-8:"), err


def test_a_missing_file_is_an_error(
    planted: Planted, capfd: pytest.CaptureFixture[str]
) -> None:
    """A named file that is not there is refused by name, exit 2."""
    status, _out, err = planted.helper("--", "workflow-templates/gone.yml", capfd=capfd)
    same(status, runner_pin.EXIT_ERROR)
    assert err.startswith(
        f"{TASK}: workflow-templates/gone.yml: cannot be read as UTF-8:"
    ), err


def test_no_separator_is_a_usage_error(
    planted: Planted, capfd: pytest.CaptureFixture[str]
) -> None:
    """No `--` between the lists: exit 2 with the usage line."""
    status, _out, err = planted.helper(".github/workflows/ci.yml", capfd=capfd)
    same(status, runner_pin.EXIT_ERROR)
    same(err.splitlines(), [f"{TASK}: {runner_pin.USAGE}"])
