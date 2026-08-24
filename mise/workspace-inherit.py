#!/usr/bin/env python3
"""Prove every [workspace.package] key reaches at least one member.

Org canon — #863. `[workspace.package]` inheritance is opt-in per member
AND per key: a key declared at the workspace root is inert until a member
writes `<key>.workspace = true`, and an inert key is policy with no
reader. Found by running #820's `lint:msrv` against release-lab, which
reported "no crate declares rust-version, skipped" against a root
manifest whose line 26 says `rust-version = "1.97"`. Both halves of the
comment written beside that line were false: no consumer read an MSRV
from any lab crate, and clippy's `incompatible_msrv` had no version to
compare against, so that lint had been silently inert on every gate run
since it was written.

The failure is the #687/#813 shape — not a check that went red, a check
that could not fire. It is invisible to every lint that reads the
manifest TEXT, which is why this one reads cargo's account of the
RESOLVED packages instead: the same source #820's msrv-plan.py reads, and
what every consumer of the manifest actually sees.

The rule is minimal and fail-closed: a key NO member takes is red. Not
"every member takes every key" — a workspace legitimately declares a key
only some members want, and release-lab keeps `description` per crate on
purpose. A key nobody takes is dead in a way a partly-taken key is not.

Reads `cargo metadata --no-deps --locked` on stdin, the belt convention:
the task runs the tool and the helper consumes, so this script starts no
processes.
"""

import json
import os
import sys
import tomllib
from pathlib import Path
from typing import NamedTuple

# The [workspace.package] keys cargo reports per package, and the field
# each is reported under.
REPORTED = {
    "authors": "authors",
    "categories": "categories",
    "description": "description",
    "documentation": "documentation",
    "edition": "edition",
    "homepage": "homepage",
    "keywords": "keywords",
    "license": "license",
    "license-file": "license_file",
    "readme": "readme",
    "repository": "repository",
    "rust-version": "rust_version",
    "version": "version",
}

# Keys whose value is a PATH. Cargo REBASES one on inheritance, so the
# member reports a different string for the same file and comparing the
# two as values is wrong. Both spellings are handled the same way rather
# than only the one measured, because the comparison below tries value
# equality first: a key cargo does not rebase still matches verbatim, so
# covering both costs nothing and guessing which is which costs a red.
PATH_KEYS = ("readme", "license-file")

# Keys a verdict cannot be reached on, and the reason is cargo's account
# rather than this script's reach. Named rather than omitted, because a
# key that silently passes is the failure class this exists to close.
#
#   exclude, include — cargo reports neither per package.
#   publish — the declared value appears NOWHERE in the account: a
#     declared false comes back `[]` and a declared true comes back
#     `None`, and a member declaring its own false reports
#     exactly what an inheriting one reports. Undecidable in BOTH
#     directions, so it is reported unjudged rather than given a verdict
#     that would be right by luck.
UNRESOLVABLE = {
    "exclude": "cargo reports no per-package value",
    "include": "cargo reports no per-package value",
    "publish": "an inherited and an own `publish` are the same in cargo's account",
}

# HOW A DECLARED VALUE COMES BACK, measured 2026-08-24 against a real
# two-member workspace built for this — one member inheriting every key,
# one inheriting none:
#
#   version, edition, rust-version, license, repository, authors,
#   keywords, categories, homepage, documentation, description
#       verbatim. A member taking none reports its own value, `None`, or
#       `[]` for the list-valued ones — so "every member reports null"
#       is NOT the test; equality against the declaration is.
#   a readme naming a path
#       rebased per member: `../../README.md` from `crates/taker/`.
#       Value equality would call release-lab's readme taken by
#       nobody, a false RED on a key all four of its members do take.
#   a readme declared true
#       the discovered path; the declared bool appears nowhere.
#   a readme declared false
#       `None` — which is what a member taking nothing reports too.
#
# So "inherited" is not one equality. It is: cargo reports, for this
# member, whatever the workspace's declaration RESOLVES to for that
# member.
#
# The one limit worth stating: a member that declares the same value
# LITERALLY is indistinguishable from one that inherits it, because
# cargo's account carries no provenance. That direction is safe — the
# declared value does reach that package, and the remedy would be a
# no-op — and the alternative is reading the member's manifest text,
# which is what made this class invisible in the first place.


class Finding(NamedTuple):
    """A declared key that reaches no member of the workspace."""

    key: str
    declared: object
    takers: list[str]


