#!/usr/bin/env python3
"""Table tests for the gate's crate surface (#813).

One row per branch of each guard, both directions: the state that must be
compiled and the nearest one that must not, the stub that constrains the
gate and the one that does not. The invariant is planted both ways —
a published crate held out of the gate, and the same crate with the
exclusion gone — because "the gate compiles what the publish path builds"
is a claim that looks identical whether it was checked or assumed, which
is precisely how edtf's v1.3.0 reached ten red publish jobs.

Mutation-checked: every guard below was broken in turn and the rows that
cover it observed to fail — one, one, two, one, two and one red row for
the six mutations named in the class docstrings, so a reader can repeat
them rather than trust this line.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import override
from unittest import mock

import decoded
import gate_surface

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:gate-surface"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    patch = mock.patch.dict(os.environ, {"MISE_TASK_NAME": TASK, "ORG_FINDINGS": ""})
    patch.start()
    unittest.addModuleCleanup(patch.stop)


ROOT = "/w"

# What a row expects, named so each carries its element type.
NOTHING: list[object] = []
NONE_BUILT: set[str] = set()


def member(
    name: str,
    *,
    features: dict[str, list[str]] | None = None,
    pgrx: bool = False,
    directory: str | None = None,
) -> dict[str, object]:
    """Build one workspace member as cargo metadata reports it.

    Returns:
        The package object, with the `pgrx` dependency present only when
        asked for — the two halves of the pgrx test are set separately so
        each can be exercised without the other.

    """
    where = directory if directory is not None else f"crates/{name}"
    return {
        "id": f"path+file://{ROOT}/{where}#1.3.1",
        "name": name,
        "version": "1.3.1",
        "features": features or {},
        "dependencies": [{"name": "pgrx"}] if pgrx else [{"name": "serde"}],
        "manifest_path": f"{ROOT}/{where}/Cargo.toml",
    }


def metadata(
    *packages: dict[str, object], foreign: dict[str, object] | None = None
) -> dict[str, object]:
    """Build a cargo metadata document whose members are the packages given.

    Returns:
        The document, optionally carrying a package that is NOT a member.

    """
    every: list[object] = list(packages)
    if foreign is not None:
        every.append(foreign)
    return {
        "workspace_root": ROOT,
        "workspace_members": [pkg["id"] for pkg in packages],
        "packages": every,
    }


def stub(*lines: str) -> Path:
    """Write a publish stub into a temporary tree.

    Returns:
        Its path.

    """
    path = Path(tempfile.mkdtemp()) / "publish.yml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def majors_of(pkg: dict[str, object]) -> list[int]:
    """Decode a package and read its majors.

    Returns:
        What `majors` reads from the decoded package.

    """
    return gate_surface.majors(gate_surface.package(pkg))


def directory_of(pkg: dict[str, object], root: str) -> str:
    """Decode a package and locate it.

    Returns:
        What `directory` reads from the decoded package.

    """
    return gate_surface.directory(gate_surface.package(pkg), root)


def crate(name: str, majors: tuple[int, ...] = ()) -> gate_surface.Crate:
    """Build one surface record of the shared three-crate workspace.

    Returns:
        The record `published` and `invariant` consume.

    """
    return gate_surface.Crate(name, f"{name}@1.3.1", majors, f"crates/{name}")


PG_FEATURES: dict[str, list[str]] = {
    "pg15": [],
    "pg16": [],
    "pg17": [],
    "pg18": [],
    "pg_test": [],
}


class MajorsTest(unittest.TestCase):
    """Deriving a pgrx extension. Mutation: drop the dependency half."""

    def test_the_dependency_and_the_features_together(self) -> None:
        """The org shape: edtf-postgres exactly."""
        majors: list[int] = [15, 16, 17, 18]
        self.assertEqual(
            majors_of(member("ext", features=PG_FEATURES, pgrx=True)), majors
        )

    def test_features_without_the_dependency_are_not_pgrx(self) -> None:
        """A crate may name a feature `pg17` and never touch pgrx."""
        self.assertEqual(
            majors_of(member("ext", features=PG_FEATURES, pgrx=False)), NOTHING
        )

    def test_the_dependency_without_features_is_not_an_extension(self) -> None:
        """A crate that merely depends on pgrx declares no majors."""
        self.assertEqual(majors_of(member("ext", pgrx=True)), NOTHING)

    def test_pg_test_is_not_a_major(self) -> None:
        """Pgrx's own harness switch would otherwise become a Postgres."""
        majors: list[int] = [17]
        self.assertEqual(
            majors_of(
                member("ext", features={"pg17": [], "pg_test": []}, pgrx=True),
            ),
            majors,
        )

    def test_only_the_majors_declared_are_returned(self) -> None:
        """A repo that drops a major must not have the gate keep it."""
        majors: list[int] = [17, 18]
        self.assertEqual(
            majors_of(
                member("ext", features={"pg17": [], "pg18": []}, pgrx=True),
            ),
            majors,
        )

    def test_the_majors_come_back_ascending(self) -> None:
        """Declaration order in a manifest is not a promise."""
        majors: list[int] = [15, 16, 18]
        self.assertEqual(
            majors_of(
                member("ext", features={"pg18": [], "pg15": [], "pg16": []}, pgrx=True),
            ),
            majors,
        )


