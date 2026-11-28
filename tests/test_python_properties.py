"""Table tests for `test:python-properties` and its audit half (#1332).

The bodies under test are the tasks' own `run` strings, read out of
mise/config.toml with tomllib and run by bash in a planted git tree,
and they call the real `mise/python-properties.sh` and
`mise/python_properties.py`. The rows that run a property use the
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

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from belt import decoded, shard, workflow_yaml
from tests import hermetic

CANON = hermetic.CANON
BELT = CANON / "mise"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
PYTHON = hermetic.tool("python3")

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
# Each body's task name, for the run that hands it to hermetic.task_run.
NAMES = {GATE: "test:python-properties", AUDIT: "audit:python-properties"}

HOLDS = """\
import unittest
from hypothesis import strategies as st
from belt.typed_hypothesis import given


class Holds(unittest.TestCase):
    @given(st.integers())
    def test_double_is_even(self, n):
        self.assertEqual((2 * n) % 2, 0)
"""

FAILS = """\
import unittest
from hypothesis import strategies as st
from belt.typed_hypothesis import given


class Fails(unittest.TestCase):
    @given(st.integers(min_value=0))
    def test_small(self, n):
        self.assertLess(n, 10)
"""

# A property taking Hypothesis's own `given`, which the belt's mypy reds
# on every property (#1604): the gate names the file and the remedy.
UNTYPED = """\
import unittest
from hypothesis import given, strategies as st


class Untyped(unittest.TestCase):
    @given(st.integers())
    def test_double_is_even(self, n):
        self.assertEqual((2 * n) % 2, 0)
"""

NO_PROPERTY = """\
import unittest


class Plain(unittest.TestCase):
    def test_plain(self):
        self.assertTrue(True)
"""

OWN_BOUNDS = """\
import unittest
from hypothesis import settings, strategies as st
from belt.typed_hypothesis import given


