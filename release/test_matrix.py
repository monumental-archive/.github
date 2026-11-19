#!/usr/bin/env python3
"""Table tests for the release matrices' one derivation (#1385).

One row per branch: each known target's runner, each refusal with its
message asserted whole, and the canon's and release-lab's real requests,
whose expected outputs and notices in testdata/matrix are what the shell
these modes replaced printed for the same request against the same
mapping. The requests are the ones recorded runs made:

- stele publish run 44161784819, job 134317322844 (go-binary targets);
- release-lab v0.28.4 publish run 43806579916: jobs 133098667199
  (rust-binary targets), 133098667244 (pgrx-extension majors) and
  133100778135 (pgrx-images majors);
- canon base-attest run 43879280295, job 133340507721 (the base set).

The mapping beside them is a frozen copy of docker/pgrx-base-images.toml
as those runs read it, so a Renovate roll of the live file moves nothing
here.
"""

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import matrix

TESTDATA = Path(__file__).with_name("testdata") / "matrix"
MAPPING = str(TESTDATA / "pgrx-base-images.toml")

NOTHING = "a release that builds nothing is not a release"
NO_IMAGE = "has no digest-pinned base image in docker/pgrx-base-images.toml"
GO_HINT = "canon targets only (<goos>-<goarch>)"


def run(*argv: str) -> tuple[int, str, str]:
    """Run the helper.

    Returns:
        The exit status, stdout and stderr.

    """
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = matrix.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def recorded(name: str) -> tuple[str, str]:
    """Read what the shell printed for a recorded request.

    Returns:
        The output lines and the notice lines, as text.

    """
    notices = TESTDATA / f"{name}.notices"
    return (
        (TESTDATA / f"{name}.outputs").read_text("utf-8"),
        notices.read_text("utf-8") if notices.exists() else "",
    )


class Rows(unittest.TestCase):
    """Shared assertions and fixtures."""

    def refused(self, argv: tuple[str, ...], message: str) -> None:
        """Assert a run refuses with exactly `message` and no outputs."""
        self.assertEqual(run(*argv), (1, "", message + "\n"))

    def mapping(self, text: str) -> str:
        """Write a mapping into a temporary directory.

        Returns:
            Its path.

        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "pgrx-base-images.toml"
        path.write_text(text, "utf-8")
        return str(path)


class Recorded(Rows):
    """The real requests: byte-identical to what the shell printed."""

    def assert_recorded(self, name: str, *argv: str) -> None:
        """Assert a mode prints a recorded request's outputs and notices."""
        outputs, notices = recorded(name)
        self.assertEqual(run(*argv), (0, outputs, notices))

    def test_stele_go_binary(self) -> None:
        """Stele's four Go targets."""
        self.assert_recorded(
            "stele-go-binary",
            "targets",
            "go-binary",
            "linux-amd64,linux-arm64,darwin-amd64,darwin-arm64",
        )

    def test_release_lab_rust_binary(self) -> None:
        """Release-lab's four Rust targets."""
        self.assert_recorded(
            "lab-rust-binary",
            "targets",
            "rust-binary",
            "x86_64-unknown-linux-musl,aarch64-unknown-linux-musl,"
            "x86_64-apple-darwin,aarch64-apple-darwin",
        )

    def test_release_lab_pgrx_extension(self) -> None:
        """Release-lab's five majors, ten legs, and the glibc floor."""
        self.assert_recorded(
            "lab-pgrx-extension", "pgrx-extension", MAPPING, "14,15,16,17,18"
        )

    def test_release_lab_pgrx_images(self) -> None:
        """Release-lab's five image majors; the step printed no notices."""
        self.assert_recorded("lab-pgrx-images", "pgrx-images", "14,15,16,17,18")

    def test_canon_base_attest(self) -> None:
        """The canon's ten pinned bases, build then smoke."""
        self.assert_recorded("canon-base-attest", "base-images", MAPPING)