class MembersTest(unittest.TestCase):
    """Which packages are the workspace's. Mutation: return all packages."""

    def test_a_dependency_is_not_a_member(self) -> None:
        """Pgrx itself is in `packages`, and is not this repo's crate."""
        doc = metadata(
            member("edtf-core"),
            foreign={
                "id": "registry+pgrx#0.19.2",
                "name": "pgrx",
                "version": "0.19.2",
                "features": PG_FEATURES,
                "dependencies": [{"name": "pgrx"}],
                "manifest_path": "/elsewhere/Cargo.toml",
            },
        )
        names = [pkg.name for pkg in gate_surface.members(gate_surface.metadata(doc))]
        only: list[str] = ["edtf-core"]
        self.assertEqual(names, only)


class DirectoryTest(unittest.TestCase):
    """Locating a member. Mutation: return the absolute manifest path."""

    def test_a_member_resolves_relative_to_the_workspace(self) -> None:
        """The form the publish stub writes."""
        self.assertEqual(
            directory_of(member("ext"), ROOT),
            "crates/ext",
        )

    def test_a_member_outside_the_root_resolves_to_nothing(self) -> None:
        """A path dependency elsewhere on disk is not a stub's crate-dir."""
        odd = member("ext")
        odd["manifest_path"] = "/somewhere/else/Cargo.toml"
        self.assertEqual(directory_of(odd, ROOT), "")


class StubInputsTest(unittest.TestCase):
    """Reading the stub. Mutation: accept an empty value as a scalar."""

    def test_the_keys_this_check_reads(self) -> None:
        """The canonical shape, edtf's own."""
        found, unreadable = gate_surface.stub_inputs(
            "jobs:\n"
            "  publish:\n"
            "    with:\n"
            "      classes: rust-crate,pgrx-extension\n"
            "      exclude: edtf-postgres\n"
            "      extension-crate-dir: crates/edtf-postgres\n"
            "      pg-majors: 15,16\n",
        )
        self.assertEqual(unreadable, NOTHING)
        self.assertEqual(found["classes"], "rust-crate,pgrx-extension")
        self.assertEqual(found["exclude"], "edtf-postgres")
        self.assertEqual(found["pg-majors"], "15,16")

    def test_a_comment_is_not_a_value(self) -> None:
        """The stubs are more comment than code."""
        found, _unreadable = gate_surface.stub_inputs(
            "    with:\n"
            "      # classes: this-is-prose\n"
            "      classes: rust-crate  # and a trailing reason\n",
        )
        self.assertEqual(found["classes"], "rust-crate")

    def test_keys_after_the_block_are_not_inputs(self) -> None:
        """`permissions:` sits at the same level and says nothing here."""
        found, _unreadable = gate_surface.stub_inputs(
            "    with:\n"
            "      classes: rust-crate\n"
            "permissions:\n"
            "  exclude: not-an-input\n",
        )
        self.assertEqual(found.get("exclude"), None)

    def test_an_empty_value_is_refused(self) -> None:
        """A block value is a publish surface this cannot read."""
        _found, unreadable = gate_surface.stub_inputs(
            "    with:\n      classes:\n        - rust-crate\n",
        )
        self.assertEqual(len(unreadable), 1)
        self.assertIn("classes", unreadable[0])

    def test_quotes_are_not_part_of_the_value(self) -> None:
        """YAML permits them; the comparison must not see them."""
        found, _unreadable = gate_surface.stub_inputs(
            '    with:\n      classes: "rust-crate"\n',
        )
        self.assertEqual(found["classes"], "rust-crate")


