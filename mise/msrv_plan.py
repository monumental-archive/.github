#!/usr/bin/env python3
"""Plan the verification of a repository's declared minimum Rust version.

Org canon — the derivation half of #820. `rust-version` in a Cargo.toml is
a public promise: cargo refuses to resolve the package for a downstream
user on an older toolchain, so raising the real minimum without saying so
breaks builds nobody in this org can see. edtf carried an `msrv:` job as a
required status check before its import, the job vanished on conformance,
and nothing ruled that it should — so the promise went unverified.

WHY A SECOND PINNED TOOLCHAIN AND NOT `cargo msrv verify`. The obvious tool
resolves the declared toolchain through rustup at run time, and the gate's
one hard law is that nothing network-dependent belongs in `ci` (CLAUDE.md).
So the repository pins its MSRV toolchain the way it pins everything else —
a second entry in `rust`, covered by `mise.lock`, checksummed and
attested — and the check compiles at that pin.

WHICH MAKES THE VERSION A FACT IN TWO PLACES, so this compares them rather
than trusting either. `Cargo.toml` stays the source: the promise is read
from cargo's own account of the workspace (`cargo metadata`, which resolves
`rust-version.workspace = true` inheritance), and a declared minimum with
no matching pin is red with a remedy naming both files. The pin is never
the input to the promise — only the thing checked against it. That closes
the failure this exists to prevent, which is not "no check" but "a green
check at the wrong version": bump the pin alone and the promise goes
unverified while the gate stays green.

MATCHING IS PATCH-EXACT, on the zero-padded form. `rust-version = "1.82"`
promises that 1.82.0 builds, so 1.82.0 is what must be pinned and what must
run; verifying at 1.82.1 would be a green answer to a question nobody
asked. Measured while building this: mise publishes rust 1.82.0 and no bare
1.82, so the padding is not a hypothetical — the org's declared form and
the org's pinnable form differ by exactly this.

THE COMPILE SURFACE IS THE GATE'S, NOT THE PUBLISH PATH'S — today. Members
a repository excludes from the gate's Rust lint (`CLIPPY_EXCLUDE`) are
excluded here too, because the exclusion is the one place the org states
what the gate can compile and a second list would drift from it. A member
excluded there while promising an MSRV is therefore UNVERIFIED, which is a
real hole and is printed as one on every run rather than left silent.
#813 is what closes it: when the gate's compile surface widens to the
publish path's, this check widens with it and no edit here is needed.

WHAT THE NUMBER IS, AND NOT ONLY THAT IT IS KEPT (#1850). Every org crate
picked a `rust-version`, none wrote down why, and none had a rule for when
it moves: edtf promised 1.82, a version no supported distribution ships;
release-lab promised 1.97 to crates whose dependencies needed 1.81; and a
dependency raising the real minimum stayed green until a compile happened
to catch it. So the number is derived, never chosen. A member's
`rust-version` is EXACTLY the higher of:

- THE FLOOR, the major.minor of the rustc Debian stable ships, read from
  `mise/rust-floor.toml` in the canon tree. Debian stable because it is one
  number a user can install and it moves on its own schedule, the way
  Postgres majors leave at end of life (#1845); Renovate's deb datasource
  follows the `stable` alias, so the next Debian release arrives as a pull
  request against that file. Ubuntu's LTS ships below what the org's
  dependencies already need, and its versioned `rustc-1.xx` packages make
  "the Ubuntu rustc" ambiguous.
- THE HIGHEST `rust-version` DECLARED by a non-member package the member
  reaches through NORMAL AND BUILD edges of the locked graph — cargo's own
  `resolve`, with the gate's features (CLIPPY_FEATURES) so an optional
  dependency the gate compiles is reached and one it never enables is not.
  Dev edges are not followed, as the compile below follows none: a consumer
  never builds a crate's test harness. Edges are followed by package id,
  so a renamed dependency counts as the package it is, and on every
  target, because a platform-conditional dependency is still part of the
  promise to the users of that platform. A member reached through another
  member contributes that member's dependencies, never its own number.

Exactly, not at most or at least: an inequality is a number someone still
picks, and picking is how the numbers above happened. A crate's own code
never raises it either. Code that needs a newer compiler waits for the
floor or a dependency to move.

EVERY MEMBER, DECLARING OR NOT (#1909). The rule's surface is every
workspace member, not the compile surface: deriving a number compiles
nothing. A member that declares no `rust-version` is a finding naming the
number it must declare, since leaving the field out would otherwise be the
one way to make no promise and pass; inheritance from `[workspace.package]`
counts as declaring, as cargo metadata resolves it. A member CLIPPY_EXCLUDE
keeps off the compile surface is held to its number like any other, and
stays unverified at it until #813 widens the surface.

A DEPENDENCY THAT NEEDS MORE THAN IT DECLARES IS NOT TAKEN. The derived
number stays where the declarations put it, and the compile at that number
fails: that red is the rule working, and `compile_failure` says so rather
than letting the remedy read as "raise the promise". The pull request that
brought the dependency stays red until it declares what it needs, the
floor passes it, or another version is chosen. There is no opt-out and no
per-crate override.

MOVING A PROMISE CAN SURFACE NEW CLIPPY FINDINGS. clippy gates some lints
on the declared `rust-version`: raising one past 1.83 turns on
`missing_const_for_fn`, and edtf's conversion to 1.85 (#1883) had to make
five functions `const fn`. Those are fixed in code, never suppressed.
"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import findings as writer
from decoded import (
    UndecodableError,
    as_array,
    as_object,
    as_str,
    is_strings,
    load_json,
    load_toml,
)
from typed_std import stdin_text

# The floor file's one key, and the shape of a Debian rustc package version
# it holds: an optional epoch, then the upstream major.minor the floor is.
# `1.85.1+dfsg1-1+deb13u1` is Debian 13's; the revision and repack suffix
# say nothing about which compiler it is.
FLOOR_KEY = "debian_stable_rustc"
DEBIAN_RUSTC = re.compile(r"(?:[0-9]+:)?([0-9]+)\.([0-9]+)\.[0-9]+(?:[+~-][^\s]*)?")

# The kinds cargo metadata gives a dependency edge. `null` is a normal
# dependency; a kind outside these is one cargo grew, and following or
# dropping it unseen would change every derived number silently.
FOLLOWED_KINDS = (None, "build")
KNOWN_KINDS = (None, "build", "dev")

# A `rust-version` is a bare partial version — major.minor or
# major.minor.patch — and cargo rejects anything else in the manifest. A
# third shape reaching here means cargo grew one, and a check that guessed
# at it would be verifying a promise it had not read.
VERSION_PARTS = (2, 3)


class Args(argparse.Namespace):
    """The parsed command line, typed."""

    toolchains: Path | None
    floor: Path | None
    exclude: str
    pgrx: str
    root: str
    failed: list[str] | None


@dataclass(frozen=True)
class Package:
    """One package, as cargo metadata reports it."""

    name: str
    version: str
    # The declared minimum, "" where the package declares none.
    rust_version: str
    # Cargo's package id, the key of its resolve graph.
    id: str = ""

    def order(self) -> tuple[str, str]:
        """Sort key: by name, then version.

        Returns:
            The key.

        """
        return (self.name, self.version)


@dataclass(frozen=True)
class Floor:
    """Debian stable's rustc, as the floor file records it."""

    version: tuple[int, int, int]
    # The package version verbatim, so a finding can name its source.
    debian: str


