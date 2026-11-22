#!/usr/bin/env python3
"""Planted rows for the canon's test runner (#1491, #1623).

Each row writes a module into a scratch tree and runs it through
`mise/unittest_guarded.py`, from a starting directory of its own, as the
canon's `test` task does. The leak rows assert the MESSAGE and the test
it names; the clean rows assert that the runner is otherwise
`python3 -m unittest`: the same output and the same exit status. The
parallel rows (#1623) assert that running each file in a worker of its
own changes nothing a reader sees but the time: the same tests, the
same failures in full, the same summary and status as one process, and
under coverage the same figure. The cost rows (#1732) assert the block
printed after each root's result: its slowest files slowest first, its
CPU-seconds, workers and wall time, and the same block in the job
summary when there is one; every other row compares what is above it.
"""

import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import decoded
import hermetic
import unittest_guarded as ug

_HERE = pathlib.Path(__file__).resolve().parent
RUNNER = _HERE / "unittest_guarded.py"
MEASURE = _HERE / "coverage-measure.sh"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
# unittest prints its own timing; two runs never agree on it.
TIMING = re.compile(r"Ran (\d+ tests?) in \d+\.\d+s")
# The cost block the runner prints last (#1732), after a blank line.
COST = re.compile(
    r"\n(cost of .+: the \d+ slowest of \d+ test files, seconds\n"
    r"(?:  .*\n)*"
    r"cost of .+: [0-9.]+ CPU-seconds, \d+ workers?, [0-9.]+ s wall\n)\Z"
)

LEAKS = """\
import os
import tempfile
import unittest


class T(unittest.TestCase):
    def test_a_leaves_a_deleted_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)

    def test_b_runs_where_the_run_started(self):
        self.assertEqual(os.getcwd(), os.environ["START"])
"""

MOVES = """\
import os
import unittest


class T(unittest.TestCase):
    def test_moves(self):
        os.chdir(os.environ["ELSEWHERE"])
"""

CLEAN = """\
import unittest


class T(unittest.TestCase):
    def test_one(self):
        self.assertEqual(1, 1)

    def test_two(self):
        self.assertTrue(True)
"""

# A file whose one test takes a known time; FAILS adds a failing test.
SLEEPS = """\
import time
import unittest


class T(unittest.TestCase):
    def test_sleeps(self):
        time.sleep({seconds})
"""

FAILS = """
    def test_wrong(self):
        self.assertEqual(1, 2)
"""

FAILING = """\
import unittest


class T(unittest.TestCase):
    def test_wrong(self):
        self.assertEqual(1, 2)
"""

# A tree of every outcome unittest reports, across three files and an
# import that fails, so the merge has each kind to carry.
MIXED = {
    "test_a_outcomes": """\
import unittest


class T(unittest.TestCase):
    def test_passes(self):
        print("printed by a test")

    def test_fails(self):
        \"\"\"A docstring the summary prints.\"\"\"
        self.assertEqual(1, 2)

    @unittest.skip("planted skip")
    def test_skipped(self):
        pass

    @unittest.expectedFailure
    def test_expected(self):
        self.assertEqual(1, 2)
""",
    "test_b_errors": """\
import unittest


class T(unittest.TestCase):
    def test_raises(self):
        raise RuntimeError("planted error")

    @unittest.expectedFailure
    def test_unexpected_success(self):
        pass
""",
    "test_c_broken_import": "import no_such_module_planted\n",
    "test_d_clean": CLEAN,
}

# Each test writes the process it ran in, so a row can count them.
PID = """\
import os
import pathlib
import unittest


class T(unittest.TestCase):
    def test_pid(self):
        out = pathlib.Path(os.environ["PIDS"]) / (__name__ + ".pid")
        out.write_text(str(os.getpid()), encoding="utf-8")
"""

# A test that ends its process: the worker leaves no result.
EXITS = """\
import os
import unittest


class T(unittest.TestCase):
    def test_exits(self):
        os._exit(3)
"""

# A test that, once its worker has written the outcome, overwrites it
# with what the row plants: the shape of a worker that died part-way.
SPOILS = """\
import atexit
import pathlib
import sys
import unittest

# The worker is the process this spoils: its argv is `--worker <spec>`.
if sys.argv[1:2] == ["--worker"]:
    OUT = pathlib.Path(sys.argv[2]).with_suffix(".out")
    atexit.register(OUT.write_text, {body!r}, encoding="utf-8")


class T(unittest.TestCase):
    def test_spoiled(self):
        pass
"""

