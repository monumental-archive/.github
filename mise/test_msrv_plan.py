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

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "msrv_plan",
    Path(__file__).with_name("msrv-plan.py"),
)
msrv_plan = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(msrv_plan)


def member(name: str, promise: str | None = None, version: str = "1.3.1") -> dict:
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


def metadata(*packages: dict, foreign: dict | None = None) -> dict:
    """Build a cargo metadata document whose members are the packages given.

    Returns:
        The document, optionally carrying one package that is NOT a
        workspace member — a dependency cargo also reports.

    """
    doc = {
        "workspace_members": [pkg["id"] for pkg in packages],
        "packages": list(packages),
    }
    if foreign is not None:
        doc["packages"].append(foreign)
    return doc


def pin(version: str, *, active: bool = True, installed: bool = True) -> dict:
    """Build one entry of `mise ls rust --json`.

    Returns:
        The entry, in the shape measured on mise 2026.8.3: every installed
        version is listed, and only the ones this repo's config asks for
        carry `active`.

    """
    return {"version": version, "active": active, "installed": installed}


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
        self.assertEqual([pkg["name"] for pkg in msrv_plan.members(doc)], ["edtf-core"])

    def test_an_empty_workspace_has_no_members(self) -> None:
        """A manifest cargo reports nothing for is not a silent pass."""
        self.assertEqual(msrv_plan.members({}), [])


class PromisesTest(unittest.TestCase):
    """Grouping the compile surface. Mutation: ignore CLIPPY_EXCLUDE."""

    def test_members_sharing_a_minimum_are_one_group(self) -> None:
        """The org shape: one rust-version in [workspace.package]."""
        groups, unreachable, unreadable = msrv_plan.promises(
            [member("edtf-core", "1.82"), member("edtf-cli", "1.82")],
            set(),
        )
        self.assertEqual(unreachable, [])
        self.assertEqual(unreadable, [])
        self.assertEqual(
            groups,
            {(1, 82, 0): ["edtf-core@1.3.1", "edtf-cli@1.3.1"]},
        )

    def test_the_two_written_forms_land_in_one_group(self) -> None:
        """A member writing 1.82.0 promises what one writing 1.82 does."""
        groups, _unreachable, _unreadable = msrv_plan.promises(
            [member("a", "1.82"), member("b", "1.82.0")],
            set(),
        )
        self.assertEqual(list(groups), [(1, 82, 0)])

    def test_differing_minimums_are_separate_groups(self) -> None:
        """Each promise is verified at its own toolchain, never the other's."""
        groups, _unreachable, _unreadable = msrv_plan.promises(
            [member("a", "1.82"), member("b", "1.90")],
            set(),
        )
        self.assertEqual(sorted(groups), [(1, 82, 0), (1, 90, 0)])

    def test_a_member_declaring_nothing_is_not_checked(self) -> None:
        """No promise, nothing to keep — and no toolchain to demand."""
        groups, unreachable, _unreadable = msrv_plan.promises(
            [member("a"), member("b", None)],
            set(),
        )
        self.assertEqual(groups, {})
        self.assertEqual(unreachable, [])

    def test_an_excluded_member_is_reported_and_not_checked(self) -> None:
        """edtf-postgres exactly: promises 1.82, gate cannot compile it."""
        groups, unreachable, _unreadable = msrv_plan.promises(
            [member("edtf-core", "1.82"), member("edtf-postgres", "1.82")],
            {"edtf-postgres"},
        )
        self.assertEqual(groups, {(1, 82, 0): ["edtf-core@1.3.1"]})
        self.assertEqual(len(unreachable), 1)
        self.assertIn("edtf-postgres@1.3.1", unreachable[0])
        self.assertIn("unverified", unreachable[0])

    def test_an_excluded_member_declaring_nothing_says_nothing(self) -> None:
        """The note exists to name an unkept promise, not every exclusion."""
        _groups, unreachable, _unreadable = msrv_plan.promises(
            [member("lab-pg")],
            {"lab-pg"},
        )
        self.assertEqual(unreachable, [])

    def test_a_declaration_this_cannot_compare_is_refused(self) -> None:
        """Guessing would verify a promise the check never read."""
        _groups, _unreachable, unreadable = msrv_plan.promises(
            [member("a", "stable")],
            set(),
        )
        self.assertEqual(len(unreadable), 1)
        self.assertIn("stable", unreadable[0])


