#!/usr/bin/env python3
"""Run `unittest discover`, a file per process, naming tests that move it.

A test that changes the process's working directory and does not put it
back leaves every later test in the wrong place, or in a directory that
no longer exists, and a subprocess started from there can fail on it
(a shell's "getcwd() failed"). The failure then lands on whichever test
runs next, by discovery order and by platform, never on the test that
caused it (#1491).

This is `python3 -m unittest` with two changes. First, a result class
that, after every test, checks the working directory still exists and
is the one the run started in. A test that leaves it elsewhere is an
ERROR on that test, by name, and the directory is put back so the next
test is judged on its own.

Second, the suite runs its files in parallel (#1623). Discovery happens
here, as unittest's own; then each test file runs in a process of its
own, as many at once as there are workers, and the results are merged
back into one unittest result: each file's progress printed in
discovery order, every failure printed in full, one summary, one exit
status. A file that could not be imported is reported here, in
discovery order, exactly as unittest reports it. The worker count is
the machine's core count capped at WORKER_CAP, so a workstation running
several gates is not starved; ORG_TEST_WORKERS sets it, and 1 runs the
suite serially in this one process, as plain unittest does. Failfast
(`-f`) also runs serially: stopping at the first failure is an order.

Coverage needs nothing from here: a worker is `python3`, started with
this process's environment, so the measurement's start-up hook reaches
it as it reaches any child, and its data file is its own (`parallel =
true`, combined after). Stdlib only (#364). Discovery, arguments,
output and exit status are otherwise unittest's own.
"""

import json
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict, override
from warnings import catch_warnings, simplefilter

if TYPE_CHECKING:
    from types import ModuleType

START = str(Path.cwd())
SELF = str(Path(__file__).resolve())

# A warnings filter action, as unittest's runner takes it.
type Action = Literal["default", "error", "ignore", "always", "module", "once"]
ACTIONS: dict[str, Action] = {
    "default": "default",
    "error": "error",
    "ignore": "ignore",
    "always": "always",
    "module": "module",
    "once": "once",
}

# The default ceiling on workers: the gate runner has four cores, and a
# workstation running several gates at once should keep cores to spare.
WORKER_CAP = 8
WORKERS = "ORG_TEST_WORKERS"

# Where the process was last sent. A deleted directory has no name to
# ask for, so the last successful `os.chdir` is the only way to say
# which one a test left behind.
_chdir = os.chdir
_last = {"path": START}


def _recording_chdir(path: os.PathLike[str] | str | int) -> None:
    """Change directory and remember where to, for the report."""
    _chdir(path)
    _last["path"] = str(Path.cwd())


os.chdir = _recording_chdir


class LeftTheProcessError(Exception):
    """A test returned with the process somewhere other than it started."""


def where() -> str | None:
    """Say where the process is, unless that is where the run started.

    Returns:
        The message naming the directory, or None when it is unchanged.

    """
    try:
        here = str(Path.cwd())
    except FileNotFoundError:
        return f"left the process in {_last['path']} (deleted)"
    if here != START:
        return f"left the process in {here} (moved from {START})"
    return None


def workers() -> int:
    """Read the worker count: ORG_TEST_WORKERS, or the cores up to the cap.

    Returns:
        How many test files run at once; 1 runs serially.

    """
    raw = os.environ.get(WORKERS, "").strip()
    if not raw:
        return max(1, min(os.process_cpu_count() or 1, WORKER_CAP))
    if not raw.isdecimal() or int(raw) < 1:
        sys.exit(
            f"unittest-guarded: {WORKERS}={raw!r} is not a worker count (1 or more)"
        )
    return int(raw)


class GuardedResult(unittest.TextTestResult):
    """unittest's text result, plus the working-directory check."""

    @override
    def stopTest(self, test: unittest.TestCase) -> None:
        """After every test, judge the directory and put it back."""
        message = where()
        if message is not None:
            _chdir(START)
            _last["path"] = START
            # Reported, never raised: the test has already returned, and
            # there is no frame of its to point at.
            self.addError(
                test, (LeftTheProcessError, LeftTheProcessError(message), None)
            )
        super().stopTest(test)


