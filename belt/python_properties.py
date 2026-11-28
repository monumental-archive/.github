"""Run a repository's Hypothesis properties under the belt's profile.

The body of `test:python-properties` (the gate) and
`audit:python-properties` (the Monday audit), run by
`python-properties.sh` on the pytest tool venv's interpreter (#1332,
#2177). The two profiles live here and nowhere else, so no repository
sets its own bounds:

- `gate`: `derandomize=True`, so each test's examples are derived from
  the test itself and two runs execute the same examples; a bounded
  `max_examples`, no example database, no deadline.
- `audit`: a random seed, printed before anything runs and taken from
  ORG_PROPERTY_SEED when set, so a failure reproduces exactly; a larger
  `max_examples`; no example database, no deadline.

Neither profile has a deadline or the `too_slow` health check: both
measure wall time, and wall time on a shared runner is not a property of
the code. A determinism that depends on the machine's load is not one.

The files run under pytest, which collects Hypothesis tests natively,
with this module as a plugin (`-p belt.python_properties`), so every
xdist worker registers the same two profiles before any file is
imported; Hypothesis's own pytest options load the profile and, in the
audit, the seed. The tree the belt package sits in is put on each
process's path (pytest's `pythonpath`), so `from belt.typed_hypothesis
import given` (the typed `given` the belt delivers, #1604) resolves in
any repository.

At collection the plugin refuses, by name, a file that holds no
Hypothesis test (a property file with no property is a check that looks
like success), and a test that restates any of the profile's four bounds
(`max_examples`, `derandomize`, `database`, `deadline`) with its own
`@settings`: the belt owns them. The audit also prints pytest's ten
slowest tests, so its bound is measured from its own log (#1631).

ORG_SHARD=k/n (1 <= k <= n, the one variable every belt adapter reads,
belt/shard.py, #2160) takes one shard of the files (#1631): the files
sorted, every n-th starting at the k-th. Unset, or 1/1, is every file,
as given. A value that is not k/n is refused by name; a shard that
holds no file says so and exits 0. A shard reports the files it chose
from and the ones it kept, and the gate holds the union of every
shard's to the whole list. The audit runs n legs with one seed.

The files are spread over pytest-xdist's workers: ORG_TEST_WORKERS
sets their count, at most eight by default, and 1 runs every file in
this one process.

Usage:
  belt python-properties gate|audit FILE...

Exits 0 when every property held, 1 when one failed or a file could not
be run, 2 on a usage error.
"""

from __future__ import annotations

import os
import random
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, settings

from belt import findings, shard
from belt.tree import ROOT

if TYPE_CHECKING:
    from collections.abc import Callable

GATE_EXAMPLES = 200
AUDIT_EXAMPLES = 10_000
BOUNDS = ("max_examples", "derandomize", "database", "deadline")
EXIT_USAGE = 2
# A profile name and at least one file.
MIN_ARGS = 2
# The default worker count's ceiling, and the variable that sets it.
MAX_WORKERS = 8
WORKERS = "ORG_TEST_WORKERS"
LABELS = {"gate": "test:python-properties", "audit": "audit:python-properties"}
SEED = "ORG_PROPERTY_SEED"
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


def is_property(func: object) -> bool:
    """Say whether a test function is a Hypothesis test.

    Returns:
        True for a function `@given` wrapped.

    """
    flag: object = getattr(func, "is_hypothesis_test", False)
    return bool(flag)


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


def refusals(items: list[tuple[str, str, object]], profile: settings) -> list[str]:
    """Judge what was collected: one line per refused file or test.

    `items` are (file, test id, the test's function) in collection order.

    Returns:
        A line per file with no Hypothesis test, then one per test that
        sets its own bounds.

    """
    out: list[str] = []
    seen: dict[str, bool] = {}
    for path, _, func in items:
        seen[path] = seen.get(path, False) or is_property(func)
    out += [
        f"{path}: holds no Hypothesis test (no @given)"
        for path, has in seen.items()
        if not has
    ]
    for _, nodeid, func in items:
        own = own_bounds(func, profile)
        if own:
            out.append(
                f"{nodeid} sets its own bounds ({', '.join(own)});"
                " the belt's profile owns them"
            )
    return out


