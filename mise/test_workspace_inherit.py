#!/usr/bin/env python3
"""Table tests for the workspace-inheritance check, guard branches first.

Every fixture here is shaped from a MEASURED cargo account, never from
what cargo's documentation implies. A hand-written fixture proves only
that the code agrees with the fixture, and three of the four resolution
shapes below would have been guessed wrong — each one a false RED on a
repository that had done nothing at all.

Measured 2026-08-24 with `cargo metadata --no-deps --format-version 1`
against a purpose-built two-member workspace (one member inheriting every
key, one inheriting none), and against release-lab and edtf:

    declared                     inheriting member    member taking none
    version/edition/license/…    the value verbatim   own value, or None
    authors/keywords/categories  the list verbatim    []  (not None)
    readme = "README.md"         "../../README.md"    None
    readme = true                "../../README.md"    None
    publish = false              []                   []   <- identical
    publish = true               None                 []

The `[]` row is why "every member reports null" is not the test, the
rebased-path row is why value equality alone is not the test, and the
two `publish` rows are why publish gets no verdict at all.

Run through the gate as `mise run test`, which `ci` collects.
"""

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "workspace_inherit",
    Path(__file__).with_name("workspace-inherit.py"),
)
wi = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(wi)

ROOT = Path("/ws")

# `readme = true` and `readme = false` as TOML hands them over: a
# declared VALUE the check resolves, never a flag on the call.
DISCOVER = True
NO_README = False


def member(name: str, **fields: object) -> dict:
    """Build one package as cargo metadata reports it.

    Returns:
        A package dict carrying a manifest two directories down, which
        is the layout every workspace in the org uses.

    """
    return {
        "name": name,
        "id": f"path+file:///ws/crates/{name}#0.1.0",
        "manifest_path": f"/ws/crates/{name}/Cargo.toml",
        **fields,
    }


class TestInherits(unittest.TestCase):
    """The one question this check asks, per key and per member."""

    def test_a_verbatim_value_is_taken(self) -> None:
        """The ordinary shape: cargo reports the declaration unchanged."""
        taker = member("taker", rust_version="1.82")
        self.assertEqual(wi.inherits("rust-version", "1.82", taker, ROOT), True)

    def test_a_member_with_its_own_value_takes_nothing(self) -> None:
        """A different value is a different declaration, not inheritance."""
        own = member("own", edition="2018")
        self.assertEqual(wi.inherits("edition", "2021", own, ROOT), False)

    def test_an_absent_field_takes_nothing(self) -> None:
        """release-lab's live case: rust_version None on all four."""
        bare = member("bare", rust_version=None)
        self.assertEqual(wi.inherits("rust-version", "1.97", bare, ROOT), False)

    def test_a_list_valued_key_reports_empty_not_null(self) -> None:
        """Measured: a member taking no `keywords` reports `[]`.

        So an inertness test written as "every member reports null"
        would call this key taken and never fire.
        """
        rows = [member("bare", keywords=[]), member("taker", keywords=["k"])]
        self.assertEqual(
            [wi.inherits("keywords", ["k"], row, ROOT) for row in rows],
            [False, True],
        )

    def test_a_path_key_is_rebased_by_cargo_and_still_counts(self) -> None:
        """THE row that value equality gets wrong.

        release-lab declares `readme = "README.md"` and all four members
        take it; cargo reports `../../README.md` for each. Comparing the
        two as strings reds a key nobody got wrong.
        """
        taker = member("taker", readme="../../README.md")
        self.assertEqual(wi.inherits("readme", "README.md", taker, ROOT), True)

    def test_a_rebased_path_naming_another_file_does_not_count(self) -> None:
        """The other direction: rebasing is not a free pass."""
        other = member("other", readme="../../OTHER.md")
        self.assertEqual(wi.inherits("readme", "README.md", other, ROOT), False)

    def test_a_bool_readme_resolves_to_whatever_cargo_found(self) -> None:
        """A readme declared true asks cargo to discover the file.

        The declared bool appears nowhere in the account, so the test is
        whether the member reports a readme at all.
        """
        rows = [member("taker", readme="../../README.md"), member("bare", readme=None)]
        self.assertEqual(
            [wi.inherits("readme", DISCOVER, row, ROOT) for row in rows],
            [True, False],
        )

    def test_a_false_readme_is_undecidable(self) -> None:
        """A readme declared false and "no readme" are one account.

        A member taking it reports None, and so does a member taking
        nothing — so this returns None rather than a verdict that would
        be right by luck.
        """
        bare = member("bare", readme=None)
        self.assertIsNone(wi.inherits("readme", NO_README, bare, ROOT))


