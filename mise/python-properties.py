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
the path too, so `from org_hypothesis import given` (the typed `given`
the belt delivers, #1604) resolves in any repository. It must hold at
least one
Hypothesis test: a property file with no property is a check that looks
like success. The output is the tests in file and name order and their
outcome, then every failure's traceback — Hypothesis prints the shrunk
falsifying example in it — and no timings, so a passing gate run is
byte-identical to the next.

Usage:
  python-properties.py gate|audit FILE...

Exits 0 when every property held, 1 when one failed or a file could not
be run, 2 on a usage error.
"""

import importlib.util
import os
import random
import sys
import traceback
import unittest
from pathlib import Path

from hypothesis import HealthCheck, seed, settings

GATE_EXAMPLES = 200
AUDIT_EXAMPLES = 10_000
BOUNDS = ("max_examples", "derandomize", "database", "deadline")
EXIT_USAGE = 2
# A profile name and at least one file.
MIN_ARGS = 2
# The belt: org_hypothesis.py sits beside this runner.
BELT = Path(__file__).resolve().parent
LABELS = {"gate": "test:python-properties", "audit": "audit:python-properties"}

settings.register_profile(
    "gate",
    derandomize=True,
    max_examples=GATE_EXAMPLES,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "audit",
    derandomize=False,
    max_examples=AUDIT_EXAMPLES,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def load(path: Path, index: int) -> object:
    """Import one property file from its own directory.

    Returns:
        The module.

    """
    for directory in (str(BELT), str(path.resolve().parent)):
        if directory not in sys.path:
            sys.path.insert(0, directory)
    name = f"prop_{index}_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
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
    return sorted(out, key=lambda t: t.id())


def method(test: unittest.TestCase) -> object:
    """Find the function a test case runs.

    Returns:
        The class attribute behind the test's method name.

    """
    name = test._testMethodName  # ruff: ignore[private-member-access] - unittest names the method only here
    return getattr(type(test), name, None)


def is_property(func: object) -> bool:
    """Say whether a test function is a Hypothesis test.

    Returns:
        True for a function `@given` wrapped.

    """
    return bool(getattr(func, "is_hypothesis_test", False))


def own_bounds(func: object, profile: settings) -> list[str]:
    """Name the profile bounds a test restates with its own `@settings`.

    Returns:
        `field=value` for each bound that differs from the profile.

    """
    mine = getattr(func, "_hypothesis_internal_use_settings", None)
    if mine is None:
        return []
    return [
        f"{field}={getattr(mine, field)!r}"
        for field in BOUNDS
        if getattr(mine, field) != getattr(profile, field)
    ]


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
        if chosen is not None and is_property(func):
            name = test._testMethodName  # ruff: ignore[private-member-access] - unittest names the method only here
            setattr(type(test), name, seed(chosen)(func))
        status, text = outcome(test)
        report.lines.append(f"{path}: {test.id()} ... {status}")
        if text:
            report.failures.append(f"{path}: {test.id()}\n{text}")


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
        run_file(path, index, settings.default, chosen, report)
    for line in report.lines:
        print(line)
    for text in report.failures:
        print(f"\n{text}", file=sys.stderr)
    for text in report.refused:
        print(f"{label}: {text}", file=sys.stderr)
    if report.failures and chosen is not None:
        print(
            f"{label}: seed {chosen} — commit each falsifying example above"
            " as an @example row with its fix",
            file=sys.stderr,
        )
    if report.failures or report.refused:
        return 1
    print(f"{label}: {len(report.lines)} test(s) in {len(files)} file(s) held")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Read the profile name and the files.

    Returns:
        The run's exit status.

    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) < MIN_ARGS or args[0] not in LABELS:
        print("usage: python-properties.py gate|audit FILE...", file=sys.stderr)
        return EXIT_USAGE
    return run(args[0], [Path(a) for a in args[1:]])


if __name__ == "__main__":
    sys.exit(main())