# Code under measurement, and a two-file suite over it that also runs a
# helper as a child, as the canon's suites do.
MEASURED = {
    "code.py": """\
def grade(n):
    if n > 10:
        return "big"
    if n < 0:
        return "negative"
    return "small"


def never():
    return "unrun"
""",
    "helper.py": """\
import sys

if sys.argv[1] == "x":
    print("x")
else:
    print("other")
""",
    "test_big.py": """\
import subprocess
import sys
import unittest

import code


class T(unittest.TestCase):
    def test_big(self):
        self.assertEqual(code.grade(11), "big")

    def test_helper(self):
        subprocess.run([sys.executable, "helper.py", "x"], check=True)
""",
    "test_small.py": """\
import unittest

import code


class T(unittest.TestCase):
    def test_small(self):
        self.assertEqual(code.grade(1), "small")
""",
}

# The stub `mise`: answers the leg's read of the test task with the
# planted task, and nothing else.
MISE = """\
#!/usr/bin/env bash
if [[ "$1 $2 $3" == "tasks info test" ]]; then
  cat "${STUB_TASK}"
  exit 0
fi
exit 1
"""


def planted(module: object) -> bool:
    """Say whether a module was imported from outside this canon and Python.

    Returns:
        True for a module a row planted in a scratch tree.

    """
    file: object = getattr(module, "__file__", None)
    return isinstance(file, str) and not file.startswith((
        str(_HERE.parent),
        sys.prefix,
        sys.base_prefix,
    ))


def split_cost(stderr: str) -> tuple[str, list[str]]:
    """Split the runner's stderr at the cost block it prints last.

    Returns:
        What is above the block, and the block's lines.

    Raises:
        AssertionError: when there is no block at the end.

    """
    found = COST.search(stderr)
    if found is None:
        msg = f"no cost block at the end of:\n{stderr}"
        raise AssertionError(msg)
    return stderr[: found.start()], stderr[found.start(1) :].splitlines()


def files(block: list[str]) -> list[tuple[str, float]]:
    """Read the block's file rows: each module and its seconds, in order.

    Returns:
        The rows between the heading and the totals.

    """
    out: list[tuple[str, float]] = []
    for line in block[1:-1]:
        seconds, name = line.split()
        out.append((name, float(seconds)))
    return out


def totals(block: list[str], root: str) -> tuple[float, str, float]:
    """Read the block's last line: CPU-seconds, workers, wall seconds.

    Returns:
        The CPU-seconds, the workers phrase and the wall seconds.

    Raises:
        AssertionError: when the line does not name the root.

    """
    head = f"cost of {root}: "
    if not block[-1].startswith(head):
        msg = f"the totals do not name {root}: {block[-1]}"
        raise AssertionError(msg)
    cpu, workers, wall = block[-1].removeprefix(head).split(", ")
    return (
        float(cpu.removesuffix(" CPU-seconds")),
        workers,
        float(wall.removesuffix(" s wall")),
    )


def written_files(base: pathlib.Path) -> list[pathlib.Path]:
    """List the files under a scratch tree, less Python's bytecode caches.

    Returns:
        Every path below `base`, sorted.

    """
    return sorted(p for p in base.rglob("*") if "__pycache__" not in p.parts)


def above(stderr: str) -> str:
    """Return what the runner printed above its cost block.

    Returns:
        The text before the block, which is unittest's own.

    """
    return split_cost(stderr)[0]


def call(argv: list[str], env: dict[str, str]) -> hermetic.Called:
    """Call the runner's `main` in this process (#1696).

    `hermetic.call_main` makes `env` the process environment for the call,
    so the workers the runner starts inherit it as they did, and reads a
    worker's refusal, `sys.exit(message)`, as Python reports it. Beside
    it, `sys.argv` is the script's, which a planted module may read; the
    search path is put back after, and the planted modules leave
    `sys.modules`, so the next row's tree imports its own.

    Returns:
        The status, and what the runner wrote to each stream.

    """
    search = sys.path[:]
    try:
        with mock.patch.object(sys, "argv", [str(RUNNER), *argv]):
            return hermetic.call_main(lambda: ug.main(argv), env=env)
    finally:
        sys.path[:] = search
        for name in [n for n, m in sys.modules.items() if planted(m)]:
            del sys.modules[name]