class TestJudge(unittest.TestCase):
    """The rule: red only where NO member takes the key."""

    def test_a_key_every_member_takes_is_not_reported(self) -> None:
        """Every member inheriting, which is edtf's live shape."""
        members = [member("a", rust_version="1.82"), member("b", rust_version="1.82")]
        self.assertEqual(wi.judge({"rust-version": "1.82"}, members, ROOT, []), [])

    def test_a_key_only_one_member_takes_is_not_reported(self) -> None:
        """THE branch a stricter rule would get wrong.

        A workspace legitimately declares a key only some members want.
        One taker is a reader, and a key with a reader is not inert.
        """
        members = [member("a", rust_version="1.82"), member("b", rust_version=None)]
        self.assertEqual(wi.judge({"rust-version": "1.82"}, members, ROOT, []), [])

    def test_a_key_no_member_takes_is_reported_with_every_member(self) -> None:
        """release-lab's live case: the remedy names who could take it."""
        members = [member("a", rust_version=None), member("b", rust_version=None)]
        findings = wi.judge({"rust-version": "1.97"}, members, ROOT, [])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].key, "rust-version")
        self.assertEqual(findings[0].takers, ["a", "b"])

    def test_publish_is_reported_unjudged_rather_than_guessed(self) -> None:
        """Measured: an inherited and an own publish are one account.

        A declared false comes back `[]` either way, so a verdict here
        would be right by luck in both directions.
        """
        report: list[str] = []
        members = [member("a", publish=[])]
        self.assertEqual(wi.judge({"publish": False}, members, ROOT, report), [])
        self.assertTrue(any("publish" in line for line in report))

    def test_a_key_cargo_does_not_report_is_named_not_skipped(self) -> None:
        """`exclude` and `include` have no per-package value at all."""
        report: list[str] = []
        members = [member("a")]
        self.assertEqual(wi.judge({"exclude": ["x"]}, members, ROOT, report), [])
        self.assertTrue(any("exclude" in line for line in report))

    def test_an_unknown_key_is_named_not_skipped(self) -> None:
        """A key this task cannot model is stated, never passed quietly."""
        report: list[str] = []
        self.assertEqual(wi.judge({"invented": 1}, [member("a")], ROOT, report), [])
        self.assertTrue(any("invented" in line for line in report))

    def test_an_undecidable_key_is_not_red(self) -> None:
        """No taker plus no verdict is a note, not a finding."""
        report: list[str] = []
        members = [member("a", readme=None)]
        self.assertEqual(wi.judge({"readme": False}, members, ROOT, report), [])
        self.assertTrue(report)


class TestWorkspacePackage(unittest.TestCase):
    """Reading the declaration, which is the one thing read as TEXT.

    Only the declared KEYS come from the manifest; whether anyone takes
    them is always cargo's account. Reading the members' text is what
    made this class invisible, and is what the issue rejected.
    """

    def test_reads_the_declared_table(self) -> None:
        """The table arrives as written."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text(
                '[workspace.package]\nrust-version = "1.82"\n',
                encoding="utf-8",
            )
            self.assertEqual(wi.workspace_package(root), {"rust-version": "1.82"})

    def test_a_workspace_without_the_table_reads_empty(self) -> None:
        """The skip-clean branch's input."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text(
                '[workspace]\nmembers = ["crates/*"]\n',
                encoding="utf-8",
            )
            self.assertEqual(wi.workspace_package(root), {})

    def test_an_unreadable_manifest_stops_the_task(self) -> None:
        """A manifest missing where cargo named the root is broken."""
        with TemporaryDirectory() as tmp, self.assertRaises(SystemExit):
            wi.workspace_package(Path(tmp))


class TestMain(unittest.TestCase):
    """The guards, driven end to end over a real metadata document."""

    @staticmethod
    def _run(manifest: str, packages: list[dict]) -> tuple[int, str, str]:
        """Lay out a workspace root, feed cargo's account, capture all.

        Returns:
            The exit status, stdout and stderr.

        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text(manifest, encoding="utf-8")
            for package in packages:
                crate = root / "crates" / package["name"]
                package["manifest_path"] = str(crate / "Cargo.toml")
            document = {
                "workspace_root": str(root),
                "workspace_members": [p["id"] for p in packages],
                "packages": packages,
            }
            out, err = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(wi.sys, "stdin", io.StringIO(json.dumps(document))),
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                status = wi.main()
            return status, out.getvalue(), err.getvalue()

    def test_no_workspace_package_table_skips_clean(self) -> None:
        """Nothing is declared here, so nothing can be inert."""
        status, out, _ = self._run(
            '[workspace]\nmembers = ["crates/*"]\n',
            [member("a")],
        )
        self.assertEqual(status, 0)
        self.assertIn("no [workspace.package] table, skipped", out)

    def test_an_inherited_key_passes_and_says_what_it_measured(self) -> None:
        """The green path counts the keys and the members."""
        status, out, _ = self._run(
            '[workspace.package]\nrust-version = "1.82"\n',
            [member("a", rust_version="1.82")],
        )
        self.assertEqual(status, 0)
        self.assertIn("1 [workspace.package] key", out)

    def test_an_inert_key_fails_with_the_one_line_remedy(self) -> None:
        """The red path names the key, the members and the fix."""
        status, _, err = self._run(
            '[workspace.package]\nrust-version = "1.97"\n',
            [member("a", rust_version=None), member("b", rust_version=None)],
        )
        self.assertEqual(status, 1)
        self.assertIn("reach no member", err)
        self.assertIn("rust-version.workspace = true", err)
        self.assertIn("a, b", err)

    def test_a_declared_table_with_no_member_fails(self) -> None:
        """A declaration with nobody to read it, in the limit.

        Skipping here would be the vacuous success this whole class is
        about: the keys are declared and reach nothing.
        """
        status, _, err = self._run('[workspace.package]\nversion = "0.1.0"\n', [])
        self.assertEqual(status, 1)
        self.assertIn("names no workspace member", err)

    def test_unreadable_metadata_stops_the_task(self) -> None:
        """A broken account is refused, never read as an empty one."""
        with (
            mock.patch.object(wi.sys, "stdin", io.StringIO("not json")),
            self.assertRaises(SystemExit),
        ):
            wi.main()


if __name__ == "__main__":
    sys.exit(not unittest.main(exit=False).result.wasSuccessful())
