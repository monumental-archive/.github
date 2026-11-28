"""Table tests for lint:cold-attested and its helper (#117, #1934, #1911).

Each row of CASES plants workflows and asserts the exit status and
every line the lint prints, one row per branch of the marker rules: a
marked cache, an unmarked one, a marker on a step with no cache, a
marker a blank line or another line ends, a marker at another indent,
prose naming the marker below a block's first line, the two
cache-by-default shapes with and without `cache: false`, a step closed
by a dedent or by the next file, a nested list item that is not a step,
a commented-out cache, and an empty file. Two rows run the task's own
body, read from mise/config.toml, as mise runs it
(`support.task_run`): the skip when there are no workflows, a clean
tree, an unmarked cache, and two findings in one file, each its own
line in the order found. CASES call the helper's `main` in this
process, in the planted tree. Two more call it with no workflow and
with a missing one. Every row asserts the whole of each stream: a red
row scored on exit alone can pass for the wrong reason.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import cold_attested, decoded
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:cold-attested"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
HEAD = "on: push\njobs:\n  a:\n    runs-on: ubuntu-26.04\n    steps:\n"
MARK = "      # unattested-path: a dev cache, nothing here is signed\n"
CACHE = "      - uses: actions/cache@v5\n        with:\n          path: x\n"
CHECKOUT = "      - uses: actions/checkout@v6\n"
SETUP = "      - uses: actions/setup-go@v7\n"
MISE = "      - uses: jdx/mise-action@v4\n"
OFF = "        with:\n          cache: false\n"
WF = ".github/workflows"
CLEAN = [cold_attested.CLEAN]


def red(*found: tuple[str, str]) -> list[str]:
    """Build the lines some findings print.

    Returns:
        One line per finding, `<task>: <file>:<line>: <message>`.

    """
    return [f"{TASK}: {where}: {message}" for where, message in found]


def default(kind: str) -> str:
    """Name the cache-by-default message for one shape.

    Returns:
        The message.

    """
    return cold_attested.DEFAULT.format(kind)


# name -> (files in order, status, stdout lines, stderr lines)
CASES: dict[str, tuple[dict[str, str], int, list[str], list[str]]] = {
    "a marked cache is clean": (
        {"a.yml": HEAD + CHECKOUT + MARK + CACHE},
        0,
        CLEAN,
        [],
    ),
    "an unmarked cache is red at its uses line": (
        {"a.yml": HEAD + CHECKOUT + CACHE},
        1,
        [],
        red((f"{WF}/a.yml:7", cold_attested.NO_MARKER)),
    ),
    "a marker on a step with no cache is red at the step": (
        {"a.yml": HEAD + MARK + CHECKOUT},
        1,
        [],
        red((f"{WF}/a.yml:7", cold_attested.NO_CACHE)),
    ),
    "a marker a blank line ends introduces no step": (
        {"a.yml": HEAD + MARK + "\n" + CACHE},
        1,
        [],
        red(
            (f"{WF}/a.yml:6", cold_attested.NO_STEP),
            (f"{WF}/a.yml:8", cold_attested.NO_MARKER),
        ),
    ),
    "a marker another line ends introduces no step": (
        {"a.yml": HEAD + CHECKOUT + "        with:\n" + MARK + "          x: y\n"},
        1,
        [],
        red((f"{WF}/a.yml:8", cold_attested.NO_STEP)),
    ),
    "a marker at another indent introduces no step": (
        {"a.yml": HEAD + "    " + MARK + CACHE},
        1,
        [],
        red(
            (f"{WF}/a.yml:6", cold_attested.NO_STEP),
            (f"{WF}/a.yml:7", cold_attested.NO_MARKER),
        ),
    ),
    "prose below a block's first line is no marker": (
        {"a.yml": HEAD + "      # Why:\n" + MARK + CACHE},
        1,
        [],
        red((f"{WF}/a.yml:8", cold_attested.NO_MARKER)),
    ),
    "a setup step that says cache: false is clean": (
        {"a.yml": HEAD + SETUP + OFF + MISE + OFF},
        0,
        CLEAN,
        [],
    ),
    "a setup step left at its default is red": (
        {"a.yml": HEAD + SETUP + CHECKOUT},
        1,
        [],
        red((f"{WF}/a.yml:6", default("actions/setup-*"))),
    ),
    "a mise-action step left at its default is red": (
        {"a.yml": HEAD + MISE},
        1,
        [],
        red((f"{WF}/a.yml:6", default("mise-action"))),
    ),
    "a marked setup step is clean": (
        {"a.yml": HEAD + MARK + SETUP},
        0,
        CLEAN,
        [],
    ),
    "a dedent closes the step": (
        {"a.yml": HEAD + SETUP + "  b:\n    runs-on: x\n"},
        1,
        [],
        red((f"{WF}/a.yml:6", default("actions/setup-*"))),
    ),
    "a nested list item is not a step": (
        {"a.yml": HEAD + MARK + CACHE + "          - name: x\n"},
        0,
        CLEAN,
        [],
    ),
    "a commented-out cache declares nothing": (
        {"a.yml": HEAD + "      # - uses: actions/cache@v5\n\n" + CHECKOUT},
        0,
        CLEAN,
        [],
    ),
    "the next file closes the last one's step, named in its file": (
        {"a.yml": HEAD + MISE, "b.yaml": HEAD + CHECKOUT},
        1,
        [],
        red((f"{WF}/a.yml:6", default("mise-action"))),
    ),
    "an empty file is read as no lines": (
        {"a.yml": HEAD + MARK + CACHE, "b.yml": ""},
        0,
        CLEAN,
        [],
    ),
    "a marker at a file's end is dropped with its file": (
        {"a.yml": HEAD + CHECKOUT + MARK, "b.yml": HEAD + CHECKOUT},
        0,
        CLEAN,
        [],
    ),
    "a carriage return is a blank": (
        {"a.yml": HEAD + MARK + "\r\n" + CACHE},
        1,
        [],
        red(
            (f"{WF}/a.yml:6", cold_attested.NO_STEP),
            (f"{WF}/a.yml:8", cold_attested.NO_MARKER),
        ),
    ),
}


def same[T](got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    assert got == want, msg or None


def body() -> str:
    """Read lint:cold-attested's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


