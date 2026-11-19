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
under coverage the same figure.
"""

import json
import pathlib
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest

import hermetic

_HERE = pathlib.Path(__file__).resolve().parent
RUNNER = _HERE / "unittest_guarded.py"
MEASURE = _HERE / "coverage-measure.sh"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
# unittest prints its own timing; two runs never agree on it.
TIMING = re.compile(r"Ran (\d+ tests?) in \d+\.\d+s")

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


class Runner(unittest.TestCase):
    """The runner over planted modules."""

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
        # ruff: ignore[subprocess-without-shell-equals-true]
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

    def guarded(
        self, start: pathlib.Path, *flags: str, workers: str = "3"
    ) -> subprocess.CompletedProcess[str]:
        """Run the planted tree through the guarded runner.

        Returns:
            The finished process.

        """
        return self.run_with(
            start, [sys.executable, str(RUNNER)], *flags, workers=workers
        )

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
        self.assertIn(
            f"left the process in {start.parent / 'elsewhere'} (moved from {start})",
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
                    TIMING.sub(r"Ran \1", ours.stderr),
                    TIMING.sub(r"Ran \1", plain.stderr),
                )

    def test_verbosity_reaches_the_runner(self) -> None:
        """`-v` still prints one line per test: unittest builds the runner."""
        start, _ = self.tree(test_clean=CLEAN)
        r = self.run_with(start, [sys.executable, str(RUNNER)], "-v")
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
                    TIMING.sub(r"Ran \1", parallel.stderr),
                    TIMING.sub(r"Ran \1", serial.stderr),
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
                r = self.run_with(
                    start, [sys.executable, str(RUNNER)], workers=workers, pids=pids
                )
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
                    f"unittest-guarded: ORG_TEST_WORKERS={value!r} is not a worker "
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

    def test_an_unreadable_spec_stops_the_worker_by_name(self) -> None:
        """A worker handed a spec it cannot read exits 1, naming the file."""
        start, _ = self.tree(test_clean=CLEAN)
        spec = start.parent / "bad.json"
        for body in ("{", '{"module": 1}'):
            with self.subTest(spec=body):
                spec.write_text(body, encoding="utf-8")
                r = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                    [sys.executable, str(RUNNER), "--worker", str(spec)],
                    cwd=start,
                    env=hermetic.env(),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertEqual(
                    r.stderr, f"unittest-guarded: worker spec {spec} is unreadable\n"
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


def run_worker(
    start: pathlib.Path, spec: pathlib.Path
) -> subprocess.CompletedProcess[str]:
    """Run one worker over a spec, as the parent would.

    Returns:
        The finished worker.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    return subprocess.run(
        [sys.executable, str(RUNNER), "--worker", str(spec)],
        cwd=start,
        env=hermetic.env(),
        capture_output=True,
        text=True,
        check=False,
    )


class Shapes(unittest.TestCase):
    """Every wrong shape the outcome and spec readers refuse (#1659)."""

    tree = Runner.tree
    run_with = staticmethod(Runner.run_with)
    guarded = Runner.guarded

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
                r = run_worker(start, spec)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertEqual(
                    r.stderr, f"unittest-guarded: worker spec {spec} is unreadable\n"
                )

    def test_a_file_with_no_tests_is_planned_as_nothing(self) -> None:
        """A discovered module with no tests gets no worker; the rest run."""
        start, _ = self.tree(test_empty="import unittest\n", test_clean=CLEAN)
        r = self.guarded(start)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ran 2 tests in", r.stderr)
        self.assertTrue(r.stderr.rstrip().endswith("OK"), r.stderr)

    def test_no_worker_count_takes_the_cores_up_to_the_cap(self) -> None:
        """Unset, the count is the machine's, and the suite runs as usual."""
        start, _ = self.tree(test_clean=CLEAN, test_more=CLEAN)
        r = self.guarded(start, workers="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ran 4 tests in", r.stderr)
        self.assertTrue(r.stderr.rstrip().endswith("OK"), r.stderr)

    def test_a_spec_with_no_warnings_action_runs_its_module(self) -> None:
        """An absent action is valid: the worker runs and writes its outcome."""
        start, _ = self.tree(test_clean=CLEAN)
        spec = write_spec(start, patterns=["*one"])
        r = run_worker(start, spec)
        self.assertEqual(r.returncode, 0, r.stderr)
        outcome = json.loads(spec.with_suffix(".out").read_text(encoding="utf-8"))
        self.assertEqual(1, outcome["run"])
        self.assertEqual([], outcome["errors"] + outcome["failures"])


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
            # ruff: ignore[subprocess-without-shell-equals-true]
            subprocess.run(argv, cwd=tree, env=env, check=True)
        # ruff: ignore[subprocess-without-shell-equals-true]
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
