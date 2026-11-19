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
import warnings
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

START = str(Path.cwd())
SELF = str(Path(__file__).resolve())

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

    # ruff: ignore[invalid-function-name]
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


class Reported:
    """A test that ran in a worker, standing in by its description."""

    def __init__(self, description: str) -> None:
        """Hold the description the worker's result gave the test."""
        self.description = description

    def __str__(self) -> str:
        """Return the description, which is what the summary prints.

        Returns:
            The worker's description of the test.

        """
        return self.description

    @staticmethod
    # ruff: ignore[invalid-function-name]
    def shortDescription() -> None:
        """Return nothing: the description already carries the docstring."""


class RecordingLoader(unittest.TestLoader):
    """unittest's loader, remembering which suite each module produced."""

    def __init__(self) -> None:
        """Start with no modules recorded."""
        super().__init__()
        self.modules: dict[int, tuple[str, unittest.TestSuite]] = {}

    # ruff: ignore[invalid-function-name]
    def loadTestsFromModule(  # type: ignore[override]
        self, module: object, *args: object, **kwargs: object
    ) -> unittest.TestSuite:
        """Load as unittest does, and record the suite against the module.

        Returns:
            The module's suite, unchanged.

        """
        suite = super().loadTestsFromModule(module, *args, **kwargs)  # type: ignore[arg-type]
        self.modules[id(suite)] = (getattr(module, "__name__", ""), suite)
        return suite


LOADER = RecordingLoader()

# A unit of the plan: a module's name and suite, run in a worker, or a
# test with no module (an import that failed), run here.
Unit = tuple[str | None, unittest.TestSuite | unittest.TestCase]


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


def flatten(test: unittest.TestSuite | unittest.TestCase) -> list[object]:
    """List the tests of a suite.

    Returns:
        Every test case, depth first.

    """
    if isinstance(test, unittest.TestSuite):
        return [case for item in test for case in flatten(item)]
    return [test]


class GuardedRunner(unittest.TextTestRunner):
    """unittest's text runner, reporting through GuardedResult.

    A class rather than an instance, so unittest still builds it with the
    command line's verbosity, failfast and buffer settings.
    """

    resultclass = GuardedResult

    def run(self, test: unittest.TestSuite | unittest.TestCase) -> unittest.TestResult:  # type: ignore[override]
        """Run the suite, its files in parallel when there are workers.

        Returns:
            The merged result.

        """
        count = workers()
        if count == 1 or self.failfast or not isinstance(test, unittest.TestSuite):
            return super().run(test)
        return super().run(self.parallel(plan(test), count))

    def spec(self, module: str) -> dict[str, object]:
        """Describe one worker's run.

        Returns:
            What the worker needs to run one module as this run would.

        """
        return {
            "module": module,
            "path": sys.path,
            "patterns": LOADER.testNamePatterns,
            "verbosity": self.verbosity,
            "buffer": self.buffer,
            "tb_locals": self.tb_locals,
            "warnings": self.warnings,
        }

    def parallel(
        self, units: list[Unit], count: int
    ) -> Callable[[unittest.TestResult], None]:
        """Build the callable unittest's runner runs in place of the suite.

        Returns:
            A callable that runs every unit and merges into the result.

        """

        def run_all(result: unittest.TestResult) -> None:
            with (
                tempfile.TemporaryDirectory() as scratch,
                ThreadPoolExecutor(max_workers=count) as pool,
            ):
                started: list[Future[subprocess.CompletedProcess[str]] | None] = []
                for index, (module, _) in enumerate(units):
                    if module is None:
                        started.append(None)
                        continue
                    spec = Path(scratch) / f"{index}.json"
                    spec.write_text(json.dumps(self.spec(module)), encoding="utf-8")
                    started.append(pool.submit(work, spec))
                for index, ((module, item), future) in enumerate(
                    zip(units, started, strict=True)
                ):
                    if future is None:
                        item(result)
                        continue
                    outcome = Path(scratch) / f"{index}.out"
                    merge(result, module or "", future.result(), outcome)

        return run_all


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
    if not outcome.exists():
        # A worker that died without a result (a crash, an exit from a
        # test) is an error on its module, never a module that vanished.
        holder = Reported(f"{module} (worker)")
        result.testsRun += 1
        result.errors.append((
            holder,  # type: ignore[arg-type]
            (
                f"the worker for {module} exited {done.returncode} with no result; "
                "its output is printed above\n"
            ),
        ))
        return
    got = json.loads(outcome.read_text(encoding="utf-8"))
    result.testsRun += got["run"]
    for name in ("errors", "failures", "expectedFailures"):
        getattr(result, name).extend(
            (Reported(description), text) for description, text in got[name]
        )
    result.skipped.extend((Reported(d), reason) for d, reason in got["skipped"])
    result.unexpectedSuccesses.extend(Reported(d) for d in got["unexpectedSuccesses"])
    result.collectedDurations.extend((d, s) for d, s in got["durations"])


class WorkerRunner(unittest.TextTestRunner):
    """Run one module, print its progress, and write its outcome."""

    resultclass = GuardedResult

    def __init__(self, outcome: Path, **kwargs: object) -> None:
        """Remember where the outcome goes."""
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.outcome = outcome

    def run(self, test: unittest.TestSuite | unittest.TestCase) -> unittest.TestResult:  # type: ignore[override]
        """Run the module; the summary is the parent's to print.

        Returns:
            The worker's result.

        """
        result = self._makeResult()
        unittest.registerResult(result)
        result.buffer = self.buffer
        result.tb_locals = self.tb_locals
        with warnings.catch_warnings():
            if self.warnings:
                warnings.simplefilter(self.warnings)
            result.startTestRun()
            try:
                test(result)
            finally:
                result.stopTestRun()

        def described(pairs: list[tuple[object, str]]) -> list[list[str]]:
            return [[result.getDescription(t), text] for t, text in pairs]  # type: ignore[arg-type]

        self.outcome.write_text(
            json.dumps({
                "run": result.testsRun,
                "errors": described(result.errors),
                "failures": described(result.failures),
                "expectedFailures": described(result.expectedFailures),
                "skipped": described(result.skipped),
                "unexpectedSuccesses": [
                    result.getDescription(t)  # type: ignore[arg-type]
                    for t in result.unexpectedSuccesses
                ],
                "durations": result.collectedDurations,
            }),
            encoding="utf-8",
        )
        return result


def worker(spec_path: Path) -> None:
    """Run the one module a spec names, as the parent's run would."""
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
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
