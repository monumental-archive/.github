#!/usr/bin/env python3
"""Table tests for the MSRV verification plan (#820).

One row per branch of each guard, both directions: the state that must
produce a check and the nearest one that must not. The two the issue names
explicitly are here twice over — a promise the pins do not cover, and a pin
moved away from the promise — because "the declared minimum and the pinned
toolchain agree" is a claim that looks identical whether it was tested or
assumed.

Mutation-checked: every guard below was broken in turn and the rows that
cover it observed to fail — three, one, three, one and five red rows for
the five mutations named in the class docstrings, so a reader can repeat
rather than trust this line. A check that admits the wrong toolchain looks
exactly like a check that works.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import decoded
import msrv_plan

# What the planner returns, named so an expected value states its type.
Version = tuple[int, int, int]
Groups = dict[Version, list[str]]
Pinned = dict[Version, str]


def member(
    name: str, promise: str | None = None, version: str = "1.3.1"
) -> dict[str, object]:
    """Build one workspace member as cargo metadata reports it.

    Returns:
        The package object, with `rust_version` present only when declared
        — cargo omits nothing, it reports null, and both are exercised.

    """
    return {
        "id": f"path+file:///w/{name}#{version}",
        "name": name,
        "version": version,
        "rust_version": promise,
    }


def metadata(
    *packages: dict[str, object],
    foreign: dict[str, object] | None = None,
    nodes: list[dict[str, object]] | None = None,
    extra: tuple[dict[str, object], ...] = (),
) -> dict[str, object]:
    """Build a cargo metadata document whose members are the packages given.

    Returns:
        The document, optionally carrying packages that are NOT workspace
        members — dependencies cargo also reports — and a resolve graph:
        the nodes given, or one per package with no edges.

    """
    listed = [*packages, *extra]
    if foreign is not None:
        listed.append(foreign)
    return {
        "workspace_members": [pkg["id"] for pkg in packages],
        "packages": listed,
        "resolve": {
            "nodes": nodes if nodes is not None else [node(p) for p in listed],
        },
    }


def dependency(
    name: str, promise: str | None, version: str = "1.0.0"
) -> dict[str, object]:
    """Build one non-member package as cargo metadata reports it.

    Returns:
        The package object, its id registry-shaped.

    """
    return {
        "id": f"registry+https://github.com/rust-lang/crates.io-index#{name}@{version}",
        "name": name,
        "version": version,
        "rust_version": promise,
    }


def edge(
    pkg: dict[str, object],
    *kinds: str | None,
    target: str | None = None,
    name: str | None = None,
) -> dict[str, object]:
    """Build one resolve edge in the shape cargo 1.97.1 gives.

    Measured on a scratch workspace with path dependencies of every kind:
    a normal edge's kind is null, a build edge's "build", a dev edge's
    "dev"; a `[target.'cfg(windows)'.dependencies]` entry carries its cfg
    in `target`; a renamed dependency's `name` is the rename, with
    hyphens as underscores, and only `pkg` says which package it is; and a
    dependency declared under two kinds is ONE edge with two `dep_kinds`.

    Returns:
        The edge, one `dep_kinds` entry per kind given (normal if none).

    """
    said = kinds or (None,)
    return {
        "name": name or str(pkg["name"]).replace("-", "_"),
        "pkg": pkg["id"],
        "dep_kinds": [{"kind": kind, "target": target} for kind in said],
    }


def node(pkg: dict[str, object], *deps: dict[str, object]) -> dict[str, object]:
    """Build one resolve node.

    Returns:
        The node for pkg, with the edges given.

    """
    return {"id": pkg["id"], "deps": list(deps)}


def floor_file(debian: str) -> Path:
    """Write a floor file holding one Debian rustc package version.

    Returns:
        Its path.

    """
    path = Path(tempfile.mkdtemp()) / "rust-floor.toml"
    path.write_text(
        f'# renovate: debian-stable-rustc\ndebian_stable_rustc = "{debian}"\n',
        encoding="utf-8",
    )
    return path


# Debian 13's, the floor the canon records today (#1850).
TRIXIE = "1.85.1+dfsg1-1+deb13u1"
# A floor at the promise the pin rows below were written for, so they
# keep testing the pin and nothing else.
PIN_ROWS_FLOOR = "1.82.0+dfsg3-1"


def pin(
    version: str, *, active: bool = True, installed: bool = True
) -> dict[str, object]:
    """Build one entry of `mise ls rust --json`.

    Returns:
        The entry, in the shape measured on mise 2026.8.3: every installed
        version is listed, and only the ones this repo's config asks for
        carry `active`.

    """
    return {"version": version, "active": active, "installed": installed}


def typed(packages: list[dict[str, object]]) -> list[msrv_plan.Package]:
    """Decode packages the way the check decodes cargo metadata.

    Returns:
        The packages.

    """
    return [msrv_plan.package(p, decoded.as_str(p["name"], "name")) for p in packages]


def two_minimums() -> dict[str, object]:
    """Build a workspace whose two members derive two different minimums.

    Returns:
        `a` at the pin rows' floor, and `b` at 1.90 because a dependency
        it reaches declares 1.90.

    """
    a, b = member("a", "1.82"), member("b", "1.90")
    needs = dependency("needs", "1.90")
    return metadata(
        a, b, foreign=needs, nodes=[node(a), node(b, edge(needs)), node(needs)]
    )


class ParseVersionTest(unittest.TestCase):
    """Reading a bare version. Mutation: accept any length of parts."""

    def test_a_two_part_version_is_zero_padded(self) -> None:
        """The form every org Cargo.toml actually uses."""
        self.assertEqual(msrv_plan.parse_version("1.82"), (1, 82, 0))

    def test_a_three_part_version_is_taken_as_written(self) -> None:
        """The form mise pins."""
        self.assertEqual(msrv_plan.parse_version("1.82.1"), (1, 82, 1))

    def test_the_two_forms_of_the_same_minimum_are_equal(self) -> None:
        """The whole reason the comparison is not textual."""
        self.assertEqual(
            msrv_plan.parse_version("1.82"),
            msrv_plan.parse_version("1.82.0"),
        )

    def test_a_channel_name_is_not_a_version(self) -> None:
        """The dated nightly every org Rust repo also pins."""
        self.assertIsNone(msrv_plan.parse_version("nightly-2026-07-20"))

    def test_a_range_is_not_a_version(self) -> None:
        """A shape cargo does not accept must not be guessed at."""
        self.assertIsNone(msrv_plan.parse_version(">=1.82"))

    def test_a_single_component_is_not_a_version(self) -> None:
        """`rust-version = "1"` names no toolchain to pin."""
        self.assertIsNone(msrv_plan.parse_version("1"))

    def test_four_components_are_not_a_version(self) -> None:
        """Longer than cargo's grammar is unreadable, not truncatable."""
        self.assertIsNone(msrv_plan.parse_version("1.82.0.1"))

    def test_a_suffixed_version_is_not_a_version(self) -> None:
        """`1.82.0-beta.1` is a channel, and cannot verify a promise."""
        self.assertIsNone(msrv_plan.parse_version("1.82.0-beta.1"))

    def test_a_non_ascii_digit_is_not_a_version(self) -> None:
        """`1.2.²` passes str.isdigit, and int() refuses it (#1555)."""
        self.assertIsNone(msrv_plan.parse_version("1.2.²"))
        self.assertIsNone(msrv_plan.parse_version("\u0661.82"))


