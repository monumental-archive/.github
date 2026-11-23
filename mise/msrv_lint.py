#!/usr/bin/env python3
"""The body of lint:msrv, run by its task (#1911).

Usage:
  msrv_lint.py

The task keeps its guard (a tracked Cargo.toml), the call, and its skip
message; everything after the guard is here, so it comes under the
coverage and mutation legs (CONTRIBUTING, "A helper's language").
Behaviour, messages and exit statuses are the body's own, unchanged.

The plan is mise/msrv_plan.py's and the compile surface
mise/gate_surface.py's, each run as the body ran it; the steps every
Rust task's helper shares (the pgrx derivation and provisioning among
them) are mise/rust_task.py's. The task's reasoning for the rule
rides beside the task in mise/config.toml; the reasoning for each step
rides beside the step below.

`workspace` takes the workspace's manifest as a parameter: None is
cargo's own discovery from the working directory, the root, which is
the only workspace this checks today.

Run in the repository's root, with cargo, mise and the belt on PATH;
stdlib only.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import findings
import rust_task
from rust_task import belt, features, output, python, run

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

TASK = "lint:msrv"
NO_CARGO = "Cargo.toml present but cargo missing — pin rust in mise.toml"
NO_MISE = "mise missing — the toolchain pins this reads are mise's"
UNREADABLE = "unreadable plan record from msrv_plan.py"
# Fields of an msrv_plan.py record: kind, toolchain or note, own flags,
# specs.
FIELDS = 4


def say(line: str) -> None:
    """Print one of the task's lines, before any command's own output."""
    print(f"{TASK}: {line}", flush=True)


def records(plan: str) -> list[list[str]]:
    """Split msrv_plan.py's records into their four fields.

    Returns:
        Each record's fields, padded; a line with no kind is skipped,
        as the body's `read` loop skipped it.

    """
    rows = [line.split("\t", FIELDS - 1) for line in plan.splitlines()]
    return [[*row, *[""] * (FIELDS - len(row))] for row in rows if row[0]]


def check(
    toolchain: str,
    own: str,
    specs: str,
    manifest: Sequence[str],
    prepared: Mapping[str, str],
) -> int:
    """Compile one group at the toolchain it names, at its pgrx major.

    A crate with feature flags of its own gets them INSTEAD of the
    gate's, never as well: they contradict each other. A pgrx group's
    own flags are `--no-default-features --features pgNN`, so their
    digits are its one major, compiled in a subshell holding that
    major's ICU headers and Postgres (#1774, #1940); `default` has none
    and adds none. A red compile is at the DERIVED number, which the
    plan has already held each promise to, so msrv_plan.py says what
    that means (#1850) and the remedy never reads as "raise the
    promise"; the status stays cargo's own.

    Returns:
        cargo's status.

    """
    use = features() if own == "default" else own.split()
    # One `-p` per spec: msrv_plan.py joins them with single spaces.
    flags = f"-p {specs.replace(' ', ' -p ')}".split()
    major = "".join(c for c in own if c.isdigit())
    say(f"compiling at {toolchain}: {specs}")
    argv = ["cargo", "check", "--locked", *manifest, *use, *flags]
    status = rust_task.at_major(
        major, argv, {**prepared, "RUSTUP_TOOLCHAIN": toolchain}
    )
    if status != 0:
        run([python(), str(belt() / "msrv_plan.py"), "--failed", toolchain, specs])
    return status


def planned(at: Sequence[str], toolchains: Path) -> tuple[int, str, str, str]:
    """Read one workspace and plan its checks.

    `cargo fetch --locked` first, the lint:rust precedent, so nothing
    after it downloads. The whole locked graph, not --no-deps:
    msrv_plan.py derives each promise from what the dependencies declare
    (#1850), resolved with the gate's features, so an optional
    dependency the gate compiles is read and one it never enables is
    not.

    Returns:
        The status: the first failure's, else 0; the plan, and the
        extensions and majors pgrx-env.sh derived.

    """
    cargo = shutil.which("cargo") or "cargo"
    status = run([cargo, "fetch", "--locked", *at])
    if status != 0:
        return status, "", "", ""
    graph = [cargo, "metadata", "--format-version", "1", "--locked", *at, *features()]
    status, metadata = output(graph)
    if status != 0:
        return status, "", "", ""
    exclude = os.environ.get("CLIPPY_EXCLUDE", "")
    status, surface = output(
        [python(), str(belt() / "gate_surface.py"), "--exclude", exclude], metadata
    )
    if status != 0:
        return status, "", "", ""
    # pgrx-env.sh says, once, which major each extension is built at:
    # its highest, the same major lint:pg-clippy and fix:rust build it
    # at (#813, #1795). A pgrx crate cannot be compiled with
    # --all-features, so its own flags ride the plan rather than this
    # task guessing.
    status, pairs, majors = rust_task.pgrx_pairs(surface)
    if status != 0:
        return status, "", "", ""
    pgrx = ",".join(f"{rust_task.name_of(spec)}:{major}" for spec, major, *_ in pairs)
    status, plan = output(
        [
            python(),
            str(belt() / "msrv_plan.py"),
            "--toolchains",
            str(toolchains),
            "--exclude",
            exclude,
            "--pgrx",
            pgrx,
            "--floor",
            str(belt() / "rust-floor.toml"),
        ],
        metadata,
    )
    return status, plan, pgrx, majors


def compile_plan(
    plan: str, at: Sequence[str], prepared: Mapping[str, str]
) -> tuple[int, int]:
    """Say each note and compile each check, in the plan's order.

    Returns:
        The status: the first failure's, else 0; and how many groups
        were compiled.

    """
    checked = 0
    for kind, first, own, rest in records(plan):
        match kind:
            case "note":
                say(first)
            case "check":
                checked += 1
                status = check(first, own, rest, at, prepared)
                if status != 0:
                    return status, checked
            case _:
                findings.finding("", f"{UNREADABLE}: {kind}")
                return 1, checked
    return 0, checked


def workspace(manifest: Path | None, toolchains: Path) -> tuple[int, int]:
    """Plan one workspace's declared minimums and compile each.

    Returns:
        The status: the first failure's, else 0; and how many groups
        were compiled.

    """
    at = [] if manifest is None else ["--manifest-path", str(manifest)]
    status, plan, pgrx, majors = planned(at, toolchains)
    if status != 0:
        return status, 0
    # Only when an extension is in the plan, so a repo without one pays
    # nothing.
    prepared: dict[str, str] = {}
    if pgrx and "--features pg" in plan:
        status, prepared = rust_task.register(majors)
        if status != 0:
            return status, 0
    return compile_plan(plan, at, prepared)


def main() -> int:
    """Run the body after the task's guard.

    Returns:
        The task's status.

    """
    # A repo with a Cargo.toml has rust pinned as a build input; cargo
    # absent is a broken toolchain, and skipping would be the
    # vacuous-success failure.
    if shutil.which("cargo") is None:
        findings.finding("", NO_CARGO)
        return 1
    # The pins this reads are mise's own account of them, so mise itself
    # is an input. It is always present inside a mise task; asserted
    # anyway, because the alternative to a red here is a check that
    # silently sees no pins and calls a repo unpinned.
    mise = shutil.which("mise")
    if mise is None:
        findings.finding("", NO_MISE)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        toolchains = Path(tmp) / "toolchains.json"
        # Read once, before any workspace: the pins are the machine's,
        # not a workspace's.
        status, listed = output([mise, "ls", "rust", "--json"])
        if status != 0:
            return status
        toolchains.write_text(listed, encoding="utf-8")
        status, checked = workspace(None, toolchains)
    if status != 0:
        return status
    # Both directions printed, the lint:rust precedent: a run that
    # verified nothing must not read like a run that verified everything.
    if checked == 0:
        say("every declared minimum sits outside the gate's compile surface")
        say("nothing verified — the exclusions above are what to remove")
        return 0
    say(f"{checked} declared minimum(s) compiled at the toolchain they name")
    return 0


if __name__ == "__main__":
    sys.exit(main())
