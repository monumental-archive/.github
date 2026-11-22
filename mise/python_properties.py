#!/usr/bin/env python3
"""Run a repository's Hypothesis properties under the belt's profile.

The body of `test:python-properties` (the gate) and
`audit:python-properties` (the Monday audit), run by
`python-properties.sh` on the Hypothesis tool venv's interpreter
(#1332). The two profiles live here and nowhere else, so no repository
sets its own bounds:

- `gate`: `derandomize=True` — each test's examples are derived from
  the test itself, so two runs execute the same examples — a bounded
  `max_examples`, no example database, no deadline.
- `audit`: a random seed, printed before anything runs and taken from
  ORG_PROPERTY_SEED when set, so a failure reproduces exactly; a larger
  `max_examples`; no example database, no deadline.

Neither profile has a deadline or the `too_slow` health check: both
measure wall time, and wall time on a shared runner is not a property of
the code. A determinism that depends on the machine's load is not one.

The profile is loaded BEFORE any file is imported, because a test's
settings are fixed when its decorators run. A test that restates any of
the profile's four bounds (`max_examples`, `derandomize`, `database`,
`deadline`) with its own `@settings` is refused by name: the belt owns
them.

Each `prop_*.py` file is imported from its own directory, as the
`unittest` discovery beside it would, with the belt's own directory on
the path too, so `from typed_hypothesis import given` (the typed `given`
the belt delivers, #1604) resolves in any repository. It must hold at
least one
Hypothesis test: a property file with no property is a check that looks
like success. The output is the tests in file and name order and their
outcome, each file's printed as that file finishes, then every
failure's traceback — Hypothesis prints the shrunk falsifying example
in it. The gate prints no timings, so a passing gate run is
byte-identical to the next; the audit prints each file's elapsed
seconds after its tests, so its bound is measured from its own log
(#1631).

ORG_PROPERTY_SHARD=k/n takes one shard of the files (#1631, the
mutation leg's MUTANTS_SHARD form): the files sorted, every n-th
starting at the k-th. Unset is every file, as given. A value that is
not k/n with k < n is refused by name; a shard that holds no file says
so and exits 0. The audit runs n legs with one seed, each a shard, so a
week's properties finish in the time of the slowest leg.

With no shard named, more than one file and more than one worker, the
runner fans out over that same form (#1741): it starts n children of
itself, child k with ORG_PROPERTY_SHARD=k/n, n the smaller of the worker
count and the file count, and so uses every core its job has. The
worker count is the test runner's, `workers()` from unittest_guarded.py:
ORG_TEST_WORKERS sets it, and 1 runs every file in this one process.
Each child's output is printed whole, in shard order, once it has
ended, so nothing interleaves; the run is red when any child is; the
last line totals the tests and files across the children. The audit's
children share one seed: ORG_PROPERTY_SEED, or one drawn here and
handed to each. A named shard never fans out. The gate's profile
derives each test's examples from the test itself, so a file is given
the same examples whichever process runs it.

Usage:
  python_properties.py gate|audit FILE...

Exits 0 when every property held, 1 when one failed or a file could not
be run, 2 on a usage error.
"""

import importlib.util
import inspect
import os
import random
import re
import subprocess
import sys
import time
import traceback
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

import findings
from hypothesis import HealthCheck, seed, settings
from unittest_guarded import workers

if TYPE_CHECKING:
    from types import ModuleType

GATE_EXAMPLES = 200
AUDIT_EXAMPLES = 10_000
BOUNDS = ("max_examples", "derandomize", "database", "deadline")
EXIT_USAGE = 2
# A profile name and at least one file.
MIN_ARGS = 2
# The belt: typed_hypothesis.py sits beside this runner.
BELT = Path(__file__).resolve().parent
LABELS = {"gate": "test:python-properties", "audit": "audit:python-properties"}
# k/n in ASCII digits, n at least one; k < n is checked on the numbers.
SHARD = re.compile(r"([0-9]+)/([1-9][0-9]*)")
# Fewer files or workers than this run in one process.
MIN_FANOUT = 2
# One test's line as `run` prints it, after its file's name.
RESULT = re.compile(r".+: \S+ \.\.\. (?:ok|skipped|FAIL)")
# Typed here: register_profile takes its settings as `**kwargs: Any`.
SUPPRESSED: list[HealthCheck] = [HealthCheck.too_slow]

settings.register_profile(
    "gate",
    derandomize=True,
    max_examples=GATE_EXAMPLES,
    database=None,
    deadline=None,
    suppress_health_check=SUPPRESSED,
)
settings.register_profile(
    "audit",
    derandomize=False,
    max_examples=AUDIT_EXAMPLES,
    database=None,
    deadline=None,
    suppress_health_check=SUPPRESSED,
)


