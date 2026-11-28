"""The pgrx images' staging and consumer proof, out of the workflow (#1381).

build-pgrx-images.yml staged the per-architecture trees and proved the
pushed image in 82 lines of inline shell with no test. The staging is
release/pgrx_images.py; the proof is release/pgrx-image-proof.sh running
the tracked docker/pgrx-consumer*.Dockerfile and docker/pgrx-*-proof.sh.

The staging rows plant real gzip tarballs and run the helper as the
workflow does, asserting each refusal's exact `::error::` line (the
wording the inline shell had). The proof rows put a recording `docker`
first on PATH and run the real script: what it builds, with which
arguments, and the Postgres-major branch (below 18, no path proof).
"""

import io
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

from belt import decoded
from belt.release import pgrx_images
from belt.typed_pytest import fixture
from tests import support

# A shipped library's mode; the FROM lines of a two-stage consumer
# Dockerfile; and the proof's calls below 18 (one build, one run) and
# at 18 (both recipes).
EXECUTABLE = 0o755
STAGES = 2
COPY_CALLS = 2
BOTH_CALLS = 4

RELEASE = support.RELEASE
CANON = RELEASE.parent
# The writer of workflow commands each proof container is given (#1660).
WRITER_MOUNT = (
    f"-v {CANON}/mise/findings.sh:/opt/org/findings.sh:ro"
    f" -v {CANON}/mise/workflow-command.sh:/opt/org/workflow-command.sh:ro"
    f" -v {CANON}/mise/tracked.sh:/opt/org/tracked.sh:ro"
    " -e ORG_FINDINGS_WRITER=/opt/org/findings.sh"
)
PROOF = RELEASE / "pgrx-image-proof.sh"
TABLE = CANON / "docker" / "pgrx-base-images.toml"
BASH = support.tool("bash")

VERSION = "1.2.3"
CRATE = "crates/edtf-postgres"


