#!/usr/bin/env python3
"""A repository's proptest properties, under the belt's seed (#1930).

The Python half of `lint:rust-properties` (the gate) and
`audit:rust-properties` (the nightly search), called by
`rust-properties.sh`, and the one answer to "is proptest locked" that
`audit_legs.py` asks for the scheduled job: the three read lock files
the same way, so they cannot disagree.

#1332's and #1549's split, for Rust: a fixed seed in the gate, a drawn
one on the scheduled run. Unlike Hypothesis and fast-check, proptest
reads its seed from the environment (`PROPTEST_RNG_SEED`), so the belt
reaches it where the tests already run, the repository's own `test`
task: the belt's `[env]` sets the variable from `ORG_PROPERTY_SEED`,
`1` when that is unset, which is every gate run. The variable arrived
in proptest 1.7.0; an older proptest ignores it without a word, so a
gate holding it would draw a fresh seed on every run. That is the lint.
The measurements are docs/tooling-verdicts.md's proptest entry.

The lock files read are every tracked `Cargo.lock` outside a vendor/
directory, with tomllib (through `decoded`): a `[[package]]` named
`proptest` is a lock of it.

Subcommands, run from the repository root:

  locked LOCKFILE...    prints `<file>: proptest <version>` for each
                        proptest each lock file holds, one per line
  lint LOCKFILE...      prints each proptest below 1.7.0, naming the
                        file and the version; exits 1 when one is
  seed                  prints ORG_PROPERTY_SEED when it is set, else
                        64 random bits; a value proptest would not
                        read as a seed is a usage error

Exits 0 when clean, 1 on a finding or an unreadable lock file, 2 on a
usage error.
"""

import os
import posixpath
import random
import re
import sys
from pathlib import Path

import decoded
import findings

EXIT_USAGE = 2
NAME = "proptest"
# The first release that reads PROPTEST_RNG_SEED: absent from
# src/test_runner/config.rs at v1.4.0, v1.5.0 and v1.6.0, present at
# v1.7.0 (read at each release tag, 2026-11-21).
MINIMUM = (1, 7, 0)
LOCKFILE = "Cargo.lock"
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")
SEED = re.compile(r"^[0-9]+$")
SEED_BITS = 64


def vendored(path: str) -> bool:
    """Say whether a tracked path is under a vendor/ directory.

    Returns:
        True for `vendor/…` and `…/vendor/…`.

    """
    return "vendor" in path.split("/")[:-1]


def lockfiles(tracked: list[str]) -> list[str]:
    """Pick the lock files read from a tracked-file list.

    Returns:
        Every `Cargo.lock` outside a vendor/ directory, in order.

    """
    return [p for p in tracked if posixpath.basename(p) == LOCKFILE and not vendored(p)]


def versions(path: Path) -> list[str]:
    """Read the proptest versions one lock file holds.

    Returns:
        Each locked proptest's version, in file order. A file that is
        not TOML, or a package entry of the wrong shape, raises
        decoded's UndecodableError, naming the field.

    """
    doc = decoded.load_toml(path.read_bytes())
    found: list[str] = []
    packages = decoded.as_array(doc.get("package", []), "[[package]]")
    for i, entry in enumerate(packages, 1):
        package = decoded.as_object(entry, f"[[package]] {i}")
        if package.get("name") == NAME:
            found.append(decoded.as_str(package.get("version"), f"{NAME} version"))
    return found


def locked(root: Path, files: list[str]) -> list[tuple[str, str]]:
    """Read every proptest the lock files hold.

    Returns:
        (file, version) for each, vendored files left out.

    Raises:
        UndecodableError: a file will not decode; the text names it.

    """
    out: list[tuple[str, str]] = []
    for name in files:
        if vendored(name):
            continue
        try:
            out += [(name, v) for v in versions(root / name)]
        except decoded.UndecodableError as err:
            msg = f"{name}: {err}"
            raise decoded.UndecodableError(msg) from None
    return out


def locks_proptest(root: Path, tracked: list[str]) -> bool:
    """Answer audit_legs.py: does a tracked lock file lock proptest.

    A lock file that will not decode answers True: the job then runs,
    and its task names the defect, as an unparsable floor is left to
    the job's task.

    Returns:
        True when one does, or one cannot be read.

    """
    try:
        return bool(locked(root, lockfiles(tracked)))
    except decoded.UndecodableError, OSError:
        return True


def below_minimum(version: str) -> bool:
    """Say whether a proptest version predates PROPTEST_RNG_SEED.

    A pre-release of 1.7.0 is below it; a version that is no semver is
    below it too, since nothing says it reads the seed.

    Returns:
        True when it is below 1.7.0.

    """
    match = VERSION.match(version)
    if match is None:
        return True
    core = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return core < MINIMUM or (core == MINIMUM and match.group(4) is not None)


def seed(given: str) -> int | None:
    """Choose the run's seed.

    Returns:
        The given seed, or 64 random bits where none is given; None
        where the given one is not a seed proptest reads (a u64).

    """
    if not given:
        return random.SystemRandom().getrandbits(SEED_BITS)
    if not SEED.match(given) or int(given) >= 1 << SEED_BITS:
        return None
    return int(given)


def main(argv: list[str]) -> int:
    """Run one subcommand.

    Returns:
        The exit status.

    """
    if not argv or argv[0] not in {"locked", "lint", "seed"}:
        findings.finding("", "expected locked, lint or seed")
        return EXIT_USAGE
    command, files = argv[0], argv[1:]
    if command == "seed":
        given = os.environ.get("ORG_PROPERTY_SEED", "")
        chosen = seed(given)
        if chosen is None or files:
            findings.finding(
                "",
                f"ORG_PROPERTY_SEED must be an integer from 0 to 2^64-1, got '{given}'",
            )
            return EXIT_USAGE
        print(chosen)
        return 0
    try:
        pins = locked(Path.cwd(), files)
    except (decoded.UndecodableError, OSError) as err:
        findings.finding("", str(err))
        return 1
    if command == "locked":
        for name, version in pins:
            print(f"{name}: {NAME} {version}")
        return 0
    low = [(n, v) for n, v in pins if below_minimum(v)]
    for name, version in low:
        print(f"{name}: {NAME} {version} is below 1.7.0")
    return 1 if low else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