class MembersTest(unittest.TestCase):
    """Which packages are the workspace's. Mutation: return all packages."""

    def test_a_dependency_is_not_a_member(self) -> None:
        """Every dependency has a rust-version; none is this repo's promise."""
        doc = metadata(
            member("edtf-core", "1.82"),
            foreign={
                "id": "registry+serde#1.0.229",
                "name": "serde",
                "version": "1.0.229",
                "rust_version": "1.31",
            },
        )
        want: list[str] = ["edtf-core"]
        got = [pkg.name for pkg in msrv_plan.members(doc)]
        self.assertEqual(got, want)

    def test_an_empty_workspace_has_no_members(self) -> None:
        """A manifest cargo reports nothing for is not a silent pass."""
        want: list[msrv_plan.Package] = []
        self.assertEqual(msrv_plan.members({}), want)


class PromisesTest(unittest.TestCase):
    """Grouping the compile surface. Mutation: ignore CLIPPY_EXCLUDE."""

    def test_members_sharing_a_minimum_are_one_group(self) -> None:
        """The org shape: one rust-version in [workspace.package]."""
        groups, unreachable, unreadable = msrv_plan.promises(
            typed([member("edtf-core", "1.82"), member("edtf-cli", "1.82")]),
            set(),
        )
        none: list[str] = []
        want: Groups = {(1, 82, 0): ["edtf-core@1.3.1", "edtf-cli@1.3.1"]}
        self.assertEqual(unreachable, none)
        self.assertEqual(unreadable, none)
        self.assertEqual(groups, want)

    def test_the_two_written_forms_land_in_one_group(self) -> None:
        """A member writing 1.82.0 promises what one writing 1.82 does."""
        groups, _unreachable, _unreadable = msrv_plan.promises(
            typed([member("a", "1.82"), member("b", "1.82.0")]),
            set(),
        )
        want: list[Version] = [(1, 82, 0)]
        got = list(groups)
        self.assertEqual(got, want)

    def test_differing_minimums_are_separate_groups(self) -> None:
        """Each promise is verified at its own toolchain, never the other's."""
        groups, _unreachable, _unreadable = msrv_plan.promises(
            typed([member("a", "1.82"), member("b", "1.90")]),
            set(),
        )
        want: list[Version] = [(1, 82, 0), (1, 90, 0)]
        got = sorted(groups)
        self.assertEqual(got, want)

    def test_a_member_declaring_nothing_is_not_checked(self) -> None:
        """No promise, nothing to keep — and no toolchain to demand."""
        groups, unreachable, _unreadable = msrv_plan.promises(
            typed([member("a"), member("b", None)]),
            set(),
        )
        nothing: Groups = {}
        none: list[str] = []
        self.assertEqual(groups, nothing)
        self.assertEqual(unreachable, none)

    def test_an_excluded_member_is_reported_and_not_checked(self) -> None:
        """edtf-postgres exactly: promises 1.82, gate cannot compile it."""
        groups, unreachable, _unreadable = msrv_plan.promises(
            typed([member("edtf-core", "1.82"), member("edtf-postgres", "1.82")]),
            {"edtf-postgres"},
        )
        want: Groups = {(1, 82, 0): ["edtf-core@1.3.1"]}
        self.assertEqual(groups, want)
        self.assertEqual(len(unreachable), 1)
        self.assertIn("edtf-postgres@1.3.1", unreachable[0])
        self.assertIn("unverified", unreachable[0])

    def test_an_excluded_member_declaring_nothing_says_nothing(self) -> None:
        """The note exists to name an unkept promise, not every exclusion."""
        _groups, unreachable, _unreadable = msrv_plan.promises(
            typed([member("lab-pg")]),
            {"lab-pg"},
        )
        none: list[str] = []
        self.assertEqual(unreachable, none)

    def test_a_declaration_this_cannot_compare_is_refused(self) -> None:
        """Guessing would verify a promise the check never read."""
        _groups, _unreachable, unreadable = msrv_plan.promises(
            typed([member("a", "stable")]),
            set(),
        )
        self.assertEqual(len(unreadable), 1)
        self.assertIn("stable", unreadable[0])