def workspace_package(root: Path) -> dict:
    """Read the [workspace.package] table of a workspace root manifest.

    Returns:
        The declared table, empty when the manifest declares none.

    """
    manifest = root / "Cargo.toml"
    try:
        doc = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        sys.exit(f"lint:workspace-inherit: {manifest} is unreadable ({exc})")
    return doc.get("workspace", {}).get("package", {})


def same_file(declared: str, reported: str, root: Path, manifest: Path) -> bool:
    """Test whether a rebased path still names the file the workspace did.

    Normalised rather than resolved: this answers a question about two
    declarations, not about what is on the disk, and a check that reads
    the filesystem would give a different verdict for a path that has not
    been created yet.

    Returns:
        True when both spellings name one path.

    """
    at_root = os.path.normpath(root / declared)
    at_member = os.path.normpath(manifest.parent / reported)
    return at_root == at_member


def inherits(key: str, declared: object, package: dict, root: Path) -> bool | None:
    """Test whether one package takes the workspace's declaration of a key.

    Returns:
        True when cargo reports for this package what the declaration
        resolves to, False when it reports something else, and None when
        the two are indistinguishable in cargo's account.

    """
    reported = package.get(REPORTED[key])
    if reported == declared:
        return True
    if key == "readme" and declared is False:
        return None
    if key == "readme" and declared is True:
        return reported is not None
    if key in PATH_KEYS and isinstance(declared, str) and isinstance(reported, str):
        return same_file(declared, reported, root, Path(package["manifest_path"]))
    return False


def judge(
    table: dict,
    members: list[dict],
    root: Path,
    report: list[str],
) -> list[Finding]:
    """Measure every declared key against what the members actually take.

    Returns:
        One finding per key no member takes, in declared order.

    """
    findings: list[Finding] = []
    for key, declared in table.items():
        if key in UNRESOLVABLE:
            report.append(f"{key}: {UNRESOLVABLE[key]}")
            continue
        if key not in REPORTED:
            report.append(f"{key}: not a key cargo reports per package")
            continue
        verdicts = [inherits(key, declared, m, root) for m in members]
        if any(v is True for v in verdicts):
            continue
        if any(v is None for v in verdicts):
            report.append(f"{key}: its declared value is not decidable per member")
            continue
        findings.append(Finding(key, declared, [m["name"] for m in members]))
    return findings


def explain(findings: list[Finding], root: Path) -> None:
    """Print the inert keys and the one line that fixes each."""
    for key, declared, takers in findings:
        print(
            f"  {key} = {declared!r} is declared and inherited by no member",
            file=sys.stderr,
        )
        print(f"        could take it: {', '.join(takers)}", file=sys.stderr)
        print(f"        add `{key}.workspace = true`", file=sys.stderr)
    print(
        f"\n  Declared in {root}/Cargo.toml under [workspace.package].\n"
        "  Inheritance is opt-in per member AND per key, so a key nobody takes\n"
        "  reaches no consumer and no tool — release-lab's `rust-version` left\n"
        "  clippy's incompatible_msrv with nothing to check against (#863).\n"
        "  A key only SOME members want is fine and is not reported here; this\n"
        "  is the one nobody wants.",
        file=sys.stderr,
    )


def main() -> int:
    """Run the check over one cargo metadata document on stdin.

    Returns:
        0 when every declared key reaches a member, 1 when one does not.

    """
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        sys.exit(f"lint:workspace-inherit: cargo metadata is unreadable ({exc})")

    root = Path(data["workspace_root"])
    table = workspace_package(root)
    if not table:
        print("lint:workspace-inherit: no [workspace.package] table, skipped")
        return 0

    ids = set(data.get("workspace_members", []))
    members = [p for p in data.get("packages", []) if p.get("id") in ids]
    if not members:
        print(
            "lint:workspace-inherit: [workspace.package] is declared and cargo "
            "metadata names no workspace member to inherit it",
            file=sys.stderr,
        )
        return 1

    report: list[str] = []
    findings = judge(table, members, root, report)
    for line in report:
        print(f"lint:workspace-inherit: unjudged — {line}", file=sys.stderr)

    counted = len(table)
    noun = "key" if counted == 1 else "keys"
    if not findings:
        print(
            f"lint:workspace-inherit: {counted} [workspace.package] {noun}, "
            f"every one inherited by a member of {len(members)}",
        )
        return 0

    print(
        f"lint:workspace-inherit: {len(findings)} of {counted} "
        f"[workspace.package] {noun} reach no member\n",
        file=sys.stderr,
    )
    explain(findings, root)
    return 1


if __name__ == "__main__":
    sys.exit(main())