@dataclass(frozen=True)
class Derived:
    """The rust-version the rule derives for one member, and why."""

    version: tuple[int, int, int]
    # As it is to be written in Cargo.toml.
    written: str
    reason: str


@dataclass(frozen=True)
class Toolchain:
    """One entry of `mise ls rust --json`, as this check reads it."""

    version: str
    active: bool
    installed: bool


def package(raw: object, where: str) -> Package:
    """Decode one package of cargo metadata.

    Returns:
        The package; a null or empty `rust_version` reads as none.

    """
    obj = as_object(raw, where)
    promise = obj.get("rust_version")
    package_id = obj.get("id")
    return Package(
        name=as_str(obj.get("name"), f"{where}.name"),
        version=as_str(obj.get("version"), f"{where}.version"),
        rust_version=as_str(promise, f"{where}.rust_version") if promise else "",
        id=package_id if isinstance(package_id, str) else "",
    )


def toolchains(listing: object) -> list[Toolchain]:
    """Decode `mise ls rust --json` into its entries.

    The fields are read as the check always read them: `active` and
    `installed` by truth, `version` as text.

    Returns:
        The entries, in listing order.

    """
    out: list[Toolchain] = []
    for index, item in enumerate(as_array(listing, "the listing")):
        raw = as_object(item, f"listing[{index}]")
        out.append(
            Toolchain(
                version=str(raw.get("version", "")),
                active=bool(raw.get("active")),
                installed=bool(raw.get("installed")),
            )
        )
    return out


