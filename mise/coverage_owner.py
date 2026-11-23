#!/usr/bin/env python3
"""Claim a coverage build directory for one measurement run (#2062).

A coverage build directory belongs to the checkout; a measurement run
belongs to a process. Two `mise run`s measuring at once in one checkout
get separate run directories (`org-coverage-<pid>`, coverage-run.sh)
but the same `CARGO_TARGET_DIR` (rust-coverage-env.sh; each pgrx
major's in pgrx-test.sh). Each run's first call cleans that directory,
so without an owner one run deletes what the other is measuring and
both read a figure from a directory neither owns.

    coverage_owner.py claim <target-dir> <run-dir>

takes an exclusive `fcntl.flock` on `<target-dir>/.org-coverage-owner.lock`
and reads `<target-dir>/.org-coverage-owner`. Absent, naming this run,
or naming a run that is not live: it writes this run's directory into
the file and exits 0. Naming another live run: it exits 1, naming that
run, and writes nothing. The caller claims before it cleans, so the
refusal ends the call before anything is cleaned or built.

Liveness is the sweep's rule (coverage-run.sh), here in one place: a
run directory named `org-coverage-<pid>` is live while it exists and
that pid is a running process; any other name (the gate's
`ORG_GATE_COVERAGE_RUN`, one run per job machine) is live while it
exists. A claim restored inside a cached `target` therefore names this
job's own directory or an absent one: never a refusal in CI.

There is no release step. A claim lapses when its run ends, because
liveness is read at the next claim; nothing has to run "finally",
which mise cannot do. Nor does any process hold the lock for the run:
a run spans processes (`test`, then `coverage:check`), and the lock
covers only the read and the write of one claim.
"""

import argparse
import fcntl
import os
import re
import sys
from pathlib import Path

import findings

OWNER = ".org-coverage-owner"
LOCK = ".org-coverage-owner.lock"
# The name coverage-run.sh gives a run of its own `mise run`.
PID_NAMED = re.compile(r"org-coverage-([0-9]+)")


def live(run: str) -> bool:
    """Say whether a run directory is a run in progress.

    Returns:
        False when the directory is gone, or is `org-coverage-<pid>`
        and no process holds that pid; True otherwise. A pid another
        user holds is live, as `ps -p` reads it in the sweep.

    """
    path = Path(run)
    if not path.is_dir():
        return False
    named = PID_NAMED.fullmatch(path.name)
    if named is None:
        return True
    try:
        os.kill(int(named.group(1)), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def claim(target: Path, run: str) -> str | None:
    """Claim `target` for `run`, unless another live run holds it.

    Returns:
        None when the claim is this run's; else the live run holding it.

    """
    target.mkdir(parents=True, exist_ok=True)
    owner = target / OWNER
    with (target / LOCK).open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        held = owner.read_text(encoding="utf-8").strip() if owner.exists() else ""
        if held and held != run and live(held):
            return held
        owner.write_text(f"{run}\n", encoding="utf-8")
    return None


class Args(argparse.Namespace):
    """The arguments, each typed as parsed."""

    command: str
    target: Path
    run: str


def parse(argv: list[str] | None) -> Args:
    """Read the command line.

    Returns:
        The parsed arguments.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("claim", help="claim a build directory for a run")
    one.add_argument("target", type=Path, help="the build directory measured into")
    one.add_argument("run", help="this run's directory (ORG_COVERAGE_RUN)")
    return parser.parse_args(argv, namespace=Args())


def main(argv: list[str] | None = None) -> int:
    """Claim, or refuse naming the run that holds the directory.

    Returns:
        0 when the directory is this run's, 1 when another live run's.

    """
    args = parse(argv)
    try:
        held = claim(args.target, args.run)
    except OSError as err:
        findings.finding("", f"could not claim {args.target}: {err}")
        return 1
    if held is not None:
        findings.finding(
            "",
            f"{args.target} is being measured by another run ({held});"
            " wait for it to finish",
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