class Targets(Rows):
    """targets: the go-binary and rust-binary plan steps."""

    RUNNERS = (
        ("go-binary", "linux-amd64", "ubuntu-24.04"),
        ("go-binary", "linux-arm64", "ubuntu-24.04-arm"),
        ("go-binary", "darwin-amd64", "macos-15-intel"),
        ("go-binary", "darwin-arm64", "macos-15"),
        ("rust-binary", "x86_64-unknown-linux-musl", "ubuntu-24.04"),
        ("rust-binary", "aarch64-unknown-linux-musl", "ubuntu-24.04-arm"),
        ("rust-binary", "x86_64-apple-darwin", "macos-15-intel"),
        ("rust-binary", "aarch64-apple-darwin", "macos-15"),
    )

    def test_each_known_target_runs_on_its_runner(self) -> None:
        """One row per table entry, outputs and notice whole."""
        for cls, target, runner in self.RUNNERS:
            with self.subTest(target=target):
                cell = f'{{"target":"{target}","runner":"{runner}"}}'
                self.assertEqual(
                    run("targets", cls, target),
                    (
                        0,
                        f'matrix={{"include":[{cell}]}}\nexpected=["{target}"]\n',
                        f"::notice::will build {target} on {runner}\n",
                    ),
                )

    def test_the_tables_hold_exactly_the_canon_targets(self) -> None:
        """A table row with no runner row above is a target with no test."""
        tables = {cls: set(matrix.TARGETS[cls][0]) for cls in matrix.TARGETS}
        rows: dict[str, set[str]] = {}
        for cls, target, _ in self.RUNNERS:
            rows.setdefault(cls, set()).add(target)
        self.assertEqual(tables, rows)

    def test_an_empty_request_is_refused(self) -> None:
        """Nothing requested, after spaces and the first line."""
        for cls in ("go-binary", "rust-binary"):
            for request in ("", "  ", "\nlinux-amd64"):
                with self.subTest(cls=cls, request=request):
                    self.refused(
                        ("targets", cls, request),
                        f"::error::no targets requested — {NOTHING}",
                    )

    def test_an_unknown_go_target_names_the_go_form(self) -> None:
        """The Go hint names the target form."""
        self.refused(
            ("targets", "go-binary", "linux-amd64,plan9-386"),
            f"::error::unknown target plan9-386; {GO_HINT}",
        )

    def test_an_unknown_rust_target_is_refused(self) -> None:
        """The Rust hint is bare."""
        self.refused(
            ("targets", "rust-binary", "x86_64-pc-windows-msvc"),
            "::error::unknown target x86_64-pc-windows-msvc; canon targets only",
        )

    def test_another_class_target_is_unknown(self) -> None:
        """The tables are per class: a Go target is no Rust target."""
        self.refused(
            ("targets", "rust-binary", "linux-amd64"),
            "::error::unknown target linux-amd64; canon targets only",
        )

    def test_an_empty_field_is_an_unknown_target(self) -> None:
        """A leading or inner empty field is kept, and refused."""
        for request in (",linux-amd64", "linux-amd64,,darwin-arm64", ","):
            with self.subTest(request=request):
                self.refused(
                    ("targets", "go-binary", request),
                    f"::error::unknown target ; {GO_HINT}",
                )

    def test_a_duplicate_is_refused(self) -> None:
        """The same target twice, spaced or not."""
        for request in ("linux-amd64,linux-amd64", "linux-amd64, linux-amd64,"):
            with self.subTest(request=request):
                self.refused(
                    ("targets", "go-binary", request),
                    "::error::duplicate target requested",
                )

    def test_an_unknown_target_is_named_before_a_duplicate(self) -> None:
        """The shell checked every target before counting duplicates."""
        self.refused(
            ("targets", "go-binary", "linux-amd64,linux-amd64,plan9-386"),
            f"::error::unknown target plan9-386; {GO_HINT}",
        )

    def test_the_request_splits_as_the_shell_split_it(self) -> None:
        """Spaces removed, one trailing comma dropped, the first line only."""
        for request in (" linux-amd64 ", "linux-amd64,", "linux-amd64\ndarwin-arm64"):
            with self.subTest(request=request):
                code, out, _ = run("targets", "go-binary", request)
                self.assertEqual(code, 0)
                self.assertIn('expected=["linux-amd64"]\n', out)

    def test_the_order_requested_is_the_order_planned(self) -> None:
        """No sorting: the caller's order is the matrix's."""
        _, out, _ = run("targets", "go-binary", "darwin-arm64,linux-amd64")
        self.assertIn('expected=["darwin-arm64","linux-amd64"]\n', out)


