"""Table tests for `test:python-properties` and its audit half (#1332).

The bodies under test are the tasks' own `run` strings, read out of
mise/config.toml with tomllib and run by bash in a planted git tree,
and they call the real `mise/python-properties.sh` and
`belt python-properties`. The rows that run a property use the
belt's real Hypothesis tool venv, found on PATH as the gate finds it:
a stub could not print Hypothesis's own falsifying example, which is
what a failing row exists to assert. The rows about the shell's own
refusals (nothing tracked, Hypothesis missing, the wrong interpreter,
an ordinary test importing Hypothesis) need no Hypothesis at all.

This file never imports Hypothesis: it runs the tool venv's
interpreter as a subprocess, so the ordinary suite stays runnable on
an interpreter with nothing installed.

Each row asserts the message, not just the exit status: a planted row
scored on exit alone can pass for the wrong reason.
"""

import os
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import decoded, python_properties, shard, workflow_yaml
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# The processes the run fans out over when it is handed four files.
WORKERS = 4

CANON = support.CANON
BELT = CANON / "mise"
BASH = support.tool("bash")
GIT = support.tool("git")
PYTHON = support.tool("python3")

CONFIG = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
TASKS = decoded.as_object(CONFIG["tasks"], "the belt's [tasks]")


def task_run(name: str) -> str:
    """Read one belt task's run string.

    Returns:
        The task body.

    """
    task = decoded.as_object(TASKS[name], name)
    return decoded.as_str(task["run"], f"{name}'s run")


GATE = task_run("test:python-properties")
AUDIT = task_run("audit:python-properties")
# Each body's task name, for the run that hands it to support.task_run.
NAMES = {GATE: "test:python-properties", AUDIT: "audit:python-properties"}

HOLDS = """\
from hypothesis import strategies as st
from belt.typed_hypothesis import given


@given(st.integers())
def test_double_is_even(n):
    assert (2 * n) % 2 == 0
"""

FAILS = """\
from hypothesis import strategies as st
from belt.typed_hypothesis import given


@given(st.integers(min_value=0))
def test_small(n):
    assert n < 10
"""

# A property taking Hypothesis's own `given`, which the belt's mypy reds
# on every property (#1604): the gate names the file and the remedy.
UNTYPED = """\
from hypothesis import given, strategies as st


@given(st.integers())
def test_double_is_even(n):
    assert (2 * n) % 2 == 0
"""

NO_PROPERTY = """\
def test_plain():
    assert True
"""

OWN_BOUNDS = """\
from hypothesis import settings, strategies as st
from belt.typed_hypothesis import given


@settings(max_examples=3)
@given(st.integers())
def test_few(n):
    assert isinstance(n, int)
"""

# A Hypothesis venv built on some other interpreter: its python answers
# the version probe with a version that is not the belt's.
STUB_PYTHON = """\
#!/usr/bin/env bash
echo "3.0.0 (stub interpreter)"
"""


# Every inherited GIT_* goes (#857): under the pre-push hook git exports
# GIT_DIR and friends, and `git init` / `git add` would then write the
# REAL repository's index instead of the planted one. Measured here:
# the first push of this file staged the deletion of every tracked file.
GIT_ISOLATED = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def plant(root: Path, files: dict[str, str], *, env: support.Env) -> None:
    """Make `root` a git tree tracking `files`."""
    subprocess.run([GIT, "init", "-q", str(root)], check=True, env=env(**GIT_ISOLATED))
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if files:
        subprocess.run(
            [GIT, "-C", str(root), "add", "-A"], check=True, env=env(**GIT_ISOLATED)
        )


def porcelain(root: Path, *, env: support.Env) -> str:
    """Read a planted tree's status, ignored files included.

    Returns:
        `git status --porcelain --ignored`'s output.

    """
    return subprocess.run(
        [GIT, "-C", str(root), "status", "--porcelain", "--ignored"],
        env=env(**GIT_ISOLATED),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def run(
    body: str,
    files: dict[str, str],
    *,
    tool_dir: str | None,
    env: dict[str, str] | None = None,
    make_env: support.Env,
) -> subprocess.CompletedProcess[str]:
    """Run a task body in a planted tree.

    Returns:
        The finished process, text mode.

    """
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch) / "repo"
        plant(root, files, env=make_env)
        path = [str(Path(PYTHON).parent), str(Path(GIT).parent), "/usr/bin", "/bin"]
        if tool_dir:
            path.insert(0, tool_dir)
        environ = make_env(
            PATH=os.pathsep.join(path),
            HOME=scratch,
            ORG_BELT_DIR=str(BELT),
            ORG_PYTHON=str(PYTHON),
            **env or {},
        )
        as_mise = support.task_run(NAMES[body], body)
        planted = porcelain(root, env=make_env)
        done = subprocess.run(
            as_mise.argv,
            cwd=root,
            env=environ | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )
        # The tree as planted, whatever the run decided: nothing written
        # into it, ignored or not (Hypothesis's `.hypothesis`, pytest's
        # cache).
        left = porcelain(root, env=make_env)
        assert left == planted, f"the task wrote into the tree:\n{left}"
        return done