@fixture
def _outside_mise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name the helper by its command, as a run outside mise does (#1934).

    An in-process call reads argv, whatever task runs this suite; an
    empty ORG_FINDINGS records nothing (unset on a runner records to the
    job's file, #1976).
    """
    monkeypatch.setattr(sys, "argv", ["pgrx-images"])
    monkeypatch.delenv("MISE_TASK_NAME", raising=False)
    monkeypatch.setenv("ORG_FINDINGS", "")


pytestmark = pytest.mark.usefixtures("_outside_mise")


def tarball(path: Path, members: dict[str, bytes], links: dict[str, str]) -> None:
    """Write a gzip tarball with the given files and symlinks.

    Each file is 0755 when its name ends in `.so`, else 0644.

    """
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".so") else 0o644
            tar.addfile(info, io.BytesIO(data))
        for name, target in links.items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)


def cell(pg: str, *, lib: bool = True, share: bool = True) -> dict[str, bytes]:
    """Lay out one cell's tarball the way the extension build ships it.

    Returns:
        Member name to bytes: the pkgroot tree, and the tarball's second
        copy at the top (going away, #1274), which staging must not read.

    """
    out = {f"usr/lib/postgresql/{pg}/lib/second-copy.so": b"no"}
    if lib:
        out[f"pkgroot/usr/lib/postgresql/{pg}/lib/edtf_postgres.so"] = b"elf"
    if share:
        root = f"pkgroot/usr/share/postgresql/{pg}/extension"
        out[f"{root}/edtf_postgres.control"] = b"control"
        out[f"{root}/edtf_postgres--1.2.3.sql"] = b"sql"
    return out


def run(
    args: list[str], cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a helper the way the workflow step does.

    Returns:
        The finished process, output captured as text.

    """
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, check=False, env=env
    )


class Stage:
    """`pgrx_images.py stage`: every branch, messages asserted."""

    def __init__(self, tmp_path: Path) -> None:
        """Make a workspace with a tarballs/ directory, removed after."""
        self.root = tmp_path
        (self.root / "tarballs").mkdir()

    def plant(self, pg: str, arch: str, **kw: bool) -> str:
        """Plant one cell's tarball.

        Returns:
            The path the messages name, relative to the workspace.

        """
        name = f"edtf_postgres-{VERSION}-pg{pg}-linux-{arch}.tar.gz"
        links = {}
        if kw.pop("link", False):
            links[f"pkgroot/usr/lib/postgresql/{pg}/lib/edtf.so"] = "edtf_postgres.so"
        tarball(self.root / "tarballs" / name, cell(pg, **kw), links)
        return f"tarballs/{name}"


@fixture
def stage_state(tmp_path: Path) -> Stage:
    """Give each row its own Stage.

    Returns:
        The row's Stage.

    """
    return Stage(tmp_path)


def test_both_architectures_staged(
    stage_state: Stage,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The one layout, for amd64 and arm64, from pkgroot only."""
    for arch in ("amd64", "arm64"):
        stage_state.plant("17", arch, link=True)
    monkeypatch.chdir(stage_state.root)
    status = pgrx_images.main([
        "stage",
        "--pg",
        "17",
        "--version",
        VERSION,
        "--crate-dir",
        CRATE,
    ])
    _out, err = capfd.readouterr()
    assert status == 0, err
    for arch in ("amd64", "arm64"):
        image = stage_state.root / "staged" / arch / "image"
        found = sorted(
            str(p.relative_to(image)) for p in image.rglob("*") if not p.is_dir()
        )
        want: list[str] = [
            "lib/edtf.so",
            "lib/edtf_postgres.so",
            "share/extension/edtf_postgres--1.2.3.sql",
            "share/extension/edtf_postgres.control",
        ]
        assert want == found
        so = image / "lib" / "edtf_postgres.so"
        assert stat.S_IMODE(so.stat().st_mode) == EXECUTABLE
        assert (image / "lib" / "edtf.so").is_symlink()


def test_a_missing_tarball(
    stage_state: Stage,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """No amd64 tarball: the build did not produce the cell."""
    stage_state.plant("18", "arm64")
    monkeypatch.chdir(stage_state.root)
    status = pgrx_images.main([
        "stage",
        "--pg",
        "18",
        "--version",
        VERSION,
        "--crate-dir",
        CRATE,
    ])
    _out, err = capfd.readouterr()
    assert status == 1
    assert err == (
        "::error::pgrx-images: missing"
        " tarballs/edtf_postgres-1.2.3-pg18-linux-amd64.tar.gz"
        " — the extension build did not produce this cell\n"
    )


def test_a_missing_second_architecture(
    stage_state: Stage,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """amd64 stages, arm64 is missing and named."""
    stage_state.plant("18", "amd64")
    monkeypatch.chdir(stage_state.root)
    status = pgrx_images.main([
        "stage",
        "--pg",
        "18",
        "--version",
        VERSION,
        "--crate-dir",
        CRATE,
    ])
    _out, err = capfd.readouterr()
    assert status == 1
    assert err == (
        "::error::pgrx-images: missing"
        " tarballs/edtf_postgres-1.2.3-pg18-linux-arm64.tar.gz"
        " — the extension build did not produce this cell\n"
    )


def test_a_tarball_without_lib(
    stage_state: Stage,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Only the second copy's lib: refused, never read (#1274)."""
    shown = stage_state.plant("16", "amd64", lib=False)
    stage_state.plant("16", "arm64")
    monkeypatch.chdir(stage_state.root)
    status = pgrx_images.main([
        "stage",
        "--pg",
        "16",
        "--version",
        VERSION,
        "--crate-dir",
        CRATE,
    ])
    _out, err = capfd.readouterr()
    assert status == 1
    assert (
        f"::error::pgrx-images: {shown} has no pkgroot/usr/lib/postgresql/16/lib\n"
    ) == err
    image_lib = stage_state.root / "staged" / "amd64" / "image" / "lib"
    assert not image_lib.exists()


def test_a_tarball_without_extension(
    stage_state: Stage,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """No share/postgresql/<major>/extension: refused by path."""
    shown = stage_state.plant("15", "amd64", share=False)
    monkeypatch.chdir(stage_state.root)
    status = pgrx_images.main([
        "stage",
        "--pg",
        "15",
        "--version",
        VERSION,
        "--crate-dir",
        CRATE,
    ])
    _out, err = capfd.readouterr()
    assert status == 1
    assert (
        f"::error::pgrx-images: {shown} has no"
        f" pkgroot/usr/share/postgresql/15/extension\n"
    ) == err


def build_rows() -> dict[str, str]:
    """Read the table's [build] rows.

    Returns:
        Postgres major to its digest-pinned build image.

    """
    table = decoded.load_toml(TABLE.read_text("utf-8"))
    rows = decoded.as_object(table.get("build"), "[build]")
    return {pg: decoded.as_str(ref, f"[build].{pg}") for pg, ref in rows.items()}


A_TABLE_THAT_IS_NOT_MAJORS_TO_REFERENCES_ROWS = [
    ("build = false\n", "[build] in {t} is not a table of majors"),
    ('build = "x16"\n', "[build] in {t} is not a table of majors"),
    ("build = {16 = []}\n", "pg16's build image in {t} is not a reference"),
]


# `pgrx_images.py base-image`: the [build] row, or a refusal.
def test_every_major_names_its_build_image(capfd: pytest.CaptureFixture[str]) -> None:
    """Each [build] row is what the verb prints."""
    rows = build_rows()
    for pg, ref in rows.items():
        status = pgrx_images.main(["base-image", "--pg", pg, "--table", str(TABLE)])
        out, _err = capfd.readouterr()
        assert (0, f"{ref}\n") == (status, out)
        assert "@sha256:" in ref


def test_a_major_with_no_row(capfd: pytest.CaptureFixture[str]) -> None:
    """A major the table does not pin is refused by name."""
    status = pgrx_images.main(["base-image", "--pg", "13", "--table", str(TABLE)])
    _out, err = capfd.readouterr()
    assert status == 1
    assert f"::error::pgrx-images: pg13 has no build image in {TABLE}\n" == err


@pytest.mark.parametrize(
    ("text", "want"), A_TABLE_THAT_IS_NOT_MAJORS_TO_REFERENCES_ROWS
)
def test_a_table_that_is_not_majors_to_references(
    text: str, want: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """#1556's shrunk examples are refused by name.

    `build = false` and a row of `[]` died with a TypeError, and
    `build = "x16"` matched pg 16 as a substring before dying.
    """
    with tempfile.TemporaryDirectory() as tmp:
        table = Path(tmp) / "pgrx-base-images.toml"
        table.write_text(text, "utf-8")
        status = pgrx_images.main(["base-image", "--pg", "16", "--table", str(table)])
        _out, err = capfd.readouterr()
        assert (1, f"::error::pgrx-images: {want.format(t=table)}\n") == (
            status,
            err,
        )


def build_image(pg: str) -> str:
    """Read the [build] row the proof must use.

    Returns:
        The digest-pinned reference.

    """
    return build_rows()[pg]


IMAGE = "ghcr.io/example/edtf-postgres"
PINNED = IMAGE + "@sha256:" + "a" * 64


# `pgrx_images.py consumer-dockerfile`: the recipes, pinned or refused.
def test_the_copy_recipe(capfd: pytest.CaptureFixture[str]) -> None:
    """The text the workflow's first heredoc wrote, byte for byte."""
    status = pgrx_images.main([
        "consumer-dockerfile",
        "--recipe",
        "copy",
        "--image",
        PINNED,
        "--base",
        build_image("17"),
        "--pg",
        "17",
    ])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert (
        f"FROM {PINNED} AS ext\n"
        f"FROM {build_image('17')}\n"
        "COPY --from=ext /lib/ /usr/lib/postgresql/17/lib/\n"
        "COPY --from=ext /share/extension/ /usr/share/postgresql/17/extension/\n"
    ) == out


def test_the_path_recipe(capfd: pytest.CaptureFixture[str]) -> None:
    """The text the workflow's second heredoc wrote, byte for byte."""
    status = pgrx_images.main([
        "consumer-dockerfile",
        "--recipe",
        "path",
        "--image",
        PINNED,
        "--base",
        build_image("18"),
        "--pg",
        "18",
    ])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert (
        f"FROM {PINNED} AS ext\n"
        f"FROM {build_image('18')}\n"
        "COPY --from=ext / /extensions/ext/\n"
    ) == out


def test_every_rendered_from_is_pinned(capfd: pytest.CaptureFixture[str]) -> None:
    """Every recipe on every [build] row: each FROM carries a digest."""
    rows = build_rows()
    for pg, base in rows.items():
        for recipe in ("copy", "path"):
            status = pgrx_images.main([
                "consumer-dockerfile",
                "--recipe",
                recipe,
                "--image",
                PINNED,
                "--base",
                base,
                "--pg",
                pg,
            ])
            out, err = capfd.readouterr()
            assert status == 0, err
            from_lines = [ln for ln in out.splitlines() if ln.startswith("FROM")]
            assert len(from_lines) == STAGES
            for line in from_lines:
                assert "@sha256:" in line.split()[1]


def test_an_unpinned_extension_image(capfd: pytest.CaptureFixture[str]) -> None:
    """A tag, not a digest: refused, nothing rendered."""
    status = pgrx_images.main([
        "consumer-dockerfile",
        "--recipe",
        "copy",
        "--image",
        IMAGE + ":1.2.3-pg17-artifact",
        "--base",
        build_image("17"),
        "--pg",
        "17",
    ])
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert (
        f"::error::pgrx-images: the extension image"
        f" {IMAGE}:1.2.3-pg17-artifact is not"
        " pinned by digest (@sha256:); the consumer proof builds only from"
        " pinned references\n"
    ) == err


def test_an_unpinned_base_image(capfd: pytest.CaptureFixture[str]) -> None:
    """A stock postgres by tag: refused, nothing rendered."""
    status = pgrx_images.main([
        "consumer-dockerfile",
        "--recipe",
        "path",
        "--image",
        PINNED,
        "--base",
        "postgres:18-bookworm",
        "--pg",
        "18",
    ])
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert err == (
        "::error::pgrx-images: the base image postgres:18-bookworm is not"
        " pinned by digest"
        " (@sha256:); the consumer proof builds only from pinned references\n"
    )


# A recording docker: each call's arguments, then the Dockerfile it was
# handed, so a row sees what the proof actually builds.
DOCKER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${DOCKER_LOG}"
args=("$@")
for i in "${!args[@]}"; do
  if [[ ${args[i]} == --file ]]; then
    sed 's/^/  | /' "${args[i + 1]}" >> "${DOCKER_LOG}"
  fi
done
"""


class Proof:
    """`pgrx-image-proof.sh`: the recipes it runs, per Postgres major."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Put a recording docker first on PATH, beside a digest record."""
        self.make_env = env
        self.root = tmp_path
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(DOCKER, "utf-8")
        docker.chmod(0o755)
        self.log = self.root / "docker.log"
        self.record = self.root / "pg.json"
        self.staged = self.root / "staged" / "amd64" / "image" / "share" / "extension"
        self.staged.mkdir(parents=True)
        (self.staged / "edtf_postgres.control").write_text("control\n")
        (self.staged / "edtf_postgres--1.2.3.sql").write_text("sql\n")
        self.env = self.make_env(
            PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            DOCKER_LOG=str(self.log),
        )

    def prove(
        self, pg: str, digest: str = "sha256:" + "a" * 64
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run the proof for one major against the canon tree.

        Returns:
            The finished process and the recorder's lines: each docker
            call, and under a build the Dockerfile it built, `  | `-led.

        """
        self.record.write_text(f'{{"pg": "{pg}", "digest": "{digest}"}}\n')
        result = run(
            [
                BASH,
                str(PROOF),
                IMAGE,
                str(self.record),
                pg,
                str(CANON),
                str(self.staged),
            ],
            self.root,
            self.env,
        )
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return result, lines


@fixture
def proof(tmp_path: Path, env: support.Env) -> Proof:
    """Give each row its own Proof.

    Returns:
        The row's Proof.

    """
    return Proof(tmp_path, env=env)


def test_below_18_runs_the_copy_recipe_only(proof: Proof) -> None:
    """pg17: one build, one run, and the branch says why it stops."""
    result, lines = proof.prove("17")
    assert result.returncode == 0, result.stderr
    assert (
        result.stdout
        == "pg17: below 18, no extension_control_path; path proof not applicable\n"
    )
    calls = [ln for ln in lines if not ln.startswith("  | ")]
    built = [ln[4:] for ln in lines if ln.startswith("  | ")]
    assert len(calls) == COPY_CALLS, lines
    assert re.search(
        r"^buildx build --file \S+/copy\.Dockerfile --load"
        r" --tag consumer-proof:pg17 \S+$",
        calls[0],
    )
    want: list[str] = [
        f"FROM {PINNED} AS ext",
        f"FROM {build_image('17')}",
        "COPY --from=ext /lib/ /usr/lib/postgresql/17/lib/",
        "COPY --from=ext /share/extension/ /usr/share/postgresql/17/extension/",
    ]
    assert want == built
    docker_dir = (CANON / "docker").resolve()
    assert (
        f"run --rm -v {docker_dir}:/proof:ro {WRITER_MOUNT} consumer-proof:pg17"
        " bash /proof/pgrx-copy-proof.sh edtf_postgres"
    ) == calls[1]


def test_18_adds_the_path_recipe(proof: Proof) -> None:
    """pg18: the copy recipe, then the image mounted whole, as postgres."""
    result, lines = proof.prove("18")
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    calls = [ln for ln in lines if not ln.startswith("  | ")]
    built = [ln[4:] for ln in lines if ln.startswith("  | ")]
    assert len(calls) == BOTH_CALLS, lines
    assert re.search(
        r"^buildx build --file \S+/path\.Dockerfile --load"
        r" --tag consumer-path-proof:pg18 \S+$",
        calls[2],
    )
    want: list[str] = [
        f"FROM {PINNED} AS ext",
        f"FROM {build_image('18')}",
        "COPY --from=ext / /extensions/ext/",
    ]
    assert want == built[4:]
    docker_dir = (CANON / "docker").resolve()
    assert (
        f"run --rm --user postgres -v {docker_dir}:/proof:ro {WRITER_MOUNT}"
        " consumer-path-proof:pg18 bash /proof/pgrx-path-proof.sh"
    ) == calls[3]


def test_a_major_with_no_build_image_builds_nothing(proof: Proof) -> None:
    """No [build] row: refused before any docker call."""
    result, lines = proof.prove("13")
    assert result.returncode == 1
    assert "::error::pgrx-images: pg13 has no build image in" in result.stderr
    assert list[str]() == lines


def test_a_record_without_a_digest_builds_nothing(proof: Proof) -> None:
    """An empty digest renders no Dockerfile, so docker never runs."""
    result, lines = proof.prove("18", digest="")
    assert result.returncode == 1
    assert (
        f"::error::pgrx-images: the extension image {IMAGE}@ is not pinned by digest"
    ) in result.stderr
    assert list[str]() == lines


@pytest.mark.usefixtures("proof")
@pytest.mark.parametrize("name", ["pgrx-copy-proof.sh", "pgrx-path-proof.sh"])
def test_the_in_image_scripts_are_tracked(name: str) -> None:
    """What runs inside the images is files a person can read and run."""
    assert os.access(CANON / "docker" / name, os.X_OK)


def test_a_staged_tree_without_one_extension_builds_nothing(proof: Proof) -> None:
    """None or two control files in the artifact's tree: no docker call."""
    (proof.staged / "edtf_postgres.control").unlink()
    result, lines = proof.prove("17")
    assert result.returncode == 1
    assert "holds no extension control file" in result.stderr
    assert list[str]() == lines
    (proof.staged / "a.control").write_text("")
    (proof.staged / "b.control").write_text("")
    result, lines = proof.prove("17")
    assert result.returncode == 1
    assert "holds 2 extension control files (a, b)" in result.stderr
    assert list[str]() == lines


class ExtensionName:
    """`pgrx_images.py extension-name`: the one shipped extension (#1439)."""

    def __init__(self, tmp_path: Path) -> None:
        """Make an empty staged share/extension tree, removed after."""
        self.tree = tmp_path


@fixture
def extension_name_state(tmp_path: Path) -> ExtensionName:
    """Give each row its own ExtensionName.

    Returns:
        The row's ExtensionName.

    """
    return ExtensionName(tmp_path)


def test_one_control_file_names_the_extension(
    extension_name_state: ExtensionName, capfd: pytest.CaptureFixture[str]
) -> None:
    """The stem of the one control file, SQL scripts beside it ignored."""
    (extension_name_state.tree / "edtf_postgres.control").write_text("")
    (extension_name_state.tree / "edtf_postgres--1.2.3.sql").write_text("")
    status = pgrx_images.main([
        "extension-name",
        "--tree",
        str(extension_name_state.tree),
    ])
    out, _err = capfd.readouterr()
    assert (status, out) == (0, "edtf_postgres\n")


def test_no_control_file(
    extension_name_state: ExtensionName, capfd: pytest.CaptureFixture[str]
) -> None:
    """An artifact that ships no extension is refused by name."""
    (extension_name_state.tree / "edtf_postgres--1.2.3.sql").write_text("")
    status = pgrx_images.main([
        "extension-name",
        "--tree",
        str(extension_name_state.tree),
    ])
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert (
        f"::error::pgrx-images: {extension_name_state.tree} holds no extension "
        "control file;"
        f" the artifact"
        " image ships no extension for the consumer proof to create\n"
    ) == err


def test_two_control_files(
    extension_name_state: ExtensionName, capfd: pytest.CaptureFixture[str]
) -> None:
    """Two control files: the proof cannot tell which it is proving."""
    (extension_name_state.tree / "edtf_postgres.control").write_text("")
    (extension_name_state.tree / "amcheck.control").write_text("")
    status = pgrx_images.main([
        "extension-name",
        "--tree",
        str(extension_name_state.tree),
    ])
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert (
        f"::error::pgrx-images: {extension_name_state.tree} holds 2 extension "
        "control files"
        f" (amcheck,"
        " edtf_postgres); the consumer proof creates the one extension the"
        " artifact image ships and cannot tell which\n"
    ) == err


COPY_PROOF = CANON / "docker" / "pgrx-copy-proof.sh"
SU = """#!/usr/bin/env bash
# su postgres -c CMD
exec bash -c "$3"
"""
PSQL = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${PSQL_LOG}"
"""


class CopyProof:
    """docker/pgrx-copy-proof.sh, run for real against a planted root."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant a stock postgres's directories and stub su and psql."""
        self.make_env = env
        self.root = tmp_path
        share = self.root / "usr" / "share" / "postgresql" / "17" / "extension"
        share.mkdir(parents=True)
        # The stock image's contrib control file that sorts first on 17 and
        # 18 (measured on #1439), beside the one the artifact shipped.
        (share / "amcheck.control").write_text("")
        (share / "edtf_postgres.control").write_text("")
        self.share = share
        bin_dir = self.root / "usr" / "lib" / "postgresql" / "17" / "bin"
        bin_dir.mkdir(parents=True)
        stubs = self.root / "stubs"
        stubs.mkdir()
        for path, text in (
            (bin_dir / "initdb", "#!/usr/bin/env bash\n"),
            (bin_dir / "pg_ctl", "#!/usr/bin/env bash\n"),
            (stubs / "su", SU),
            (stubs / "psql", PSQL),
        ):
            path.write_text(text)
            path.chmod(0o755)
        self.log = self.root / "psql.log"
        self.env = self.make_env(
            PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
            PGRX_PROOF_ROOT=str(self.root),
            PSQL_LOG=str(self.log),
        )


@fixture
def copy_proof(tmp_path: Path, env: support.Env) -> CopyProof:
    """Give each row its own CopyProof.

    Returns:
        The row's CopyProof.

    """
    return CopyProof(tmp_path, env=env)


def test_copy_proof_creates_the_shipped_extension(copy_proof: CopyProof) -> None:
    """Create edtf_postgres, though amcheck sorts first in the destination."""
    result = run(
        [BASH, str(COPY_PROOF), "edtf_postgres"], copy_proof.root, copy_proof.env
    )
    assert result.returncode == 0, result.stderr
    want: list[str] = ["-c CREATE EXTENSION edtf_postgres;"]
    assert want == copy_proof.log.read_text().splitlines()
    assert result.stdout == "created extension edtf_postgres from the COPY recipe\n"


def test_a_copy_that_did_not_land(copy_proof: CopyProof) -> None:
    """The shipped control file is absent from the destination: refused."""
    (copy_proof.share / "edtf_postgres.control").unlink()
    result = run(
        [BASH, str(COPY_PROOF), "edtf_postgres"], copy_proof.root, copy_proof.env
    )
    assert result.returncode == 1
    assert result.stderr == (
        "::error::pgrx-copy-proof.sh: edtf_postgres: edtf_postgres.control"
        " is not under /usr/share/postgresql/*/extension; the COPY did not"
        " place the shipped extension\n"
    )
    assert not copy_proof.log.exists()