def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Refuse a property file with no property, and a test's own bounds.

    Raises:
        pytest.UsageError: a refusal, every one named.

    """
    del session
    name: object = config.getoption("hypothesis_profile", None)
    profile = settings.get_profile(name if isinstance(name, str) else "default")
    found: list[tuple[str, str, object]] = []
    for item in items:
        func: object = getattr(item, "obj", None)
        found.append((str(item.path), item.nodeid, func))
    refused = refusals(found, profile)
    if refused:
        raise pytest.UsageError("\n".join(refused))


def workers(environ: dict[str, str]) -> int:
    """Count the workers the files are spread over.

    Returns:
        ORG_TEST_WORKERS when it is a positive number, else the cores,
        at most eight.

    """
    named = environ.get(WORKERS, "")
    if named.isdigit() and int(named) > 0:
        return int(named)
    return min(os.cpu_count() or 1, MAX_WORKERS)


def arguments(
    profile: str, files: list[Path], seed: int | None, count: int
) -> list[str]:
    """Build the pytest command line one run takes.

    Returns:
        The arguments: this plugin, the profile, the seed, the workers,
        the tree on the path, then the files.

    """
    args = [
        "-p",
        "belt.python_properties",
        "-p",
        "no:cacheprovider",
        f"--hypothesis-profile={profile}",
        "-o",
        f"pythonpath={ROOT}",
        "-q",
    ]
    if seed is not None:
        args += [f"--hypothesis-seed={seed}", "--durations=10"]
    if count > 1:
        args += ["-n", str(min(count, len(files)))]
    return [*args, *(str(f) for f in files)]


def run(
    profile: str,
    files: list[Path],
    environ: dict[str, str],
    pytest_main: Callable[[list[str]], int] = pytest.main,
) -> int:
    """Run the files under the profile.

    Returns:
        0 when every property held, 1 otherwise.

    """
    label = LABELS[profile]
    seed = None
    if profile == "audit":
        given = environ.get(SEED, "")
        seed = int(given) if given.isdigit() else random.SystemRandom().getrandbits(64)
        print(f"{label}: seed {seed} (reproduce: {SEED}={seed})", flush=True)
    status = pytest_main(arguments(profile, files, seed, workers(environ)))
    if status == 0:
        print(f"{label}: {len(files)} file(s) held")
        return 0
    if seed is not None:
        findings.finding(
            "",
            f"seed {seed} — commit each falsifying example above"
            " as an @example row with its fix",
        )
    else:
        findings.finding("", f"pytest exited {int(status)}; failures above")
    return 1


def main(argv: list[str]) -> int:
    """Read the profile name and the files, and run this shard's.

    Returns:
        The run's exit status.

    """
    if len(argv) < MIN_ARGS or argv[0] not in LABELS:
        findings.finding("", "usage: belt python-properties gate|audit FILE...")
        return EXIT_USAGE
    label = LABELS[argv[0]]
    files = [Path(a) for a in argv[1:]]
    try:
        named = shard.parse(os.environ.get(shard.VARIABLE, ""))
    except shard.ShardError as bad:
        findings.finding("", str(bad))
        return EXIT_USAGE
    chosen = files
    if named is not None and named != (1, 1):
        k, n = named
        chosen = sorted(files, key=str)[k - 1 :: n]
        shard.report(
            "python-properties",
            label,
            [str(f) for f in files],
            [str(f) for f in chosen],
        )
        if not chosen:
            held = f"holds none of the {len(files)} file(s), skipped"
            print(f"{label}: shard {k}/{n} {held}")
            return 0
        print(f"{label}: shard {k}/{n}, {len(chosen)} of {len(files)} file(s)")
    return run(argv[0], chosen, dict(os.environ))