def load(path: Path, index: int) -> ModuleType:
    """Import one property file from its own directory.

    Returns:
        The module.

    Raises:
        ImportError: Python finds no way to import the path.

    """
    for directory in (str(BELT), str(path.resolve().parent)):
        if directory not in sys.path:
            sys.path.insert(0, directory)
    name = f"prop_{index}_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        msg = f"no loader for {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def flatten(suite: unittest.TestSuite) -> list[unittest.TestCase]:
    """List a suite's tests.

    Returns:
        Every test case, nested suites expanded, in name order.

    """
    out: list[unittest.TestCase] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            out += flatten(item)
        else:
            out.append(item)
    return sorted(out, key=unittest.TestCase.id)


def method(test: unittest.TestCase) -> object:
    """Find the function a test case runs.

    Returns:
        The class attribute behind the test's method name.

    """
    name = test._testMethodName  # ruff: ignore[private-member-access] - unittest names the method only here
    found: object = getattr(type(test), name, None)
    return found


def is_property(func: object) -> bool:
    """Say whether a test function is a Hypothesis test.

    Returns:
        True for a function `@given` wrapped.

    """
    flag: object = getattr(func, "is_hypothesis_test", False)
    return bool(flag)


def own_bounds(func: object, profile: settings) -> list[str]:
    """Name the profile bounds a test restates with its own `@settings`.

    Returns:
        `field=value` for each bound that differs from the profile.

    """
    mine: object = getattr(func, "_hypothesis_internal_use_settings", None)
    if not isinstance(mine, settings):
        return []
    have, want = bounds(mine), bounds(profile)
    return [
        f"{field}={have[field]!r}" for field in BOUNDS if have[field] != want[field]
    ]


def bounds(chosen: settings) -> dict[str, object]:
    """Read the bounds the belt's profile owns from one settings object.

    Returns:
        Each bound in BOUNDS, by name.

    """
    return {
        "max_examples": chosen.max_examples,
        "derandomize": chosen.derandomize,
        "database": chosen.database,
        "deadline": chosen.deadline,
    }


def outcome(test: unittest.TestCase) -> tuple[str, str]:
    """Run one test.

    Returns:
        "ok", "skipped" or "FAIL", and the failure text (empty unless
        it failed).

    """
    result = unittest.TestResult()
    test.run(result)
    problems = result.failures + result.errors
    if problems:
        return "FAIL", "\n".join(text for _, text in problems)
    if result.skipped:
        return "skipped", ""
    return "ok", ""


class Report:
    """What one run found, printed once every file has run."""

    def __init__(self) -> None:
        """Start empty."""
        self.lines: list[str] = []
        self.failures: list[str] = []
        self.refused: list[str] = []


def run_file(
    path: Path, index: int, profile: settings, chosen: int | None, report: Report
) -> None:
    """Import one property file and run its tests into the report."""
    try:
        module = load(path, index)
    except Exception:  # ruff: ignore[blind-except] - any import failure is the file's own report
        report.refused.append(
            f"{path}: could not be imported\n{traceback.format_exc()}"
        )
        return
    tests = flatten(unittest.defaultTestLoader.loadTestsFromModule(module))
    if not any(is_property(method(t)) for t in tests):
        report.refused.append(f"{path}: holds no Hypothesis test (no @given)")
        return
    for test in tests:
        func = method(test)
        bounds = own_bounds(func, profile)
        if bounds:
            report.refused.append(
                f"{path}: {test.id()} sets its own bounds ({', '.join(bounds)});"
                " the belt's profile owns them",
            )
            continue
        if chosen is not None and is_property(func) and inspect.isfunction(func):
            name = test._testMethodName  # ruff: ignore[private-member-access] - unittest names the method only here
            setattr(type(test), name, seed(chosen)(func))
        status, text = outcome(test)
        report.lines.append(f"{path}: {test.id()} ... {status}")
        if text:
            report.failures.append(f"{path}: {test.id()}\n{text}")


class ShardError(ValueError):
    """An ORG_PROPERTY_SHARD this runner cannot read."""


def shard_of(files: list[Path], spec: str) -> list[Path]:
    """Take the files one shard runs.

    Returns:
        Every file, as given, when `spec` is empty; else the files
        sorted, every n-th starting at the k-th, for `spec` = `k/n`.

    Raises:
        ShardError: `spec` is not k/n with k < n.

    """
    if not spec:
        return files
    found = SHARD.fullmatch(spec)
    if found is None or int(found[1]) >= int(found[2]):
        msg = f"ORG_PROPERTY_SHARD='{spec}' is not k/n with k < n"
        raise ShardError(msg)
    return sorted(files, key=str)[int(found[1]) :: int(found[2])]


def children(spec: str, files: list[Path], cores: int) -> int:
    """Say how many children of this runner take the files.

    Returns:
        1, run here, when a shard is named, one file is given or one
        worker is; else the smaller of the workers and the files.

    """
    if spec or len(files) < MIN_FANOUT or cores < MIN_FANOUT:
        return 1
    return min(cores, len(files))


def tested(output: str, files: list[Path]) -> int:
    """Count the result lines one child printed.

    Returns:
        The lines `run` prints per test, `FILE: TEST ... STATUS`, whose
        FILE is one this run was given.

    """
    names = tuple(f"{f}: " for f in files)
    return sum(
        1
        for line in output.splitlines()
        if line.startswith(names) and RESULT.fullmatch(line)
    )


def fan_out(profile_name: str, files: list[Path], count: int) -> int:
    """Run `count` children of this runner, child k on shard k/count.

    Returns:
        0 when every child held, 1 otherwise.

    """
    label = LABELS[profile_name]
    environ = dict(os.environ)
    if profile_name == "audit" and not environ.get("ORG_PROPERTY_SEED"):
        environ["ORG_PROPERTY_SEED"] = str(random.SystemRandom().getrandbits(64))
    argv = [sys.executable, str(Path(__file__).resolve()), profile_name]
    argv += [str(f) for f in files]

    def child(k: int) -> subprocess.CompletedProcess[str]:
        shard = {"ORG_PROPERTY_SHARD": f"{k}/{count}"}
        return subprocess.run(
            argv, env=environ | shard, capture_output=True, text=True, check=False
        )

    with ThreadPoolExecutor(max_workers=count) as pool:
        done = list(pool.map(child, range(count)))
    red: list[str] = []
    tests = 0
    for k, result in enumerate(done):
        sys.stdout.write(result.stdout)
        sys.stdout.flush()
        sys.stderr.write(result.stderr)
        sys.stderr.flush()
        tests += tested(result.stdout, files)
        if result.returncode:
            red.append(f"{k}/{count}")
    total = f"{tests} test(s) in {len(files)} file(s)"
    if red:
        findings.finding("", f"{total}; shard(s) {', '.join(red)} red")
        return 1
    print(f"{label}: {total} held")
    return 0


def run(profile_name: str, files: list[Path]) -> int:
    """Load the profile, import every file, run every test.

    Returns:
        0 when every property held, 1 otherwise.

    """
    label = LABELS[profile_name]
    settings.load_profile(profile_name)
    chosen = None
    if profile_name == "audit":
        given_seed = os.environ.get("ORG_PROPERTY_SEED", "")
        chosen = (
            int(given_seed) if given_seed else random.SystemRandom().getrandbits(64)
        )
        print(f"{label}: seed {chosen} (reproduce: ORG_PROPERTY_SEED={chosen})")
    report = Report()
    for index, path in enumerate(files):
        started, printed = time.monotonic(), len(report.lines)
        run_file(path, index, settings.get_profile(profile_name), chosen, report)
        for line in report.lines[printed:]:
            print(line, flush=True)
        if chosen is not None:
            spent = time.monotonic() - started
            count = len(report.lines) - printed
            print(f"{path}: {count} test(s) in {spent:.1f}s", flush=True)
    for text in report.failures:
        print(f"\n{text}", file=sys.stderr)
    for text in report.refused:
        head, *rest = text.splitlines()
        findings.finding("", head, rest)
    if report.failures and chosen is not None:
        findings.finding(
            "",
            f"seed {chosen} — commit each falsifying example above"
            " as an @example row with its fix",
        )
    if report.failures or report.refused:
        return 1
    print(f"{label}: {len(report.lines)} test(s) in {len(files)} file(s) held")
    return 0


def main(argv: list[str]) -> int:
    """Read the profile name and the files.

    Returns:
        The run's exit status.

    """
    args = argv
    if len(args) < MIN_ARGS or args[0] not in LABELS:
        findings.finding("", "usage: python_properties.py gate|audit FILE...")
        return EXIT_USAGE
    label, spec = LABELS[args[0]], os.environ.get("ORG_PROPERTY_SHARD", "")
    files = [Path(a) for a in args[1:]]
    try:
        chosen = shard_of(files, spec)
    except ShardError as bad:
        findings.finding("", str(bad))
        return EXIT_USAGE
    if not chosen:
        print(f"{label}: shard {spec} holds none of the {len(files)} file(s), skipped")
        return 0
    if spec:
        print(f"{label}: shard {spec}, {len(chosen)} of {len(files)} file(s)")
    count = children(spec, files, workers())
    if count > 1:
        return fan_out(args[0], files, count)
    return run(args[0], chosen)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
