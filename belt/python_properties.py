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

Before anything runs, the files are collected once, in this process
and with no workers, and the plugin refuses, by name, a file that holds
no Hypothesis test (a property file with no property is a check that
looks like success), and a test that restates any of the profile's four
bounds (`max_examples`, `derandomize`, `database`, `deadline`) with its
own `@settings`: the belt owns them. The refusals are read here, not
raised in the hook: under pytest-xdist the workers collect, and a
worker's error would reach the log as a crash rather than the names.
The audit also prints pytest's ten slowest tests, so its bound is
measured from its own log (#1631).

Every run takes the belt's delivered pytest configuration with `-c`
(`mise/pytest.toml`, #2177), its root the working directory.

ORG_SHARD=k/n (1 <= k <= n, the one variable every belt adapter reads,
belt/shard.py, #2160) takes one shard of the files (#1631): the files
sorted, every n-th starting at the k-th. Unset, or 1/1, is every file,
as given. A value that is not k/n is refused by name; a shard that
holds no file says so and exits 0. A shard reports the files it chose
from and the ones it kept, and the gate holds the union of every
shard's to the whole list. The audit runs n legs with one seed.

Unsharded, the files are spread over pytest-xdist's workers (#1741):
ORG_TEST_WORKERS sets their count, at most eight by default, and 1 runs
every file in this one process. A named shard runs in this one
process: the shards already spread the files over runners.

Usage:
  belt python-properties gate|audit FILE...

Exits 0 when every property held, 1 when one failed or a file could not
be run, 2 on a usage error.
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import findings, shard
from belt.tree import MISE, ROOT

if TYPE_CHECKING:
    from collections.abc import Callable

EXIT_USAGE = 2
# A profile name and at least one file.
MIN_ARGS = 2
# The default worker count's ceiling, and the variable that sets it.
MAX_WORKERS = 8
WORKERS = "ORG_TEST_WORKERS"
LABELS = {"gate": "test:python-properties", "audit": "audit:python-properties"}
SEED = "ORG_PROPERTY_SEED"
# Where Hypothesis keeps what it writes, read by each process it runs in.
STORAGE = "HYPOTHESIS_STORAGE_DIRECTORY"
# The pytest plugin every run loads: the profiles and the refusals.
PLUGIN = "belt.property_profiles"
# The belt's one pytest configuration, delivered (#2177).
CONFIG = MISE / "pytest.toml"


def refused() -> list[str]:
    """Read what the last collection's plugin refused.

    The plugin is read from the modules pytest imported, never imported
    here: one imported before pytest starts is a module pytest's
    assertion rewriting missed, which `filterwarnings = error` reds.

    Returns:
        The plugin's refusals, one line each; none when no collection
        loaded it.

    """
    plugin = sys.modules.get(PLUGIN)
    lines: object = getattr(plugin, "REFUSED", [])
    return [str(line) for line in lines] if isinstance(lines, list) else []


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
        *("-c", str(CONFIG), "--rootdir", str(Path.cwd())),
        *("-p", PLUGIN),
        f"--hypothesis-profile={profile}",
        *("-o", f"pythonpath={ROOT}"),
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
    # Hypothesis's pytest plugin writes a patch of every failing example
    # under its storage directory, `.hypothesis` in the working tree by
    # default: outside it, so the run leaves the tree as it found it.
    os.environ[STORAGE] = tempfile.mkdtemp(prefix="org-hypothesis-")
    # And no bytecode beside the files: pytest's rewritten modules are
    # cached in `__pycache__` unless Python writes none, here and in
    # each worker it starts.
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    collected = pytest_main([
        *arguments(profile, files, None, 1),
        "--collect-only",
        "-q",
    ])
    said = refused()
    for line in said:
        findings.finding("", line)
    if said:
        return 1
    if collected != 0:
        findings.finding("", f"pytest could not collect the files (exit {collected})")
        return 1
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
    # A named shard is one runner's share: it runs in this one process.
    # Unsharded (or 1/1), the run fans out over the machine's workers.
    environ = dict(os.environ)
    if named is not None and named != (1, 1):
        environ[WORKERS] = "1"
    return run(argv[0], chosen, environ)
