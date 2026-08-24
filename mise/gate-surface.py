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
import json
import re
import sys
from pathlib import Path

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


def members(metadata: dict) -> list[dict]:
    """Take cargo's own account of which packages are workspace members.

    Returns:
        The member packages, in the order cargo reported them.

    """
    ids = set(metadata.get("workspace_members") or [])
    return [
        pkg
        for pkg in metadata.get("packages") or []
        if pkg.get("id") in ids and pkg.get("name") and pkg.get("version")
    ]


def majors(pkg: dict) -> list[int]:
    """Read the Postgres majors a member declares, from its features.

    Returns:
        The majors, ascending, or an empty list when this is not a pgrx
        extension — which takes BOTH the dependency and the features,
        because either alone is a guess.

    """
    depends = any(dep.get("name") == "pgrx" for dep in pkg.get("dependencies") or [])
    if not depends:
        return []
    found = [
        int(hit.group(1))
        for feature in pkg.get("features") or {}
        if (hit := MAJOR.match(feature))
    ]
    return sorted(found)


def directory(pkg: dict, root: str) -> str:
    """Locate a member relative to the workspace root.

    Returns:
        The crate's directory as the publish stub would write it, or the
        empty string when cargo reported no manifest path.

    """
    manifest = pkg.get("manifest_path")
    if not manifest or not root:
        return ""
    try:
        return str(Path(manifest).parent.relative_to(root))
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
        key, value = hit.group(1), hit.group(2).strip().strip("'\"")
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


def published(inputs: dict[str, str], surface: list[dict]) -> set[str]:
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
        built |= {pkg["name"] for pkg in surface} - excluded
    for klass, key in CRATE_CLASSES.items():
        if klass not in classes:
            continue
        want = inputs.get(key, "").strip("/")
        built |= {pkg["name"] for pkg in surface if pkg["dir"] == want}
    return built


def main(argv: list[str] | None = None) -> int:
    """Emit the gate's compile surface, and check it covers the publish path.

    Returns:
        A process exit status.

    """
    parser = argparse.ArgumentParser(description="the gate's crate surface")
    parser.add_argument("--exclude", default="")
    parser.add_argument("--publish", type=Path)
    args = parser.parse_args(argv)

    try:
        metadata = json.loads(sys.stdin.read())
    except json.JSONDecodeError as bad:
        print(f"{NAME}: cargo metadata did not give JSON", file=sys.stderr)
        print(f"  {bad}", file=sys.stderr)
        return 1

    root = metadata.get("workspace_root") or ""
    held = set(args.exclude.split())
    surface = [
        {
            "name": pkg["name"],
            "spec": f"{pkg['name']}@{pkg['version']}",
            "majors": majors(pkg),
            "dir": directory(pkg, root),
        }
        for pkg in members(metadata)
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
    records = []
    for crate in surface:
        kind = "pgrx" if crate["majors"] else "plain"
        names = ",".join(str(m) for m in crate["majors"]) or "-"
        head = "excluded" if crate["name"] in held else "crate"
        records.append(
            f"{head}\t{crate['spec']}\t{kind}\t{names}\t{crate['dir'] or '-'}",
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


def invariant(stub: Path, surface: list[dict], held: set[str]) -> list[str]:
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
        f"{crate['spec']} is built by the publish path and held out of the "
        "gate by CLIPPY_EXCLUDE — delete the exclusion; a crate the gate "
        "cannot compile is a crate the release finds out about"
        for crate in surface
        if crate["name"] in built and crate["name"] in held
    ]
    stated = inputs.get("pg-majors", "")
    for crate in surface:
        if not crate["majors"] or crate["name"] not in built or not stated:
            continue
        want = ",".join(str(m) for m in crate["majors"])
        if stated.replace(" ", "") != want:
            findings.append(
                f"{crate['spec']} declares features for {want} and the stub "
                f"publishes pg-majors: {stated} — the features are what "
                "cargo compiles, so the stub is the one that is wrong",
            )
    return findings


if __name__ == "__main__":
    sys.exit(main())