class PgrxExtension(Rows):
    """pgrx-extension: the build-pgrx-extension.yml plan step."""

    def test_an_empty_request_is_refused(self) -> None:
        """Nothing requested."""
        for request in ("", " "):
            with self.subTest(request=request):
                self.refused(
                    ("pgrx-extension", MAPPING, request),
                    f"::error::no PG majors requested — {NOTHING}",
                )

    def test_a_major_with_no_pinned_base_image_is_refused(self) -> None:
        """A major the mapping does not carry, numeric or not."""
        for major in ("13", "19"):
            with self.subTest(major=major):
                self.refused(
                    ("pgrx-extension", MAPPING, f"14,{major}"),
                    f"::error::pg{major} {NO_IMAGE}",
                )

    def test_a_non_numeric_major_is_refused(self) -> None:
        """A field that is not a number is named as such, not as unpinned."""
        for major in ("x", "pg14", ""):
            with self.subTest(major=major):
                self.refused(
                    ("pgrx-extension", MAPPING, f"{major},14"),
                    f"::error::{major} is not a PG major",
                )

    def test_a_major_pinned_for_one_role_only_is_refused(self) -> None:
        """Both a build and a smoke image, or no plan."""
        for text in (
            '[build]\n"14" = "a@sha256:1"\n[smoke]\n',
            '[build]\n[smoke]\n"14" = "a@sha256:1"\n',
            '[build]\n"14" = ""\n[smoke]\n"14" = "a@sha256:1"\n',
            '[smoke]\n"14" = "a@sha256:1"\n',
        ):
            with self.subTest(text=text):
                self.refused(
                    ("pgrx-extension", self.mapping(text), "14"),
                    f"::error::pg14 {NO_IMAGE}",
                )

    def test_a_duplicate_is_refused(self) -> None:
        """The same major twice."""
        self.refused(
            ("pgrx-extension", MAPPING, "16,17,16"),
            "::error::duplicate PG major requested",
        )

    def test_a_duplicate_is_named_before_an_unpinned_major(self) -> None:
        """The request's own rules run before the mapping's pins (#1531)."""
        self.refused(
            ("pgrx-extension", MAPPING, "16,13,16"),
            "::error::duplicate PG major requested",
        )

    def test_each_major_builds_on_both_arches(self) -> None:
        """Two legs per major, amd64 first, both images on each."""
        mapping = self.mapping(
            '[build]\n"16" = "b@sha256:1"\n[smoke]\n"16" = "s@sha256:2"\n'
            '[floor]\nglibc = "2.36"\n'
        )
        images = '"build":"b@sha256:1","smoke":"s@sha256:2"'
        amd = f'{{"pg":"16","arch":"amd64","runner":"ubuntu-24.04",{images}}}'
        arm = f'{{"pg":"16","arch":"arm64","runner":"ubuntu-24.04-arm",{images}}}'
        outputs = (
            f'matrix={{"include":[{amd},{arm}]}}\n'
            'expected=["16-amd64","16-arm64"]\nfloor=2.36\n'
        )
        notices = (
            "::notice::will build pg16 on ubuntu-24.04\n"
            "::notice::will build pg16 on ubuntu-24.04-arm\n"
        )
        self.assertEqual(run("pgrx-extension", mapping, "16"), (0, outputs, notices))

    def test_a_mapping_with_no_floor_prints_null(self) -> None:
        """Jq's `-r .floor.glibc` printed null; the floor check is the build's."""
        mapping = self.mapping('[build]\n"16" = "b"\n[smoke]\n"16" = "s"\n')
        _, out, _ = run("pgrx-extension", mapping, "16")
        self.assertTrue(out.endswith("floor=null\n"))


class MappingShape(Rows):
    """A mapping that is not TOML, or not the mapping's shape (#1682)."""

    NAME = "docker/pgrx-base-images.toml"

    def test_a_mapping_that_is_not_toml_is_refused(self) -> None:
        """Named as not TOML, with the parser's reason."""
        code, out, err = run("pgrx-extension", self.mapping("[build\n"), "16")
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith(f"::error::{self.NAME} is not TOML: "), err)

    def test_each_wrong_shape_is_refused_by_name(self) -> None:
        """A role or the floor that is not a table, a pin that is not a string."""
        for text, said in (
            ('build = "x16"\n', f"[build] in {self.NAME} is not a table of majors"),
            ('[smoke]\n"16" = 16\n', f"smoke.16 in {self.NAME} is not a reference"),
            ("floor = false\n", f"[floor] in {self.NAME} is not a table"),
        ):
            for argv in (("pgrx-extension", "16"), ("base-images",)):
                with self.subTest(text=text, mode=argv[0]):
                    self.refused(
                        (argv[0], self.mapping(text), *argv[1:]), f"::error::{said}"
                    )