def real_tool_dir() -> str:
    """Name the directory holding the belt's `hypothesis` entry point.

    Returns:
        The directory, which the row puts on PATH.

    """
    return str(Path(support.tool("pytest")).parent)


# What the shell decides before any property runs.
def test_no_prop_file_skips_clean_by_name(env: support.Env) -> None:
    """Nothing tracked: exit 0, skipped, named."""
    done = run(GATE, {"test_x.py": NO_PROPERTY}, tool_dir=None, make_env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout == "test:python-properties: no prop_*.py tracked, skipped\n"


def test_audit_skips_clean_by_name(env: support.Env) -> None:
    """The audit half skips the same way, under its own name."""
    done = run(AUDIT, {}, tool_dir=None, make_env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout == "audit:python-properties: no prop_*.py tracked, skipped\n"


def test_a_prop_file_without_hypothesis_is_red(env: support.Env) -> None:
    """A property file and no tool: red, with the remedy."""
    done = run(GATE, {"prop_x.py": HOLDS}, tool_dir=None, make_env=env)
    assert (done.returncode, done.stderr) == (
        1,
        (
            "test:python-properties: pytest missing — the belt pins"
            " pipx:pytest; run: mise install\n"
        ),
    )


def test_an_ordinary_test_importing_hypothesis_is_red(env: support.Env) -> None:
    """A test_*.py that imports Hypothesis: red, the file named."""
    done = run(
        GATE,
        {"prop_x.py": HOLDS, "pkg/test_leak.py": HOLDS},
        tool_dir=None,
        make_env=env,
    )
    assert done.returncode == 1
    assert (
        "import Hypothesis (or cannot be read) but are not prop_*.py files:"
        in done.stderr
    )
    assert "  pkg/test_leak.py\n" in done.stderr


def test_given_from_hypothesis_is_red(env: support.Env) -> None:
    """A property taking Hypothesis's own `given`: red, file and remedy named."""
    done = run(
        GATE,
        {"prop_x.py": HOLDS, "pkg/prop_y.py": UNTYPED},
        tool_dir=None,
        make_env=env,
    )
    assert done.returncode == 1
    assert (
        "test:python-properties: these take given from hypothesis,"
        " not the belt's typed one:\n  pkg/prop_y.py\n"
        "  write: from belt.typed_hypothesis import given\n"
    ) in done.stderr


def test_hypothesis_source_in_a_string_is_not_an_import(env: support.Env) -> None:
    """A fixture carrying Hypothesis source as text: not a leak."""
    fixture = f"HOLDS = {HOLDS!r}\n"
    done = run(
        GATE, {"prop_x.py": HOLDS, "test_x.py": fixture}, tool_dir=None, make_env=env
    )
    assert done.returncode == 1
    assert "import Hypothesis" not in done.stderr
    assert (
        "test:python-properties: pytest missing — the belt pins"
        " pipx:pytest; run: mise install\n"
    ) in done.stderr


def test_a_venv_on_another_interpreter_is_red(env: support.Env) -> None:
    """The tool venv's python is not the belt's: red, both named."""
    with tempfile.TemporaryDirectory() as venv:
        bin_dir = Path(venv) / "bin"
        bin_dir.mkdir()
        for name in ("pytest", "python"):
            (bin_dir / name).write_text(STUB_PYTHON, encoding="utf-8")
            (bin_dir / name).chmod(0o755)
        done = run(GATE, {"prop_x.py": HOLDS}, tool_dir=str(bin_dir), make_env=env)
    assert done.returncode == 1
    assert re.search(
        r"\Atest:python-properties: pytest runs on '3\.0\.0 \(stub"
        r" interpreter\)' but the suite's python3 is '3\.[^']+' — the tool"
        r" venv must be built on the belt's python pin \(reinstall: mise"
        r" install -f pipx:pytest\)\n\Z",
        done.stderr,
    )


# Rows that run a property on the belt's Hypothesis.
def test_a_passing_file_holds(env: support.Env) -> None:
    """One property that holds: exit 0, the test and the count."""
    done = run(GATE, {"pkg/prop_ok.py": HOLDS}, tool_dir=real_tool_dir(), make_env=env)
    assert done.returncode == 0, done.stderr
    assert "1 passed" in done.stdout
    assert done.stdout.endswith("test:python-properties: 1 file(s) held\n")


def test_a_failing_property_is_red_with_its_example(env: support.Env) -> None:
    """A property that fails: red, the shrunk example printed."""
    done = run(GATE, {"prop_bad.py": FAILS}, tool_dir=real_tool_dir(), make_env=env)
    assert done.returncode == 1
    assert "FAILED prop_bad.py::test_small - assert 10 < 10\n" in done.stdout
    assert "Failing test case: test_small(" in done.stdout
    assert "    n=10,\n" in done.stdout
    assert done.stderr == ("test:python-properties: pytest exited 1; failures above\n")


def test_a_file_with_no_property_is_refused(env: support.Env) -> None:
    """A prop_*.py holding no @given test: red, named."""
    done = run(
        GATE, {"prop_none.py": NO_PROPERTY}, tool_dir=real_tool_dir(), make_env=env
    )
    assert done.returncode == 1
    assert (
        "test:python-properties: prop_none.py: holds no Hypothesis test (no @given)"
    ) in done.stderr


def test_a_test_restating_a_bound_is_refused(env: support.Env) -> None:
    """@settings(max_examples=…) on a test: red, the bound named."""
    done = run(
        GATE, {"prop_own.py": OWN_BOUNDS}, tool_dir=real_tool_dir(), make_env=env
    )
    assert done.returncode == 1
    assert (
        "test:python-properties: prop_own.py::test_few sets its own bounds"
        " (max_examples=3); the belt's profile owns them"
    ) in done.stderr


def test_the_audit_prints_its_seed_and_honours_a_given_one(env: support.Env) -> None:
    """ORG_PROPERTY_SEED reproduces a run; the seed comes first."""
    done = run(
        AUDIT,
        {"prop_bad.py": FAILS},
        tool_dir=real_tool_dir(),
        env={"ORG_PROPERTY_SEED": "7"},
        make_env=env,
    )
    assert done.returncode == 1
    assert done.stdout.startswith(
        "audit:python-properties: seed 7 (reproduce: ORG_PROPERTY_SEED=7)\n"
    ), done.stdout
    assert "    n=10,\n" in done.stdout
    assert (
        "audit:python-properties: seed 7 — commit each falsifying example above"
        " as an @example row with its fix"
    ) in done.stderr


SHARD_FILES = {"a/prop_one.py": HOLDS, "b/prop_two.py": HOLDS, "prop_three.py": HOLDS}


# ORG_SHARD=k/n runs one shard of the files (#1631, #2160).
def test_a_shard_runs_every_nth_sorted_file(env: support.Env) -> None:
    """2/2 of three sorted files is the second: named, then run."""
    done = run(
        GATE,
        SHARD_FILES,
        tool_dir=real_tool_dir(),
        env={"ORG_SHARD": "2/2"},
        make_env=env,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("test:python-properties: shard 2/2, 1 of 3 file(s)\n")
    assert "b/prop_two.py: 1\n" in done.stdout
    assert "1 passed" in done.stdout
    assert done.stdout.endswith("test:python-properties: 1 file(s) held\n")


@pytest.mark.parametrize("spec", ["3/2", "0/8", "x/8", "1/0", "\u00b2/8"])
def test_a_malformed_shard_is_refused_by_name(spec: str, env: support.Env) -> None:
    """A k above n, a zero, or not digits: exit 2, the value named."""
    done = run(
        GATE,
        SHARD_FILES,
        tool_dir=real_tool_dir(),
        env={"ORG_SHARD": spec},
        make_env=env,
    )
    assert done.returncode == python_properties.EXIT_USAGE
    assert (
        f"test:python-properties: ORG_SHARD={spec!r} is not a shard (k/n, 1 <= k <= n)"
    ) in done.stderr


def test_two_shards_run_every_file_once(env: support.Env) -> None:
    """1/2 and 2/2, run for real, report every file once between them."""
    with tempfile.TemporaryDirectory() as reports:
        for spec in ("1/2", "2/2"):
            done = run(
                GATE,
                SHARD_FILES,
                tool_dir=real_tool_dir(),
                env={
                    "ORG_SHARD": spec,
                    "ORG_SHARD_TASK": "test:python-properties",
                    "ORG_SHARD_REPORTS": reports,
                },
                make_env=env,
            )
            assert done.returncode == 0, done.stderr
        got = shard.load(Path(reports))
        ran = sorted(
            decoded.as_str(r, "ran")
            for d in got
            for r in decoded.as_array(d["ran"], "ran")
        )
        want: list[str] = sorted(SHARD_FILES)
        assert want == ran
        none: list[str] = []
        assert none == shard.union(got, {"test:python-properties": 2})


def test_an_empty_shard_says_so_and_passes(env: support.Env) -> None:
    """More shards than files: the empty one is named and exits 0."""
    done = run(
        GATE,
        SHARD_FILES,
        tool_dir=real_tool_dir(),
        env={"ORG_SHARD": "8/8"},
        make_env=env,
    )
    assert done.returncode == 0, done.stderr
    assert (
        done.stdout
        == "test:python-properties: shard 8/8 holds none of the 3 file(s), skipped\n"
    )


def test_the_audit_times_its_slowest_tests(env: support.Env) -> None:
    """The audit prints pytest's ten slowest, so its bound is measured (#1631)."""
    done = run(
        AUDIT,
        {"prop_ok.py": HOLDS},
        tool_dir=real_tool_dir(),
        env={"ORG_PROPERTY_SEED": "7"},
        make_env=env,
    )
    assert done.returncode == 0, done.stderr
    assert "slowest 10 durations" in done.stdout
    assert done.stdout.endswith("audit:python-properties: 1 file(s) held\n")


# A property file whose test meets its siblings' before its first
# example, once per process, and leaves its process's pid in the
# directory PLANT_MEET names, so a row can tell one process from four.
# It waits until PLANT_PARTIES pid files are there. Run in turn, the
# first can never meet the rest: past PLANT_CEILING seconds it fails,
# and marks the rendezvous broken so the files after it fail at once.
# The outcome is kept, so every example of a test meets the same one.
# Four files finishing at all proves they ran at once; no clock decides
# the verdict.
CEILING = 60
MEETS = f"""\
import os
import time
from pathlib import Path
from hypothesis import strategies as st
from belt.typed_hypothesis import given

OUTCOME = []


def meet():
    if OUTCOME:
        if OUTCOME[0]:
            raise RuntimeError(OUTCOME[0])
        return
    meet = Path(os.environ["PLANT_MEET"])
    parties = int(os.environ["PLANT_PARTIES"])
    ceiling = float(os.environ.get("PLANT_CEILING", "{CEILING}"))
    (meet / str(os.getpid())).touch()
    began = time.monotonic()
    while True:
        if (meet / "broken").exists():
            OUTCOME.append("rendezvous broken")
            raise RuntimeError(OUTCOME[0])
        met = sum(1 for p in meet.iterdir() if p.name.isdigit())
        if met >= parties:
            OUTCOME.append("")
            return
        if time.monotonic() - began > ceiling:
            (meet / "broken").touch()
            OUTCOME.append(
                f"rendezvous timed out: {{met}} of {{parties}} after {{ceiling:g}} s"
            )
            raise RuntimeError(OUTCOME[0])
        time.sleep(0.01)


@given(st.integers())
def test_double_is_even(n):
    meet()
    assert (2 * n) % 2 == 0
"""
FOUR = {f"{d}/prop_{d}.py": MEETS for d in "abcd"}


def pids(meet: dict[str, str]) -> set[str]:
    """Read the processes the MEETS tests ran in.

    Returns:
        Each distinct pid left in the rendezvous directory.

    """
    where = Path(meet["PLANT_MEET"])
    return {p.name for p in where.iterdir() if p.name.isdigit()}


class FanOut:
    """No shard named: the runner fans out over its own shards (#1741)."""

    def __init__(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path

    def meet(self, parties: int, **env: str) -> dict[str, str]:
        """Name a fresh rendezvous for `parties` files.

        Returns:
            The environment the MEETS files read, plus `env`.

        """
        scratch = tempfile.mkdtemp(dir=self.tmp)
        return {"PLANT_MEET": scratch, "PLANT_PARTIES": str(parties)} | env


@fixture
def fan_out(tmp_path: Path) -> FanOut:
    """Give each row its own FanOut.

    Returns:
        The row's FanOut.

    """
    return FanOut(tmp_path)


def test_four_files_run_side_by_side(fan_out: FanOut, env: support.Env) -> None:
    """Four files that wait for each other all finish, in four workers."""
    meet = fan_out.meet(WORKERS, ORG_TEST_WORKERS="8")
    done = run(GATE, FOUR, tool_dir=real_tool_dir(), env=meet, make_env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(pids(meet)) == WORKERS, done.stdout
    assert "4 passed" in done.stdout
    assert done.stdout.endswith("test:python-properties: 4 file(s) held\n")


def test_files_run_in_turn_never_meet(fan_out: FanOut, env: support.Env) -> None:
    """ORG_TEST_WORKERS=1 over files that wait for four: the ceiling fails it."""
    meet = fan_out.meet(4, ORG_TEST_WORKERS="1", PLANT_CEILING="2")
    done = run(GATE, FOUR, tool_dir=real_tool_dir(), env=meet, make_env=env)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "RuntimeError: rendezvous timed out: 1 of 4 after 2 s" in done.stdout
    assert "4 failed" in done.stdout
    assert len(pids(meet)) == 1, done.stdout


def test_one_worker_runs_every_file_in_one_process(
    fan_out: FanOut, env: support.Env
) -> None:
    """ORG_TEST_WORKERS=1: one pid, every file held."""
    meet = fan_out.meet(1, ORG_TEST_WORKERS="1")
    done = run(GATE, FOUR, tool_dir=real_tool_dir(), env=meet, make_env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(pids(meet)) == 1, done.stdout
    assert done.stdout.endswith("test:python-properties: 4 file(s) held\n")


def test_a_named_shard_starts_no_worker(fan_out: FanOut, env: support.Env) -> None:
    """ORG_SHARD=1/2 with workers to spare: one process, its two files."""
    meet = fan_out.meet(1, ORG_TEST_WORKERS="8", ORG_SHARD="1/2")
    done = run(GATE, FOUR, tool_dir=real_tool_dir(), env=meet, make_env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(pids(meet)) == 1, done.stdout
    assert done.stdout.startswith("test:python-properties: shard 1/2, 2 of 4 file(s)\n")
    assert "2 passed" in done.stdout
    assert done.stdout.endswith("test:python-properties: 2 file(s) held\n")


def test_one_of_one_is_the_whole_run(fan_out: FanOut, env: support.Env) -> None:
    """ORG_SHARD=1/1, a split row's unsharded task: it fans out as unset."""
    meet = fan_out.meet(WORKERS, ORG_TEST_WORKERS="8", ORG_SHARD="1/1")
    done = run(GATE, FOUR, tool_dir=real_tool_dir(), env=meet, make_env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(pids(meet)) == WORKERS, done.stdout


# Both property jobs are bounded, sharded and share one seed (#1631).
def test_both_jobs_carry_a_bound_planned_shards_and_one_seed() -> None:
    """A timeout, the plan's shards as k/n, and the seed from a planning job.

    One job since #1858: the canon runs the consumers' workflow
    through self-deep-tests.yml rather than a copy of the job. The
    count is the plan's since #2160: the repository's shards.toml,
    else the canon's default.
    """
    seeds = {
        "repo-deep-tests.yml": "needs.python-properties-seed.outputs.seed",
    }
    for workflow, seed in seeds.items():
        path = CANON / ".github" / "workflows" / workflow
        jobs = workflow_yaml.jobs(workflow_yaml.document(path))
        job = jobs["python-properties"]
        assert isinstance(job.get("timeout-minutes"), int)
        strategy = decoded.as_object(job["strategy"], "strategy")
        matrix = decoded.as_object(strategy["matrix"], "matrix")
        planned = "needs.python-properties-seed.outputs"
        assert f"${{{{ fromJSON({planned}.python-shards) }}}}" == matrix["shard"]
        env = {
            k: v
            for scope in (job, *workflow_yaml.steps(job))
            for k, v in decoded.as_object(scope.get("env") or {}, "env").items()
        }
        assert (
            f"${{{{ matrix.shard }}}}/${{{{ {planned}.python-of }}}}"
            == env["ORG_SHARD"]
        )
        assert f"${{{{ {seed} }}}}" == env["ORG_PROPERTY_SEED"]
        planner = jobs["python-properties-seed"]
        outputs = decoded.as_object(planner["outputs"], "outputs")
        for name in ("python-shards", "python-of"):
            assert f"${{{{ steps.shards.outputs.{name} }}}}" == outputs[name]


# The runner in this process, over a stand-in pytest: the rows above
# start the task body, whose runner is a child they do not measure.
GATE_LABEL = python_properties.LABELS["gate"]
AUDIT_LABEL = python_properties.LABELS["audit"]
# The plugin as the runner reads it, after a collection: a stand-in,
# since importing the real one here is a module pytest could not
# rewrite when a row's own pytest loads it.


class StandIn(types.ModuleType):
    """The plugin module as the runner reads it: its refusals."""

    def __init__(self) -> None:
        """Name the module as the plugin, refusing nothing."""
        super().__init__(python_properties.PLUGIN)
        self.REFUSED: list[str] = []


STAND_IN = StandIn()


class Pytest:
    """A stand-in for `pytest.main`: each call recorded, each answer told."""

    def __init__(self, *answers: tuple[int, list[str]]) -> None:
        """Answer the calls in turn: a status, and what the plugin refused."""
        self.answers = list(answers)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> int:
        """Record one call and give its answer.

        Returns:
            The status told for this call.

        """
        self.calls.append(argv)
        status, refused = self.answers.pop(0)
        STAND_IN.REFUSED = refused
        return status


@fixture
def labelled(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Ready `run` with a stand-in pytest, restoring what a run sets.

    Returns:
        A function that names the task `profile` runs under.

    """
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    monkeypatch.setitem(sys.modules, python_properties.PLUGIN, STAND_IN)
    STAND_IN.REFUSED = []
    for name in (python_properties.STORAGE, "PYTHONDONTWRITEBYTECODE"):
        monkeypatch.delenv(name, raising=False)

    def label(profile: str) -> None:
        monkeypatch.setenv("MISE_TASK_NAME", python_properties.LABELS[profile])

    return label


# The runner over a stand-in pytest: collect, then run.
def test_a_held_run_collects_then_runs(
    labelled: Callable[[str], None], capfd: pytest.CaptureFixture[str]
) -> None:
    """Collected with no refusal, run, held: exit 0 and the count."""
    stub = Pytest((0, []), (0, []))
    labelled("gate")
    status = python_properties.run(
        "gate", [Path("a/prop_x.py")], {"ORG_TEST_WORKERS": "1"}, pytest_main=stub
    )
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, f"{GATE_LABEL}: 1 file(s) held\n", "")
    collect, run = stub.calls
    assert collect[-3:] == ["a/prop_x.py", "--collect-only", "-q"]
    assert run[-1] == "a/prop_x.py"
    assert "--collect-only" not in run
    assert os.environ[python_properties.STORAGE]
    assert sys.dont_write_bytecode
    assert os.environ["PYTHONDONTWRITEBYTECODE"] == "1"


def test_each_refusal_is_named_and_nothing_runs(
    labelled: Callable[[str], None], capfd: pytest.CaptureFixture[str]
) -> None:
    """The plugin refused two: both named, exit 1, no run after."""
    refused = ["prop_x.py: holds no Hypothesis test (no @given)", "prop_y.py::t"]
    stub = Pytest((0, refused))
    labelled("gate")
    status = python_properties.run(
        "gate", [Path("prop_x.py"), Path("prop_y.py")], {}, pytest_main=stub
    )
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert err == "".join(f"{GATE_LABEL}: {line}\n" for line in refused)
    assert len(stub.calls) == 1


def test_a_collection_that_failed_is_red_by_its_status(
    labelled: Callable[[str], None], capfd: pytest.CaptureFixture[str]
) -> None:
    """Pytest could not collect (an import error): exit 1, its status named."""
    stub = Pytest((2, []))
    labelled("gate")
    status = python_properties.run("gate", [Path("prop_x.py")], {}, pytest_main=stub)
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert err == f"{GATE_LABEL}: pytest could not collect the files (exit 2)\n"


def test_a_failed_gate_run_is_red(
    labelled: Callable[[str], None], capfd: pytest.CaptureFixture[str]
) -> None:
    """A property failed: exit 1, pytest's status named."""
    labelled("gate")
    status = python_properties.run(
        "gate", [Path("prop_x.py")], {}, pytest_main=Pytest((0, []), (1, []))
    )
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert err == f"{GATE_LABEL}: pytest exited 1; failures above\n"


def test_the_runner_prints_the_audit_seed_and_honours_a_given_one(
    labelled: Callable[[str], None], capfd: pytest.CaptureFixture[str]
) -> None:
    """ORG_PROPERTY_SEED reproduces a run; the seed comes first."""
    stub = Pytest((0, []), (1, []))
    labelled("audit")
    status = python_properties.run(
        "audit",
        [Path("prop_x.py")],
        {"ORG_PROPERTY_SEED": "7", "ORG_TEST_WORKERS": "1"},
        pytest_main=stub,
    )
    out, err = capfd.readouterr()
    assert status == 1
    assert out == f"{AUDIT_LABEL}: seed 7 (reproduce: ORG_PROPERTY_SEED=7)\n"
    assert err == (
        f"{AUDIT_LABEL}: seed 7 — commit each falsifying example above as an"
        " @example row with its fix\n"
    )
    assert "--hypothesis-seed=7" not in stub.calls[0]
    assert [
        word for word in stub.calls[1] if word.startswith("--d") or "seed" in word
    ] == ["--hypothesis-seed=7", "--durations=10"]


def test_more_workers_than_one_spread_the_files(
    labelled: Callable[[str], None], capfd: pytest.CaptureFixture[str]
) -> None:
    """Two workers asked for, two files: `-n 2` on the run, none to collect."""
    stub = Pytest((0, []), (0, []))
    labelled("gate")
    files = [Path("prop_a.py"), Path("prop_b.py")]
    status = python_properties.run(
        "gate", files, {python_properties.WORKERS: "2"}, pytest_main=stub
    )
    assert (status, capfd.readouterr().out) == (0, f"{GATE_LABEL}: 2 file(s) held\n")
    collect, run_ = stub.calls
    assert "-n" not in collect
    assert run_[run_.index("-n") + 1] == "2"


@pytest.mark.parametrize("named", ["", "0", "x"])
def test_workers_default_to_the_cores_at_most_eight(named: str) -> None:
    """ORG_TEST_WORKERS unset, zero or not a number: the cores, capped."""
    cores = min(os.cpu_count() or 1, python_properties.MAX_WORKERS)
    assert python_properties.workers({python_properties.WORKERS: named}) == cores


def test_an_audit_with_no_seed_chooses_and_prints_one(
    labelled: Callable[[str], None], capfd: pytest.CaptureFixture[str]
) -> None:
    """No ORG_PROPERTY_SEED: a seed is drawn, printed, and handed to pytest."""
    stub = Pytest((0, []), (0, []))
    labelled("audit")
    status = python_properties.run("audit", [Path("prop_x.py")], {}, pytest_main=stub)
    out = capfd.readouterr().out
    assert status == 0
    seed = re.fullmatch(
        rf"{AUDIT_LABEL}: seed (\d+) \(reproduce: ORG_PROPERTY_SEED=\1\)\n"
        rf"{AUDIT_LABEL}: 1 file\(s\) held\n",
        out,
    )
    assert seed is not None
    assert f"--hypothesis-seed={seed.group(1)}" in stub.calls[1]


class Ran:
    """A stand-in for the run itself: what it was handed, answering 0."""

    def __init__(self) -> None:
        """Hold no call yet."""
        self.calls: list[tuple[str, list[Path], dict[str, str]]] = []

    def __call__(self, profile: str, files: list[Path], environ: dict[str, str]) -> int:
        """Record one call.

        Returns:
            0, as a run that held.

        """
        self.calls.append((profile, files, environ))
        return 0


@fixture
def ran(monkeypatch: pytest.MonkeyPatch, as_task: Callable[[str], None]) -> Ran:
    """Put a stand-in run under `main`, naming the gate's task.

    Returns:
        The stand-in, holding each call `main` made.

    """
    stand_in = Ran()
    monkeypatch.setattr(python_properties, "run", stand_in)
    monkeypatch.setenv("ORG_SHARD_REPORTS", "")
    as_task(GATE_LABEL)
    return stand_in


def test_an_unsharded_run_takes_every_file_as_given(ran: Ran) -> None:
    """No ORG_SHARD: every file, in the order given, the workers untouched."""
    assert python_properties.main(["gate", "b/prop_y.py", "a/prop_x.py"]) == 0
    ((profile, files, environ),) = ran.calls
    assert (profile, files) == ("gate", [Path("b/prop_y.py"), Path("a/prop_x.py")])
    assert python_properties.WORKERS not in environ


def test_one_of_one_is_every_file_unsharded(
    ran: Ran, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """ORG_SHARD=1/1 is the whole run: no shard line, the workers untouched."""
    monkeypatch.setenv(shard.VARIABLE, "1/1")
    assert python_properties.main(["gate", "prop_x.py"]) == 0
    assert not capfd.readouterr().out
    ((_, _, environ),) = ran.calls
    assert python_properties.WORKERS not in environ


def test_a_named_shard_runs_its_share_in_one_process(
    ran: Ran, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """2/2 of three files is the second sorted one, on one worker."""
    monkeypatch.setenv(shard.VARIABLE, "2/2")
    argv = ["gate", "c/prop_z.py", "a/prop_x.py", "b/prop_y.py"]
    assert python_properties.main(argv) == 0
    assert capfd.readouterr().out == f"{GATE_LABEL}: shard 2/2, 1 of 3 file(s)\n"
    ((_, files, environ),) = ran.calls
    assert files == [Path("b/prop_y.py")]
    assert environ[python_properties.WORKERS] == "1"


def test_a_shard_holding_no_file_says_so_and_runs_nothing(
    ran: Ran, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """3/3 of two files holds none: exit 0, said, nothing run."""
    monkeypatch.setenv(shard.VARIABLE, "3/3")
    assert python_properties.main(["gate", "prop_a.py", "prop_b.py"]) == 0
    held = "holds none of the 2 file(s), skipped"
    assert capfd.readouterr().out == f"{GATE_LABEL}: shard 3/3 {held}\n"
    assert ran.calls == []


def test_a_shard_that_is_not_k_of_n_is_a_usage_error(
    ran: Ran, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """ORG_SHARD=3/2 is refused by name, exit 2, nothing run."""
    monkeypatch.setenv(shard.VARIABLE, "3/2")
    assert python_properties.main(["gate", "prop_a.py"]) == python_properties.EXIT_USAGE
    assert capfd.readouterr().err == (
        f"{GATE_LABEL}: ORG_SHARD='3/2' is not a shard (k/n, 1 <= k <= n)\n"
    )
    assert ran.calls == []


def test_no_plugin_loaded_is_no_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A collection that never loaded the plugin refused nothing."""
    monkeypatch.delitem(sys.modules, python_properties.PLUGIN, raising=False)
    assert python_properties.refused() == []


# The real plugin, collecting planted property files in a pytest this
# row runs in process: what it refuses, and what it lets through.
PLANTED_HOLDS = """\
from hypothesis import given, strategies as st


@given(st.integers())
def test_holds(n):
    assert 2 * n % 2 == 0
"""
PLANTED_PLAIN = """\
def test_plain():
    pass
"""
PLANTED_BOUNDED = """\
from hypothesis import given, settings, strategies as st


@settings(max_examples=3)
@given(st.integers())
def test_few(n):
    assert isinstance(n, int)
"""


class Kept:
    """A plugin for a row's own pytest: it keeps the belt's plugin module.

    The row's pytest imports the belt's plugin itself and drops it from
    `sys.modules` when it returns, as every in-process run does; this
    holds the module it collected with, for the runner to read.
    """

    def __init__(self) -> None:
        """Hold no module yet."""
        self.module: types.ModuleType | None = None

    def pytest_collection_finish(self) -> None:
        """Keep the plugin module the collection ran."""
        self.module = sys.modules.get(python_properties.PLUGIN)


def collected_by_the_plugin(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, **files: str
) -> int:
    """Run the gate over planted files, its pytest a real in-process one.

    Returns:
        The runner's exit status.

    """
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    monkeypatch.chdir(pytester.path)
    monkeypatch.setenv("MISE_TASK_NAME", GATE_LABEL)
    for name, text in files.items():
        pytester.makepyfile(**{name: text})

    def inprocess(argv: list[str]) -> int:
        # Each run imports the plugin afresh, with the Hypothesis it
        # imports: the module kept from the last would register its
        # profiles with a Hypothesis this run no longer has.
        monkeypatch.delitem(sys.modules, python_properties.PLUGIN, raising=False)
        kept = Kept()
        plugins: list[object] = [kept]
        status = int(pytester.runpytest_inprocess(*argv, plugins=plugins).ret)
        if kept.module is not None:
            monkeypatch.setitem(sys.modules, python_properties.PLUGIN, kept.module)
        return status

    paths = [pytester.path / f"{name}.py" for name in files]
    environ = {python_properties.WORKERS: "1"}
    return python_properties.run("gate", paths, environ, pytest_main=inprocess)


def test_the_plugin_lets_a_property_file_run(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A file whose one test is a property, under the profile's bounds, holds."""
    status = collected_by_the_plugin(pytester, monkeypatch, prop_holds=PLANTED_HOLDS)
    assert status == 0, capfd.readouterr()
    assert capfd.readouterr().out.endswith(f"{GATE_LABEL}: 1 file(s) held\n")


def test_the_plugin_refuses_a_bare_file_and_a_test_with_its_own_bounds(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Both refusals named by the real plugin, and nothing run after."""
    status = collected_by_the_plugin(
        pytester, monkeypatch, prop_plain=PLANTED_PLAIN, prop_few=PLANTED_BOUNDED
    )
    err = capfd.readouterr().err
    assert status == 1
    assert f"{GATE_LABEL}: prop_plain.py: holds no Hypothesis test (no @given)" in err
    assert (
        f"{GATE_LABEL}: prop_few.py::test_few sets its own bounds (max_examples=3);"
        " the belt's profile owns them"
    ) in err
