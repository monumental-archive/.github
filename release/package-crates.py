#!/usr/bin/env python3
"""Package a workspace's crates and keep exactly what crates.io receives.

The rust-crate class's Package step (#1384 moved it here from the
workflow's shell, refusals unchanged). Run from the caller's workspace
root, with EXCLUDE_FLAGS as the resolve step wrote it (` --exclude a
--exclude b`, or empty).

VERIFIED, THEN WITHOUT VERIFYING (#1273). `exclude` means only "this job
cannot compile it", so an excluded member is still packaged: the
verified `cargo package` leaves it out, and a second run, `--no-verify`
over every member, writes its .crate without compiling it (its compile
check is the class that owns it, pgrx-extension). That run rewrites the
verified crates too, so each must come out byte-identical, or the bytes
hashed and published would not be the bytes that were verified.
Measured on a fixture workspace, cargo 1.97.1: the second run
reproduces the first byte for byte. No --allow-dirty anywhere: omitting
the flag is what refuses a dirty tree.

THE SUBJECT SET IS CRATES.IO'S (#1310). `cargo package` does not read
the `publish` key, so it writes a crate for a `publish = false` member
too, and every crate left in target/package is hashed, planned,
uploaded and fetched back from static.crates.io by the verifier, which
fails on one the registry never got. So a crate
release/crates-io-members.sh does not name is removed here, after it
was packaged, before anything hashes it; and a crate it names that
cargo did not write fails by name. That helper is the one the
publisher reads, so the subjects and the uploads are one set.

Exit status: 0 packaged, 1 refused, else cargo's own failing status.
"""

import hashlib
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = Path("target") / "package"


class CargoFailedError(Exception):
    """A cargo run failed; its own output already said why."""

    def __init__(self, status: int) -> None:
        """Keep cargo's exit status, which the step exits with."""
        super().__init__(status)
        self.status = status


def cargo(*args: str) -> None:
    """Run cargo, its output straight to the step's log.

    Raises:
        CargoFailedError: cargo exited non-zero.

    """
    done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        ["cargo", *args],  # ruff: ignore[start-process-with-partial-path] - the job's pinned cargo, on PATH
        check=False,
    )
    if done.returncode != 0:
        raise CargoFailedError(done.returncode)


def digests() -> dict[Path, str]:
    """Hash every crate cargo wrote.

    Returns:
        Each .crate in target/package to its sha256, in name order.

    """
    return {
        c: hashlib.sha256(c.read_bytes()).hexdigest()
        for c in sorted(PACKAGE.glob("*.crate"))
    }


def changed_unverified(verified: dict[Path, str]) -> list[str]:
    """Name each verified crate the --no-verify run rewrote differently.

    Returns:
        One error line per crate whose bytes moved.

    """
    lines = []
    for crate, was in verified.items():
        now = hashlib.sha256(crate.read_bytes()).hexdigest()
        if now != was:
            lines.append(
                f"::error::{crate.name} changed when packaged again"
                f" without verifying: {was} verified, {now} now"
            )
    return lines


def published() -> list[tuple[str, str]]:
    """Read the members crates.io receives, from the publisher's helper.

    Returns:
        (name, version) per member, in the helper's order.

    Raises:
        CargoFailedError: the helper failed; it said why.

    """
    done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [str(HERE / "crates-io-members.sh")],
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise CargoFailedError(done.returncode)
    out = []
    for line in done.stdout.splitlines():
        name, _, version = line.strip("\t").partition("\t")
        if name:
            out.append((name, version.strip("\t")))
    return out


def package(flags: str) -> list[str]:
    """Package, compare, and cut the set to what crates.io receives.

    Returns:
        The refusal lines; empty when the set is packaged and cut.

    """
    cargo("package", "--workspace", "--locked", *flags.split())
    if flags.strip():
        verified = digests()
        if not verified:
            return ["::error::cargo package wrote no crate to verify"]
        cargo("package", "--workspace", "--locked", "--no-verify")
        changed = changed_unverified(verified)
        if changed:
            return changed
    ships = set()
    missing = []
    for name, version in published():
        crate = f"{name}-{version}.crate"
        ships.add(crate)
        if not (PACKAGE / crate).is_file():
            missing.append(
                f"::error::{name} {version} publishes to crates.io,"
                f" but cargo package wrote no {crate}"
            )
    if missing:
        return missing
    for crate in sorted(PACKAGE.glob("*.crate")):
        if crate.name not in ships:
            crate.unlink()
            print(
                f"{crate.name} is not published to crates.io (publish key);"
                " not a release subject",
                flush=True,
            )
    return []


def main() -> int:
    """Run the Package step.

    Returns:
        The exit status: 0 packaged, 1 refused, else cargo's.

    """
    try:
        refused = package(os.environ.get("EXCLUDE_FLAGS", ""))
    except CargoFailedError as e:
        return e.status
    for line in refused:
        print(line, flush=True)
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