def digits(text: str) -> bool:
    """Say whether text is a run of ASCII digits.

    `str.isdigit` also accepts `²` and other digits `int()` refuses
    (#1555: `1.2.²` raised ValueError), and no version spells one.

    Returns:
        True for one or more of 0-9 and nothing else.

    """
    return text.isascii() and text.isdigit()


def parse_version(raw: str) -> tuple[int, int, int] | None:
    """Read a bare version into its zero-padded parts.

    Returns:
        The three components, or None when this is not a bare version —
        a rustup channel name (`nightly-2026-07-20`), a range, a suffix.

    """
    parts = raw.strip().split(".")
    if len(parts) not in VERSION_PARTS or not all(digits(p) for p in parts):
        return None
    parts += ["0"] * (3 - len(parts))
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def show(version: tuple[int, int, int]) -> str:
    """Write a version back out in the form a pin takes.

    Returns:
        The zero-padded three-component string.

    """
    return ".".join(str(part) for part in version)


def members(metadata: object) -> list[Package]:
    """Take cargo's own account of which packages are workspace members.

    Returns:
        The member packages that carry a name and a version, in the
        order cargo reported them.

    Raises:
        UndecodableError: the document is not that shape, naming where.

    """
    doc = as_object(metadata, "cargo metadata")
    listed = doc.get("workspace_members") or []
    if not is_strings(listed):
        msg = "workspace_members is not a list of strings"
        raise UndecodableError(msg)
    ids = set(listed)
    found: list[Package] = []
    for index, raw in enumerate(as_array(doc.get("packages") or [], "packages")):
        where = f"packages[{index}]"
        pkg = as_object(raw, where)
        package_id = pkg.get("id")
        if not isinstance(package_id, str) or package_id not in ids:
            continue
        if pkg.get("name") and pkg.get("version"):
            found.append(package(pkg, where))
    return found


def read_floor(text: str) -> Floor:
    """Read Debian stable's rustc from the floor file.

    Returns:
        The floor: the package's major.minor, zero-padded, and the
        package version as recorded.

    Raises:
        UndecodableError: the file is not TOML, lacks the key, or holds
            a version that is not a Debian rustc package's.

    """
    debian = as_str(load_toml(text).get(FLOOR_KEY), FLOOR_KEY)
    match = DEBIAN_RUSTC.fullmatch(debian)
    if match is None:
        msg = f'{FLOOR_KEY} = "{debian}" is not a Debian rustc package version'
        raise UndecodableError(msg)
    return Floor((int(match.group(1)), int(match.group(2)), 0), debian)


def followed(dep: dict[str, object], where: str) -> bool:
    """Say whether one resolve edge is part of the promise.

    Returns:
        True when any of its kinds is normal or build, on any target.

    Raises:
        UndecodableError: the edge carries no kinds, or one cargo did not
            have when this was written.

    """
    kinds = as_array(dep.get("dep_kinds"), f"{where}.dep_kinds")
    if not kinds:
        msg = f"{where}.dep_kinds is empty"
        raise UndecodableError(msg)
    found = False
    for index, raw in enumerate(kinds):
        kind = as_object(raw, f"{where}.dep_kinds[{index}]").get("kind")
        if kind not in KNOWN_KINDS:
            msg = f"{where}.dep_kinds[{index}].kind {kind!r} is not one this reads"
            raise UndecodableError(msg)
        found = found or kind in FOLLOWED_KINDS
    return found


def graph(metadata: object) -> tuple[dict[str, Package], dict[str, list[str]]]:
    """Read every package and the normal and build edges between them.

    Returns:
        Each package by id, and each package id mapped to the ids its
        normal and build dependencies resolve to.

    Raises:
        UndecodableError: the document is not that shape, naming where —
            including a document with no resolve graph, which is what
            `cargo metadata --no-deps` gives.

    """
    doc = as_object(metadata, "cargo metadata")
    packages: dict[str, Package] = {}
    for index, raw in enumerate(as_array(doc.get("packages") or [], "packages")):
        pkg = package(raw, f"packages[{index}]")
        packages[pkg.id] = pkg
    resolve = doc.get("resolve")
    if resolve is None:
        msg = "no resolve graph: the dependencies were not read (--no-deps?)"
        raise UndecodableError(msg)
    edges: dict[str, list[str]] = {}
    nodes = as_array(as_object(resolve, "resolve").get("nodes"), "resolve.nodes")
    for index, raw in enumerate(nodes):
        where = f"resolve.nodes[{index}]"
        node = as_object(raw, where)
        out = edges.setdefault(as_str(node.get("id"), f"{where}.id"), [])
        for at, dep_raw in enumerate(as_array(node.get("deps"), f"{where}.deps")):
            dep = as_object(dep_raw, f"{where}.deps[{at}]")
            if followed(dep, f"{where}.deps[{at}]"):
                out.append(as_str(dep.get("pkg"), f"{where}.deps[{at}].pkg"))
    return packages, edges