class PublishedTest(unittest.TestCase):
    """Which crates get built. Mutation: ignore the stub's `exclude`."""

    @staticmethod
    def surface() -> list[gate_surface.Crate]:
        """Build the three-crate workspace these rows share.

        Returns:
            The surface records `published` consumes.

        """
        return [crate("core"), crate("wasm"), crate("ext", (17,))]

    def test_the_workspace_classes_take_every_member(self) -> None:
        """rust-crate publishes the workspace, not a named list."""
        built: set[str] = {"core", "wasm", "ext"}
        self.assertEqual(
            gate_surface.published({"classes": "rust-crate"}, self.surface()), built
        )

    def test_the_stub_exclusion_removes_a_crate(self) -> None:
        """Edtf's own: the extension is not a crate-class artifact."""
        built: set[str] = {"core", "wasm"}
        self.assertEqual(
            gate_surface.published(
                {"classes": "rust-crate", "exclude": "ext"},
                self.surface(),
            ),
            built,
        )

    def test_the_extension_class_puts_it_back(self) -> None:
        """Excluded from the crate jobs, built by the extension jobs."""
        built: set[str] = {"core", "wasm", "ext"}
        self.assertEqual(
            gate_surface.published(
                {
                    "classes": "rust-crate,pgrx-extension",
                    "exclude": "ext",
                    "extension-crate-dir": "crates/ext",
                },
                self.surface(),
            ),
            built,
        )

    def test_the_wasm_class_names_its_crate_by_directory(self) -> None:
        """crate-dir, not a package name."""
        built: set[str] = {"wasm"}
        self.assertEqual(
            gate_surface.published(
                {"classes": "wasm-npm", "crate-dir": "crates/wasm"},
                self.surface(),
            ),
            built,
        )

    def test_classes_that_build_no_crate_constrain_nothing(self) -> None:
        """A repo publishing only an image or an archive."""
        self.assertEqual(
            gate_surface.published(
                {"classes": "oci-image,source-archive"},
                self.surface(),
            ),
            NONE_BUILT,
        )