class PinsTest(unittest.TestCase):
    """Reading mise's pins. Mutation: count inactive entries as pinned."""

    def test_an_active_installed_pin_counts(self) -> None:
        """The ordinary state after `mise install`."""
        pinned, absent = msrv_plan.pins(msrv_plan.toolchains([pin("1.82.0")]))
        want: Pinned = {(1, 82, 0): "1.82.0"}
        none: list[str] = []
        self.assertEqual(pinned, want)
        self.assertEqual(absent, none)

    def test_a_toolchain_installed_by_hand_is_not_a_pin(self) -> None:
        """It would pass on this laptop and be absent in CI."""
        pinned, absent = msrv_plan.pins(
            msrv_plan.toolchains([pin("1.82.0", active=False)])
        )
        nothing: Pinned = {}
        none: list[str] = []
        self.assertEqual(pinned, nothing)
        self.assertEqual(absent, none)

    def test_a_declared_pin_that_is_not_installed_is_held_apart(self) -> None:
        """`cargo +1.82.0` would fail; the remedy is install, not edit."""
        pinned, absent = msrv_plan.pins(
            msrv_plan.toolchains([pin("1.82.0", installed=False)])
        )
        nothing: Pinned = {}
        want: list[str] = ["1.82.0"]
        self.assertEqual(pinned, nothing)
        self.assertEqual(absent, want)

    def test_a_nightly_pin_is_not_an_msrv_candidate(self) -> None:
        """Every org Rust repo pins one; none of them verifies a minimum."""
        pinned, _absent = msrv_plan.pins(
            msrv_plan.toolchains([pin("nightly-2026-07-20")])
        )
        nothing: Pinned = {}
        self.assertEqual(pinned, nothing)

    def test_the_stable_pin_can_be_the_msrv_pin(self) -> None:
        """A repo whose minimum IS its build toolchain owes no second entry."""
        pinned, _absent = msrv_plan.pins(
            msrv_plan.toolchains([pin("1.97.1"), pin("nightly-2026-07-20")])
        )
        want: Pinned = {(1, 97, 1): "1.97.1"}
        self.assertEqual(pinned, want)


