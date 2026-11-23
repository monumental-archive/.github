#!/usr/bin/env python3
"""The bodies of lint:rust and fix:rust, run by their tasks (#1911).

Usage:
  rust_lint.py lint
  rust_lint.py fix

Each task keeps its guard (a tracked Cargo.toml), the call, and its skip
message; everything after the guard is here, so it comes under the
coverage and mutation legs (CONTRIBUTING, "A helper's language").
Behaviour, messages and exit statuses are the bodies' own, unchanged.

The task's reasoning for each step rides beside the step below. The
clippy ladder stays in mise/clippy-ladder.sh and the pgrx provisioning
in mise/pgrx-env.sh, each written once for every task that needs it;
this file calls their functions through bash rather than restating them.

Run in the repository's root, with cargo, rustup and the belt on PATH;
stdlib only.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import findings
import rust_suppressions
import tracked

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

BELT = Path(__file__).parent
BASH = shutil.which("bash") or "bash"
# The task shell's flags ([task_config] shell), for the belt functions
# this file calls: they ran under them in the bodies.
SHELL = [BASH, "--norc", "-euo", "pipefail", "-c"]
# Source a belt script, then call one of its functions with the rest.
CALL = '. "$1" && shift && "$@"'
# The same, then write every exported variable NUL-separated to a file:
# what a function exported for the calls after it (org_pgrx_darwin_prepare).
EXPORTS = (
    '. "$1" && out=$2 && shift 2 && "$@" && '
    '{ for name in $(compgen -e); do printf "%s=%s\\0" "${name}" "${!name}"; done; }'
    ' > "${out}"'
)
# What bash itself exports or rewrites in every shell, which no function
# exported for its callers.
BASH_OWN = frozenset({"SHLVL", "_", "PWD", "OLDPWD"})
PIN = 'rust = { version = "1.xx.x", components = "clippy,rustfmt,llvm-tools" }'
NO_CARGO = "Cargo.toml present but cargo missing — pin rust in mise.toml"
WHOLE_FILE = re.compile(rb"^[ \t\v\f\r]*#!\[rustfmt::skip", re.MULTILINE)
NARROW = re.compile(rb"^[ \t\v\f\r]*#\[rustfmt::skip", re.MULTILINE)
WARNING = re.compile(r"^Warning", re.MULTILINE)
UNKNOWN_LINT = re.compile(r"E0602|has been renamed to")
MANIFESTS = (":!vendor/**", "Cargo.toml", "*/Cargo.toml")
CONF = (
    "clippy.toml",
    ".clippy.toml",
    "*/clippy.toml",
    "*/.clippy.toml",
    "rustfmt.toml",
    ".rustfmt.toml",
    "*/rustfmt.toml",
    "*/.rustfmt.toml",
)
USAGE = "usage: rust_lint.py lint|fix"
# A record's fields: excluded or crate, the spec, plain or pgrx.
RECORD, SPEC, KIND = 0, 1, 2


def python() -> str:
    """Name the belt's Python, which runs the belt's other helpers.

    Returns:
        ORG_PYTHON, as the bodies read it; this interpreter where it is
        unset.

    """
    return os.environ.get("ORG_PYTHON") or sys.executable


def name_of(spec: str) -> str:
    """Take a package spec's name, as bash's `${spec%@*}` does.

    Returns:
        The text before the last `@`; the whole spec when it has none.

    """
    head, at, _ = spec.rpartition("@")
    return head if at else spec


def run(argv: Sequence[str], env: Mapping[str, str] | None = None) -> int:
    """Run a command with this process's streams.

    Returns:
        Its exit status.

    """
    return subprocess.run(argv, env=env, check=False).returncode


def output(argv: Sequence[str], stdin: str = "") -> tuple[int, str]:
    """Run a command, reading what it prints; its stderr is this process's.

    Returns:
        Its exit status and its standard output.

    """
    done = subprocess.run(
        argv, input=stdin, stdout=subprocess.PIPE, text=True, check=False
    )
    return done.returncode, done.stdout


def belt_call(script: str, *call: str, stdin: str = "") -> tuple[int, str]:
    """Call a function of a belt shell script.

    Returns:
        Its exit status and its standard output.

    """
    return output([*SHELL, CALL, "belt", str(BELT / script), *call], stdin)


def belt_exports(script: str, *call: str) -> tuple[int, dict[str, str]]:
    """Call a belt function that exports, and take what it exported.

    Returns:
        Its exit status, and every variable it set or changed in the
        environment, for the commands that run after it.

    """
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "exports"
        status = run([*SHELL, EXPORTS, "belt", str(BELT / script), str(dump), *call])
        if status != 0:
            return status, {}
        listing = dump.read_bytes()
    exported: dict[str, str] = {}
    for entry in tracked.split(listing):
        key, _, value = entry.partition("=")
        if key not in BASH_OWN and os.environ.get(key) != value:
            exported[key] = value
    return 0, exported


def installed_components() -> list[str]:
    """List the rustup components the repository's own pin installed.

    Returns:
        One line per component; none when rustup is absent or fails.

    """
    rustup = shutil.which("rustup")
    if rustup is None:
        return []
    done = subprocess.run(
        [rustup, "component", "list", "--installed"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return done.stdout.splitlines() if done.returncode == 0 else []


def has(components: list[str], name: str) -> bool:
    """Say whether a component is installed, as `grep -q "^<name>"` does.

    Returns:
        True when a line starts with the name.

    """
    return any(line.startswith(name) for line in components)


def ladder() -> tuple[int, list[str], str]:
    """Read the org's one clippy ladder, from mise/clippy-ladder.sh.

    THE LADDER LIVES IN THE BELT'S clippy-ladder.sh (#813). It moved the
    day a SECOND task acquired the same obligation: `lint:pg-clippy`
    sets this exact level on the pgrx crate the gate never compiled, and
    a second copy of a ten-name list is the drift #752 recorded — the
    list said ten and the prose above it said nine.

    Returns:
        The status, the flags that ride clippy's `--`, and the count of
        named contradictions.

    """
    status, flags = belt_call("clippy-ladder.sh", "org_clippy_ladder")
    if status != 0:
        return status, [], ""
    status, count = belt_call("clippy-ladder.sh", "org_clippy_contradiction_count")
    return status, flags.split(), count.strip()


def features() -> list[str]:
    """Take the features every crate is linted with.

    CLIPPY_FEATURES lets a repo whose features are mutually exclusive
    say so; everything else gets --all-features, because an unlinted
    feature is unlinted code.

    Returns:
        The flag words, split as bash splits an unquoted value.

    """
    return (os.environ.get("CLIPPY_FEATURES") or "--all-features").split()


def surface(cargo: str, where: Sequence[str] = ()) -> tuple[int, str]:
    """Derive the workspace's compile surface, the belt's one derivation (#813).

    gate_surface.py reads cargo's own account — `workspace_members`, not
    `workspace_default_members` — and classifies each member, so
    lint:rust, fix:rust, lint:pg-clippy, test:pgrx and lint:msrv cannot
    disagree about which crates exist or what kind each one is. `where`
    is cargo's `--manifest-path` naming the workspace; empty, cargo
    finds the one at the working directory.

    Returns:
        The status (the helper's when it failed, else cargo's, as
        pipefail reads the pipe) and the records.

    """
    meta_status, meta = output([
        cargo,
        "metadata",
        "--no-deps",
        "--format-version",
        "1",
        "--locked",
        *where,
    ])
    exclude = os.environ.get("CLIPPY_EXCLUDE", "")
    status, records = output(
        [python(), str(BELT / "gate_surface.py"), "--exclude", exclude], meta
    )
    return status or meta_status, records


def records(text: str) -> list[list[str]]:
    """Split gate_surface.py's records into fields.

    Returns:
        Each record's fields; a line naming no spec is skipped, as the
        bodies' `read` loops skipped it.

    """
    rows = [line.split("\t") for line in text.splitlines()]
    return [row for row in rows if len(row) > SPEC and row[SPEC]]


def clippy_pass(argv: Sequence[str]) -> tuple[int, str]:
    """Run one clippy pass, its output on stdout as it comes and kept.

    Returns:
        clippy's status and everything it printed.

    """
    kept: list[str] = []
    with subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    ) as proc:
        if proc.stdout is not None:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "backslashreplace")
                sys.stdout.write(line)
                sys.stdout.flush()
                kept.append(line)
    return proc.returncode, "".join(kept)


def local_config() -> list[str]:
    """Name the repo-local Rust tool configs the tree tracks.

    The knobs are org policy, so a repo-local clippy.toml is a silent
    lowering of them — the unexplained .gitleaks.toml ruling.

    ONE TREE TRACKS THESE NAMES LEGITIMATELY. The belt delivers
    mise/clippy.toml and mise/rustfmt.toml, and `*/clippy.toml` matches
    them, so on the canon this refusal named the canon's own delivered
    configs and told the maintainer to delete the files the belt runs on
    (#746). #691's way out was a rename — taplo-org.toml, biome-org.json
    — and for clippy it does not exist: CLIPPY_CONF_DIR names a
    DIRECTORY, and clippy reads `clippy.toml` or `.clippy.toml` inside
    it and nothing else. Measured, both ways: the same file under a -org
    name is not an error, it is SILENCE — clippy lints on at its own
    defaults, exit 0, every org knob gone and the gate green.

    So the exclusion is by IDENTITY, not by path: only a tree that
    tracks the canon's own identity declaration (#579) may hold these
    two exact paths, and a consumer that tracks its own mise/clippy.toml
    is refused exactly as before. This is not #691's rejected
    ORG_BELT_DIR comparison, which assumed the belt directory and the
    working tree name one checkout; an identity file assumes nothing
    about where the belt came from.

    Returns:
        The tracked configs that are not the canon's delivered ones.

    """
    spec = list(CONF)
    if tracked.tracked("security/identity.toml"):
        spec += [":!mise/clippy.toml", ":!mise/rustfmt.toml"]
    return tracked.tracked(*spec)


def rustfmt_skips(sources: list[str]) -> str:
    """Find formatting suppressions with no written decision.

    Held to the same standard as clippy's — except that rustfmt's
    attribute takes no reason and has no `#[expect]` analogue, so it can
    never retire itself. A whole-file `#![rustfmt::skip]` is refused
    outright, and a narrow one states why in the marker-comment idiom
    `lint:capability-boundary` already uses.

    Returns:
        ` <file>(whole-file)` or ` <file>(no marker)` for each.

    """
    skips = ""
    for name in sources:
        try:
            text = Path(name).read_bytes()
        except OSError:
            continue
        if WHOLE_FILE.search(text):
            skips += f" {name}(whole-file)"
        elif NARROW.search(text) and b"rustfmt-skip:" not in text:
            skips += f" {name}(no marker)"
    return skips


def format_check(cargo: str) -> int:
    """Check the tree is formatted as the org formats it.

    `cargo fmt` rather than a tracked-file list — the lint:go deviation
    with the argument running the other way. rustfmt walks each target's
    module tree, so a .rs file no crate references is never visited; but
    a file no crate references is not a build input either, and rustc
    cannot see it any more than rustfmt can. Feeding it the tracked list
    would also mean choosing an edition here, when Cargo.toml states it
    per crate and `cargo fmt` reads it from there.

    Returns:
        0 when clean, else the task's status.

    """
    done = subprocess.run(
        [
            cargo,
            "fmt",
            "--all",
            "--check",
            "--",
            "--config-path",
            str(BELT / "rustfmt.toml"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="backslashreplace",
        check=False,
    )
    # Both of rustfmt's config failure modes are WARNINGS that exit 0
    # (measured): an unknown key, and a nightly-only option on a stable
    # toolchain. Either formats to a weaker standard behind a green gate,
    # so the warnings are the check and the exit code is only half of it.
    if WARNING.search(done.stdout):
        sys.stderr.write(done.stdout)
        findings.finding(
            "", "rustfmt warned — an ignored option means a weaker format, silently"
        )
        return 1
    if done.returncode != 0:
        sys.stderr.write(done.stdout)
        findings.finding(
            "", "formatting differs from the org's — run 'mise run fix:rust'"
        )
        return 1
    print("lint:rust: formatting clean")
    return 0


def preflight(cargo: str | None) -> tuple[str, list[str]] | None:
    """Check cargo and clippy are there, for lint:rust.

    A repo with a Cargo.toml has rust pinned as a build input; cargo
    absent is a broken toolchain, and skipping would be the
    vacuous-success failure. clippy is a rustup COMPONENT of the repo's
    own pin, not a belt binary: mise installs rust through rustup and
    inherits whatever profile the machine already has, so on a minimal
    profile clippy is simply absent and this task would lint nothing.
    Asserted with a remedy, never installed: a task-time rustup races
    every other task driving the toolchain (measured twice on the lab's
    v0.18.0 release PR, #117).

    Returns:
        cargo and the installed components; None after a finding.

    """
    if cargo is None:
        findings.finding("", NO_CARGO)
        return None
    installed = installed_components()
    if not has(installed, "clippy"):
        findings.finding(
            "",
            "clippy component missing — declare it with the rust pin in mise.toml:",
            [PIN, "(installed with the toolchain, before tasks run — never mid-run)"],
        )
        return None
    return cargo, installed


def checks_before_clippy(cargo: str, installed: list[str], manifests: list[str]) -> int:
    """Run lint:rust's checks that need no compile, in the body's order.

    Returns:
        0 when every one passes, else the task's status.

    """
    local = local_config()
    if local:
        findings.finding(
            "",
            "repo-local Rust tool configuration is not the org's:",
            [
                *local,
                (
                    "the knobs live in the canon's mise/clippy.toml"
                    " and mise/rustfmt.toml,"
                ),
                "delivered through CLIPPY_CONF_DIR and rustfmt's --config-path",
                (
                    "a setting this repo genuinely needs is argued into the canon,"
                    " not kept locally"
                ),
            ],
        )
        return 1
    # The rustfmt leg, first because it is seconds against clippy's
    # minutes. rustfmt is a rustup component of the repo's own pin
    # exactly like clippy, asserted with a remedy and never installed.
    if not has(installed, "rustfmt"):
        findings.finding(
            "",
            "rustfmt component missing — declare it with the rust pin in mise.toml:",
            [PIN],
        )
        return 1
    sources = tracked.tracked(":!vendor/**", "*.rs")
    skips = rustfmt_skips(sources)
    if skips:
        findings.finding(
            "",
            f"formatting suppressed without a written decision:{skips}",
            [
                "#![rustfmt::skip] exempts an entire file — never allowed",
                (
                    "#[rustfmt::skip] must carry a '// rustfmt-skip: <why>'"
                    " marker above it"
                ),
                (
                    "rustfmt has no #[expect] analogue, so the comment is the only"
                    " thing a reviewer sees"
                ),
            ],
        )
        return 1
    status = format_check(cargo)
    if status != 0:
        return status
    # EVERY SUPPRESSION IS AN `expect`, and two checks make that true
    # together (#1857): restriction's allow_attributes refuses an outer
    # `#[allow]`, and rust_suppressions.py refuses the inner and manifest
    # forms it cannot see, over the same tracked files. Its docstring
    # carries the measurements.
    return rust_suppressions.check([*manifests, *sources])


class Population:
    """What one lint:rust run linted, and what it left to another task."""

    def __init__(self) -> None:
        """Start empty."""
        self.linted: list[str] = []
        self.red: list[str] = []
        self.excluded: list[str] = []
        self.extensions: list[str] = []
        self.status = 0
        self.output = ""


def lint_members(cargo: str, rows: list[list[str]], flags: list[str]) -> Population:
    """Lint every member as its own package, past a red one (#726).

    Under `-D warnings` a finding IS a compile error, so a red crate
    emits no metadata and every crate that depends on it is never
    linted at all — and the number this task prints then reads as a
    workspace total when it is one crate's. Measured on both Rust
    imports: clearing the last `iiif-core` finding moved iiif-server's
    count 24 -> 319 (#671); on edtf every total ever recorded was
    `edtf-core` alone with four crates unseen (#669).

    `--keep-going` is NOT enough: it builds SIBLINGS, never a DEPENDENT,
    which has nothing to compile against (measured at -j1 on a two-crate
    fixture). It is kept, per pass, because inside one pass it is what
    makes a red lib still report its own test target.

    `--no-deps` is what makes a per-crate pass work at all: a `-p` run
    otherwise includes path dependencies that are members, so the red
    dependency fails the pass before its dependent is reached. Every
    member is still primary in exactly one pass, so coverage equals the
    old single pass when nothing is red and is strictly larger when
    something is. Not fail-open through the build cache, measured in
    both orders.

    A pgrx extension is SKIPPED HERE and linted by `lint:pg-clippy` at
    the same level: its pgNN features are mutually exclusive, so
    `--all-features` is a hard error on it. Derived, never declared.

    Returns:
        The population, every pass's status and output.

    """
    seen = Population()
    words = features()
    for row in rows:
        spec = row[SPEC]
        name = name_of(spec)
        if row[RECORD] == "excluded":
            seen.excluded.append(name)
            continue
        if row[KIND : KIND + 1] == ["pgrx"]:
            seen.extensions.append(name)
            continue
        seen.linted.append(name)
        status, said = clippy_pass([
            cargo,
            "clippy",
            "-p",
            spec,
            "--no-deps",
            "--all-targets",
            *words,
            "--locked",
            "--keep-going",
            "--",
            *flags,
        ])
        seen.output += said
        if status != 0:
            seen.red.append(name)
            seen.status = status
    return seen


def joined(names: list[str]) -> str:
    """Join names as the bodies built their lists, each after a space.

    Returns:
        ` a b c`, or nothing for no names.

    """
    return "".join(f" {name}" for name in names)


def verdict(seen: Population, count: str) -> int:
    """Say what the clippy passes found, the population first (#726).

    Returns:
        The task's status.

    """
    # A bare `--features NAME` means different things to the two shapes:
    # cargo accepts a name held by ANY member when one pass covers the
    # workspace, and rejects it for a member that lacks it when each
    # crate gets its own pass (measured, both).
    if "does not contain this feature" in seen.output:
        findings.finding(
            "",
            "CLIPPY_FEATURES names a feature some crate in this workspace lacks",
            [
                (
                    "every crate is linted as its own package (#726),"
                    " so the value has to be one"
                ),
                (
                    "each member accepts — --all-features, or --no-default-features"
                    " — and a crate"
                ),
                "that genuinely cannot take it is named in CLIPPY_EXCLUDE instead",
            ],
        )
        return 1
    # A lint name rustc does not recognise is warning E0602, and `-D
    # warnings` cannot reach it: it is emitted by the command-line parser
    # before lint levels apply (measured). With levels stated as
    # exclusions a stale name fails closed anyway, but the run must say
    # WHY rather than leave a maintainer reading a contradiction that
    # looks like a false positive.
    if UNKNOWN_LINT.search(seen.output):
        findings.finding(
            "",
            (
                "a lint name above is unknown to this toolchain"
                " (see E0602/renamed above)"
            ),
            [
                (
                    "clippy renamed or removed it — update the exclusion list"
                    " in mise/config.toml"
                ),
            ],
        )
        return 1
    # The population, printed before the verdict and in BOTH directions,
    # so that no run of this task can read as a whole workspace when it
    # was not one (#726).
    print(f"lint:rust: {len(seen.linted)} crate(s) linted:{joined(seen.linted)}")
    if seen.extensions:
        print(
            "lint:rust: pgrx extension(s), linted by lint:pg-clippy:"
            + joined(seen.extensions)
        )
    if seen.excluded:
        print(f"lint:rust: excluded by CLIPPY_EXCLUDE:{joined(seen.excluded)}")
    if seen.red:
        findings.finding("", f"red:{joined(seen.red)}")
        return seen.status
    print(
        f"lint:rust: clean at every group (restriction minus {count}"
        " named contradictions)"
    )
    return 0


def lint() -> int:
    """Run lint:rust after its guard.

    Returns:
        The task's status.

    """
    found = preflight(shutil.which("cargo"))
    if found is None:
        return 1
    cargo, installed = found
    status = checks_before_clippy(cargo, installed, tracked.tracked(*MANIFESTS))
    return status or clippy_leg(cargo)


def clippy_leg(cargo: str) -> int:
    """Run lint:rust's clippy passes, once the checks before them pass.

    Returns:
        The task's status.

    """
    # The canon's clippy.toml, delivered by directory: an unresolvable
    # belt would leave clippy at its own defaults, silently.
    os.environ["CLIPPY_CONF_DIR"] = str(BELT)
    status, flags, count = ladder()
    if status != 0:
        return status
    # Deterministic: the outcome is a function of the tree, the lock and
    # the pinned toolchain. `cargo fetch --locked` first, the lint:deny
    # precedent — fetching pinned inputs is the same category as fetching
    # the toolchain.
    status = run([cargo, "fetch", "--locked"])
    if status != 0:
        return status
    status, listed = surface(cargo)
    if status != 0:
        return status
    if not listed.strip():
        findings.finding(
            "", "a Cargo.toml is tracked but cargo metadata names no workspace member"
        )
        return 1
    return verdict(lint_members(cargo, records(listed), flags), count)


def register(majors: str) -> tuple[int, dict[str, str]]:
    """Ready this machine to build the extensions, before anything compiles.

    The SDK, and the ICU headers and Postgres pin of every major the
    pass builds, checked first (#1774, #1940).

    Returns:
        The status, and what org_pgrx_darwin_prepare exported for the
        compiles after it.

    """
    status, prepared = belt_exports("pgrx-env.sh", "org_pgrx_darwin_prepare", majors)
    if status != 0:
        return status, {}
    listed, installed = output([
        shutil.which("mise") or "mise",
        "ls",
        "aqua:theseus-rs/postgresql-binaries",
        "--json",
    ])
    checked, _ = output(
        [python(), str(BELT / "pgrx_postgres.py"), "--majors", majors], installed
    )
    return checked or listed, prepared


def at_major(major: str, argv: Sequence[str], prepared: Mapping[str, str]) -> int:
    """Run one compile in a shell holding its own major's headers and Postgres.

    org_pgrx_major's subshell ends with the command, so the next major
    starts from the caller's environment (#1774, #1940).

    Returns:
        The command's status.

    """
    shell = [*SHELL, CALL, "belt", str(BELT / "pgrx-env.sh"), "org_pgrx_major", major]
    return run([*shell, *argv], {**os.environ, **prepared})


def fix_extensions(
    cargo: str, pairs: list[list[str]], majors: str, flags: list[str]
) -> int:
    """Fix each pgrx extension at its own highest major.

    Without this pass `mise run fix` could not touch a crate the gate
    lints, which is the asymmetry #691 named wearing a new hat: a
    finding a developer cannot ask the machine to fix is a finding they
    will fix by hand or not at all.

    Returns:
        The status: the first failure's, else 0.

    """
    status, prepared = register(majors)
    if status != 0:
        return status
    fixed = ""
    for spec, major, *_ in pairs:
        fixed += f" {name_of(spec)}(pg{major})"
        # --allow-dirty for the reason the workspace pass carries it, and
        # doubly so here: the workspace pass ran first and wrote.
        status = at_major(
            major,
            [
                cargo,
                "clippy",
                "--fix",
                "--allow-dirty",
                "-p",
                name_of(spec),
                "--no-deps",
                "--all-targets",
                "--locked",
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
    print(f"fix:rust: pgrx extension(s) fixed:{fixed}")
    return 0


def pgrx_pairs(found: str) -> tuple[int, list[list[str]], str]:
    """Take each extension's major, and the majors to register (#1795).

    From the belt's one derivation, mise/pgrx-env.sh: the gate's lint
    and the fix pass build each extension at the same major. One step
    per assignment, so a failed derivation is never an empty list.

    Returns:
        The status, `[spec, major]` per extension, and the majors.

    """
    status, highest = belt_call("pgrx-env.sh", "org_pgrx_highest", stdin=found)
    if status != 0:
        return status, [], ""
    status, majors = belt_call("pgrx-env.sh", "org_pgrx_union", stdin=highest)
    pairs = [line.split("\t") for line in highest.splitlines()]
    return status, [pair for pair in pairs if len(pair) > 1 and pair[0]], majors.strip()


def fix() -> int:
    """Run fix:rust after its guard.

    Returns:
        The task's status.

    """
    cargo = shutil.which("cargo")
    if cargo is None:
        findings.finding("", NO_CARGO)
        return 1
    installed = installed_components()
    for component in ("clippy", "rustfmt"):
        if not has(installed, component):
            findings.finding(
                "",
                f"{component} component missing"
                " — declare it with the rust pin in mise.toml",
            )
            return 1
    return fix_leg(cargo)


def fix_leg(cargo: str) -> int:
    """Run fix:rust's formatter and fix passes, once cargo and its components are.

    Returns:
        The task's status.

    """
    os.environ["CLIPPY_CONF_DIR"] = str(BELT)
    # Format first: clippy --fix rewrites source, and handing it
    # canonically formatted input keeps its edits from arriving
    # pre-mangled.
    status = run([
        cargo,
        "fmt",
        "--all",
        "--",
        "--config-path",
        str(BELT / "rustfmt.toml"),
    ])
    if status != 0:
        return status
    print("fix:rust: formatted")
    status, flags, _ = ladder()
    if status != 0:
        return status
    status = run([cargo, "fetch", "--locked"])
    if status != 0:
        return status
    # Same derivation as the gate leg, for the same reason: this leg's
    # mirror of #726 is the POPULATION, and a population computed twice
    # is two populations (#813).
    status, found = surface(cargo)
    if status != 0:
        return status
    status, pairs, majors = pgrx_pairs(found)
    if status != 0:
        return status
    return fix_workspace(cargo, records(found), pairs, (majors, flags))


def fix_workspace(
    cargo: str,
    rows: list[list[str]],
    pairs: list[list[str]],
    build: tuple[str, list[str]],
) -> int:
    """Fix the workspace in one pass, then each extension in its own.

    The POPULATION is declared here too (#726). `cargo clippy --fix`
    without `--workspace` selects the workspace's DEFAULT members, so a
    repo could drop a crate from the write-mode twin by editing a field
    nobody reads as a lint setting (measured on a two-crate fixture with
    `default-members = ["a"]`). CLIPPY_EXCLUDE applies for the reason it
    applies to the gate leg — a crate that cannot build on a runner
    cannot be fixed on one either (`--exclude` is accepted under
    `--fix`).

    What this leg does NOT need is the gate leg's per-crate iteration.
    `cargo fix` caps lint denials to warnings ("the `clippy::<lint>`
    lint ignores `-D warnings`"), so a red crate never stops the crates
    behind it: measured with b depending on a and both red, ONE `--fix`
    pass rewrote both and exited 0.

    The extensions are held out of the workspace pass and fixed in
    their own: their pgNN features contradict each other, so
    --all-features cannot compile one.

    Returns:
        The task's status.

    """
    majors, flags = build
    excludes: list[str] = []
    fixing: list[str] = []
    excluded: list[str] = []
    for row in rows:
        name = name_of(row[SPEC])
        if row[RECORD] == "excluded":
            excludes += ["--exclude", name]
            excluded.append(name)
        elif row[KIND : KIND + 1] != ["pgrx"]:
            fixing.append(name)
    for spec, *_ in pairs:
        excludes += ["--exclude", name_of(spec)]
    status = run([
        cargo,
        "clippy",
        "--fix",
        "--allow-dirty",
        "--workspace",
        *excludes,
        *features(),
        "--locked",
        "--",
        *flags,
    ])
    if status != 0:
        return status
    print(f"fix:rust:{joined(fixing) or ' none'} rewritten where clippy could")
    if pairs:
        status = fix_extensions(cargo, pairs, majors, flags)
        if status != 0:
            return status
    if excluded:
        print(f"fix:rust: excluded by CLIPPY_EXCLUDE:{joined(excluded)}")
    print("fix:rust: applied what is machine-applicable — the rest is judgement")
    return 0


def main(argv: list[str]) -> int:
    """Run one task's body.

    Returns:
        The task's status; 2 on a usage error.

    """
    match argv:
        case ["lint"]:
            return lint()
        case ["fix"]:
            return fix()
        case _:
            findings.finding("", USAGE)
            return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