class PinsTest(unittest.TestCase):
    """Reading mise's pins. Mutation: count inactive entries as pinned."""

    def test_an_active_installed_pin_counts(self) -> None:
        """The ordinary state after `mise install`."""
        pinned, absent = msrv_plan.pins([pin("1.82.0")])
        self.assertEqual(pinned, {(1, 82, 0): "1.82.0"})
        self.assertEqual(absent, [])

    def test_a_toolchain_installed_by_hand_is_not_a_pin(self) -> None:
        """It would pass on this laptop and be absent in CI."""
        pinned, absent = msrv_plan.pins([pin("1.82.0", active=False)])
        self.assertEqual(pinned, {})
        self.assertEqual(absent, [])

    def test_a_declared_pin_that_is_not_installed_is_held_apart(self) -> None:
        """`cargo +1.82.0` would fail; the remedy is install, not edit."""
        pinned, absent = msrv_plan.pins([pin("1.82.0", installed=False)])
        self.assertEqual(pinned, {})
        self.assertEqual(absent, ["1.82.0"])

    def test_a_nightly_pin_is_not_an_msrv_candidate(self) -> None:
        """Every org Rust repo pins one; none of them verifies a minimum."""
        pinned, _absent = msrv_plan.pins([pin("nightly-2026-07-20")])
        self.assertEqual(pinned, {})

    def test_the_stable_pin_can_be_the_msrv_pin(self) -> None:
        """A repo whose minimum IS its build toolchain owes no second entry."""
        pinned, _absent = msrv_plan.pins([pin("1.97.1"), pin("nightly-2026-07-20")])
        self.assertEqual(pinned, {(1, 97, 1): "1.97.1"})


class MainTest(unittest.TestCase):
    """The whole check. Mutation: emit the plan even when nothing matched."""

    @staticmethod
    def invoke(doc: object, listing: object, exclude: str = "") -> tuple:
        """Run the planner over one workspace and one toolchain listing.

        Returns:
            Exit status, stdout and stderr.

        """
        path = Path(tempfile.mkdtemp()) / "toolchains.json"
        path.write_text(
            json.dumps(listing) if listing is not None else "{",
            encoding="utf-8",
        )
        argv = ["--toolchains", str(path), "--exclude", exclude]
        out, err = io.StringIO(), io.StringIO()
        original = msrv_plan.sys.stdin
        msrv_plan.sys.stdin = io.StringIO(
            json.dumps(doc) if doc is not None else "{",
        )
        try:
            with redirect_stdout(out), redirect_stderr(err):
                status = msrv_plan.main(argv)
        finally:
            msrv_plan.sys.stdin = original
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
            "check\t1.82.0\tedtf-core@1.3.1 edtf-cli@1.3.1",
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

    def test_no_declaration_anywhere_plans_nothing(self) -> None:
        """The skip the task turns into "no crate declares rust-version"."""
        status, out, err = self.invoke(
            metadata(member("stele-core"), member("stele-cli")),
            [pin("1.97.1")],
        )
        self.assertEqual(status, 0, msg=err)
        self.assertEqual(out.strip(), "")

    def test_every_promise_excluded_plans_notes_and_no_check(self) -> None:
        """A repo whose only declaring crate the gate cannot compile."""
        status, out, err = self.invoke(
            metadata(member("lab-core"), member("lab-pg", "1.82")),
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
        self.assertEqual(lines[1], "check\t1.82.0\tedtf-core@1.3.1")

    def test_two_minimums_plan_two_checks(self) -> None:
        """Each at its own pin; neither verified by the other's toolchain."""
        status, out, err = self.invoke(
            metadata(member("a", "1.82"), member("b", "1.90")),
            [pin("1.82.0"), pin("1.90.0")],
        )
        self.assertEqual(status, 0, msg=err)
        self.assertEqual(
            out.strip().splitlines(),
            ["check\t1.82.0\ta@1.3.1", "check\t1.90.0\tb@1.3.1"],
        )

    def test_one_uncovered_minimum_reds_the_whole_plan(self) -> None:
        """A partial plan would compile half a promise and report success."""
        status, out, _err = self.invoke(
            metadata(member("a", "1.82"), member("b", "1.90")),
            [pin("1.82.0")],
        )
        self.assertEqual(status, 1)
        self.assertEqual(out, "")

    def test_a_declaration_this_cannot_compare_is_red(self) -> None:
        """Refuse rather than verify a promise the check never read."""
        status, _out, err = self.invoke(
            metadata(member("a", "stable")),
            [pin("1.97.1")],
        )
        self.assertEqual(status, 1)
        self.assertIn("cannot compare", err)

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


if __name__ == "__main__":
    unittest.main()