class MainTest(unittest.TestCase):
    """The whole check. Mutation: emit the plan even when nothing matched."""

    @staticmethod
    def invoke(
        doc: object,
        listing: object,
        exclude: str = "",
        pgrx: str = "",
        floor: str = PIN_ROWS_FLOOR,
    ) -> tuple[int, str, str]:
        """Run the planner over one workspace and one toolchain listing.

        Returns:
            Exit status, stdout and stderr.

        """
        path = Path(tempfile.mkdtemp()) / "toolchains.json"
        path.write_text(
            json.dumps(listing) if listing is not None else "{",
            encoding="utf-8",
        )
        argv = [
            *("--toolchains", str(path), "--floor", str(floor_file(floor))),
            *("--exclude", exclude, "--pgrx", pgrx),
        ]
        out, err = io.StringIO(), io.StringIO()
        stdin = io.StringIO(json.dumps(doc) if doc is not None else "{")
        with (
            mock.patch("sys.stdin", stdin),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            status = msrv_plan.main(argv)
        return status, out.getvalue(), err.getvalue()

    def test_a_pinned_minimum_plans_one_check(self) -> None:
        """The org's own shape: 1.82 declared, 1.82.0 pinned beside the stable."""
        status, out, err = self.invoke(
            metadata(member("edtf-core", "1.82"), member("edtf-cli", "1.82")),
            [pin("1.97.1"), pin("1.82.0"), pin("nightly-2026-07-20")],
        )
        self.assertEqual(status, 0, msg=err)
        self.assertEqual(
            out.strip(),
            "check\t1.82.0\tdefault\tedtf-core@1.3.1 edtf-cli@1.3.1",
        )

    def test_a_promise_no_pin_covers_is_red(self) -> None:
        """Direction one: the state edtf is in today, before the pin lands."""
        status, out, err = self.invoke(
            metadata(member("edtf-core", "1.82")),
            [pin("1.97.1"), pin("nightly-2026-07-20")],
        )
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("Cargo.toml", err)
        self.assertIn("mise.toml", err)
        self.assertIn("1.82.0", err)

    def test_a_pin_moved_off_the_promise_is_red(self) -> None:
        """Direction two: the pin bumped alone, which a green check would hide."""
        status, out, err = self.invoke(
            metadata(member("edtf-core", "1.82")),
            [pin("1.97.1"), pin("1.85.0")],
        )
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("1.85.0", err)
        self.assertIn("the same toolchain", err)

    def test_a_patch_apart_is_not_the_same_promise(self) -> None:
        """1.82 promises 1.82.0; a green run at 1.82.1 answers nothing."""
        status, _out, err = self.invoke(
            metadata(member("edtf-core", "1.82")),
            [pin("1.82.1")],
        )
        self.assertEqual(status, 1)
        self.assertIn("1.82.0", err)

    def test_a_pin_that_is_not_installed_is_red_and_says_so(self) -> None:
        """Otherwise the remedy printed would be an edit that changes nothing."""
        status, _out, err = self.invoke(
            metadata(member("edtf-core", "1.82")),
            [pin("1.82.0", installed=False)],
        )
        self.assertEqual(status, 1)
        self.assertIn("not installed", err)

    def test_every_promise_excluded_plans_notes_and_no_check(self) -> None:
        """A repo whose only crate the gate cannot compile."""
        status, out, err = self.invoke(
            metadata(member("lab-pg", "1.82")),
            [pin("1.97.1")],
            exclude="lab-pg",
        )
        self.assertEqual(status, 0, msg=err)
        self.assertTrue(out.startswith("note\t"))
        self.assertNotIn("check\t", out)

    def test_an_excluded_promise_rides_beside_a_checked_one(self) -> None:
        """Both records in one plan, which is edtf after this lands."""
        status, out, err = self.invoke(
            metadata(member("edtf-core", "1.82"), member("edtf-postgres", "1.82")),
            [pin("1.82.0")],
            exclude="edtf-postgres",
        )
        self.assertEqual(status, 0, msg=err)
        lines = out.strip().splitlines()
        self.assertTrue(lines[0].startswith("note\t"))
        self.assertEqual(lines[1], "check\t1.82.0\tdefault\tedtf-core@1.3.1")

    def test_two_minimums_plan_two_checks(self) -> None:
        """Each at its own pin; neither verified by the other's toolchain."""
        status, out, err = self.invoke(
            two_minimums(),
            [pin("1.82.0"), pin("1.90.0")],
        )
        want = ["check\t1.82.0\tdefault\ta@1.3.1", "check\t1.90.0\tdefault\tb@1.3.1"]
        self.assertEqual(status, 0, msg=err)
        self.assertEqual(out.strip().splitlines(), want)

    def test_one_uncovered_minimum_reds_the_whole_plan(self) -> None:
        """A partial plan would compile half a promise and report success."""
        status, out, _err = self.invoke(
            two_minimums(),
            [pin("1.82.0")],
        )
        self.assertEqual(status, 1)
        self.assertEqual(out, "")

    def test_a_pgrx_member_is_checked_with_its_own_features(self) -> None:
        """#813: --all-features cannot compile a crate whose features fight."""
        status, out, err = self.invoke(
            metadata(member("edtf-core", "1.82"), member("edtf-postgres", "1.82")),
            [pin("1.82.0")],
            pgrx="edtf-postgres:18",
        )
        want = [
            "check\t1.82.0\tdefault\tedtf-core@1.3.1",
            (
                "check\t1.82.0\t--no-default-features --features pg18\t"
                "edtf-postgres@1.3.1"
            ),
        ]
        self.assertEqual(status, 0, msg=err)
        self.assertEqual(out.strip().splitlines(), want)

    def test_a_workspace_of_nothing_but_an_extension_still_plans(self) -> None:
        """The plain record must not be emitted empty."""
        status, out, err = self.invoke(
            metadata(member("ext", "1.82")),
            [pin("1.82.0")],
            pgrx="ext:17",
        )
        self.assertEqual(status, 0, msg=err)
        self.assertEqual(
            out.strip(),
            "check\t1.82.0\t--no-default-features --features pg17\text@1.3.1",
        )

    def test_a_pgrx_name_with_no_major_is_ignored(self) -> None:
        """A malformed pair must not silently drop a crate from the plan."""
        nothing: dict[str, str] = {}
        self.assertEqual(msrv_plan.features("ext:"), nothing)
        self.assertEqual(msrv_plan.features(":18"), nothing)
        self.assertEqual(msrv_plan.features(""), nothing)
        self.assertEqual(msrv_plan.features("ext:¹⁸"), nothing)

    def test_a_declaration_this_cannot_compare_is_red(self) -> None:
        """Refuse rather than verify a promise the check never read."""
        status, _out, err = self.invoke(
            metadata(member("a", "stable")),
            [pin("1.97.1")],
        )
        self.assertEqual(status, 1)
        self.assertIn("cannot compare", err)

    def test_a_document_of_the_wrong_shape_is_red_by_where(self) -> None:
        """A wrong-shaped account or listing is named, never a crash."""
        good = metadata(member("a", "1.82"))
        cases: tuple[tuple[object, object, str], ...] = (
            ([], [pin("1.82.0")], "cargo metadata: cargo metadata is not an object"),
            (
                {"workspace_members": [1]},
                [pin("1.82.0")],
                "cargo metadata: workspace_members is not a list of strings",
            ),
            (
                {"workspace_members": ["x"], "packages": [1]},
                [pin("1.82.0")],
                "cargo metadata: packages[0] is not an object",
            ),
            (
                metadata(member("a", "1.82") | {"rust_version": 1.82}),
                [pin("1.82.0")],
                "cargo metadata: packages[0].rust_version is not a string",
            ),
            (good, {}, "`mise ls rust`: the listing is not a list"),
            (good, ["1.82.0"], "`mise ls rust`: listing[0] is not an object"),
        )
        for doc, listing, said in cases:
            with self.subTest(said=said):
                status, out, err = self.invoke(doc, listing)
                self.assertEqual((1, "", f"msrv-plan: {said}\n"), (status, out, err))

    def test_metadata_that_is_not_json_is_red(self) -> None:
        """A broken cargo must not read as a workspace with no promises."""
        status, _out, err = self.invoke(None, [pin("1.97.1")])
        self.assertEqual(status, 1)
        self.assertIn("did not give JSON", err)

    def test_a_toolchain_listing_that_is_not_json_is_red(self) -> None:
        """Same for mise: no listing is not the same as no pins."""
        status, _out, err = self.invoke(metadata(member("a", "1.82")), None)
        self.assertEqual(status, 1)
        self.assertIn("did not give JSON", err)


# The workspace the edge filter was measured on (#1850): cargo 1.97.1's
# `cargo metadata --format-version 1 --locked --all-features` over two
# members and path dependencies of every kind, reproduced here edge for
# edge. Each dependency declares a different number, so the derived one
# says which edge was followed.
APP = member("app", "1.85", "0.1.0")
TOOL = member("tool", "1.85", "0.1.0")
PLAIN = dependency("plain", "1.70")
DEEP = dependency("deep", "1.87", "0.1.0")
RENAMED = dependency("renamed", "1.80", "2.1.0")
OPTIONAL = dependency("optional", "1.88", "0.5.0")
UNDECLARED = dependency("undeclared", None, "0.2.0")
WINONLY = dependency("winonly", "1.90", "0.3.0")
BUILDER = dependency("builder", "1.86", "0.9.0")
DEVONLY = dependency("devonly", "1.99", "4.0.0")
SHAPES = (PLAIN, DEEP, RENAMED, OPTIONAL, UNDECLARED, WINONLY, BUILDER, DEVONLY)
APP_EDGES = {
    "plain": edge(PLAIN),
    "renamed": edge(RENAMED, name="other_name"),
    "optional": edge(OPTIONAL),
    "undeclared": edge(UNDECLARED),
    "tool": edge(TOOL),
    "winonly": edge(WINONLY, target="cfg(windows)"),
    "builder": edge(BUILDER, "build"),
    "devonly": edge(DEVONLY, "dev"),
}
# A floor below every dependency above, so the derived number is always
# a dependency's and says which edge was followed.
LOW = ((1, 60, 0), "1.60.0+dfsg1-1")


def shapes(
    *keep: str,
    tool: tuple[dict[str, object], ...] = (),
    more: tuple[dict[str, object], ...] = (),
    devonly: tuple[dict[str, object], ...] = (),
) -> dict[str, object]:
    """Build the measured workspace with only some of app's edges.

    Returns:
        The metadata document: app keeps the named edges (all when none
        are named); tool has its dev edge and the edges given; devonly has
        the edges given; `more` are further non-member packages.

    """
    chosen = [e for k, e in APP_EDGES.items() if not keep or k in keep]
    nodes = [
        node(APP, *chosen),
        node(TOOL, edge(DEVONLY, "dev"), *tool),
        node(PLAIN, edge(DEEP)),
        node(DEVONLY, *devonly),
        *(node(p) for p in (*SHAPES, *more) if p["name"] not in {"plain", "devonly"}),
    ]
    return metadata(APP, TOOL, nodes=nodes, extra=(*SHAPES, *more))


def derived(
    doc: dict[str, object], name: str, floor: msrv_plan.Floor | None = None
) -> msrv_plan.Derived:
    """Derive one member of a document the way the check does.

    Returns:
        What the rule derives for the member named.

    """
    workspace = msrv_plan.members(doc)
    packages, edges = msrv_plan.graph(doc)
    ours = {pkg.id for pkg in workspace}
    outside = {i: p for i, p in packages.items() if i not in ours}
    start = next(pkg.id for pkg in workspace if pkg.name == name)
    return msrv_plan.derive(start, outside, edges, floor or msrv_plan.Floor(*LOW))


class EdgeFilterTest(unittest.TestCase):
    """Which edges the derivation follows, on the measured shapes.

    Mutations, each observed red: follow dev edges (four rows), follow
    only untargeted edges (two), key edges by `name` rather than `pkg`
    (ten here, one in RuleTest, one in MainTest), stop at the first hop
    (two).
    """

    def test_every_kind_together_derives_the_highest_followed(self) -> None:
        """The measured workspace: winonly's 1.90, never devonly's 1.99."""
        got = derived(shapes(), "app")
        self.assertEqual((got.version, got.written), ((1, 90, 0), "1.90"))
        self.assertEqual(got.reason, "`winonly 0.3.0` requires it")

    def test_a_target_specific_dependency_counts(self) -> None:
        """A cfg(windows) dependency is part of the promise to Windows users."""
        self.assertEqual(derived(shapes("winonly"), "app").version, (1, 90, 0))

    def test_a_dev_only_dependency_does_not_count(self) -> None:
        """Tool's one dependency is dev: nothing is reached, the floor holds."""
        got = derived(shapes(), "tool")
        self.assertEqual(got.version, (1, 60, 0))
        self.assertIn("the floor", got.reason)

    def test_a_dev_edge_is_not_walked_through(self) -> None:
        """What a dev dependency needs is no more the promise than it is."""
        heavy = dependency("heavy", "1.98")
        doc = shapes("devonly", more=(heavy,), devonly=(edge(heavy),))
        self.assertEqual(derived(doc, "app").version, (1, 60, 0))

    def test_a_renamed_dependency_counts_as_the_package_it_is(self) -> None:
        """`other_name` is only the extern name; the finding names renamed."""
        got = derived(shapes("renamed"), "app")
        self.assertEqual(got.reason, "`renamed 2.1.0` requires it")

    def test_an_optional_dependency_the_features_enable_counts(self) -> None:
        """--all-features put the edge in the graph, so it is followed."""
        self.assertEqual(derived(shapes("optional"), "app").version, (1, 88, 0))

    def test_a_build_dependency_counts(self) -> None:
        """A build script is compiled by the consumer's toolchain too."""
        self.assertEqual(derived(shapes("builder"), "app").version, (1, 86, 0))

    def test_a_transitive_dependency_counts(self) -> None:
        """Plain declares 1.70 and reaches deep at 1.87."""
        got = derived(shapes("plain"), "app")
        self.assertEqual(got.reason, "`deep 0.1.0` requires it")

    def test_an_edge_both_dev_and_normal_counts(self) -> None:
        """Cargo gives one edge two kinds; the normal one decides."""
        both = edge(DEVONLY, "dev", None)
        doc = metadata(APP, foreign=DEVONLY, nodes=[node(APP, both), node(DEVONLY)])
        self.assertEqual(derived(doc, "app").version, (1, 99, 0))

    def test_an_undeclared_dependency_moves_nothing(self) -> None:
        """A dependency that declares nothing is held back by the compile."""
        self.assertEqual(derived(shapes("undeclared"), "app").version, (1, 60, 0))

    def test_a_member_contributes_its_dependencies_not_its_number(self) -> None:
        """The member app reaches tool, whose own 1.85 is no dependency's."""
        self.assertEqual(derived(shapes("tool"), "app").version, (1, 60, 0))
        via = dependency("via", "1.95")
        raised = shapes("tool", tool=(edge(via),), more=(via,))
        self.assertEqual(derived(raised, "app").version, (1, 95, 0))

    def test_a_tie_names_the_same_dependency_every_run(self) -> None:
        """Two at the highest number: the lower name, whatever the order."""
        one, two = dependency("zeta", "1.90"), dependency("alpha", "1.90")
        for order in ((one, two), (two, one)):
            with self.subTest(first=order[0]["name"]):
                doc = metadata(
                    APP,
                    nodes=[node(APP, *(edge(d) for d in order)), node(one), node(two)],
                    extra=order,
                )
                got = derived(doc, "app").reason
                self.assertEqual(got, "`alpha 1.0.0` requires it")

    def test_the_floor_wins_a_tie_with_a_dependency(self) -> None:
        """The floor holds the number whatever the dependency says."""
        doc = metadata(APP, foreign=PLAIN, nodes=[node(APP, edge(PLAIN)), node(PLAIN)])
        floor = msrv_plan.Floor((1, 70, 0), "1.70.0+dfsg1-1")
        self.assertIn("the floor", derived(doc, "app", floor).reason)

    def test_a_kind_cargo_did_not_have_is_refused(self) -> None:
        """Following or dropping it unseen would move every number."""
        doc = metadata(
            APP, foreign=PLAIN, nodes=[node(APP, edge(PLAIN, "proc")), node(PLAIN)]
        )
        with self.assertRaisesRegex(decoded.UndecodableError, "'proc' is not one"):
            msrv_plan.graph(doc)

    def test_an_edge_with_no_kinds_is_refused(self) -> None:
        """Pre-1.41 cargo gave none; this cannot tell what it is."""
        bare = {"name": "plain", "pkg": PLAIN["id"], "dep_kinds": []}
        doc = metadata(APP, foreign=PLAIN, nodes=[node(APP, bare), node(PLAIN)])
        with self.assertRaisesRegex(decoded.UndecodableError, "dep_kinds is empty"):
            msrv_plan.graph(doc)

    def test_a_reached_declaration_that_is_not_a_version_is_refused(self) -> None:
        """Cargo would not have accepted it; guessing would move the number."""
        odd = dependency("odd", "1.x")
        doc = metadata(APP, foreign=odd, nodes=[node(APP, edge(odd)), node(odd)])
        with self.assertRaisesRegex(decoded.UndecodableError, "odd 1.0.0 declares"):
            derived(doc, "app")


class FloorTest(unittest.TestCase):
    """Reading the floor file.

    Mutation: take the whole upstream version as the floor, patch and all
    (two rows red, two more in RuleTest).
    """

    def test_debian_stables_rustc_is_its_major_minor(self) -> None:
        """Debian 13's package: the revision and repack say nothing."""
        got = msrv_plan.read_floor(floor_file(TRIXIE).read_text(encoding="utf-8"))
        self.assertEqual(got, msrv_plan.Floor((1, 85, 0), TRIXIE))

    def test_the_forms_debian_writes_are_read(self) -> None:
        """An epoch, a tilde, a bare upstream: all name one compiler."""
        for debian, want in (
            ("1:1.85.1+dfsg1-1", (1, 85, 0)),
            ("1.90.0~beta.1-1", (1, 90, 0)),
            ("1.85.1", (1, 85, 0)),
        ):
            with self.subTest(debian=debian):
                text = f'debian_stable_rustc = "{debian}"'
                self.assertEqual(msrv_plan.read_floor(text).version, want)

    def test_what_is_not_a_rustc_package_version_is_refused(self) -> None:
        """A floor this cannot read must not become a floor of zero."""
        for text, said in (
            ('debian_stable_rustc = "1.85"', "not a Debian rustc"),
            ('debian_stable_rustc = "rustc-1.85.1"', "not a Debian rustc"),
            ('debian_stable_rustc = "1.85.1 trixie"', "not a Debian rustc"),
            ("rustc = 1", "debian_stable_rustc is not a string"),
            ("debian_stable_rustc = ", ""),
        ):
            with (
                self.subTest(text=text),
                self.assertRaisesRegex(decoded.UndecodableError, said),
            ):
                msrv_plan.read_floor(text)

    def test_the_canon_floor_file_reads(self) -> None:
        """The tracked file itself, so a Renovate bump cannot break it."""
        text = (Path(__file__).parent / "rust-floor.toml").read_text(encoding="utf-8")
        self.assertGreaterEqual(msrv_plan.read_floor(text).version, (1, 85, 0))


def converted(promise: str, *deps: dict[str, object]) -> dict[str, object]:
    """Build one member reaching each dependency given directly.

    Returns:
        The metadata document.

    """
    core = member("edtf-core", promise, "1.3.5")
    nodes = [node(core, *(edge(d) for d in deps)), *(node(d) for d in deps)]
    return metadata(core, nodes=nodes, extra=deps)


SERDE = dependency("serde_derive", "1.71", "1.0.228")
WASM_BINDGEN = dependency("wasm-bindgen", "1.81", "0.2.130")
PGRX = dependency("pgrx", "1.96", "0.19.3")


class RuleTest(unittest.TestCase):
    """The promise is exactly the derived number (#1850).

    Mutations, each observed red: hold the promise to at least the
    derived number rather than exactly (one row), drop the floor term
    (three here, one in EdgeFilterTest), drop the dependency term (one
    here, nine in EdgeFilterTest, one in MainTest), count a reached
    member's own number (one).
    """

    invoke = staticmethod(MainTest.invoke)

    def test_promise_below_the_floor_is_a_finding(self) -> None:
        """Before #1883 edtf promised 1.82, which no distribution ships."""
        status, out, err = self.invoke(
            converted("1.82", SERDE), [pin("1.82.0")], floor=TRIXIE
        )
        self.assertEqual((status, out), (1, ""))
        self.assertIn(
            "edtf-core@1.3.5 declares rust-version 1.82; the rule derives 1.85: "
            f"the floor: Debian stable's rustc is {TRIXIE}",
            err,
        )
        self.assertIn("never chosen", err)
        self.assertIn("missing_const_for_fn", err)

    def test_promise_above_the_floor_with_no_dependency_reason_is_a_finding(
        self,
    ) -> None:
        """release-lab before #1883: 1.97 for a crate needing 1.81."""
        status, out, err = self.invoke(
            converted("1.97", WASM_BINDGEN), [pin("1.97.0")], floor=TRIXIE
        )
        self.assertEqual((status, out), (1, ""))
        self.assertIn(
            "declares rust-version 1.97; the rule derives 1.85: the floor", err
        )

    def test_promise_raised_by_a_dependency_passes_and_names_it(self) -> None:
        """lab-pg: 1.96 because pgrx 0.19.3 declares it, and says so."""
        status, out, err = self.invoke(
            converted("1.96", SERDE, PGRX), [pin("1.96.0")], floor=TRIXIE
        )
        self.assertEqual(status, 0, msg=err)
        self.assertEqual(out.strip(), "check\t1.96.0\tdefault\tedtf-core@1.3.5")
        floor = msrv_plan.Floor((1, 85, 0), TRIXIE)
        self.assertEqual(
            derived(converted("1.96", SERDE, PGRX), "edtf-core", floor).reason,
            "`pgrx 0.19.3` requires it",
        )
        status, _out, err = self.invoke(
            converted("1.85", SERDE, PGRX), [pin("1.85.0")], floor=TRIXIE
        )
        self.assertEqual(status, 1)
        self.assertIn("the rule derives 1.96: `pgrx 0.19.3` requires it", err)

    def test_promise_at_the_floor_passes(self) -> None:
        """The edtf crates after #1883: 1.85, every dependency declaring less."""
        status, out, err = self.invoke(
            converted("1.85", SERDE, WASM_BINDGEN), [pin("1.85.0")], floor=TRIXIE
        )
        self.assertEqual(status, 0, msg=err)
        self.assertEqual(out.strip(), "check\t1.85.0\tdefault\tedtf-core@1.3.5")

    def test_compile_failure_at_the_derived_number_names_the_rule(self) -> None:
        """Item 4: the red is the rule working, and the remedy says so."""
        err = io.StringIO()
        with redirect_stderr(err):
            status = msrv_plan.main(["--failed", "1.85.0", "edtf-core@1.3.5"])
        said = err.getvalue()
        self.assertEqual(status, 1)
        self.assertIn("edtf-core@1.3.5 failed to compile at 1.85.0,", said)
        self.assertIn("the rust-version the rule derives", said)
        self.assertIn("the crate may not raise it", said)
        self.assertIn("declares less than it\n  needs, and is held back", said)

    def test_an_excluded_member_declaring_another_number_is_red(self) -> None:
        """#1909: CLIPPY_EXCLUDE keeps a crate out of the compile, not the rule."""
        status, out, err = self.invoke(
            converted("1.82"), [pin("1.85.0")], exclude="edtf-core", floor=TRIXIE
        )
        self.assertEqual((status, out), (1, ""))
        self.assertIn(
            "edtf-core@1.3.5 declares rust-version 1.82; the rule derives 1.85: "
            f"the floor: Debian stable's rustc is {TRIXIE}",
            err,
        )

    def test_an_excluded_member_at_its_number_is_noted_not_checked(self) -> None:
        """Held to the rule, it is still unverified at the number, and says so."""
        status, out, err = self.invoke(
            converted("1.85"), [pin("1.85.0")], exclude="edtf-core", floor=TRIXIE
        )
        self.assertEqual(status, 0, msg=err)
        self.assertTrue(out.startswith("note\t"))
        self.assertIn("unverified", out)
        self.assertNotIn("check\t", out)

    def test_a_member_declaring_nothing_is_red_at_the_floor(self) -> None:
        """#1909: leaving the field out is not a way to make no promise."""
        core, app = member("edtf-core", "1.85", "1.3.5"), member("app")
        doc = metadata(
            core,
            app,
            nodes=[node(core, edge(SERDE)), node(app, edge(SERDE)), node(SERDE)],
            extra=(SERDE,),
        )
        status, out, err = self.invoke(doc, [pin("1.85.0")], floor=TRIXIE)
        self.assertEqual((status, out), (1, ""))
        self.assertIn(
            "app@1.3.1 declares no rust-version; the rule derives 1.85: the floor: "
            f"Debian stable's rustc is {TRIXIE}",
            err,
        )
        self.assertNotIn("edtf-core", err)
        self.assertIn("a crate declaring none included", err)

    def test_a_member_declaring_nothing_is_red_at_its_dependency(self) -> None:
        """#1909: the number named is the derived one, dependency and all."""
        status, out, err = self.invoke(
            converted("", SERDE, PGRX), [pin("1.96.0")], floor=TRIXIE
        )
        self.assertEqual((status, out), (1, ""))
        self.assertIn(
            "edtf-core@1.3.5 declares no rust-version; the rule derives 1.96: "
            "`pgrx 0.19.3` requires it",
            err,
        )

    def test_a_workspace_declaring_nothing_is_red_per_member(self) -> None:
        """#1909: once the skip "no crate declares rust-version"."""
        status, out, err = self.invoke(
            metadata(member("stele-core"), member("stele-cli")),
            [pin("1.97.1")],
            floor=TRIXIE,
        )
        self.assertEqual((status, out), (1, ""))
        said = [line for line in err.splitlines() if "declares no" in line]
        self.assertEqual(
            said,
            [
                f"  {name}@1.3.1 declares no rust-version; the rule derives 1.85: "
                f"the floor: Debian stable's rustc is {TRIXIE}"
                for name in ("stele-core", "stele-cli")
            ],
        )

    def test_a_member_reached_through_another_lends_no_number(self) -> None:
        """lab-cli reaches lab-core; only lab-core's dependencies count.

        Here the reached member promises 1.99 and nothing it reaches
        declares more than the floor, so it is the one finding: were its
        own number counted, app would derive 1.99 and be a second.
        """
        app, ext = member("app", "1.85"), member("ext", "1.99")
        doc = metadata(app, ext, nodes=[node(app, edge(ext)), node(ext)])
        status, out, err = self.invoke(doc, [pin("1.85.0")], floor=TRIXIE)
        self.assertEqual((status, out), (1, ""))
        self.assertIn("ext@1.3.1 declares rust-version 1.99; the rule derives", err)
        self.assertNotIn("app@", err)

    def test_metadata_without_a_resolve_graph_is_red(self) -> None:
        """`--no-deps` metadata would derive every number as the floor."""
        doc = converted("1.85")
        doc.pop("resolve")
        status, _out, err = self.invoke(doc, [pin("1.85.0")], floor=TRIXIE)
        self.assertEqual(status, 1)
        self.assertIn("no resolve graph", err)

    def test_an_unreadable_floor_file_is_red_and_named(self) -> None:
        """No floor is not a floor of zero."""
        status, _out, err = self.invoke(
            converted("1.85"), [pin("1.85.0")], floor="1.85"
        )
        self.assertEqual(status, 1)
        self.assertIn("the floor file", err)

    def test_planning_without_the_floor_is_refused(self) -> None:
        """The rule has no opt-out, so neither has the command line."""
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            msrv_plan.main(["--toolchains", "x"])


if __name__ == "__main__":
    unittest.main()
