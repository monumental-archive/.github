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
pgrx provisioning and the per-major subshell — is mise/rust_lint.py's,
which calls mise/clippy-ladder.sh and mise/pgrx-env.sh rather than
restating them, so all three tasks build an extension the same way.

Run in the repository's root, with cargo, rustup and the belt on PATH;
stdlib only.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import TYPE_CHECKING

import findings
import rust_lint

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
    status = rust_lint.run([cargo, "fetch", "--locked", *where])
    if status != 0:
        return status, [], ""
    status, found = rust_lint.surface(cargo, where)
    if status != 0:
        return status, [], ""
    return rust_lint.pgrx_pairs(found)


def workspace(cargo: str, where: Sequence[str] = ()) -> int:
    """Lint each pgrx extension of one workspace at its highest major.

    `where` is cargo's `--manifest-path` naming the workspace, handed to
    every cargo command that reads it; empty, cargo finds the one at the
    working directory, which is the only workspace this task lints.

    Returns:
        The task's status.

    """
    status, pairs, majors = derive(cargo, where)
    if status != 0:
        return status
    if not pairs:
        print("lint:pg-clippy: no pgrx extension tracked, skipped")
        return 0
    # clippy is a rustup COMPONENT of the repo's own pin, asserted with a
    # remedy and never installed mid-run — the lint:rust reasoning
    # verbatim.
    if not rust_lint.has(rust_lint.installed_components(), "clippy"):
        findings.finding(
            "",
            "clippy component missing — declare it with the rust pin in mise.toml:",
            [rust_lint.PIN],
        )
        return 1
    os.environ["CLIPPY_CONF_DIR"] = str(rust_lint.BELT)
    # The org's one ladder: a pgrx crate is linted at exactly what every
    # crate is (#1995).
    status, flags, count = rust_lint.ladder()
    if status != 0:
        return status
    # The SDK, and every major's ICU headers and Postgres pin checked
    # (#1774, #1940).
    status, prepared = rust_lint.register(majors)
    if status != 0:
        return status
    return lint_each(cargo, where, pairs, (flags, count), prepared)


def lint_each(
    cargo: str,
    where: Sequence[str],
    pairs: list[list[str]],
    ladder: tuple[list[str], str],
    prepared: Mapping[str, str],
) -> int:
    """Run clippy on each extension at its own major, then say so.

    `ladder` is the flags that ride clippy's `--` and the count of
    named contradictions they leave out.

    Returns:
        The first red clippy's status, else 0.

    """
    flags, count = ladder
    linted = ""
    for spec, major in pairs:
        linted += f" {rust_lint.name_of(spec)}(pg{major})"
        # `--no-deps` is load-bearing: without it clippy also lints the
        # workspace's other crates under --no-default-features, where
        # their lint expectations do not fire. In a subshell holding its
        # own major's ICU headers and Postgres, so none reaches the next.
        status = rust_lint.at_major(
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
            return status
    print(f"lint:pg-clippy: {len(pairs)} extension(s) linted:{linted}")
    print(
        "lint:pg-clippy: clean at every group"
        f" (restriction minus {count} named contradictions)"
    )
    return 0


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
        findings.finding("", rust_lint.NO_CARGO)
        return 1
    return workspace(cargo)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