def reached(start: str, edges: dict[str, list[str]]) -> set[str]:
    """Walk the normal and build edges out of one package.

    Returns:
        Every package id reachable from start, start itself excluded.

    """
    seen: set[str] = set()
    todo = [start]
    while todo:
        for dep in edges.get(todo.pop(), []):
            if dep not in seen and dep != start:
                seen.add(dep)
                todo.append(dep)
    return seen


def derive(
    start: str,
    packages: dict[str, Package],
    edges: dict[str, list[str]],
    floor: Floor,
) -> Derived:
    """Derive one member's rust-version from the floor and its graph.

    `packages` holds the non-member packages only: a member reached
    through another contributes its dependencies, never its own number.

    Returns:
        The higher of the floor and the highest rust-version a non-member
        package reached through normal and build edges declares; the
        floor where they tie, since it holds the number regardless.

    Raises:
        UndecodableError: a reached package declares a rust-version that
            is not a bare version, which cargo would not have accepted.

    """
    best: Package | None = None
    best_version = floor.version
    # Walked in name order so that, of two dependencies declaring the same
    # highest number, the one named is the same on every run.
    for pkg in sorted(
        (packages[dep] for dep in reached(start, edges) if dep in packages),
        key=Package.order,
    ):
        if not pkg.rust_version:
            continue
        version = parse_version(pkg.rust_version)
        if version is None:
            msg = (
                f"{pkg.name} {pkg.version} declares rust-version "
                f'"{pkg.rust_version}", which is not a bare version'
            )
            raise UndecodableError(msg)
        # Strictly above: where a dependency ties the floor, the floor
        # holds the number regardless of it.
        if version > best_version:
            best, best_version = pkg, version
    if best is None:
        return Derived(
            floor.version,
            f"{floor.version[0]}.{floor.version[1]}",
            f"the floor: Debian stable's rustc is {floor.debian}",
        )
    return Derived(
        best_version, best.rust_version, f"`{best.name} {best.version}` requires it"
    )


def derivations(
    metadata: object,
    workspace: list[Package],
    floor: Floor,
) -> list[str]:
    """Compare every workspace member with the derived number.

    Every member, not only those that declare one or that the gate
    compiles (#1909): deriving a number compiles nothing, so the compile
    surface has no say in who is held to it, and leaving the field out
    is not a way to make no promise.

    Returns:
        One line per member whose rust-version differs from the one the
        rule derives, or that declares none, naming the derived number
        and why. An UndecodableError from the graph or a reached
        declaration passes through.

    """
    packages, edges = graph(metadata)
    ours = {pkg.id for pkg in workspace}
    outside = {i: p for i, p in packages.items() if i not in ours}
    found: list[str] = []
    for pkg in workspace:
        want = derive(pkg.id, outside, edges, floor)
        said = (
            f"declares rust-version {pkg.rust_version}"
            if pkg.rust_version
            else "declares no rust-version"
        )
        if not pkg.rust_version or parse_version(pkg.rust_version) != want.version:
            found.append(
                f"{pkg.name}@{pkg.version} {said}; the rule derives "
                f"{want.written}: {want.reason}",
            )
    return found


def promises(
    packages: list[Package],
    exclude: set[str],
) -> tuple[dict[tuple[int, int, int], list[str]], list[str], list[str]]:
    """Group the gate's compile surface by the minimum each member promises.

    Returns:
        The members to check per declared minimum, one note per member
        whose promise the gate cannot reach, and one line per member
        whose declaration this cannot compare.

    """
    groups: dict[tuple[int, int, int], list[str]] = {}
    unreachable: list[str] = []
    unreadable: list[str] = []
    for pkg in packages:
        promise = pkg.rust_version
        spec = f"{pkg.name}@{pkg.version}"
        if not promise:
            continue
        if pkg.name in exclude:
            unreachable.append(
                f"{spec} promises rust-version {promise} and is excluded from "
                "the gate's compile surface by CLIPPY_EXCLUDE — unverified",
            )
            continue
        version = parse_version(promise)
        if version is None:
            unreadable.append(
                f'{spec} declares rust-version = "{promise}", which is not a '
                "bare version this check can pin a toolchain to",
            )
            continue
        groups.setdefault(version, []).append(spec)
    return groups, unreachable, unreadable


