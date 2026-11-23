#!/usr/bin/env python3
"""The body of lint:pg-clippy, run by its task (#1911).

Usage:
  pg_clippy.py

The task keeps its guard (a tracked Cargo.toml), the call, and its skip
message; everything after the guard is here, so it comes under the
coverage and mutation legs (CONTRIBUTING, "A helper's language").
Behaviour, messages and exit statuses are the body's own, unchanged.

The crate this lints is the one the gate never compiled (#813): the
task's comment carries that history. The plumbing it shares with
lint:rust and fix:rust — the compile surface, the clippy ladder, the
pgrx provisioning and the per-major subshell — is mise/rust_task.py's,
which calls mise/clippy-ladder.sh and mise/pgrx-env.sh rather than
restating them, so all three tasks build an extension the same way.

It runs once per workspace the tree tracks (#2004), the roots named by
mise/rust_workspaces.py, so an extension outside the root workspace is
linted like one inside it.

Run in the repository's root, with cargo, rustup and the belt on PATH;
stdlib only.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import TYPE_CHECKING, NamedTuple

import findings
import rust_task
import rust_workspaces

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

USAGE = "usage: pg_clippy.py"


def derive(cargo: str, where: Sequence[str]) -> tuple[int, list[list[str]], str]:
    """Derive one workspace's extensions, each at its highest major.

    One step per derivation: a failed one is never read as a workspace
    with no extension, which would skip clean having checked nothing.
    Each extension's major, and the majors to register, come from
    pgrx-env.sh, written once for every task that builds one (#1795).

    Returns:
        The status, `[spec, major]` per extension, and the majors.

    """
    status = rust_task.run([cargo, "fetch", "--locked", *where])
    if status != 0:
        return status, [], ""
    status, found = rust_task.surface(cargo, where)
    if status != 0:
        return status, [], ""
    return rust_task.pgrx_pairs(found)


class Linted(NamedTuple):
    """What one workspace's pass did."""

    status: int
    extensions: int
    # The count of named contradictions the ladder left out, once read.
    contradictions: str


def workspace(cargo: str, root: str) -> Linted:
    """Lint each pgrx extension of one workspace at its highest major.

    `root` is the workspace's root manifest, handed to every cargo
    command that reads it as `--manifest-path`.

    Returns:
        The workspace's status, and how many extensions it linted.

    """
    where = ("--manifest-path", root)
    status, pairs, majors = derive(cargo, where)
    if status != 0:
        return Linted(status, 0, "")
    if not pairs:
        return Linted(0, 0, "")
    # clippy is a rustup COMPONENT of the repo's own pin, asserted with a
    # remedy and never installed mid-run — the lint:rust reasoning
    # verbatim.
    if not rust_task.has(rust_task.installed_components(), "clippy"):
        findings.finding(
            "",
            "clippy component missing — declare it with the rust pin in mise.toml:",
            [rust_task.PIN],
        )
        return Linted(1, 0, "")
    os.environ["CLIPPY_CONF_DIR"] = str(rust_task.belt())
    # The org's one ladder: a pgrx crate is linted at exactly what every
    # crate is (#1995).
    status, flags, count = rust_task.ladder()
    if status != 0:
        return Linted(status, 0, "")
    # The SDK, and every major's ICU headers and Postgres pin checked
    # (#1774, #1940).
    status, prepared = rust_task.register(majors)
    if status != 0:
        return Linted(status, 0, "")
    return lint_each(cargo, root, pairs, (flags, count), prepared)


def lint_each(
    cargo: str,
    root: str,
    pairs: list[list[str]],
    ladder: tuple[list[str], str],
    prepared: Mapping[str, str],
) -> Linted:
    """Run clippy on each extension at its own major, then say so.

    `ladder` is the flags that ride clippy's `--` and the count of
    named contradictions they leave out.

    Returns:
        The first red clippy's status, else 0, and how many it linted.

    """
    flags, count = ladder
    where = ("--manifest-path", root)
    linted = ""
    for spec, major in pairs:
        linted += f" {rust_task.name_of(spec)}(pg{major})"
        # `--no-deps` is load-bearing: without it clippy also lints the
        # workspace's other crates under --no-default-features, where
        # their lint expectations do not fire. In a subshell holding its
        # own major's ICU headers and Postgres, so none reaches the next.
        status = rust_task.at_major(
            major,
            [
                cargo,
                "clippy",
                "-p",
                spec,
                *where,
                "--no-deps",
                "--all-targets",
                "--locked",
                "--keep-going",
                "--no-default-features",
                "--features",
                f"pg{major}",
                "--",
                *flags,
            ],
            prepared,
        )
        if status != 0:
            return Linted(status, 0, "")
    print(f"lint:pg-clippy: {root}: {len(pairs)} extension(s) linted:{linted}")
    return Linted(0, len(pairs), count)


def main(argv: list[str]) -> int:
    """Run the task's body.

    Returns:
        The task's status; 2 on a usage error.

    """
    if argv:
        findings.finding("", USAGE)
        return 2
    cargo = shutil.which("cargo")
    if cargo is None:
        findings.finding("", rust_task.NO_CARGO)
        return 1
    trees = rust_workspaces.resolved()
    if trees is None:
        return 1
    # EVERY WORKSPACE IN THE TREE (#2004), each run even when an earlier
    # one is red; the task exits with the last red status.
    status = 0
    linted = 0
    count = ""
    for root in trees:
        done = workspace(cargo, root)
        status = done.status or status
        linted += done.extensions
        count = done.contradictions or count
    if status != 0:
        return status
    if not linted:
        print("lint:pg-clippy: no pgrx extension tracked, skipped")
        return 0
    print(
        "lint:pg-clippy: clean at every group"
        f" (restriction minus {count} named contradictions)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