class Reported(unittest.TestCase):
    """A test that ran in a worker, standing in by its description.

    A TestCase so the result's lists hold what their type says; it is
    never run, only printed.
    """

    def __init__(self, description: str) -> None:
        """Hold the description the worker's result gave the test."""
        super().__init__()
        self.description = description

    @override
    def __str__(self) -> str:
        """Return the description, which is what the summary prints.

        Returns:
            The worker's description of the test.

        """
        return self.description

    @override
    def shortDescription(self) -> str | None:
        """Return nothing: the description already carries the docstring.

        Returns:
            None.

        """
        return None


class Spec(TypedDict):
    """What a worker needs to run one module as the parent's run would."""

    module: str
    path: list[str]
    patterns: list[str] | None
    verbosity: int
    buffer: bool
    tb_locals: bool
    warnings: Action | None


class Outcome(TypedDict):
    """What a worker hands back: its counts and descriptions."""

    run: int
    errors: list[tuple[str, str]]
    failures: list[tuple[str, str]]
    expectedFailures: list[tuple[str, str]]
    skipped: list[tuple[str, str]]
    unexpectedSuccesses: list[str]
    durations: list[tuple[str, float]]


class RecordingLoader(unittest.TestLoader):
    """unittest's loader, remembering which suite each module produced."""

    def __init__(self) -> None:
        """Start with no modules recorded."""
        super().__init__()
        self.modules: dict[int, tuple[str, unittest.TestSuite]] = {}

    @override
    def loadTestsFromModule(
        self, module: ModuleType, *, pattern: str | None = None
    ) -> unittest.TestSuite:
        """Load as unittest does, and record the suite against the module.

        Returns:
            The module's suite, unchanged.

        """
        suite = super().loadTestsFromModule(module, pattern=pattern)
        self.modules[id(suite)] = (module.__name__, suite)
        return suite


LOADER = RecordingLoader()

# A unit of the plan: a module's name and suite, run in a worker, or a
# test with no module (an import that failed), run here.
type Unit = tuple[str | None, unittest.TestSuite | unittest.TestCase]


def plan(suite: unittest.TestSuite) -> list[Unit]:
    """Split a discovered suite into modules, in discovery order.

    Returns:
        One unit per module with tests, and one per test that belongs to
        no module, each where discovery put it.

    """
    units: list[Unit] = []
    for item in suite:
        recorded = LOADER.modules.get(id(item))
        if recorded is not None and recorded[1] is item:
            if item.countTestCases():
                units.append((recorded[0], item))
        elif isinstance(item, unittest.TestSuite):
            units.extend(plan(item))
        else:
            units.append((None, item))
    return units


class Parallel(unittest.TestSuite):
    """The discovered suite, its modules run in workers and merged here."""

    def __init__(self, units: list[Unit], spec: Spec, count: int) -> None:
        """Hold the plan, the worker settings and the worker count."""
        super().__init__()
        self.units = units
        self.spec = spec
        self.count = count

    @override
    def run(
        self, result: unittest.TestResult, debug: bool = False
    ) -> unittest.TestResult:
        """Run every unit, printing and merging in discovery order.

        Returns:
            The result, holding every unit's outcome.

        """
        with (
            tempfile.TemporaryDirectory() as scratch,
            ThreadPoolExecutor(max_workers=self.count) as pool,
        ):
            started: list[Future[subprocess.CompletedProcess[str]] | None] = []
            for index, (module, _) in enumerate(self.units):
                if module is None:
                    started.append(None)
                    continue
                spec = Path(scratch) / f"{index}.json"
                spec.write_text(
                    json.dumps(self.spec | {"module": module}), encoding="utf-8"
                )
                started.append(pool.submit(work, spec))
            for index, ((module, item), future) in enumerate(
                zip(self.units, started, strict=True)
            ):
                if future is None:
                    item(result)
                    continue
                outcome = Path(scratch) / f"{index}.out"
                merge(result, module or "", future.result(), outcome)
        return result


class GuardedRunner(unittest.TextTestRunner):
    """unittest's text runner, reporting through GuardedResult.

    A class rather than an instance, so unittest still builds it with the
    command line's verbosity, failfast and buffer settings.
    """

    resultclass = GuardedResult

    @override
    def run(
        self, test: unittest.TestSuite | unittest.TestCase
    ) -> unittest.TextTestResult:
        """Run the suite, its files in parallel when there are workers.

        Returns:
            The merged result.

        """
        count = workers()
        if count == 1 or self.failfast or not isinstance(test, unittest.TestSuite):
            return super().run(test)
        spec: Spec = {
            "module": "",
            "path": sys.path,
            "patterns": LOADER.testNamePatterns,
            "verbosity": self.verbosity,
            "buffer": self.buffer,
            "tb_locals": self.tb_locals,
            "warnings": self.warnings,
        }
        return super().run(Parallel(plan(test), spec, count))