class ColdAttested:
    """Planted workflows; the lint's verdict on them."""

    def __init__(
        self, tmp_path: Path, *, env: support.Env, track: support.Track
    ) -> None:
        """Plant an empty workflows directory."""
        self.make_env = env
        self.track_tree = track
        self.tree = tmp_path
        (self.tree / WF).mkdir(parents=True)
        self.env = self.make_env(
            MISE_TASK_NAME=TASK,
            ORG_BELT_DIR=str(BELT),
            ORG_PYTHON=sys.executable,
        )

    def plant(self, files: dict[str, str]) -> list[str]:
        """Write workflows.

        Returns:
            Their paths from the tree's top, in order.

        """
        names = []
        for name, text in files.items():
            rel = f"{WF}/{name}"
            (self.tree / rel).write_text(text, encoding="utf-8")
            names.append(rel)
        return names

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
def cold_attested_state(
    tmp_path: Path, env: support.Env, track: support.Track
) -> ColdAttested:
    """Give each row its own ColdAttested."""
    return ColdAttested(tmp_path, env=env, track=track)


def test_no_workflows_is_skipped_by_name(cold_attested_state: ColdAttested) -> None:
    """No workflow file: the task says it skipped."""
    done = cold_attested_state.task()
    same(done.returncode, 0, done.stderr)
    skipped = ["lint:cold-attested: no workflows, skipped"]
    same(done.stdout.splitlines(), skipped)


def test_a_clean_tree_passes_through_the_task(
    cold_attested_state: ColdAttested,
) -> None:
    """No cache anywhere: green, the summary on stdout."""
    cold_attested_state.plant({"a.yml": HEAD + CHECKOUT})
    done = cold_attested_state.task()
    same((done.returncode, done.stderr), (0, ""))
    same(done.stdout.splitlines(), CLEAN)


def test_each_finding_is_its_own_line_through_the_task(
    cold_attested_state: ColdAttested,
) -> None:
    """A default-cache step and a stray marker: two findings, in order."""
    cold_attested_state.plant({"a.yml": HEAD + MISE + MARK + "\n"})
    done = cold_attested_state.task()
    same((done.returncode, done.stdout), (1, ""))
    same(
        done.stderr.splitlines(),
        red(
            (f"{WF}/a.yml:7", cold_attested.NO_STEP),
            (f"{WF}/a.yml:6", default("mise-action")),
        ),
    )


def test_an_unmarked_cache_is_red_through_the_task(
    cold_attested_state: ColdAttested,
) -> None:
    """The task body hands the helper every workflow, in glob order."""
    cold_attested_state.plant({"a.yaml": HEAD + CACHE, "b.yml": HEAD + MARK + CACHE})
    done = cold_attested_state.task()
    same(done.returncode, cold_attested.EXIT_RED)
    same(done.stdout, "")
    same(
        done.stderr.splitlines(),
        red((f"{WF}/a.yaml:6", cold_attested.NO_MARKER)),
    )


@pytest.mark.parametrize("case", CASES.values(), ids=list(CASES))
def test_cases(
    case: tuple[dict[str, str], int, list[str], list[str]],
    cold_attested_state: ColdAttested,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each row of CASES, through the helper's main."""
    files, status, out, err = case
    cold_attested_state.enter(monkeypatch)
    done = cold_attested.main([*cold_attested_state.plant(files)])
    said, complained = capfd.readouterr()
    same(done, status, complained)
    same(said.splitlines(), out)
    same(complained.splitlines(), err)


def test_no_workflow_is_a_usage_error(
    cold_attested_state: ColdAttested,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Called with nothing to read: exit 2, saying how to call it."""
    cold_attested_state.enter(monkeypatch)
    with pytest.raises(SystemExit) as ended:
        cold_attested.main([])
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt cold-attested "), err
    assert err.splitlines()[-1] == (
        "belt cold-attested: error: the following arguments are required: WORKFLOW"
    )


def test_a_missing_workflow_is_a_read_error(
    cold_attested_state: ColdAttested,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A named file that is not there: exit 2, naming it."""
    cold_attested_state.enter(monkeypatch)
    status = cold_attested.main([f"{WF}/gone.yml"])
    out, err = capfd.readouterr()
    same(status, cold_attested.EXIT_ERROR)
    same(out, "")
    assert err.startswith(f"{TASK}: {WF}/gone.yml: cannot be read as UTF-8: "), err


def test_a_workflow_not_utf8_is_a_read_error(
    cold_attested_state: ColdAttested,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bytes that are not UTF-8: exit 2, naming the file."""
    (cold_attested_state.tree / WF / "a.yml").write_bytes(b"on: push\n# \xe9\n")
    cold_attested_state.enter(monkeypatch)
    status = cold_attested.main([f"{WF}/a.yml"])
    out, err = capfd.readouterr()
    same(status, cold_attested.EXIT_ERROR)
    same(out, "")
    assert err.startswith(f"{TASK}: {WF}/a.yml: cannot be read as UTF-8: "), err