def pins(
    listing: list[Toolchain],
) -> tuple[dict[tuple[int, int, int], str], list[str]]:
    """Read the rust toolchains this repository's config actually pins.

    Only ACTIVE entries count. `mise ls` reports every version installed on
    the machine, and a toolchain somebody installed by hand is not a pin:
    it would pass locally and be absent in CI, which is the divergence the
    belt exists to prevent.

    Returns:
        Each pinned bare version mapped to the string that names it, and
        the pinned versions that are declared but not installed.

    """
    pinned: dict[tuple[int, int, int], str] = {}
    absent: list[str] = []
    for entry in listing:
        if not entry.active:
            continue
        raw = entry.version
        version = parse_version(raw)
        if version is None:
            continue
        if entry.installed:
            pinned[version] = raw
        else:
            absent.append(raw)
    return pinned, absent


def remedy(
    version: tuple[int, int, int],
    specs: list[str],
    pinned: list[str],
) -> list[str]:
    """Say what disagrees and name both files it lives in.

    Returns:
        The lines of the finding, already indented for the caller.

    """
    have = ", ".join(sorted(pinned)) if pinned else "no bare version at all"
    want = show(version)
    return [
        f"Cargo.toml promises rust-version {want}: {', '.join(specs)}",
        f"mise.toml pins rust: {have}",
        f'  add the MSRV toolchain beside the stable pin: {{ version = "{want}" }}',
        "  or change Cargo.toml's rust-version to the version this repo pins —",
        "  whichever is edited, the two must name the same toolchain",
    ]


def features(declared: str) -> dict[str, str]:
    """Read which members need feature flags of their own, and which.

    A pgrx extension cannot be compiled with `--all-features`: its pgNN
    features contradict each other. `gate_surface.py` derives which
    members those are and at which major, and hands the pairs here, so
    this file never has to know what pgrx is (#813).

    Returns:
        Each such member mapped to the cargo flags that compile it.

    """
    chosen: dict[str, str] = {}
    for pair in declared.split(","):
        name, _, major = pair.partition(":")
        if name.strip() and digits(major.strip()):
            chosen[name.strip()] = f"--no-default-features --features pg{major.strip()}"
    return chosen


def rule_remedy() -> list[str]:
    """Say how a promise that differs from the rule is mended.

    Returns:
        The lines after the findings, already indented for the caller.

    """
    return [
        "  declare each rust-version in Cargo.toml as exactly the number",
        "  derived, a crate declaring none included, and the MSRV pin in",
        "  mise.toml to match it",
        "  the number is derived, never chosen: the higher of Debian stable's",
        "  rustc (mise/rust-floor.toml in the canon) and the highest",
        "  rust-version the crate's normal and build dependencies declare;",
        "  a crate's own code never raises it",
        "  raising it past 1.83 turns on clippy's MSRV-gated lints",
        "  (missing_const_for_fn): fix what they find in code, never suppress",
    ]


def compile_failure(toolchain: str, specs: str) -> list[str]:
    """Say what a red compile at the derived number means.

    The check above has already held each promise to the derived number,
    so the toolchain that failed IS that number. Raising the promise is
    the remedy a reader reaches for, and the one the rule refuses.

    Returns:
        The lines of the finding.

    """
    return [
        f"{specs} failed to compile at {toolchain},",
        "  the rust-version the rule derives",
        "  the crate may not raise it: the number is the higher of Debian",
        "  stable's rustc and the highest rust-version its normal and build",
        "  dependencies declare, and its own code never moves it",
        "  a dependency that fails to compile here declares less than it",
        "  needs, and is held back: keep the version it replaced, or choose",
        "  another, until it declares what it needs or the floor passes it",
    ]


def read_inputs(
    listing_file: Path,
) -> tuple[object, list[Package], list[Toolchain]] | None:
    """Decode cargo metadata on stdin and `mise ls rust` from its file.

    Returns:
        The metadata document, the workspace's members and the toolchain
        entries, or None having said on stderr which document was refused
        and why.

    """
    try:
        metadata = load_json(stdin_text())
        listing = load_json(listing_file.read_text(encoding="utf-8"))
    except UndecodableError as bad:
        writer.finding(
            "", "cargo metadata or `mise ls rust` did not give JSON", [str(bad)]
        )
        return None
    try:
        workspace = members(metadata)
    except UndecodableError as bad:
        writer.finding("cargo metadata", str(bad))
        return None
    try:
        return metadata, workspace, toolchains(listing)
    except UndecodableError as bad:
        writer.finding("`mise ls rust`", str(bad))
        return None