def work(spec: Path) -> subprocess.CompletedProcess[str]:
    """Run one worker on a spec, capturing what it prints.

    Returns:
        The finished worker.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    return subprocess.run(
        [sys.executable, SELF, "--worker", str(spec)],
        capture_output=True,
        text=True,
        check=False,
    )


def merge(
    result: unittest.TestResult,
    module: str,
    done: subprocess.CompletedProcess[str],
    outcome: Path,
) -> None:
    """Print a worker's output and fold its outcome into the result."""
    sys.stdout.write(done.stdout)
    sys.stdout.flush()
    sys.stderr.write(done.stderr)
    sys.stderr.flush()
    got = read_outcome(outcome)
    if not isinstance(got, dict):
        # A worker that died without a result (a crash, an exit from a
        # test), or part-way through writing it, is an error on its
        # module, never a module that vanished or a traceback here.
        result.testsRun += 1
        result.errors.append((
            Reported(f"{module} (worker)"),
            (
                f"the worker for {module} exited {done.returncode} with {got}; "
                "its output is printed above\n"
            ),
        ))
        return
    result.testsRun += got["run"]
    result.errors.extend((Reported(d), text) for d, text in got["errors"])
    result.failures.extend((Reported(d), text) for d, text in got["failures"])
    result.expectedFailures.extend(
        (Reported(d), text) for d, text in got["expectedFailures"]
    )
    result.skipped.extend((Reported(d), reason) for d, reason in got["skipped"])
    result.unexpectedSuccesses.extend(Reported(d) for d in got["unexpectedSuccesses"])
    result.collectedDurations.extend((d, s) for d, s in got["durations"])


class WorkerRunner(GuardedRunner):
    """Run one module, print its progress, and write its outcome."""

    def __init__(
        self,
        outcome: Path,
        *,
        verbosity: int,
        buffer: bool,
        tb_locals: bool,
        warnings: Action | None,
    ) -> None:
        """Take the parent's settings, and where the outcome goes."""
        super().__init__(
            verbosity=verbosity, buffer=buffer, tb_locals=tb_locals, warnings=warnings
        )
        self.outcome = outcome

    @override
    def run(
        self, test: unittest.TestSuite | unittest.TestCase
    ) -> unittest.TextTestResult:
        """Run the module; the summary is the parent's to print.

        Returns:
            The worker's result.

        """
        result = self._makeResult()
        unittest.registerResult(result)
        result.buffer = self.buffer
        result.tb_locals = self.tb_locals
        with catch_warnings():
            if self.warnings:
                simplefilter(self.warnings)
            result.startTestRun()
            try:
                test(result)
            finally:
                result.stopTestRun()

        def described(
            pairs: list[tuple[unittest.TestCase, str]],
        ) -> list[tuple[str, str]]:
            return [(result.getDescription(t), text) for t, text in pairs]

        outcome: Outcome = {
            "run": result.testsRun,
            "errors": described(result.errors),
            "failures": described(result.failures),
            "expectedFailures": described(result.expectedFailures),
            "skipped": described(result.skipped),
            "unexpectedSuccesses": [
                result.getDescription(t) for t in result.unexpectedSuccesses
            ],
            "durations": list(result.collectedDurations),
        }
        self.outcome.write_text(json.dumps(outcome), encoding="utf-8")
        return result


# A decoded [description, text] or [description, seconds] has two items.
PAIR = 2


def load(path: Path) -> object:
    """Read a JSON file the runner wrote, or None when it cannot be read.

    Returns:
        The decoded document, as an object to be checked.

    """
    try:
        document: object = json.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeDecodeError, json.JSONDecodeError:
        return None
    return document


def strings(value: object) -> list[str] | None:
    """Check a decoded list of strings.

    Returns:
        The strings, or None when the value is not a list of them.

    """
    if not isinstance(value, list):
        return None
    out: list[str] = []
    item: object
    for item in value:
        if not isinstance(item, str):
            return None
        out.append(item)
    return out


def pairs(value: object) -> list[tuple[str, str]] | None:
    """Check a decoded list of [description, text] pairs.

    Returns:
        The pairs, or None when the value is not a list of them.

    """
    if not isinstance(value, list):
        return None
    out: list[tuple[str, str]] = []
    item: object
    for item in value:
        both = strings(item)
        if both is None or len(both) != PAIR:
            return None
        out.append((both[0], both[1]))
    return out


