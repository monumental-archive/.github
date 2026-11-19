#!/usr/bin/env python3
"""Say which crates the gate compiles, how, and whether that is enough.

Org canon — the derivation half of #813. edtf's v1.3.0 publish failed all
ten pgrx jobs on a parse error the gate had never seen, because the gate
had never compiled that crate. A task existed (`pg:lint`) and could not
matter: named outside the `lint:*` wildcard so `ci` never collected it,
and broken on its own terms. The same class as #687 — not a test that was
skipped, a test that could not fail.

ONE DERIVATION, FOUR CONSUMERS. `lint:rust`, `lint:msrv`, `lint:pg-clippy`
and `test:pgrx` all need the same answer — which members are pgrx
extensions, which Postgres majors each declares, and which members the
gate holds out — and four copies of that answer would drift. So it is
computed once, here, from cargo's own account of the workspace.

A PGRX EXTENSION IS DERIVED, NEVER DECLARED. The old shape was a repo
naming the crate in `CLIPPY_EXCLUDE` and every belt task believing it,
which is how a published crate stayed uncompiled for forty releases. A
member is a pgrx extension when it depends on `pgrx` AND carries features
named `pgNN` — both, because either alone is a guess. Its majors are those
features, read from the manifest and never assumed to be 14..18: a repo
that drops a major must not have the gate keep testing it, and one that
adds a major must not have the gate quietly skip it. `pg_test` is not a
major and does not match.

WHY THE MAJORS ARE NOT A LIST SOMEONE MAINTAINS. The publish stub already
carries `pg-majors`, and the crate already carries its features; the two
can disagree, and when they do the crate's features are the truth — they
are what cargo compiles. The stub is checked against them rather than
trusted (`lint:gate-surface`), which is the same shape as #820's MSRV
agreement check and prevents the same failure: a green run that quietly
covered less than it claimed.

THE INVARIANT, WITH `--publish`. Every crate the publish path builds must
be compiled by the gate. The publish path's crates are read from the
stub's own `with:` block — `rust-crate`/`rust-binary` take every member
except the declared `exclude`, `pgrx-extension` takes
`extension-crate-dir`, `wasm-npm` takes `crate-dir` — and a crate that the
publish path builds while the gate holds it out is a finding. That is what
makes release-lab's blind spot impossible to recreate: the exclusion
itself becomes the red.

The stub is parsed line by line rather than with a YAML library, the way
every other belt check that reads a workflow does. The parser is strict:
a `with:` block it cannot read is a finding, never a shrug, because a stub
this cannot parse is a publish surface nobody checked.
"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import decoded
import typed_std

NAME = "gate-surface"

# A Postgres major feature, and nothing else. `pg_test` is pgrx's own
# harness switch and names no major; `pgrx` is the dependency, not a
# feature. Anchored both ends so neither can pass.
MAJOR = re.compile(r"^pg([0-9]+)$")

# The classes whose jobs build crates, and which key names the crate. A
# class absent from here builds no crate of this workspace's (source
# archives, images, Go binaries), so it constrains nothing.
CRATE_CLASSES = {
    "pgrx-extension": "extension-crate-dir",
    "wasm-npm": "crate-dir",
}
# The classes that build the workspace itself, minus whatever the stub
# excludes from them.
WORKSPACE_CLASSES = {"rust-crate", "rust-binary"}


def listed(value: object) -> list[object]:
    """Take a cargo metadata field as a list, or as empty when it is not one.

    Returns:
        The list, or an empty one.

    """
    return value if decoded.is_array(value) else []


def mapped(value: object) -> dict[str, object]:
    """Take a cargo metadata field as an object, or as empty when it is not one.

    Returns:
        The object, or an empty one.

    """
    return value if decoded.is_object(value) else {}


def string(value: object) -> str:
    """Take a cargo metadata field as a string, or as empty when it is not one.

    Returns:
        The string, or an empty one.

    """
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class Package:
    """One package of cargo metadata, as this check reads it.

    A field of the wrong type reads as absent (#1555): cargo prints these
    shapes, and a document that does not is a package this check cannot
    name, never a crash further down.
    """

    id: str
    name: str
    version: str
    features: tuple[str, ...]  # the feature names
    dependencies: tuple[str, ...]  # the dependency names
    manifest_path: str


def package(value: object) -> Package:
    """Decode one package of cargo metadata.

    Returns:
        The package, its wrong-typed fields empty.

    """
    pkg = mapped(value)
    return Package(
        id=string(pkg.get("id")),
        name=string(pkg.get("name")),
        version=string(pkg.get("version")),
        features=tuple(mapped(pkg.get("features"))),
        dependencies=tuple(
            string(mapped(dep).get("name")) for dep in listed(pkg.get("dependencies"))
        ),
        manifest_path=string(pkg.get("manifest_path")),
    )


@dataclass(frozen=True)
class Metadata:
    """Cargo's account of a workspace: its root, members and packages."""

    root: str
    member_ids: frozenset[str]
    packages: tuple[Package, ...]


def metadata(value: dict[str, object]) -> Metadata:
    """Decode cargo metadata's top-level object.

    Returns:
        The workspace; a package that is not an object is no package.

    """
    return Metadata(
        root=string(value.get("workspace_root")),
        member_ids=frozenset(
            i for i in listed(value.get("workspace_members")) if isinstance(i, str)
        ),
        packages=tuple(
            package(pkg)
            for pkg in listed(value.get("packages"))
            if decoded.is_object(pkg)
        ),
    )


@dataclass(frozen=True)
class Crate:
    """One record of the gate's compile surface."""

    name: str
    spec: str
    majors: tuple[int, ...]
    dir: str


def members(meta: Metadata) -> list[Package]:
    """Take cargo's own account of which packages are workspace members.

    Returns:
        The member packages, in the order cargo reported them.

    """
    return [
        pkg
        for pkg in meta.packages
        if pkg.id and pkg.id in meta.member_ids and pkg.name and pkg.version
    ]


def majors(pkg: Package) -> list[int]:
    """Read the Postgres majors a member declares, from its features.

    Returns:
        The majors, ascending, or an empty list when this is not a pgrx
        extension — which takes BOTH the dependency and the features,
        because either alone is a guess.

    """
    if "pgrx" not in pkg.dependencies:
        return []
    found = [
        int(typed_std.group(hit, 1) or "")
        for feature in pkg.features
        if (hit := MAJOR.match(feature))
    ]
    return sorted(found)


def directory(pkg: Package, root: str) -> str:
    """Locate a member relative to the workspace root.

    Returns:
        The crate's directory as the publish stub would write it, or the
        empty string when cargo reported no manifest path.

    """
    if not pkg.manifest_path or not root:
        return ""
    try:
        return str(Path(pkg.manifest_path).parent.relative_to(root))
    except ValueError:
        return ""


def stub_inputs(text: str) -> tuple[dict[str, str], list[str]]:
    """Read the `with:` block of a publish stub.

    Returns:
        Its scalar keys, and one line per shape this cannot read. Only
        the keys this check knows about are returned; an unknown key is
        not a finding, because the stub carries plenty that says nothing
        about which crates are built.

    """
    wanted = {"classes", "exclude", "pg-majors", *CRATE_CLASSES.values()}
    found: dict[str, str] = {}
    unreadable: list[str] = []
    inside = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if re.match(r"^\s*with:\s*$", line):
            inside = True
            continue
        if inside and not line.startswith((" ", "\t")):
            inside = False
        if not inside:
            continue
        hit = re.match(r"^\s+([a-z0-9-]+):\s*(.*)$", line)
        if not hit:
            continue
        key = typed_std.group(hit, 1) or ""
        value = (typed_std.group(hit, 2) or "").strip().strip("'\"")
        if key not in wanted:
            continue
        if not value:
            unreadable.append(
                f"{key}: is empty or not a scalar — this check reads the "
                "stub's inputs as written, and cannot read a block value",
            )
            continue
        found[key] = value
    return found, unreadable


def published(inputs: dict[str, str], surface: list[Crate]) -> set[str]:
    """Work out which crates of this workspace the publish path builds.

    Returns:
        Their names. Empty when the stub declares no crate-building
        class, which is a repository that publishes something other than
        this workspace.

    """
    classes = {c.strip() for c in inputs.get("classes", "").split(",") if c.strip()}
    excluded = {c.strip() for c in inputs.get("exclude", "").split(",") if c.strip()}
    built: set[str] = set()
    if classes & WORKSPACE_CLASSES:
        built |= {crate.name for crate in surface} - excluded
    for klass, key in CRATE_CLASSES.items():
        if klass not in classes:
            continue
        want = inputs.get(key, "").strip("/")
        built |= {crate.name for crate in surface if crate.dir == want}
    return built


class Args(argparse.Namespace):
    """The command line, typed where argparse writes it."""

    exclude: str
    publish: Path | None


def main(argv: list[str] | None = None) -> int:
    """Emit the gate's compile surface, and check it covers the publish path.

    Returns:
        A process exit status.

    """
    parser = argparse.ArgumentParser(description="the gate's crate surface")
    parser.add_argument("--exclude", default="")
    parser.add_argument("--publish", type=Path)
    args = parser.parse_args(argv, namespace=Args())

    try:
        document = decoded.load_json(typed_std.stdin_text())
    except typed_std.NoStdinError as bad:
        print(f"{NAME}: {bad}", file=sys.stderr)
        return 1
    except decoded.UndecodableError as bad:
        print(f"{NAME}: cargo metadata did not give JSON", file=sys.stderr)
        print(f"  {bad}", file=sys.stderr)
        return 1
    if not decoded.is_object(document):
        # cargo prints an object; any other document names no workspace,
        # and must not read as one with no crates (#1555: it raised).
        print(f"{NAME}: cargo metadata is not a JSON object", file=sys.stderr)
        return 1

    meta = metadata(document)
    held = set(args.exclude.split())
    surface = [
        Crate(
            name=pkg.name,
            spec=f"{pkg.name}@{pkg.version}",
            majors=tuple(majors(pkg)),
            dir=directory(pkg, meta.root),
        )
        for pkg in members(meta)
    ]

    # The directory rides the record because `cargo pgrx test` runs in the
    # crate's own directory rather than taking a `-p`, and a task that
    # re-derived it from the name would be the second derivation this
    # file exists to prevent.
    # NO FIELD IS EVER EMPTY, and `-` is why. The consumers are bash
    # `read` loops with IFS set to tab, and tab is a WHITESPACE separator:
    # bash collapses a run of them, so an empty field does not arrive as
    # empty — it vanishes and every field after it shifts left by one.
    # Measured the hard way, on a plain crate whose empty major list made
    # its directory arrive as its majors.
    records: list[str] = []
    for crate in surface:
        kind = "pgrx" if crate.majors else "plain"
        names = ",".join(str(m) for m in crate.majors) or "-"
        head = "excluded" if crate.name in held else "crate"
        records.append(
            f"{head}\t{crate.spec}\t{kind}\t{names}\t{crate.dir or '-'}",
        )

    if args.publish is not None:
        findings = invariant(args.publish, surface, held)
        if findings:
            print(
                f"{NAME}: the gate does not compile what the publish path builds",
                file=sys.stderr,
            )
            for line in findings:
                print(f"  {line}", file=sys.stderr)
            return 1

    print("\n".join(records))
    return 0


def invariant(stub: Path, surface: list[Crate], held: set[str]) -> list[str]:
    """Check that every crate the publish path builds is compiled by the gate.

    Returns:
        One line per finding, already worded as a remedy.

    """
    if not stub.is_file():
        missing = (
            f"{stub} does not exist — a versioned repository's publish "
            "surface cannot be checked against a stub that is not there"
        )
        return [missing]
    inputs, unreadable = stub_inputs(stub.read_text(encoding="utf-8"))
    if unreadable:
        return [f"{stub}: {line}" for line in unreadable]
    built = published(inputs, surface)
    findings = [
        f"{crate.spec} is built by the publish path and held out of the "
        "gate by CLIPPY_EXCLUDE — delete the exclusion; a crate the gate "
        "cannot compile is a crate the release finds out about"
        for crate in surface
        if crate.name in built and crate.name in held
    ]
    stated = inputs.get("pg-majors", "")
    for crate in surface:
        if not crate.majors or crate.name not in built or not stated:
            continue
        want = ",".join(str(m) for m in crate.majors)
        if stated.replace(" ", "") != want:
            findings.append(
                f"{crate.spec} declares features for {want} and the stub "
                f"publishes pg-majors: {stated} — the features are what "
                "cargo compiles, so the stub is the one that is wrong",
            )
    return findings


if __name__ == "__main__":
    sys.exit(main())
