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

import sys
import tempfile
from pathlib import Path

import pytest

from belt.release import matrix
from belt.typed_pytest import fixture
from tests import support

TESTDATA = Path(__file__).resolve().parent / "testdata" / "matrix"
MAPPING = str(TESTDATA / "pgrx-base-images.toml")
TRACKED = support.CANON / "docker" / "pgrx-base-images.toml"

NOTHING = "a release that builds nothing is not a release"
NO_IMAGE = "has no digest-pinned base image in docker/pgrx-base-images.toml"
GO_HINT = "canon targets only (<goos>-<goarch>)"


@fixture
def _outside_mise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name the helper by its command, as a run outside mise does (#1934).

    An in-process call reads argv, whatever task runs this suite; an
    empty ORG_FINDINGS records nothing (unset on a runner records to the
    job's file, #1976).
    """
    monkeypatch.setattr(sys, "argv", ["matrix"])
    monkeypatch.delenv("MISE_TASK_NAME", raising=False)
    monkeypatch.setenv("ORG_FINDINGS", "")


pytestmark = pytest.mark.usefixtures("_outside_mise")


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


class Rows:
    """Shared assertions and fixtures."""

    def __init__(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path

    def mapping(self, text: str) -> str:
        """Write a mapping into a temporary directory.

        Returns:
            Its path.

        """
        tmp = tempfile.mkdtemp(dir=self.tmp)
        path = Path(tmp) / "pgrx-base-images.toml"
        path.write_text(text, "utf-8")
        return str(path)


@fixture
def rows_state(tmp_path: Path) -> Rows:
    """Give each row its own Rows.

    Returns:
        The row's Rows.

    """
    return Rows(tmp_path)


def refused(
    argv: tuple[str, ...], message: str, *, capfd: pytest.CaptureFixture[str]
) -> None:
    """Assert a run refuses with exactly `message` and no outputs."""
    status = matrix.main([*argv])
    out, err = capfd.readouterr()
    assert (status, out, err) == (1, "", message + "\n")


def assert_recorded(name: str, *argv: str, capfd: pytest.CaptureFixture[str]) -> None:
    """Assert a mode prints a recorded request's outputs and notices."""
    outputs, notices = recorded(name)
    status = matrix.main([*argv])
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, outputs, notices)


@pytest.mark.usefixtures("rows_state")
def test_stele_go_binary(capfd: pytest.CaptureFixture[str]) -> None:
    """Stele's four Go targets."""
    assert_recorded(
        "stele-go-binary",
        "targets",
        "go-binary",
        "linux-amd64,linux-arm64,darwin-amd64,darwin-arm64",
        capfd=capfd,
    )


@pytest.mark.usefixtures("rows_state")
def test_release_lab_rust_binary(capfd: pytest.CaptureFixture[str]) -> None:
    """Release-lab's four Rust targets."""
    assert_recorded(
        "lab-rust-binary",
        "targets",
        "rust-binary",
        "x86_64-unknown-linux-musl,aarch64-unknown-linux-musl,"
        "x86_64-apple-darwin,aarch64-apple-darwin",
        capfd=capfd,
    )


@pytest.mark.usefixtures("rows_state")
def test_release_lab_pgrx_extension(capfd: pytest.CaptureFixture[str]) -> None:
    """Release-lab's five majors, ten legs, and the glibc floor."""
    assert_recorded(
        "lab-pgrx-extension", "pgrx-extension", MAPPING, "14,15,16,17,18", capfd=capfd
    )


@pytest.mark.usefixtures("rows_state")
def test_release_lab_pgrx_images(capfd: pytest.CaptureFixture[str]) -> None:
    """Release-lab's five image majors; the step printed no notices."""
    assert_recorded("lab-pgrx-images", "pgrx-images", "14,15,16,17,18", capfd=capfd)


@pytest.mark.usefixtures("rows_state")
def test_canon_base_attest(capfd: pytest.CaptureFixture[str]) -> None:
    """The canon's ten pinned bases, build then smoke."""
    assert_recorded("canon-base-attest", "base-images", MAPPING, capfd=capfd)


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


@pytest.mark.usefixtures("rows_state")
@pytest.mark.parametrize(("cls", "target", "runner"), RUNNERS)
def test_each_known_target_runs_on_its_runner(
    cls: str, target: str, runner: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """One row per table entry, outputs and notice whole."""
    cell = f'{{"target":"{target}","runner":"{runner}"}}'
    status = matrix.main(["targets", cls, target])
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        f'matrix={{"include":[{cell}]}}\nexpected=["{target}"]\n',
        f"::notice::will build {target} on {runner}\n",
    )


@pytest.mark.usefixtures("rows_state")
def test_the_tables_hold_exactly_the_canon_targets() -> None:
    """A table row with no runner row above is a target with no test."""
    tables = {cls: set(matrix.TARGETS[cls][0]) for cls in matrix.TARGETS}
    rows: dict[str, set[str]] = {}
    for cls, target, _ in RUNNERS:
        rows.setdefault(cls, set()).add(target)
    assert tables == rows


@pytest.mark.usefixtures("rows_state")
def test_an_empty_request_is_refused(capfd: pytest.CaptureFixture[str]) -> None:
    """Nothing requested, after spaces and the first line."""
    for cls in ("go-binary", "rust-binary"):
        for request in ("", "  ", "\nlinux-amd64"):
            refused(
                ("targets", cls, request),
                f"::error::matrix: no targets requested — {NOTHING}",
                capfd=capfd,
            )


@pytest.mark.usefixtures("rows_state")
def test_an_unknown_go_target_names_the_go_form(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The Go hint names the target form."""
    refused(
        ("targets", "go-binary", "linux-amd64,plan9-386"),
        f"::error::matrix: unknown target plan9-386; {GO_HINT}",
        capfd=capfd,
    )


@pytest.mark.usefixtures("rows_state")
def test_an_unknown_rust_target_is_refused(capfd: pytest.CaptureFixture[str]) -> None:
    """The Rust hint is bare."""
    refused(
        ("targets", "rust-binary", "x86_64-pc-windows-msvc"),
        "::error::matrix: unknown target x86_64-pc-windows-msvc; canon targets only",
        capfd=capfd,
    )


@pytest.mark.usefixtures("rows_state")
def test_another_class_target_is_unknown(capfd: pytest.CaptureFixture[str]) -> None:
    """The tables are per class: a Go target is no Rust target."""
    refused(
        ("targets", "rust-binary", "linux-amd64"),
        "::error::matrix: unknown target linux-amd64; canon targets only",
        capfd=capfd,
    )


@pytest.mark.usefixtures("rows_state")
@pytest.mark.parametrize("asked", [",linux-amd64", "linux-amd64,,darwin-arm64", ","])
def test_an_empty_field_is_an_unknown_target(
    asked: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """A leading or inner empty field is kept, and refused."""
    refused(
        ("targets", "go-binary", asked),
        f"::error::matrix: unknown target ; {GO_HINT}",
        capfd=capfd,
    )


@pytest.mark.usefixtures("rows_state")
@pytest.mark.parametrize(
    "asked", ["linux-amd64,linux-amd64", "linux-amd64, linux-amd64,"]
)
def test_a_duplicate_is_refused(asked: str, capfd: pytest.CaptureFixture[str]) -> None:
    """The same target twice, spaced or not."""
    refused(
        ("targets", "go-binary", asked),
        "::error::matrix: duplicate target requested",
        capfd=capfd,
    )


@pytest.mark.usefixtures("rows_state")
def test_an_unknown_target_is_named_before_a_duplicate(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The shell checked every target before counting duplicates."""
    refused(
        ("targets", "go-binary", "linux-amd64,linux-amd64,plan9-386"),
        f"::error::matrix: unknown target plan9-386; {GO_HINT}",
        capfd=capfd,
    )


@pytest.mark.usefixtures("rows_state")
@pytest.mark.parametrize(
    "asked", [" linux-amd64 ", "linux-amd64,", "linux-amd64\ndarwin-arm64"]
)
def test_the_request_splits_as_the_shell_split_it(
    asked: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """Spaces removed, one trailing comma dropped, the first line only."""
    code = matrix.main(["targets", "go-binary", asked])
    out = capfd.readouterr().out
    assert code == 0
    assert 'expected=["linux-amd64"]\n' in out


@pytest.mark.usefixtures("rows_state")
def test_the_order_requested_is_the_order_planned(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """No sorting: the caller's order is the matrix's."""
    matrix.main(["targets", "go-binary", "darwin-arm64,linux-amd64"])
    out = capfd.readouterr().out
    assert 'expected=["darwin-arm64","linux-amd64"]\n' in out


@pytest.mark.usefixtures("rows_state")
@pytest.mark.parametrize("asked", ["", " "])
def test_pgrx_extension_an_empty_request_is_refused(
    asked: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """Nothing requested."""
    refused(
        ("pgrx-extension", MAPPING, asked),
        f"::error::matrix: no PG majors requested — {NOTHING}",
        capfd=capfd,
    )


@pytest.mark.usefixtures("rows_state")
@pytest.mark.parametrize("major", ["13", "19"])
def test_a_major_with_no_pinned_base_image_is_refused(
    major: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """A major the mapping does not carry, numeric or not."""
    refused(
        ("pgrx-extension", MAPPING, f"14,{major}"),
        f"::error::matrix: pg{major} {NO_IMAGE}",
        capfd=capfd,
    )


@pytest.mark.usefixtures("rows_state")
@pytest.mark.parametrize("major", ["x", "pg14", ""])
def test_a_non_numeric_major_is_refused(
    major: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """A field that is not a number is named as such, not as unpinned."""
    refused(
        ("pgrx-extension", MAPPING, f"{major},14"),
        f"::error::matrix: {major} is not a PG major",
        capfd=capfd,
    )


@pytest.mark.parametrize(
    "text",
    [
        '[build]\n"14" = "a@sha256:1"\n[smoke]\n',
        '[build]\n[smoke]\n"14" = "a@sha256:1"\n',
        '[build]\n"14" = ""\n[smoke]\n"14" = "a@sha256:1"\n',
        '[smoke]\n"14" = "a@sha256:1"\n',
    ],
)
def test_a_major_pinned_for_one_role_only_is_refused(
    rows_state: Rows, text: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """Both a build and a smoke image, or no plan."""
    refused(
        ("pgrx-extension", rows_state.mapping(text), "14"),
        f"::error::matrix: pg14 {NO_IMAGE}",
        capfd=capfd,
    )


@pytest.mark.usefixtures("rows_state")
def test_pgrx_extension_a_duplicate_is_refused(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The same major twice."""
    refused(
        ("pgrx-extension", MAPPING, "16,17,16"),
        "::error::matrix: duplicate PG major requested",
        capfd=capfd,
    )


@pytest.mark.usefixtures("rows_state")
def test_a_duplicate_is_named_before_an_unpinned_major(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The request's own rules run before the mapping's pins (#1531)."""
    refused(
        ("pgrx-extension", MAPPING, "16,13,16"),
        "::error::matrix: duplicate PG major requested",
        capfd=capfd,
    )


def test_each_major_builds_on_both_arches(
    rows_state: Rows, capfd: pytest.CaptureFixture[str]
) -> None:
    """Two legs per major, amd64 first, both images on each."""
    mapping = rows_state.mapping(
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
    status = matrix.main(["pgrx-extension", mapping, "16"])
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, outputs, notices)


def test_a_mapping_with_no_floor_prints_null(
    rows_state: Rows, capfd: pytest.CaptureFixture[str]
) -> None:
    """Jq's `-r .floor.glibc` printed null; the floor check is the build's."""
    mapping = rows_state.mapping('[build]\n"16" = "b"\n[smoke]\n"16" = "s"\n')
    matrix.main(["pgrx-extension", mapping, "16"])
    out = capfd.readouterr().out
    assert out.endswith("floor=null\n")


@pytest.mark.usefixtures("rows_state")
def test_pg19_is_planned_from_the_real_mapping(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The tracked mapping serves Postgres 19 (#1848).

    The pins are read from the file itself, so a Renovate roll of
    either digest moves nothing here; what is held is that 19 has
    both images and plans a cell per architecture.
    """
    notices = (
        "::notice::will build pg19 on ubuntu-24.04\n"
        "::notice::will build pg19 on ubuntu-24.04-arm\n"
    )
    code = matrix.main(["pgrx-extension", str(TRACKED), "19"])
    out, err = capfd.readouterr()
    assert (code, err) == (0, notices)
    pins = matrix.load(TRACKED)
    build, smoke = pins.roles["build"]["19"], pins.roles["smoke"]["19"]
    images = f'"build":"{build}","smoke":"{smoke}"'
    amd = f'{{"pg":"19","arch":"amd64","runner":"ubuntu-24.04",{images}}}'
    arm = f'{{"pg":"19","arch":"arm64","runner":"ubuntu-24.04-arm",{images}}}'
    assert out == (
        f'matrix={{"include":[{amd},{arm}]}}\n'
        'expected=["19-amd64","19-arm64"]\n'
        f"floor={pins.floor}\n"
    )


NAME = "docker/pgrx-base-images.toml"


def test_a_mapping_that_is_not_toml_is_refused(
    rows_state: Rows, capfd: pytest.CaptureFixture[str]
) -> None:
    """Named as not TOML, with the parser's reason."""
    code = matrix.main(["pgrx-extension", rows_state.mapping("[build\n"), "16"])
    out, err = capfd.readouterr()
    assert (code, out) == (1, "")
    assert err.startswith(f"::error::matrix: {NAME} is not TOML: "), err


def test_each_wrong_shape_is_refused_by_name(
    rows_state: Rows, capfd: pytest.CaptureFixture[str]
) -> None:
    """A role or the floor that is not a table, a pin that is not a string."""
    for text, said in (
        ('build = "x16"\n', f"[build] in {NAME} is not a table of majors"),
        ('[smoke]\n"16" = 16\n', f"smoke.16 in {NAME} is not a reference"),
        ("floor = false\n", f"[floor] in {NAME} is not a table"),
    ):
        modes: tuple[tuple[str, ...], ...] = (
            ("pgrx-extension", "16"),
            ("base-images",),
        )
        for argv in modes:
            refused(
                (argv[0], rows_state.mapping(text), *argv[1:]),
                f"::error::matrix: {said}",
                capfd=capfd,
            )


@pytest.mark.usefixtures("rows_state")
@pytest.mark.parametrize("asked", ["", "  "])
def test_pgrx_images_an_empty_request_is_refused(
    asked: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """Nothing requested."""
    refused(
        ("pgrx-images", asked),
        f"::error::matrix: no PG majors requested — {NOTHING}",
        capfd=capfd,
    )


@pytest.mark.usefixtures("rows_state")
@pytest.mark.parametrize("major", ["x", "1a", "pg14", "-1", "1.5", "٣"])
def test_pgrx_images_a_non_numeric_major_is_refused(
    major: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """ASCII digits only, as `^[0-9]+$` held it."""
    refused(
        ("pgrx-images", f"14,{major}"),
        f"::error::matrix: {major} is not a PG major",
        capfd=capfd,
    )


@pytest.mark.usefixtures("rows_state")
def test_an_empty_field_is_not_a_major(capfd: pytest.CaptureFixture[str]) -> None:
    """A leading empty field is kept, and refused."""
    refused(("pgrx-images", ",14"), "::error::matrix:  is not a PG major", capfd=capfd)


@pytest.mark.usefixtures("rows_state")
def test_pgrx_images_a_duplicate_is_refused(capfd: pytest.CaptureFixture[str]) -> None:
    """The same major twice would build and push one image twice (#1531)."""
    refused(
        ("pgrx-images", "14,14"),
        "::error::matrix: duplicate PG major requested",
        capfd=capfd,
    )


# requested_majors: every rule about the request itself, once (#1531).
def assert_refused(request: str, message: str) -> None:
    """Assert a request is refused with exactly `message`."""
    with pytest.raises(matrix.RefusalError) as caught:
        matrix.requested_majors(request)
    assert str(caught.value) == message


def test_requested_majors_an_empty_request_is_refused() -> None:
    """Nothing requested."""
    assert_refused("", f"no PG majors requested — {NOTHING}")


def test_requested_majors_a_non_numeric_major_is_refused() -> None:
    """A field that is not ASCII digits."""
    assert_refused("14,pg15", "pg15 is not a PG major")


def test_requested_majors_a_duplicate_is_refused() -> None:
    """The same major twice."""
    assert_refused("16,17,16", "duplicate PG major requested")


def test_a_clean_list_is_returned_in_order() -> None:
    """The order requested is the order returned."""
    majors: list[str] = ["18", "14", "16"]
    assert matrix.requested_majors("18,14,16") == majors


@pytest.mark.parametrize(
    "ref", ["postgres:14-bookworm", "postgres:14@sha512:ab", "postgres@"]
)
def test_a_reference_with_no_digest_is_refused_bare(
    rows_state: Rows, ref: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """The heredoc's SystemExit wording, unprefixed as it always was."""
    refused(
        ("base-images", rows_state.mapping(f'[smoke]\n"14" = "{ref}"\n')),
        f"matrix: smoke.14: no digest in {ref!r}",
        capfd=capfd,
    )


@pytest.mark.parametrize(
    "text", ["", '[floor]\nglibc = "2.36"\n', "[build]\n[smoke]\n"]
)
def test_an_empty_file_is_refused(
    rows_state: Rows, text: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """A mapping that pins nothing approves nothing."""
    refused(
        ("base-images", rows_state.mapping(text)),
        "::error::matrix: no pinned base images found — an empty"
        " approval"
        " set approves nothing",
        capfd=capfd,
    )


def test_a_cell_names_the_repository_without_its_tag(
    rows_state: Rows, capfd: pytest.CaptureFixture[str]
) -> None:
    """Docker Hub's short names are expanded; keys sort as strings."""
    mapping = rows_state.mapping(
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
    status = matrix.main(["base-images", mapping])
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        f'matrix={{"include":[{ten},{nine}]}}\n',
        notices,
    )


# A malformed command line is a usage error, never a plan.
@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param(
            [],
            "belt matrix: error: the following arguments are required: mode",
            id="nothing",
        ),
        pytest.param(
            ["targets", "oci-image", "x"],
            (
                "belt matrix targets: error: argument CLASS: invalid choice: "
                "'oci-image' (choose from 'go-binary', 'rust-binary')"
            ),
            id='["targets", "oci-image", "x"]',
        ),
        pytest.param(
            ["targets", "go-binary"],
            "belt matrix targets: error: the following arguments are required: REQUEST",
            id='["targets", "go-binary"]',
        ),
        pytest.param(
            ["pgrx-images"],
            (
                "belt matrix pgrx-images: error: the following arguments are "
                "required: REQUEST"
            ),
            id='["pgrx-images"]',
        ),
        pytest.param(
            ["base-images"],
            (
                "belt matrix base-images: error: the following arguments are "
                "required: MAPPING"
            ),
            id='["base-images"]',
        ),
        pytest.param(
            ["pgrx-extension", MAPPING],
            (
                "belt matrix pgrx-extension: error: the following arguments are "
                "required: REQUEST"
            ),
            id='["pgrx-extension", MAPPING]',
        ),
    ],
)
def test_malformed_command_lines(
    argv: list[str], error: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """A usage error: argparse's exit 2 and its error.

    A missing argument or an unknown class prints the usage.
    """
    with pytest.raises(SystemExit) as ended:
        matrix.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt matrix "), err
    assert err.splitlines()[-1] == error


# The workflows pass the caller's text after `--`: a request that reads
# as an option is a request, refused by name, never argparse's help.
@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param(
            ["targets", "go-binary", "--", "-h"],
            "::error::matrix: unknown target -h; canon targets only (<goos>-<goarch>)",
            id="targets",
        ),
        pytest.param(
            ["pgrx-images", "--", "-h"],
            "::error::matrix: -h is not a PG major",
            id="pgrx-images",
        ),
    ],
)
def test_a_request_that_reads_as_an_option_is_refused(
    argv: list[str],
    error: str,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`-h` after `--`: exit 1 naming it, no plan and no help."""
    monkeypatch.setenv("MISE_TASK_NAME", "")
    monkeypatch.setenv("ORG_FINDINGS", "")
    monkeypatch.setattr(sys, "argv", ["matrix"])
    status = matrix.main(argv)
    out, err = capfd.readouterr()
    assert (status, out, err) == (1, "", f"{error}\n")