def timings(value: object) -> list[tuple[str, float]] | None:
    """Check a decoded list of [description, seconds] pairs.

    Returns:
        The pairs, or None when the value is not a list of them.

    """
    if not isinstance(value, list):
        return None
    out: list[tuple[str, float]] = []
    item: object
    for item in value:
        if not isinstance(item, list) or len(item) != PAIR:
            return None
        name: object = item[0]
        seconds: object = item[1]
        if (
            not isinstance(name, str)
            or not isinstance(seconds, (int, float))
            or isinstance(seconds, bool)
        ):
            return None
        out.append((name, float(seconds)))
    return out


def read_outcome(path: Path) -> Outcome | str:
    """Read and check a worker's outcome file.

    Returns:
        The outcome, or what was wrong with it, for the error message.

    """
    if not path.exists():
        return "no result"
    unreadable = "an unreadable result"
    document = load(path)
    if not isinstance(document, dict):
        return unreadable
    run: object = document.get("run")
    if not isinstance(run, int) or isinstance(run, bool):
        return unreadable
    lists = {
        name: pairs(document.get(name))
        for name in ("errors", "failures", "expectedFailures", "skipped")
    }
    errors, failures = lists["errors"], lists["failures"]
    expected, skipped = lists["expectedFailures"], lists["skipped"]
    if errors is None or failures is None or expected is None or skipped is None:
        return unreadable
    unexpected = strings(document.get("unexpectedSuccesses"))
    durations = timings(document.get("durations"))
    if unexpected is None or durations is None:
        return unreadable
    return {
        "run": run,
        "errors": errors,
        "failures": failures,
        "expectedFailures": expected,
        "skipped": skipped,
        "unexpectedSuccesses": unexpected,
        "durations": durations,
    }


def read_action(value: object) -> tuple[bool, Action | None]:
    """Check a decoded warnings action, which may be absent.

    Returns:
        Whether it is valid, and the action (None when absent).

    """
    if value is None:
        return True, None
    action = ACTIONS.get(value) if isinstance(value, str) else None
    return action is not None, action


def read_spec(path: Path) -> Spec | None:
    """Read and check a worker's spec file.

    Returns:
        The spec, or None when it cannot be read or is the wrong shape.

    """
    document = load(path)
    if not isinstance(document, dict):
        return None
    module: object = document.get("module")
    search = strings(document.get("path"))
    if not isinstance(module, str) or search is None:
        return None
    raw_patterns: object = document.get("patterns")
    patterns = None if raw_patterns is None else strings(raw_patterns)
    if raw_patterns is not None and patterns is None:
        return None
    verbosity: object = document.get("verbosity")
    buffer: object = document.get("buffer")
    tb_locals: object = document.get("tb_locals")
    if not isinstance(verbosity, int) or not isinstance(buffer, bool):
        return None
    valid, action = read_action(document.get("warnings"))
    if not isinstance(tb_locals, bool) or not valid:
        return None
    return {
        "module": module,
        "path": search,
        "patterns": patterns,
        "verbosity": verbosity,
        "buffer": buffer,
        "tb_locals": tb_locals,
        "warnings": action,
    }


def worker(spec_path: Path) -> None:
    """Run the one module a spec names, as the parent's run would."""
    spec = read_spec(spec_path)
    if spec is None:
        sys.exit(f"unittest-guarded: worker spec {spec_path} is unreadable")
    sys.path[:] = spec["path"]
    loader = unittest.TestLoader()
    loader.testNamePatterns = spec["patterns"]
    suite = loader.loadTestsFromName(spec["module"])
    WorkerRunner(
        spec_path.with_suffix(".out"),
        verbosity=spec["verbosity"],
        buffer=spec["buffer"],
        tb_locals=spec["tb_locals"],
        warnings=spec["warnings"],
    ).run(suite)


def main() -> None:
    """Hand the command line to unittest, with the guarded result."""
    if sys.argv[1:2] == ["--worker"]:
        worker(Path(sys.argv[2]))
        return
    # `python3 -m unittest` puts the working directory first on the
    # import path; a script puts its own directory there. Match -m.
    sys.path[0] = START
    unittest.main(
        module=None,
        argv=["python3 -m unittest", *sys.argv[1:]],
        testLoader=LOADER,
        testRunner=GuardedRunner,
    )


if __name__ == "__main__":
    main()