def rule(
    metadata: object,
    workspace: list[Package],
    floor_file: Path,
    root: str = "",
) -> bool:
    """Hold every workspace member to the derived number.

    A member CLIPPY_EXCLUDE keeps off the compile surface is held like
    any other: the exclusion says what the gate cannot compile, not
    which number the crate may promise (#1909).

    `root` is the workspace's manifest, the finding's subject (#2011).

    Returns:
        True when every member declares exactly it; False having said on
        stderr which do not, or which input could not be read.

    """
    try:
        floor = read_floor(floor_file.read_text(encoding="utf-8"))
    except (OSError, UndecodableError) as bad:
        writer.finding(f"the floor file {floor_file}", str(bad))
        return False
    try:
        found = derivations(metadata, workspace, floor)
    except UndecodableError as bad:
        writer.finding("cargo metadata", str(bad))
        return False
    if found:
        writer.finding(
            root,
            "a minimum is undeclared or differs from the one the rule derives",
            [*found, *(line.removeprefix("  ") for line in rule_remedy())],
        )
    return not found


def main(argv: list[str] | None = None) -> int:
    """Emit the check plan for one workspace, or the disagreement.

    Returns:
        A process exit status.

    """
    parser = argparse.ArgumentParser(description="the org's MSRV verification plan")
    parser.add_argument("--toolchains", type=Path)
    parser.add_argument("--floor", type=Path)
    parser.add_argument("--exclude", default="")
    parser.add_argument("--pgrx", default="")
    # The workspace's manifest, named as the subject of each finding about
    # its members, so a tree of several workspaces says which (#2011).
    parser.add_argument("--root", default="")
    parser.add_argument("--failed", nargs=2, metavar=("TOOLCHAIN", "SPECS"))
    args = parser.parse_args(argv, namespace=Args())

    if args.failed:
        head, *rest = compile_failure(*args.failed)
        writer.finding(args.root, head, [line.removeprefix("  ") for line in rest])
        return 1
    if args.toolchains is None or args.floor is None:
        parser.error("--toolchains and --floor are required to plan")

    inputs = read_inputs(args.toolchains)
    if inputs is None:
        return 1
    metadata, workspace, pinned_toolchains = inputs

    exclude = set(args.exclude.split())
    groups, unreachable, unreadable = promises(workspace, exclude)
    if unreadable:
        writer.finding(
            args.root, "a declared minimum this check cannot compare", unreadable
        )
        return 1
    if not rule(metadata, workspace, args.floor, args.root):
        return 1
    return plan(groups, unreachable, pinned_toolchains, features(args.pgrx), args.root)


def plan(
    groups: dict[tuple[int, int, int], list[str]],
    unreachable: list[str],
    pinned_toolchains: list[Toolchain],
    extensions: dict[str, str],
    root: str = "",
) -> int:
    """Emit one check per declared minimum, or the pins that disagree.

    Returns:
        A process exit status.

    """
    pinned, absent = pins(pinned_toolchains)
    records = [f"note\t{line}" for line in unreachable]
    findings: list[str] = []
    for version, specs in sorted(groups.items()):
        toolchain = pinned.get(version)
        if toolchain is None:
            findings.extend(remedy(version, specs, [*pinned.values(), *absent]))
            continue
        plain = [spec for spec in specs if spec.split("@")[0] not in extensions]
        if plain:
            # `default`, never an empty field: a plan is a tab-separated
            # record, and a bash `read` with IFS=tab collapses a run of
            # whitespace separators, so an empty field would shift every
            # field after it left by one. Its consumer, msrv_lint.py
            # (#1911), reads `default` as the gate's own features.
            records.append(f"check\t{toolchain}\tdefault\t{' '.join(plain)}")
        records.extend(
            f"check\t{toolchain}\t{extensions[spec.split('@')[0]]}\t{spec}"
            for spec in specs
            if spec.split("@")[0] in extensions
        )
    if findings:
        if absent:
            findings.append(f"({', '.join(absent)} is pinned but not installed)")
        writer.finding(
            root, "the declared minimum and the pinned toolchain disagree", findings
        )
        return 1

    print("\n".join(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
