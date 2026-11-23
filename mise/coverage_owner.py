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

A claim also leaves cargo's `CACHEDIR.TAG` in the directory (#2070).
Cargo writes the tag only into a build directory it creates itself,
and `cargo clean` refuses one without it; the claim makes the
directory before cargo first builds there, so without the tag every
later clean of it was refused. A restored cache that holds such an
untagged directory is tagged at its next claim.

    coverage_owner.py clean

runs `cargo llvm-cov clean --workspace` in the claimed directory
(`CARGO_TARGET_DIR`) and is red when the clean did not happen.
cargo-llvm-cov reports a `cargo clean` that failed only as a warning
and exits 0, so the stale workspace executables the clean exists to
remove (#2061) stayed and joined the figure with zero hits: edtf's
release measured 72% on a suite that holds its 90% floor.
"""

import argparse
import fcntl
import os
import re
import subprocess
import sys
from pathlib import Path

import findings

OWNER = ".org-coverage-owner"
LOCK = ".org-coverage-owner.lock"
# The name coverage-run.sh gives a run of its own `mise run`.
PID_NAMED = re.compile(r"org-coverage-([0-9]+)")
# The tag cargo writes into a build directory it creates, byte for byte
# (cargo 1.97); `cargo clean` reads the signature line.
TAG = "CACHEDIR.TAG"
SIGNATURE = "Signature: 8a477f597d28d172789f06886806bc55"
TAG_TEXT = (
    f"{SIGNATURE}\n"
    "# This file is a cache directory tag created by cargo.\n"
    "# For information about cache directory tags see https://bford.info/cachedir/\n"
)
# How cargo-llvm-cov 0.9.1 reports a cargo it ran that failed: a warning,
# and its own exit status 0.
FAILED = "warning: process didn't exit successfully:"
CLEAN = ("cargo", "llvm-cov", "clean", "--workspace")


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


def tag(target: Path) -> None:
    """Leave cargo's cache directory tag in `target`, unless it holds one."""
    path = target / TAG
    if path.is_file():
        first = path.read_text(encoding="utf-8", errors="replace").partition("\n")[0]
        if first == SIGNATURE:
            return
    path.write_text(TAG_TEXT, encoding="utf-8")


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
        tag(target)
        owner.write_text(f"{run}\n", encoding="utf-8")
    return None


def clean() -> int:
    """Run the workspace clean, red when cargo refused or failed it.

    Returns:
        0 when the clean ran; else 1, or cargo-llvm-cov's own status.

    """
    done = subprocess.run(CLEAN, stderr=subprocess.PIPE, text=True, check=False)
    sys.stderr.write(done.stderr)
    if done.returncode != 0:
        return done.returncode
    refused = [ln for ln in done.stderr.splitlines() if ln.startswith(FAILED)]
    if refused:
        target = os.environ.get("CARGO_TARGET_DIR", "")
        findings.finding(
            "",
            f"the workspace clean of {target or 'the build directory'} did not"
            " happen — its old executables would join the figure with zero"
            " hits (#2061): cargo-llvm-cov reported the failure above as a"
            " warning",
        )
        return 1
    return 0


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
    sub.add_parser("clean", help="clean the workspace out of CARGO_TARGET_DIR")
    return parser.parse_args(argv, namespace=Args())


def main(argv: list[str] | None = None) -> int:
    """Claim, or refuse naming the run that holds the directory; or clean.

    Returns:
        0 when the directory is this run's, 1 when another live run's;
        for `clean`, its status.

    """
    args = parse(argv)
    if args.command == "clean":
        return clean()
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