class Planted(unittest.TestCase):
    """Planted trees and the ways a row runs them; no rows of its own."""

    def tree(self, **modules: str) -> tuple[pathlib.Path, pathlib.Path]:
        """Write modules into a scratch tree beside a starting directory.

        Returns:
            The starting directory and the tree's discovery root.

        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = pathlib.Path(tmp.name).resolve()
        start = base / "start"
        root = start / "suite"
        root.mkdir(parents=True)
        (base / "elsewhere").mkdir()
        for name, body in modules.items():
            (root / f"{name}.py").write_text(body, encoding="utf-8")
        return start, root

    @staticmethod
    def run_with(
        start: pathlib.Path,
        command: list[str],
        *flags: str,
        workers: str = "3",
        pids: pathlib.Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a discovery command from `start`, with `workers` workers.

        Returns:
            The finished process.

        """
        env = hermetic.env(
            START=str(start),
            ELSEWHERE=str(start.parent / "elsewhere"),
            ORG_TEST_WORKERS=workers,
            PIDS=str(pids or ""),
        )
        return subprocess.run(
            [
                *command,
                "discover",
                *flags,
                *("--start-directory", "suite", "--pattern", "test_*.py"),
            ],
            cwd=start,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def guarded(
        start: pathlib.Path,
        *flags: str,
        workers: str = "3",
        pids: pathlib.Path | None = None,
        summary: pathlib.Path | None = None,
    ) -> hermetic.Called:
        """Run the planted tree through the guarded runner, in this process.

        The discovery the script rows ran from `start` with a relative
        `suite` is run here on its absolute path, so this process never
        leaves its own directory: the runner judges every test against it,
        and a planted test's START is that directory.

        Returns:
            The status, and what the runner wrote to each stream.

        """
        env = hermetic.env(
            START=str(pathlib.Path.cwd()),
            ELSEWHERE=str(start.parent / "elsewhere"),
            ORG_TEST_WORKERS=workers,
            PIDS=str(pids or ""),
        )
        if summary is not None:
            env["GITHUB_STEP_SUMMARY"] = str(summary)
        return call(
            [
                "discover",
                *flags,
                *("--start-directory", str(start / "suite"), "--pattern", "test_*.py"),
            ],
            env,
        )


class Runner(Planted):
    """The runner over planted modules."""

    def test_a_deleted_directory_is_an_error_on_the_test_that_left_it(self) -> None:
        """The leaking test is the ERROR, by name; the next one passes."""
        start, _ = self.tree(test_leaks=LEAKS)
        r = self.guarded(start)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn(
            "ERROR: test_a_leaves_a_deleted_directory (test_leaks.T"
            ".test_a_leaves_a_deleted_directory)",
            r.stderr,
        )
        self.assertRegex(
            r.stderr,
            r"LeftTheProcessError: left the process in \S+ \(deleted\)",
        )
        self.assertNotIn("test_b_runs_where_the_run_started (", r.stderr)
        self.assertIn("FAILED (errors=1)", r.stderr)

    def test_a_moved_directory_names_where_it_started(self) -> None:
        """A directory that still exists is named, with the start beside it."""
        start, _ = self.tree(test_moves=MOVES)
        r = self.guarded(start)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("ERROR: test_moves (test_moves.T.test_moves)", r.stderr)
        # The worker starts where this process is, and judges against it.
        self.assertIn(
            f"left the process in {start.parent / 'elsewhere'}"
            f" (moved from {pathlib.Path.cwd()})",
            r.stderr,
        )

    def test_otherwise_it_is_unittest(self) -> None:
        """A clean or failing module: unittest's output and status, exactly."""
        for name, body, workers in (
            (n, b, w)
            for n, b in (("test_clean", CLEAN), ("test_failing", FAILING))
            for w in ("1", "3")
        ):
            with self.subTest(module=name, workers=workers):
                start, _ = self.tree(**{name: body})
                plain = self.run_with(start, [sys.executable, "-m", "unittest"])
                ours = self.guarded(start, workers=workers)
                self.assertEqual(ours.returncode, plain.returncode)
                self.assertEqual(ours.stdout, plain.stdout)
                self.assertEqual(
                    TIMING.sub(r"Ran \1", above(ours.stderr)),
                    TIMING.sub(r"Ran \1", plain.stderr),
                )

    def test_verbosity_reaches_the_runner(self) -> None:
        """`-v` still prints one line per test: unittest builds the runner."""
        start, _ = self.tree(test_clean=CLEAN)
        r = self.guarded(start, "-v")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("test_one (test_clean.T.test_one) ... ok", r.stderr)

    def test_a_failing_file_is_printed_in_full_and_reds(self) -> None:
        """Under workers, a file's failure is printed whole; status 1."""
        start, _ = self.tree(test_failing=FAILING, test_clean=CLEAN)
        r = self.guarded(start)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("FAIL: test_wrong (test_failing.T.test_wrong)", r.stderr)
        self.assertIn("self.assertEqual(1, 2)", r.stderr)
        self.assertIn("AssertionError: 1 != 2", r.stderr)
        self.assertIn("Ran 3 tests in", r.stderr)
        self.assertIn("FAILED (failures=1)", r.stderr)

    def test_an_import_that_fails_is_reported(self) -> None:
        """A file that cannot import is an ERROR by name, not a lost file."""
        start, _ = self.tree(
            test_broken="import no_such_module_planted\n", test_clean=CLEAN
        )
        r = self.guarded(start)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn(
            "ERROR: test_broken (unittest.loader._FailedTest.test_broken)", r.stderr
        )
        self.assertIn("No module named 'no_such_module_planted'", r.stderr)
        self.assertIn("FAILED (errors=1)", r.stderr)

    def test_serial_and_parallel_report_the_same(self) -> None:
        """Every outcome, -v and not: one process and three say the same."""
        start, _ = self.tree(**MIXED)
        for flags in ((), ("-v",)):
            with self.subTest(flags=flags):
                serial = self.guarded(start, *flags, workers="1")
                parallel = self.guarded(start, *flags, workers="3")
                self.assertEqual(parallel.returncode, 1, parallel.stderr)
                self.assertEqual(parallel.returncode, serial.returncode)
                self.assertEqual(parallel.stdout, serial.stdout)
                self.assertEqual(
                    TIMING.sub(r"Ran \1", above(parallel.stderr)),
                    TIMING.sub(r"Ran \1", above(serial.stderr)),
                )
                self.assertIn(
                    "FAILED (failures=1, errors=2, skipped=1, expected failures=1, "
                    "unexpected successes=1)",
                    parallel.stderr,
                )

    def test_one_worker_runs_in_one_process(self) -> None:
        """ORG_TEST_WORKERS=1 is one process; more is one per file."""
        start, _ = self.tree(test_one=PID, test_two=PID, test_three=PID)
        for workers, processes in (("1", 1), ("3", 3)):
            with self.subTest(workers=workers):
                pids = start.parent / f"pids-{workers}"
                pids.mkdir()
                r = self.guarded(start, workers=workers, pids=pids)
                self.assertEqual(r.returncode, 0, r.stderr)
                seen = {p.read_text(encoding="utf-8") for p in pids.iterdir()}
                self.assertEqual(len(list(pids.iterdir())), 3)
                self.assertEqual(len(seen), processes)

    def test_a_worker_count_that_is_not_one_is_refused(self) -> None:
        """Zero and words are refused by name, never read as the default."""
        start, _ = self.tree(test_clean=CLEAN)
        for value in ("0", "many", "-2"):
            with self.subTest(value=value):
                r = self.guarded(start, workers=value)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn(
                    f"{RUNNER.name}: ORG_TEST_WORKERS={value!r} is not a worker "
                    "count (1 or more)",
                    r.stderr,
                )

    def test_a_worker_that_dies_is_an_error_on_its_file(self) -> None:
        """A file whose process ends mid-run is an ERROR, never a pass."""
        start, _ = self.tree(test_exits=EXITS, test_clean=CLEAN)
        r = self.guarded(start)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("ERROR: test_exits (worker)", r.stderr)
        self.assertIn("the worker for test_exits exited 3 with no result", r.stderr)
        self.assertIn("FAILED (errors=1)", r.stderr)

    def test_an_unreadable_outcome_is_an_error_on_its_file(self) -> None:
        """A truncated or wrong-shaped result reds its file; the rest report."""
        for name, body in (
            ("truncated", '{"run": 1, "errors": [['),
            ("wrong-shaped", '{"run": "one", "errors": {}}'),
        ):
            with self.subTest(outcome=name):
                start, _ = self.tree(
                    test_spoiled=SPOILS.format(body=body), test_clean=CLEAN
                )
                r = self.guarded(start)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn("ERROR: test_spoiled (worker)", r.stderr)
                self.assertIn(
                    "the worker for test_spoiled exited 0 with an unreadable result",
                    r.stderr,
                )
                self.assertIn("Ran 3 tests in", r.stderr)
                self.assertIn("FAILED (errors=1)", r.stderr)

    def test_a_tree_with_no_test_exits_as_unittest_does(self) -> None:
        """Nothing ran and nothing was skipped: unittest's own status, 5."""
        start, _ = self.tree(test_empty="import unittest\n")
        r = self.guarded(start)
        self.assertEqual(r.returncode, 5, r.stderr)
        self.assertIn("NO TESTS RAN", r.stderr)

    def test_an_unreadable_spec_stops_the_worker_by_name(self) -> None:
        """A worker handed a spec it cannot read exits 1, naming the file.

        The runner's one script row (#1696): it starts as a process, reads
        its arguments and exits with the documented status.
        """
        start, _ = self.tree(test_clean=CLEAN)
        spec = start.parent / "bad.json"
        for body in ("{", '{"module": 1}'):
            with self.subTest(spec=body):
                spec.write_text(body, encoding="utf-8")
                r = subprocess.run(
                    [sys.executable, str(RUNNER), "--worker", str(spec)],
                    cwd=start,
                    env=hermetic.env(),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertEqual(
                    r.stderr, f"{RUNNER.name}: worker spec {spec} is unreadable\n"
                )


class Cost(Planted):
    """What a root cost, printed after its result (#1732)."""

    def costed(
        self, start: pathlib.Path, workers: str, summary: pathlib.Path | None = None
    ) -> tuple[hermetic.Called, str, list[str]]:
        """Run the planted tree and split its stderr at the cost block.

        Returns:
            The run, what is above the block, and the block's lines.

        """
        r = self.guarded(start, workers=workers, summary=summary)
        top, block = split_cost(r.stderr)
        return r, top, block

    def test_the_cost_lists_the_slowest_files_first(self) -> None:
        """Planted seconds: listed slowest first, then the totals."""
        # The runner wraps the stream as unittest's results expect it.
        stream = ug.GuardedRunner(stream=io.StringIO()).stream
        result = ug.GuardedResult(stream, descriptions=True, verbosity=1)
        result.files = {"test_quick": 0.1, "test_slow": 0.4, "test_middle": 0.25}
        result.workers = 3
        result.wall = 0.5
        block = ug.cost(result, "tests")
        want = [
            "cost of tests: the 3 slowest of 3 test files, seconds",
            "      0.40  test_slow",
            "      0.25  test_middle",
            "      0.10  test_quick",
            "cost of tests: 0.75 CPU-seconds, 3 workers, 0.50 s wall",
        ]
        self.assertEqual(block, want)

    def test_the_cost_follows_the_result(self) -> None:
        """After the result, passing or failing: each file, then totals.

        The order is the planted row's: measured seconds can invert
        under load, so this row asserts only lower bounds.
        """
        sleeps = {"test_quick": 0.1, "test_slow": 0.4, "test_middle": 0.25}
        for failing, workers in ((f, w) for f in (False, True) for w in ("1", "3")):
            with self.subTest(failing=failing, workers=workers):
                start, root = self.tree(**{
                    name: SLEEPS.format(seconds=seconds)
                    + (FAILS if failing and name == "test_quick" else "")
                    for name, seconds in sleeps.items()
                })
                r, top, block = self.costed(start, workers)
                self.assertEqual(r.returncode, int(failing), r.stderr)
                result = "FAILED (failures=1)" if failing else "OK"
                self.assertTrue(top.endswith(f"\n{result}\n"), r.stderr)
                self.assertEqual(
                    block[0], f"cost of {root}: the 3 slowest of 3 test files, seconds"
                )
                named = files(block)
                names: list[str] = sorted(pair[0] for pair in named)
                planted: list[str] = sorted(sleeps)
                self.assertEqual(names, planted)
                for name, seconds in named:
                    self.assertGreaterEqual(seconds, sleeps[name])
                cpu, used, wall = totals(block, str(root))
                self.assertAlmostEqual(cpu, sum(s for _, s in named), delta=0.02)
                self.assertGreaterEqual(wall, max(sleeps.values()))
                self.assertEqual(
                    used, f"{workers} worker{'' if workers == '1' else 's'}"
                )

    def test_the_cost_names_at_most_ten_files(self) -> None:
        """Twelve files: the ten slowest are named, all twelve are counted."""
        start, root = self.tree(**{f"test_f{n:02d}": CLEAN for n in range(12)})
        r, _, block = self.costed(start, "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            block[0], f"cost of {root}: the 10 slowest of 12 test files, seconds"
        )
        self.assertEqual(len(block), 12)

    def test_an_import_that_fails_is_costed_by_its_module(self) -> None:
        """A file that will not import is named as itself, never as unittest's."""
        start, _ = self.tree(
            test_broken="import no_such_module_planted\n", test_clean=CLEAN
        )
        for workers in ("1", "3"):
            with self.subTest(workers=workers):
                _, _, block = self.costed(start, workers)
                names = sorted(pair[0] for pair in files(block))
                both: list[str] = ["test_broken", "test_clean"]
                self.assertEqual(names, both)

    def test_the_cost_goes_to_the_job_summary_when_there_is_one(self) -> None:
        """Set, the block is appended there; unset, nothing else is written."""
        start, _ = self.tree(test_clean=CLEAN)
        summary = start.parent / "summary.md"
        summary.write_text("before\n", encoding="utf-8")
        _, _, block = self.costed(start, "3", summary)
        self.assertEqual(
            summary.read_text(encoding="utf-8"),
            "before\n```text\n" + "\n".join(block) + "\n```\n",
        )
        summary.unlink()
        before = written_files(start.parent)
        _, _, block = self.costed(start, "3")
        self.assertTrue(block)
        self.assertEqual(written_files(start.parent), before)

    def test_a_name_with_a_newline_stays_on_its_line(self) -> None:
        """Outside text is escaped as workflow_command escapes it: one line."""
        start, _ = self.tree()
        root = start / "sui\nte"
        root.mkdir()
        (root / "test_clean.py").write_text(CLEAN, encoding="utf-8")
        summary = start.parent / "summary.md"
        env = hermetic.env(
            START=str(pathlib.Path.cwd()), GITHUB_STEP_SUMMARY=str(summary)
        )
        r = call(
            ["discover", "--start-directory", str(root), "--pattern", "test_*.py"], env
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        escaped = str(start / "sui%0Ate")
        _, block = split_cost(r.stderr)
        self.assertEqual(
            block[0], f"cost of {escaped}: the 1 slowest of 1 test files, seconds"
        )
        self.assertTrue(block[-1].startswith(f"cost of {escaped}: "), block[-1])
        self.assertIn(
            f"\ncost of {escaped}: the 1 slowest", summary.read_text(encoding="utf-8")
        )


# A worker outcome every field of which reads; the rows below spoil one.
GOOD_OUTCOME = {
    "run": 1,
    "errors": [],
    "failures": [],
    "expectedFailures": [],
    "skipped": [],
    "unexpectedSuccesses": [],
    "durations": [],
}


def write_spec(start: pathlib.Path, **fields: object) -> pathlib.Path:
    """Write a worker spec for test_clean, with the fields replaced.

    The search path is the suite's root ahead of this interpreter's own,
    as the parent hands it: a worker replaces sys.path with it.

    Returns:
        The spec file.

    """
    document: dict[str, object] = {
        "module": "test_clean",
        "path": [str(start / "suite"), *sys.path],
        "patterns": None,
        "verbosity": 1,
        "buffer": False,
        "tb_locals": False,
        "warnings": None,
    }
    spec = start.parent / "spec.json"
    spec.write_text(json.dumps(document | fields), encoding="utf-8")
    return spec


def run_worker(spec: pathlib.Path) -> hermetic.Called:
    """Run one worker over a spec, as the parent would, in this process.

    Returns:
        The status, and what the worker wrote to each stream.

    """
    return call(["--worker", str(spec)], hermetic.env())


class Shapes(Planted):
    """Every wrong shape the outcome and spec readers refuse (#1659)."""

    def test_each_wrong_outcome_field_is_an_error_on_its_file(self) -> None:
        """One field of the wrong shape reds the file, whichever it is."""
        for field, value in (
            ("errors", {}),
            ("failures", [["a test"]]),
            ("skipped", [["a test", 1]]),
            ("unexpectedSuccesses", {}),
            ("unexpectedSuccesses", [1]),
            ("durations", {}),
            ("durations", [["a test"]]),
            ("durations", [["a test", True]]),
            ("durations", [[1, 0.5]]),
        ):
            body = json.dumps(GOOD_OUTCOME | {field: value})
            with self.subTest(outcome=body):
                start, _ = self.tree(
                    test_spoiled=SPOILS.format(body=body), test_clean=CLEAN
                )
                r = self.guarded(start)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn(
                    "the worker for test_spoiled exited 0 with an unreadable result",
                    r.stderr,
                )
                self.assertIn("FAILED (errors=1)", r.stderr)

    def test_each_wrong_spec_field_stops_the_worker(self) -> None:
        """Any field of the wrong shape is the one refusal, naming the file."""
        start, _ = self.tree(test_clean=CLEAN)
        for field, value in (
            ("path", [1]),
            ("patterns", "test_one"),
            ("verbosity", "1"),
            ("buffer", 1),
            ("tb_locals", "no"),
            ("warnings", "loudly"),
        ):
            with self.subTest(field=field):
                spec = write_spec(start, **{field: value})
                r = run_worker(spec)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertEqual(
                    r.stderr, f"{RUNNER.name}: worker spec {spec} is unreadable\n"
                )

    def test_a_file_with_no_tests_is_planned_as_nothing(self) -> None:
        """A discovered module with no tests gets no worker; the rest run."""
        start, _ = self.tree(test_empty="import unittest\n", test_clean=CLEAN)
        r = self.guarded(start)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ran 2 tests in", r.stderr)
        self.assertTrue(above(r.stderr).endswith("\nOK\n"), r.stderr)

    def test_no_worker_count_takes_the_cores_up_to_the_cap(self) -> None:
        """Unset, the count is the machine's, and the suite runs as usual."""
        start, _ = self.tree(test_clean=CLEAN, test_more=CLEAN)
        r = self.guarded(start, workers="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ran 4 tests in", r.stderr)
        self.assertTrue(above(r.stderr).endswith("\nOK\n"), r.stderr)

    def test_a_spec_with_no_warnings_action_runs_its_module(self) -> None:
        """An absent action is valid: the worker runs and writes its outcome."""
        start, _ = self.tree(test_clean=CLEAN)
        spec = write_spec(start, patterns=["*one"])
        r = run_worker(spec)
        self.assertEqual(r.returncode, 0, r.stderr)
        outcome = decoded.as_object(
            decoded.load_json(spec.with_suffix(".out").read_text(encoding="utf-8")),
            "outcome",
        )
        self.assertEqual(1, outcome["run"])
        errors = decoded.as_array(outcome["errors"], "errors")
        failures = decoded.as_array(outcome["failures"], "failures")
        none: list[object] = []
        self.assertEqual(none, errors + failures)


class Parts(unittest.TestCase):
    """The hooks unittest itself calls, each called here by name (#1696)."""

    def test_stop_test_names_a_moved_directory_and_puts_it_back(self) -> None:
        """A test that leaves elsewhere is an ERROR naming both directories."""
        stream = ug.GuardedRunner(stream=io.StringIO(), verbosity=0).stream
        result = ug.GuardedResult(stream, descriptions=True, verbosity=0)
        case = unittest.FunctionTestCase(lambda: None)
        with tempfile.TemporaryDirectory() as elsewhere:
            result.startTest(case)
            os.chdir(elsewhere)
            result.stopTest(case)
            self.assertEqual(str(pathlib.Path.cwd()), ug.START)
        ((test, text),) = result.errors
        self.assertIs(test, case)
        self.assertIn(
            f"LeftTheProcessError: left the process in"
            f" {pathlib.Path(elsewhere).resolve()} (moved from {ug.START})",
            text,
        )

    def test_the_loader_records_each_module_s_suite(self) -> None:
        """The loader answers unittest's suite and records it by its id."""

        class Case(unittest.TestCase):
            def test_one(self) -> None:
                """Hold."""

        module = type(sys)("planted_module")
        namespace: dict[str, object] = vars(module)
        namespace["Case"] = Case
        loader = ug.RecordingLoader()
        suite = loader.loadTestsFromModule(module)
        self.assertEqual(suite.countTestCases(), 1)
        self.assertEqual(loader.modules[id(suite)], ("planted_module", suite))

    def test_a_test_s_file_is_its_module_or_the_one_that_failed(self) -> None:
        """A test is its module's; a failed import is the module it names."""
        missing = "no_such_module_planted"
        failed = next(iter(unittest.TestLoader().loadTestsFromName(missing)))
        if not isinstance(failed, unittest.TestCase):
            self.fail(f"unittest loaded {failed!r} for a failed import")
        rows: list[tuple[unittest.TestCase, str]] = [
            (self, __name__),
            (failed, missing),
        ]
        for test, file in rows:
            with self.subTest(file=file):
                self.assertEqual(ug.file_of(test), file)

    def test_a_duration_is_collected_and_added_to_its_file(self) -> None:
        """Unittest's list still gets it; the file's total adds it up."""
        stream = ug.GuardedRunner(stream=io.StringIO(), verbosity=0).stream
        result = ug.GuardedResult(stream, descriptions=True, verbosity=0)
        result.startTestRun()
        result.addDuration(self, 0.25)
        result.addDuration(self, 0.5)
        self.assertEqual(len(result.collectedDurations), 2)
        spent: dict[str, float] = {__name__: 0.75}
        self.assertEqual(result.files, spent)

    def test_a_reported_test_is_its_description(self) -> None:
        """A worker's test prints as its description, with no docstring line."""
        reported = ug.Reported("test_one (test_clean.T.test_one)")
        self.assertEqual(str(reported), "test_one (test_clean.T.test_one)")
        self.assertIsNone(reported.shortDescription())


class Measured(unittest.TestCase):
    """Under coverage, workers measure what one process measures (#1623 item 2)."""

    def measure(self, workers: str) -> subprocess.CompletedProcess[str]:
        """Run coverage-measure.sh's python leg over the planted suite.

        Returns:
            The finished measurement.

        """
        hermetic.tool("coverage")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = pathlib.Path(tmp.name).resolve()
        tree, stubs = base / "tree", base / "stubs"
        tree.mkdir()
        stubs.mkdir()
        for name, body in MEASURED.items():
            (tree / name).write_text(body, encoding="utf-8")
        (stubs / "mise").write_text(MISE, encoding="utf-8")
        (stubs / "mise").chmod(0o755)
        run = f"python3 {RUNNER} discover --start-directory . --pattern 'test_*.py'"
        task = base / "task.json"
        task.write_text(f'{{"dir": "{tree}", "run": ["{run}"]}}', encoding="utf-8")
        env = hermetic.env(
            ORG_PYTHON=sys.executable,
            ORG_TEST_WORKERS=workers,
            STUB_TASK=str(task),
        )
        # The suite this row runs in may itself be measured: its hook
        # must not reach the measurement under test. Without
        # COVERAGE_PROCESS_START the hook starts nothing, so PYTHONPATH
        # stays, and with it the mutation leg's hook (#1542).
        env.pop("COVERAGE_PROCESS_START", None)
        env["PATH"] = f"{stubs}:{env.get('PATH', '')}"
        for argv in ([GIT, "init", "-q"], [GIT, "add", "."]):
            subprocess.run(argv, cwd=tree, env=env, check=True)
        # The suite runs once, through `measured`, as the repository's
        # test task runs it (#1667); the leg then reads that run.
        (tree / ".coverage-floor").write_text("", encoding="utf-8")
        env["ORG_COVERAGE_RUN"] = str(base / "run")
        subprocess.run(
            [str(MEASURE.parent / "measured"), BASH, "-c", run],
            cwd=tree,
            env=env,
            capture_output=True,
            check=True,
        )
        return subprocess.run(
            [BASH, str(MEASURE), "coverage:test", "python"],
            cwd=tree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_figure_is_the_same_serial_and_parallel(self) -> None:
        """The same statements and branches, one process or one per file."""
        serial = self.measure("1")
        parallel = self.measure("2")
        self.assertEqual(serial.returncode, 0, serial.stderr)
        self.assertEqual(parallel.returncode, 0, parallel.stderr)
        self.assertRegex(
            serial.stdout, r"\Apython [0-9.]+\npython-branches [0-9.]+\n\Z"
        )
        self.assertNotIn("python 100.0\n", serial.stdout)
        self.assertEqual(parallel.stdout, serial.stdout)


if __name__ == "__main__":
    unittest.main()
