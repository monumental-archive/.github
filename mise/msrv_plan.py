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
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from decoded import (
    UndecodableError,
    as_array,
    as_object,
    as_str,
    is_strings,
    load_json,
)
from typed_std import stdin_text

NAME = "msrv-plan"

# A `rust-version` is a bare partial version — major.minor or
# major.minor.patch — and cargo rejects anything else in the manifest. A
# third shape reaching here means cargo grew one, and a check that guessed
# at it would be verifying a promise it had not read.
VERSION_PARTS = (2, 3)


class Args(argparse.Namespace):
    """The parsed command line, typed."""

    toolchains: Path
    exclude: str
    pgrx: str


@dataclass(frozen=True)
class Package:
    """One workspace member, as cargo metadata reports it."""

    name: str
    version: str
    # The declared minimum, "" where the member declares none.
    rust_version: str


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
    return Package(
        name=as_str(obj.get("name"), f"{where}.name"),
        version=as_str(obj.get("version"), f"{where}.version"),
        rust_version=as_str(promise, f"{where}.rust_version") if promise else "",
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


def read_inputs(listing_file: Path) -> tuple[list[Package], list[Toolchain]] | None:
    """Decode cargo metadata on stdin and `mise ls rust` from its file.

    Returns:
        The workspace's members and the toolchain entries, or None
        having said on stderr which document was refused and why.

    """
    try:
        metadata = load_json(stdin_text())
        listing = load_json(listing_file.read_text(encoding="utf-8"))
    except UndecodableError as bad:
        print(
            f"{NAME}: cargo metadata or `mise ls rust` did not give JSON",
            file=sys.stderr,
        )
        print(f"  {bad}", file=sys.stderr)
        return None
    try:
        workspace = members(metadata)
    except UndecodableError as bad:
        print(f"{NAME}: cargo metadata: {bad}", file=sys.stderr)
        return None
    try:
        return workspace, toolchains(listing)
    except UndecodableError as bad:
        print(f"{NAME}: `mise ls rust`: {bad}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    """Emit the check plan for one workspace, or the disagreement.

    Returns:
        A process exit status.

    """
    parser = argparse.ArgumentParser(description="the org's MSRV verification plan")
    parser.add_argument("--toolchains", type=Path, required=True)
    parser.add_argument("--exclude", default="")
    parser.add_argument("--pgrx", default="")
    args = parser.parse_args(argv, namespace=Args())

    inputs = read_inputs(args.toolchains)
    if inputs is None:
        return 1
    workspace, pinned_toolchains = inputs

    groups, unreachable, unreadable = promises(
        workspace,
        set(args.exclude.split()),
    )
    if unreadable:
        print(f"{NAME}: a declared minimum this check cannot compare", file=sys.stderr)
        for line in unreadable:
            print(f"  {line}", file=sys.stderr)
        return 1

    pinned, absent = pins(pinned_toolchains)
    extensions = features(args.pgrx)
    records = [f"note\t{line}" for line in unreachable]
    findings: list[str] = []
    for version, specs in sorted(groups.items()):
        toolchain = pinned.get(version)
        if toolchain is None:
            findings.extend(remedy(version, specs, [*pinned.values(), *absent]))
            continue
        plain = [spec for spec in specs if spec.split("@")[0] not in extensions]
        if plain:
            # `default`, never an empty field: the consumer is a bash
            # `read` loop with IFS=tab, and bash collapses a run of
            # whitespace separators, so an empty field shifts every field
            # after it left by one.
            records.append(f"check\t{toolchain}\tdefault\t{' '.join(plain)}")
        records.extend(
            f"check\t{toolchain}\t{extensions[spec.split('@')[0]]}\t{spec}"
            for spec in specs
            if spec.split("@")[0] in extensions
        )
    if findings:
        print(
            f"{NAME}: the declared minimum and the pinned toolchain disagree",
            file=sys.stderr,
        )
        for line in findings:
            print(f"  {line}", file=sys.stderr)
        if absent:
            print(
                f"  ({', '.join(absent)} is pinned but not installed)",
                file=sys.stderr,
            )
        return 1

    print("\n".join(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