class Bounded(unittest.TestCase):
    @settings(max_examples=3)
    @given(st.integers())
    def test_few(self, n):
        self.assertIsInstance(n, int)
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
_GIT_ENV = {
    **hermetic.env(),
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def plant(root: Path, files: dict[str, str]) -> None:
    """Make `root` a git tree tracking `files`."""
    subprocess.run([GIT, "init", "-q", str(root)], check=True, env=_GIT_ENV)
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if files:
        subprocess.run([GIT, "-C", str(root), "add", "-A"], check=True, env=_GIT_ENV)


def run(
    body: str,
    files: dict[str, str],
    *,
    tool_dir: str | None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a task body in a planted tree.

    Returns:
        The finished process, text mode.

    """
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch) / "repo"
        plant(root, files)
        path = [str(Path(PYTHON).parent), str(Path(GIT).parent), "/usr/bin", "/bin"]
        if tool_dir:
            path.insert(0, tool_dir)
        environ = hermetic.env(
            PATH=os.pathsep.join(path),
            HOME=scratch,
            ORG_BELT_DIR=str(BELT),
            ORG_PYTHON=str(PYTHON),
            **env or {},
        )
        as_mise = hermetic.task_run(NAMES[body], body)
        return subprocess.run(
            as_mise.argv,
            cwd=root,
            env=environ | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


def real_tool_dir() -> str:
    """Name the directory holding the belt's `hypothesis` entry point.

    Returns:
        The directory, which the row puts on PATH.

    """
    return str(Path(hermetic.tool("hypothesis")).parent)


class TestRefusals:
    """What the shell decides before any property runs."""

    def test_no_prop_file_skips_clean_by_name(self) -> None:
        """Nothing tracked: exit 0, skipped, named."""
        done = run(GATE, {"test_x.py": NO_PROPERTY}, tool_dir=None)
        assert done.returncode == 0, done.stderr
        assert done.stdout == "test:python-properties: no prop_*.py tracked, skipped\n"

    def test_audit_skips_clean_by_name(self) -> None:
        """The audit half skips the same way, under its own name."""
        done = run(AUDIT, {}, tool_dir=None)
        assert done.returncode == 0, done.stderr
        assert done.stdout == "audit:python-properties: no prop_*.py tracked, skipped\n"

    def test_a_prop_file_without_hypothesis_is_red(self) -> None:
        """A property file and no tool: red, with the remedy."""
        done = run(GATE, {"prop_x.py": HOLDS}, tool_dir=None)
        assert (done.returncode, done.stderr) == (
            1,
            (
                "test:python-properties: hypothesis missing — the belt pins"
                " pipx:hypothesis; run: mise install\n"
            ),
        )

    def test_an_ordinary_test_importing_hypothesis_is_red(self) -> None:
        """A test_*.py that imports Hypothesis: red, the file named."""
        done = run(
            GATE,
            {"prop_x.py": HOLDS, "pkg/test_leak.py": HOLDS},
            tool_dir=None,
        )
        assert done.returncode == 1
        assert (
            "import Hypothesis (or cannot be read) but are not prop_*.py files:"
            in done.stderr
        )
        assert "  pkg/test_leak.py\n" in done.stderr

    def test_given_from_hypothesis_is_red(self) -> None:
        """A property taking Hypothesis's own `given`: red, file and remedy named."""
        done = run(GATE, {"prop_x.py": HOLDS, "pkg/prop_y.py": UNTYPED}, tool_dir=None)
        assert done.returncode == 1
        assert (
            "test:python-properties: these take given from hypothesis,"
            " not the belt's typed one:\n  pkg/prop_y.py\n"
            "  write: from typed_hypothesis import given\n"
        ) in done.stderr

    def test_hypothesis_source_in_a_string_is_not_an_import(self) -> None:
        """A fixture carrying Hypothesis source as text: not a leak."""
        fixture = f"HOLDS = {HOLDS!r}\n"
        done = run(GATE, {"prop_x.py": HOLDS, "test_x.py": fixture}, tool_dir=None)
        assert done.returncode == 1
        assert "import Hypothesis" not in done.stderr
        assert (
            "test:python-properties: hypothesis missing — the belt pins"
            " pipx:hypothesis; run: mise install\n"
        ) in done.stderr

    def test_a_venv_on_another_interpreter_is_red(self) -> None:
        """The tool venv's python is not the belt's: red, both named."""
        with tempfile.TemporaryDirectory() as venv:
            bin_dir = Path(venv) / "bin"
            bin_dir.mkdir()
            for name in ("hypothesis", "python"):
                (bin_dir / name).write_text(STUB_PYTHON, encoding="utf-8")
                (bin_dir / name).chmod(0o755)
            done = run(GATE, {"prop_x.py": HOLDS}, tool_dir=str(bin_dir))
        assert done.returncode == 1
        assert re.search(
            r"\Atest:python-properties: hypothesis runs on '3\.0\.0 \(stub"
            r" interpreter\)' but the suite's python3 is '3\.[^']+' — the tool"
            r" venv must be built on the belt's python pin \(reinstall: mise"
            r" install -f pipx:hypothesis\)\n\Z",
            done.stderr,
        )


class TestProperties:
    """Rows that run a property on the belt's Hypothesis."""

    def test_a_passing_file_holds(self) -> None:
        """One property that holds: exit 0, the test and the count."""
        done = run(GATE, {"pkg/prop_ok.py": HOLDS}, tool_dir=real_tool_dir())
        assert done.returncode == 0, done.stderr
        assert done.stdout == (
            "pkg/prop_ok.py: prop_0_prop_ok.Holds.test_double_is_even ... ok\n"
            "test:python-properties: 1 test(s) in 1 file(s) held\n"
        )

    def test_a_failing_property_is_red_with_its_example(self) -> None:
        """A property that fails: red, the shrunk example printed."""
        done = run(GATE, {"prop_bad.py": FAILS}, tool_dir=real_tool_dir())
        assert done.returncode == 1
        assert "prop_0_prop_bad.Fails.test_small ... FAIL\n" in done.stdout
        assert "AssertionError: 10 not less than 10" in done.stderr
        assert "Failing test case: test_small(" in done.stderr
        assert "    n=10,\n" in done.stderr

    def test_a_file_with_no_property_is_refused(self) -> None:
        """A prop_*.py holding no @given test: red, named."""
        done = run(GATE, {"prop_none.py": NO_PROPERTY}, tool_dir=real_tool_dir())
        assert done.returncode == 1
        assert (
            "test:python-properties: prop_none.py: holds no Hypothesis test (no @given)"
        ) in done.stderr

    def test_a_test_restating_a_bound_is_refused(self) -> None:
        """@settings(max_examples=…) on a test: red, the bound named."""
        done = run(GATE, {"prop_own.py": OWN_BOUNDS}, tool_dir=real_tool_dir())
        assert done.returncode == 1
        assert (
            "prop_own.py: prop_0_prop_own.Bounded.test_few sets its own bounds"
            " (max_examples=3); the belt's profile owns them"
        ) in done.stderr

    def test_the_audit_prints_its_seed_and_honours_a_given_one(self) -> None:
        """ORG_PROPERTY_SEED reproduces a run; the seed comes first."""
        done = run(
            AUDIT,
            {"prop_bad.py": FAILS},
            tool_dir=real_tool_dir(),
            env={"ORG_PROPERTY_SEED": "7"},
        )
        assert done.returncode == 1
        assert done.stdout.startswith(
            "audit:python-properties: seed 7 (reproduce: ORG_PROPERTY_SEED=7)\n"
        ), done.stdout
        assert "    n=10,\n" in done.stderr
        assert (
            "audit:python-properties: seed 7 — commit each falsifying example above"
            " as an @example row with its fix"
        ) in done.stderr


class TestScript:
    """The one row that starts the runner as a process: the script contract.

    Its in-process rows are prop_python_properties.py's: the runner
    imports Hypothesis, which only the belt's Hypothesis venv carries.
    """

    def test_the_runner_runs_as_a_script(self) -> None:
        """No file named: the usage line on stderr, exit 2."""
        venv = Path(os.path.realpath(hermetic.tool("hypothesis"))).parent
        done = subprocess.run(
            [str(venv / "python"), str(BELT / "python_properties.py"), "gate"],
            env=hermetic.env(),
            capture_output=True,
            text=True,
            check=False,
        )
        assert (done.returncode, done.stdout, done.stderr) == (
            2,
            "",
            ("python_properties.py: usage: python_properties.py gate|audit FILE...\n"),
        )


SHARD_FILES = {"a/prop_one.py": HOLDS, "b/prop_two.py": HOLDS, "prop_three.py": HOLDS}


class TestShards:
    """ORG_SHARD=k/n runs one shard of the files (#1631, #2160)."""

    def test_a_shard_runs_every_nth_sorted_file(self) -> None:
        """2/2 of three sorted files is the second: named, then run."""
        done = run(
            GATE,
            SHARD_FILES,
            tool_dir=real_tool_dir(),
            env={"ORG_SHARD": "2/2"},
        )
        assert done.returncode == 0, done.stderr
        assert done.stdout == (
            "test:python-properties: shard 2/2, 1 of 3 file(s)\n"
            "b/prop_two.py: prop_0_prop_two.Holds.test_double_is_even ... ok\n"
            "test:python-properties: 1 test(s) in 1 file(s) held\n"
        )

    @pytest.mark.parametrize("spec", ("3/2", "0/8", "x/8", "1/0", "\u00b2/8"))
    def test_a_malformed_shard_is_refused_by_name(self, spec: object) -> None:
        """A k above n, a zero, or not digits: exit 2, the value named."""
        done = run(
            GATE,
            SHARD_FILES,
            tool_dir=real_tool_dir(),
            env={"ORG_SHARD": spec},
        )
        assert done.returncode == 2
        assert (
            f"test:python-properties: ORG_SHARD={spec!r}"
            " is not a shard (k/n, 1 <= k <= n)"
        ) in done.stderr

    def test_two_shards_run_every_file_once(self) -> None:
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

    def test_an_empty_shard_says_so_and_passes(self) -> None:
        """More shards than files: the empty one is named and exits 0."""
        done = run(
            GATE,
            SHARD_FILES,
            tool_dir=real_tool_dir(),
            env={"ORG_SHARD": "8/8"},
        )
        assert done.returncode == 0, done.stderr
        assert (
            done.stdout
            == "test:python-properties: shard 8/8 holds none of the 3 file(s), skipped\n"
        )

    def test_the_audit_times_each_file_as_it_finishes(self) -> None:
        """Each file's line, then its count and elapsed seconds."""
        done = run(
            AUDIT,
            {"prop_ok.py": HOLDS},
            tool_dir=real_tool_dir(),
            env={"ORG_PROPERTY_SEED": "7"},
        )
        assert done.returncode == 0, done.stderr
        assert re.search(
            r"prop_ok.py: prop_0_prop_ok.Holds.test_double_is_even ... ok\n"
            r"prop_ok.py: 1 test\(s\) in [0-9]+\.[0-9]s\n"
            r"audit:python-properties: 1 test\(s\) in 1 file\(s\) held\n$",
            done.stdout,
        )


# A property file that meets its siblings before it finishes importing,
# and says which process imported it, so a row can tell one process
# from four. Each writes its pid into the directory PLANT_MEET names,
# then waits until PLANT_PARTIES pid files are there. Run in turn, the
# first can never meet the rest: past PLANT_CEILING seconds it fails,
# and marks the rendezvous broken so the files after it fail at once.
# Four files finishing at all proves they ran at once; no clock decides
# the verdict.
CEILING = 60
MEETS = f"""\
import os
import sys
import time
import unittest
from pathlib import Path
from hypothesis import strategies as st
from belt.typed_hypothesis import given

print("pid", os.getpid(), file=sys.stderr)
meet = Path(os.environ["PLANT_MEET"])
parties = int(os.environ["PLANT_PARTIES"])
ceiling = float(os.environ.get("PLANT_CEILING", "{CEILING}"))
(meet / str(os.getpid())).touch()
began = time.monotonic()
while True:
    if (meet / "broken").exists():
        raise RuntimeError("rendezvous broken")
    met = sum(1 for p in meet.iterdir() if p.name.isdigit())
    if met >= parties:
        break
    if time.monotonic() - began > ceiling:
        (meet / "broken").touch()
        raise RuntimeError(
            f"rendezvous timed out: {{met}} of {{parties}} after {{ceiling:g}} s"
        )
    time.sleep(0.01)


class Meets(unittest.TestCase):
    @given(st.integers())
    def test_double_is_even(self, n):
        self.assertEqual((2 * n) % 2, 0)
"""
FOUR = {f"{d}/prop_{d}.py": MEETS for d in "abcd"}


def ran(name: str, index: int) -> str:
    """One file's result line, as the runner prints it.

    Returns:
        The line for prop_NAME.py loaded at `index`, with its newline.

    """
    return (
        f"{name}/prop_{name}.py:"
        f" prop_{index}_prop_{name}.Meets.test_double_is_even ... ok\n"
    )


def pids(stderr: str) -> set[str]:
    """Read the processes the MEETS files were imported in.

    Returns:
        Each distinct pid printed.

    """
    return {line for line in stderr.splitlines() if line.startswith("pid ")}


class TestFanOut:
    """No shard named: the runner fans out over its own shards (#1741)."""

    def meet(self, parties: int, **env: str) -> dict[str, str]:
        """Name a fresh rendezvous for `parties` files.

        Returns:
            The environment the MEETS files read, plus `env`.

        """
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        return {"PLANT_MEET": scratch.name, "PLANT_PARTIES": str(parties)} | env

    def test_four_files_run_side_by_side_in_shard_order(self) -> None:
        """Four files that wait for each other all finish, in shard order."""
        done = run(
            GATE, FOUR, tool_dir=real_tool_dir(), env=self.meet(4, ORG_TEST_WORKERS="8")
        )
        assert done.returncode == 0, done.stderr
        assert len(pids(done.stderr)) == 4, done.stderr
        assert done.stdout == (
            "".join(
                f"test:python-properties: shard {k}/4, 1 of 4 file(s)\n"
                + ran(name, 0)
                + "test:python-properties: 1 test(s) in 1 file(s) held\n"
                for k, name in enumerate("abcd")
            )
            + "test:python-properties: 4 test(s) in 4 file(s) held\n"
        )

    def test_files_run_in_turn_never_meet(self) -> None:
        """ORG_TEST_WORKERS=1 over files that wait for four: the ceiling fails it."""
        done = run(
            GATE,
            FOUR,
            tool_dir=real_tool_dir(),
            env=self.meet(4, ORG_TEST_WORKERS="1", PLANT_CEILING="2"),
        )
        assert done.returncode == 1, done.stderr
        assert (
            "test:python-properties: a/prop_a.py: could not be imported\n"
            in done.stderr
        )
        assert (
            done.stderr.count("RuntimeError: rendezvous timed out: 1 of 4 after 2 s\n")
            == 1
        ), done.stderr

    def test_one_worker_runs_every_file_in_one_process(self) -> None:
        """ORG_TEST_WORKERS=1: no shard, one pid, files loaded in turn."""
        done = run(
            GATE, FOUR, tool_dir=real_tool_dir(), env=self.meet(1, ORG_TEST_WORKERS="1")
        )
        assert done.returncode == 0, done.stderr
        assert len(pids(done.stderr)) == 1, done.stderr
        assert done.stdout == (
            "".join(ran(name, i) for i, name in enumerate("abcd"))
            + "test:python-properties: 4 test(s) in 4 file(s) held\n"
        )

    def test_a_named_shard_starts_no_child(self) -> None:
        """ORG_SHARD=1/2 with workers to spare: one process."""
        done = run(
            GATE,
            FOUR,
            tool_dir=real_tool_dir(),
            env=self.meet(1, ORG_TEST_WORKERS="8", ORG_SHARD="1/2"),
        )
        assert done.returncode == 0, done.stderr
        assert len(pids(done.stderr)) == 1, done.stderr
        assert done.stdout == (
            "test:python-properties: shard 1/2, 2 of 4 file(s)\n"
            + "".join(ran(name, i) for i, name in enumerate("ac"))
            + "test:python-properties: 2 test(s) in 2 file(s) held\n"
        )

    def test_one_of_one_is_the_whole_run(self) -> None:
        """ORG_SHARD=1/1, a split row's unsharded task: it fans out as unset."""
        done = run(
            GATE,
            FOUR,
            tool_dir=real_tool_dir(),
            env=self.meet(4, ORG_TEST_WORKERS="8", ORG_SHARD="1/1"),
        )
        assert done.returncode == 0, done.stderr
        assert len(pids(done.stderr)) == 4, done.stderr


class TestWorkflowShape:
    """Both property jobs are bounded, sharded and share one seed (#1631)."""

    def test_both_jobs_carry_a_bound_planned_shards_and_one_seed(self) -> None:
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
            with self.subTest(workflow=workflow):
                path = CANON / ".github" / "workflows" / workflow
                jobs = workflow_yaml.jobs(workflow_yaml.document(path))
                job = jobs["python-properties"]
                assert isinstance(job.get("timeout-minutes"), int)
                strategy = decoded.as_object(job["strategy"], "strategy")
                matrix = decoded.as_object(strategy["matrix"], "matrix")
                planned = "needs.python-properties-seed.outputs"
                assert (
                    f"${{{{ fromJSON({planned}.python-shards) }}}}" == matrix["shard"]
                )
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