class InvariantTest(unittest.TestCase):
    """The check itself. Mutation: report a finding only when built is empty."""

    @override
    def setUp(self) -> None:
        """Build the stub every row here shares."""
        self.surface = PublishedTest.surface()
        self.stub = stub(
            "    with:",
            "      classes: rust-crate,pgrx-extension",
            "      exclude: ext",
            "      extension-crate-dir: crates/ext",
            "      pg-majors: 17",
        )

    def test_a_published_crate_held_out_of_the_gate_is_a_finding(self) -> None:
        """Edtf as it stands: the defect this issue exists for."""
        findings = gate_surface.invariant(self.stub, self.surface, {"ext"})
        self.assertEqual(len(findings), 1)
        self.assertIn("ext@1.3.1", findings[0])
        self.assertIn("CLIPPY_EXCLUDE", findings[0])

    def test_the_same_tree_with_the_exclusion_gone_is_clean(self) -> None:
        """The other direction, which is what this PR leaves behind."""
        self.assertEqual(
            gate_surface.invariant(self.stub, self.surface, set()), NOTHING
        )

    def test_an_unpublished_crate_may_be_held_out(self) -> None:
        """CLIPPY_EXCLUDE stays legal for a crate the release does not build.

        Note what this row had to do to be true: a `rust-crate` stub
        publishes EVERY workspace member, so the only crate the gate may
        hold out is one the stub excludes too. That is the invariant
        biting, not a quirk of the fixture.
        """
        surface = [*self.surface, crate("bench")]
        both = stub(
            "    with:",
            "      classes: rust-crate,pgrx-extension",
            "      exclude: ext,bench",
            "      extension-crate-dir: crates/ext",
            "      pg-majors: 17",
        )
        self.assertEqual(gate_surface.invariant(both, surface, {"bench"}), NOTHING)

    def test_a_stub_claiming_fewer_majors_than_the_crate_is_a_finding(self) -> None:
        """The features are what cargo compiles; the stub is the copy."""
        thin = stub(
            "    with:",
            "      classes: pgrx-extension",
            "      extension-crate-dir: crates/ext",
            "      pg-majors: 15,17",
        )
        findings = gate_surface.invariant(thin, self.surface, set())
        self.assertEqual(len(findings), 1)
        self.assertIn("pg-majors", findings[0])

    def test_spaces_in_the_stub_major_list_are_not_a_disagreement(self) -> None:
        """`15, 17` and `15,17` are the same claim."""
        spaced = stub(
            "    with:",
            "      classes: pgrx-extension",
            "      extension-crate-dir: crates/ext",
            "      pg-majors: 17",
        )
        self.assertEqual(gate_surface.invariant(spaced, self.surface, set()), NOTHING)

    def test_stated_majors_the_mapping_pins_are_clean(self) -> None:
        """Features equal to the stub, every major served by the canon."""
        surface = [crate("ext", (15, 16, 17, 18))]
        full = stub(
            "    with:",
            "      classes: pgrx-extension",
            "      extension-crate-dir: crates/ext",
            "      pg-majors: 15,16,17,18",
        )
        self.assertEqual(gate_surface.invariant(full, surface, set()), NOTHING)

    def test_stub_without_pg_majors_is_a_finding(self) -> None:
        """No default matrix: an unstated request is not built at a guess."""
        bare = stub(
            "    with:",
            "      classes: pgrx-extension",
            "      extension-crate-dir: crates/ext",
        )
        findings = gate_surface.invariant(bare, self.surface, set())
        self.assertEqual(len(findings), 1)
        self.assertIn("states no pg-majors", findings[0])

    def test_declared_major_without_base_image_is_a_finding(self) -> None:
        """A major the canon does not serve is the gate's red, not the tag's."""
        surface = [crate("ext", (13, 17))]
        retired = stub(
            "    with:",
            "      classes: pgrx-extension",
            "      extension-crate-dir: crates/ext",
            "      pg-majors: 13,17",
        )
        findings = gate_surface.invariant(retired, surface, set())
        self.assertEqual(len(findings), 1)
        self.assertIn("ext@1.3.1", findings[0])
        self.assertIn("pg13 has no digest-pinned base image", findings[0])

    def test_a_missing_stub_is_a_finding(self) -> None:
        """Silence about a publish surface is not a clean publish surface."""
        findings = gate_surface.invariant(
            Path("/nope/publish.yml"),
            self.surface,
            set(),
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("does not exist", findings[0])


class MainTest(unittest.TestCase):
    """The whole check. Mutation: emit the plan even when a finding stands."""

    @staticmethod
    def invoke(
        doc: object, exclude: str = "", publish: Path | None = None
    ) -> tuple[int, str, str]:
        """Run the derivation over one workspace.

        Returns:
            Exit status, stdout and stderr.

        """
        argv = ["--exclude", exclude]
        if publish is not None:
            argv += ["--publish", str(publish)]
        out, err = io.StringIO(), io.StringIO()
        stdin = io.StringIO(json.dumps(doc) if doc is not None else "{")
        with (
            mock.patch("sys.stdin", stdin),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            status = gate_surface.main(argv)
        return status, out.getvalue(), err.getvalue()

    def test_a_plain_workspace_is_all_crate_records(self) -> None:
        """Every repo in the org that is not edtf or release-lab."""
        status, out, err = self.invoke(metadata(member("a"), member("b")))
        self.assertEqual(status, 0, msg=err)
        records: list[str] = [
            "crate\ta@1.3.1\tplain\t-\tcrates/a",
            "crate\tb@1.3.1\tplain\t-\tcrates/b",
        ]
        self.assertEqual(out.splitlines(), records)

    def test_an_extension_carries_its_majors(self) -> None:
        """What test:pgrx iterates and lint:pg-clippy picks from."""
        status, out, err = self.invoke(
            metadata(member("ext", features=PG_FEATURES, pgrx=True)),
        )
        self.assertEqual(status, 0, msg=err)
        self.assertEqual(
            out.strip(),
            "crate\text@1.3.1\tpgrx\t15,16,17,18\tcrates/ext",
        )

    def test_an_excluded_member_is_reported_as_excluded(self) -> None:
        """Held out, and never silently absent from the population."""
        status, out, err = self.invoke(metadata(member("a"), member("b")), exclude="b")
        self.assertEqual(status, 0, msg=err)
        self.assertIn("excluded\tb@1.3.1\tplain\t-\tcrates/b", out)
        self.assertIn("crate\ta@1.3.1\tplain\t-\tcrates/a", out)

    def test_the_invariant_reds_and_emits_no_plan(self) -> None:
        """A consumer must not act on a surface the check just refused."""
        published = stub(
            "    with:",
            "      classes: rust-crate",
        )
        status, out, err = self.invoke(
            metadata(member("a"), member("b")),
            exclude="b",
            publish=published,
        )
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("b@1.3.1", err)

    def test_no_record_field_is_ever_empty(self) -> None:
        """Bash collapses a run of tabs, so an empty field shifts the rest.

        Measured: a plain crate's empty major list made its DIRECTORY
        arrive as its majors in `lint:msrv`, which then handed cargo a
        package spec as a positional argument. `-` is the placeholder,
        and this row is what stops it being deleted as noise.
        """
        _status, out, _err = self.invoke(
            metadata(
                member("plain"),
                member("ext", features={"pg17": []}, pgrx=True),
            ),
        )
        for line in out.splitlines():
            fields = line.split("\t")
            self.assertEqual(len(fields), 5, msg=line)
            for field in fields:
                self.assertNotEqual(field, "", msg=line)

    def test_metadata_that_is_not_an_object_is_red(self) -> None:
        """The shrunk example, `[]`: refused by name, not raised (#1555)."""
        not_an_object: list[object] = []
        status, out, err = self.invoke(not_an_object)
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("is not a JSON object", err)

    def test_fields_of_the_wrong_type_read_as_absent(self) -> None:
        """A package, member id or field of the wrong type is no crate (#1555)."""
        good = metadata(member("a"))
        packages = decoded.as_array(good["packages"], "packages")
        ids = decoded.as_array(good["workspace_members"], "workspace_members")
        doc: dict[str, object] = {
            **good,
            "packages": [1, *packages, {"id": [], "name": 2}],
            "workspace_members": [[], *ids],
            "workspace_root": 5,
        }
        status, out, err = self.invoke(doc)
        self.assertEqual(status, 0, msg=err)
        self.assertEqual(len(out.splitlines()), 1)

    def test_no_standard_input_is_red(self) -> None:
        """A process started with no stdin is refused by name, not raised."""
        err = io.StringIO()
        with (
            mock.patch("sys.stdin", None),
            redirect_stderr(err),
            redirect_stdout(io.StringIO()),
        ):
            status = gate_surface.main([])
        self.assertEqual(status, 1)
        self.assertEqual(
            err.getvalue(),
            f"{TASK}: no standard input: the process was started without one\n",
        )

    def test_metadata_that_is_not_json_is_red(self) -> None:
        """A broken cargo must not read as a workspace with no crates."""
        status, _out, err = self.invoke(None)
        self.assertEqual(status, 1)
        self.assertIn("did not give JSON", err)


if __name__ == "__main__":
    unittest.main()