class PgrxImages(Rows):
    """pgrx-images: the build-pgrx-images.yml plan step."""

    def test_an_empty_request_is_refused(self) -> None:
        """Nothing requested."""
        for request in ("", "  "):
            with self.subTest(request=request):
                self.refused(
                    ("pgrx-images", request),
                    f"::error::no PG majors requested — {NOTHING}",
                )

    def test_a_non_numeric_major_is_refused(self) -> None:
        """ASCII digits only, as `^[0-9]+$` held it."""
        for major in ("x", "1a", "pg14", "-1", "1.5", "٣"):
            with self.subTest(major=major):
                self.refused(
                    ("pgrx-images", f"14,{major}"),
                    f"::error::{major} is not a PG major",
                )

    def test_an_empty_field_is_not_a_major(self) -> None:
        """A leading empty field is kept, and refused."""
        self.refused(("pgrx-images", ",14"), "::error:: is not a PG major")

    def test_a_duplicate_is_refused(self) -> None:
        """The same major twice would build and push one image twice (#1531)."""
        self.refused(
            ("pgrx-images", "14,14"),
            "::error::duplicate PG major requested",
        )


class RequestedMajors(unittest.TestCase):
    """requested_majors: every rule about the request itself, once (#1531)."""

    def assert_refused(self, request: str, message: str) -> None:
        """Assert a request is refused with exactly `message`."""
        with self.assertRaises(matrix.RefusalError) as caught:
            matrix.requested_majors(request)
        self.assertEqual(str(caught.exception), message)

    def test_an_empty_request_is_refused(self) -> None:
        """Nothing requested."""
        self.assert_refused("", f"no PG majors requested — {NOTHING}")

    def test_a_non_numeric_major_is_refused(self) -> None:
        """A field that is not ASCII digits."""
        self.assert_refused("14,pg15", "pg15 is not a PG major")

    def test_a_duplicate_is_refused(self) -> None:
        """The same major twice."""
        self.assert_refused("16,17,16", "duplicate PG major requested")

    def test_a_clean_list_is_returned_in_order(self) -> None:
        """The order requested is the order returned."""
        self.assertEqual(matrix.requested_majors("18,14,16"), ["18", "14", "16"])


class BaseImages(Rows):
    """base-images: base-attest.yml's enumeration of the pinned set."""

    def test_a_reference_with_no_digest_is_refused_bare(self) -> None:
        """The heredoc's SystemExit wording, unprefixed as it always was."""
        for ref in ("postgres:14-bookworm", "postgres:14@sha512:ab", "postgres@"):
            with self.subTest(ref=ref):
                self.refused(
                    ("base-images", self.mapping(f'[smoke]\n"14" = "{ref}"\n')),
                    f"smoke.14: no digest in {ref!r}",
                )

    def test_an_empty_file_is_refused(self) -> None:
        """A mapping that pins nothing approves nothing."""
        for text in ("", '[floor]\nglibc = "2.36"\n', "[build]\n[smoke]\n"):
            with self.subTest(text=text):
                self.refused(
                    ("base-images", self.mapping(text)),
                    "::error::no pinned base images found — an empty approval"
                    " set approves nothing",
                )

    def test_a_cell_names_the_repository_without_its_tag(self) -> None:
        """Docker Hub's short names are expanded; keys sort as strings."""
        mapping = self.mapping(
            '[build]\n"9" = "ghcr.io/o/pg:9@sha256:1"\n"10" = "postgres:10@sha256:2"\n'
        )
        ten = (
            '{"role":"build","pg":"10","ref":"postgres:10@sha256:2",'
            '"name":"docker.io/library/postgres","digest":"sha256:2"}'
        )
        nine = (
            '{"role":"build","pg":"9","ref":"ghcr.io/o/pg:9@sha256:1",'
            '"name":"ghcr.io/o/pg","digest":"sha256:1"}'
        )
        notices = (
            "::notice::will attest build pg10: postgres:10@sha256:2\n"
            "::notice::will attest build pg9: ghcr.io/o/pg:9@sha256:1\n"
        )
        self.assertEqual(
            run("base-images", mapping),
            (0, f'matrix={{"include":[{ten},{nine}]}}\n', notices),
        )


class CommandLine(unittest.TestCase):
    """A malformed command line is a usage error, never a plan."""

    def test_malformed_command_lines(self) -> None:
        """A missing argument or an unknown class prints the usage."""
        for argv in (
            (),
            ("targets", "oci-image", "x"),
            ("targets", "go-binary"),
            ("pgrx-images",),
            ("base-images",),
            ("pgrx-extension", MAPPING),
        ):
            with self.subTest(argv=argv):
                code, out, err = run(*argv)
                self.assertEqual((code, out), (2, ""))
                self.assertTrue(err.startswith("usage: matrix.py targets"))


if __name__ == "__main__":
    unittest.main()
