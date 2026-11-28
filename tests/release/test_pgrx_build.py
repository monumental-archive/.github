"""The pgrx-extension build's tracked scripts, driven from Python (#1380).

build-pgrx-extension.yml carried this logic as shell three quotes deep
inside `docker run … bash -c '… su postgres -c "…"'`, with no tests. It
now lives in belt/release/pgrx/ and release/pgrx/ as plain scripts the
containers run from the pinned canon tree, and in
release/pgrx/previous_release.py on the runner. Each guard branch is a
row here, and every refusal row asserts its message, word for word,
against the wording the workflow carried. previous_release.py's rows
call its `main` in this process (#1698).

The debug-split and glibc rows run the real binutils over a small real
shared library built here, so they need `cc`, `readelf`, `objcopy` and
`strip` (GNU, ELF). Where those are absent (a Mac) the rows are skipped
by name; on the gate's runner a missing tool is a failure, never a skip
that reads like a pass (#364).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from belt.release.pgrx import previous_release
from belt.typed_pytest import fixture
from tests import support

CANON = support.CANON
PGRX = CANON / "release" / "pgrx"
BASH = support.tool("bash")
ON_RUNNER = os.environ.get("GITHUB_ACTIONS") == "true"
# What a helper's call answered: its status, stdout and stderr.
type Done = tuple[int, str, str]


@fixture
def _outside_mise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name the helper by its command, as a run outside mise does (#1934).

    An in-process call reads argv, whatever task runs this suite; an
    empty ORG_FINDINGS records nothing (unset on a runner records to the
    job's file, #1976).
    """
    monkeypatch.setattr(sys, "argv", ["previous-release"])
    monkeypatch.delenv("MISE_TASK_NAME", raising=False)
    monkeypatch.setenv("ORG_FINDINGS", "")


pytestmark = pytest.mark.usefixtures("_outside_mise")


def strings(*items: str) -> list[str]:
    """List the given strings, typed as strings for the assertions.

    Returns:
        The strings, in order.

    """
    return list(items)


def run(
    argv: list[str],
    env: dict[str, str],
    cwd: Path | None = None,
    *,
    make_env: support.Env,
) -> subprocess.CompletedProcess[str]:
    """Run one script with `env` laid over the process environment.

    Returns:
        The completed process, output captured as text.

    """
    return subprocess.run(
        argv,
        env=make_env(**env),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def stub(bindir: Path, name: str, body: str) -> None:
    """Write an executable bash stub named `name` into `bindir`."""
    path = bindir / name
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)


# --------------------------------------------------------------------
# floor.sh: the PostgreSQL floor block heads every script that installs
# or updates TO this release (#1274).
# --------------------------------------------------------------------

GENERATED = "CREATE FUNCTION demo() RETURNS int LANGUAGE sql AS 'SELECT 1';\n"


def filled(ext: str, version: str, xy: str, num: str) -> str:
    """Fill the template the way floor.sh fills it.

    Returns:
        The block, with the blank line floor.sh leaves after it.

    """
    block = (PGRX / "floor.sql.in").read_text().rstrip("\n")
    for field, value in (
        ("@NUM@", num),
        ("@XY@", xy),
        ("@EXT@", ext),
        ("@VERSION@", version),
    ):
        block = block.replace(field, value)
    return block + "\n\n"


class FloorBlock:
    """floor.sh against an extension directory as the package lays it out."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Lay out a control, the install script and the upgrade scripts."""
        self.make_env = env
        self.extdir = Path(str(tmp_path))
        (self.extdir / "demo_ext.control").write_text("default_version = '1.2.0'\n")
        for script in (
            "demo_ext--1.2.0.sql",
            "demo_ext--1.0.0--1.2.0.sql",
            "demo_ext--1.1.0--1.2.0.sql",
            "demo_ext--1.0.0--1.1.0.sql",
            "demo_ext--1.1.0.sql",
        ):
            (self.extdir / script).write_text(GENERATED)

    def floor(self) -> subprocess.CompletedProcess[str]:
        """Run floor.sh at version 1.2.0 over a build against 18.7.

        Returns:
            The completed process.

        """
        return run(
            [BASH, str(PGRX / "floor.sh"), str(self.extdir), "1.2.0", "18.7", "180007"],
            {},
            make_env=self.make_env,
        )


@fixture
def floor_block(tmp_path: Path, env: support.Env) -> FloorBlock:
    """Give each row its own FloorBlock.

    Returns:
        The row's FloorBlock.

    """
    return FloorBlock(tmp_path, env=env)


def test_install_and_upgrade_scripts_to_the_release_carry_it(
    floor_block: FloorBlock,
) -> None:
    """The install script and every upgrade TO the version, nothing else."""
    result = floor_block.floor()
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        result.stdout == "::notice::demo_ext 1.2.0 was built against PostgreSQL 18.7\n"
    )
    block = filled("demo_ext", "1.2.0", "18.7", "180007")
    for script in (
        "demo_ext--1.2.0.sql",
        "demo_ext--1.0.0--1.2.0.sql",
        "demo_ext--1.1.0--1.2.0.sql",
    ):
        assert block + GENERATED == (floor_block.extdir / script).read_text()
    for script in ("demo_ext--1.0.0--1.1.0.sql", "demo_ext--1.1.0.sql"):
        assert (floor_block.extdir / script).read_text() == GENERATED


def test_the_filled_block_names_the_floor(floor_block: FloorBlock) -> None:
    """No placeholder survives, and the number and X.Y are the build's."""
    floor_block.floor()
    text = (floor_block.extdir / "demo_ext--1.2.0.sql").read_text()
    assert "@" not in text
    assert text.startswith("DO $pgrx_floor$\n")
    assert "< 180007 THEN" in text
    assert "'demo_ext 1.2.0 was built against PostgreSQL 18.7;" in text


@pytest.mark.parametrize(
    "script", ["demo_ext--1.2.0.sql", "demo_ext--1.1.0--1.2.0.sql"]
)
def test_a_script_already_using_the_tag_is_refused(
    floor_block: FloorBlock, script: str
) -> None:
    """The dollar-quote tag must be one no generated script uses."""
    planted = "SELECT $pgrx_floor$ x $pgrx_floor$;\n"
    (floor_block.extdir / script).write_text(planted)
    result = floor_block.floor()
    assert result.returncode == 1
    assert (
        f"::error::floor.sh: {script} already uses the pgrx_floor tag\n"
        == result.stderr
    )
    assert planted == (floor_block.extdir / script).read_text()


def test_no_script_for_the_version_is_not_an_error(floor_block: FloorBlock) -> None:
    """A control whose version has no script yet leaves the tree alone."""
    for script in floor_block.extdir.glob("*.sql"):
        script.unlink()
    result = floor_block.floor()
    assert result.returncode == 0, result.stderr
    none: list[Path] = []
    left: list[Path] = list(floor_block.extdir.glob("*.sql"))
    assert none == left


# --------------------------------------------------------------------
# previous-release: the control-file refusal and the previous
# tarball, against a stub `gh` that serves a release listing.
# --------------------------------------------------------------------

# The stub answers from one listing in the REST shape (`tag_name`,
# `draft`, `assets[].name`), so every call a version of the helper can
# make reads the same forge: the listing `gh api` streams, one release's
# assets, a download, and `gh release view` with no tag, which is the
# LATEST release (STUB_LATEST). The helper no longer asks for that last
# one (#1592); it is answered so a row run against the code it replaced
# fails on what that code decided, not on a refused call.
GH_STUB = """
printf '%s\\n' "$*" >> "${GH_LOG}"
expr="."
tag=""
pattern=""
out=""
verb="$1 $2"
shift 2
while (($#)); do
  case "$1" in
    --jq) expr="$2"; shift ;;
    --pattern) pattern="$2"; shift ;;
    --output) out="$2"; shift ;;
    --repo | --json | -q) shift ;;
    -*) ;;
    *) tag="$1" ;;
  esac
  shift
done
[[ -n ${STUB_LISTING} ]] || { echo "HTTP 502" >&2; exit 1; }
case "${verb}" in
  api\\ *)
    jq -r "${expr}" "${STUB_LISTING}"
    ;;
  "release view")
    if [[ -z ${tag} ]]; then
      [[ -n ${STUB_LATEST} ]] || { echo "release not found" >&2; exit 1; }
      printf '%s\\n' "${STUB_LATEST}"
      exit 0
    fi
    one=$(jq --arg t "${tag}" 'first(.[] | select(.tag_name == $t))' "${STUB_LISTING}")
    [[ -n ${one} ]] || { echo "release not found" >&2; exit 1; }
    jq -r "${expr}" <<< "${one}"
    ;;
  "release download")
    hits=$(jq --arg t "${tag}" --arg p "${pattern}" \\
      '[.[] | select(.tag_name == $t) | .assets[].name | select(. == $p)] | length' \\
      "${STUB_LISTING}")
    [[ ${hits} != 0 && -n ${STUB_TARBALL} ]] || { echo "no assets match" >&2; exit 1; }
    cp "${STUB_TARBALL}" "${out}"
    ;;
esac
"""

JQ = support.optional_tool("jq")
TESTDATA = Path(__file__).resolve().parent / "testdata"
DEMO_CELL = "demo_ext-1.1.0-pg18-linux-arm64.tar.gz"


def release(tag: str, *assets: str, draft: bool = False) -> dict[str, object]:
    """Shape one release as the REST listing does.

    Returns:
        The release, with only the fields the helper reads.

    """
    return {
        "assets": [{"name": name} for name in assets],
        "draft": draft,
        "tag_name": tag,
    }


class PreviousRelease:
    """The rows share one directory: a stub gh, a crate, a log."""

    CRATE = "crates/demo-ext"

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Put a stub gh first on PATH and a crate at CRATE."""
        self.make_env = env
        if not JQ:
            pytest.skip("jq is a belt tool and drives the gh stub")
        self.root = Path(str(tmp_path))
        bindir = self.root / "bin"
        bindir.mkdir()
        stub(bindir, "gh", GH_STUB)
        self.gh = bindir / "gh"
        self.sql = self.root / self.CRATE / "sql"
        self.sql.mkdir(parents=True)
        self.log = self.root / "gh.log"
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "GH_LOG": str(self.log),
            "STUB_LISTING": "",
            "STUB_LATEST": "",
            "STUB_TARBALL": "",
        }

    def forge(self, releases: list[dict[str, object]], latest: str = "") -> None:
        """Serve `releases` as the listing, and `latest` as GitHub's latest."""
        listing = self.root / "listing.json"
        listing.write_text(json.dumps(releases))
        self.env["STUB_LISTING"] = str(listing)
        self.env["STUB_LATEST"] = latest

    def graph(self, *files: str) -> None:
        """Put these files, empty, in the crate's `sql/` directory."""
        for name in files:
            (self.sql / name).write_text("")

    def enter(self, monkeypatch: pytest.MonkeyPatch, **stubs: str) -> None:
        """Stand in the row's directory as a step meets the helper.

        The helper resolves `gh` on PATH once, at import, so the step is
        given the stub it would have found first on its PATH. `stubs`
        set what the stub answers, beside the row's own.

        """
        monkeypatch.setattr(previous_release, "GH", str(self.gh))
        answers = {f"STUB_{k.upper()}": v for k, v in stubs.items()}
        for name, value in self.make_env(**(self.env | answers)).items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(self.root)

    def previous(self, mode: str, *extra: str, version: str = "1.2.0") -> list[str]:
        """Ask in `mode` at `version` for CRATE.

        Returns:
            The helper's arguments.

        """
        args = [mode, "--repo", "acme/demo", "--version", version]
        return [*args, "--crate-dir", self.CRATE, *extra]

    def gh_calls(self) -> list[str]:
        """Each gh invocation, its arguments joined by spaces.

        Returns:
            The calls in order.

        """
        return self.log.read_text().splitlines() if self.log.exists() else []


@fixture
def previous_release_state(tmp_path: Path, env: support.Env) -> PreviousRelease:
    """Give each row its own PreviousRelease.

    Returns:
        The row's PreviousRelease.

    """
    return PreviousRelease(tmp_path, env=env)


@pytest.mark.parametrize(
    ("releases", "latest"), [([], ""), ([release("release-1")], "release-1")]
)
def test_no_previous_release(
    previous_release_state: PreviousRelease,
    releases: list[dict[str, object]],
    latest: str,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No release at all, or none carrying this extension's tarballs."""
    previous_release_state.forge(releases, latest)
    previous_release_state.graph("demo_ext--1.0.0--1.1.0.sql")
    previous_release_state.enter(monkeypatch)
    status = previous_release.main(previous_release_state.previous("guard"))
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == (
        "::notice::no previous release; first publish needs no upgrade path\n"
    )


def test_a_rerun(
    previous_release_state: PreviousRelease,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release already carries THIS version: nothing to upgrade from."""
    previous_release_state.forge([
        release("v1.2.0", "demo_ext-1.2.0-pg18-linux-arm64.tar.gz")
    ])
    previous_release_state.enter(monkeypatch)
    status = previous_release.main(previous_release_state.previous("guard"))
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == "::notice::re-run of 1.2.0; upgrade path not applicable\n"
    assert len(previous_release_state.gh_calls()) == 1


def test_the_extensions_first_publish(
    previous_release_state: PreviousRelease,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A previous release that shipped no tarball of this extension (#87).

    One meaning of first publish now (#1592): no version in the graph
    is installable. It used to be said per tag, naming GitHub's latest
    release, which is no longer what anything asks.
    """
    previous_release_state.forge([release("v1.1.0", "demo-cli.tar.gz")], "v1.1.0")
    previous_release_state.graph("demo_ext--1.0.0--1.1.0.sql")
    previous_release_state.enter(monkeypatch)
    status = previous_release.main(previous_release_state.previous("guard"))
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == "::notice::no previous release; first publish needs no upgrade path\n"
    assert previous_release_state.gh_calls()[0] == (
        "api repos/acme/demo/releases --paginate --jq"
        " .[] | select(.draft | not) | {tag: .tag_name, asset: .assets[]?.name}"
        ' | "\\(.tag)\\t\\(.asset)"'
    )


def test_a_missing_upgrade_path_is_refused(
    previous_release_state: PreviousRelease,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shipped before, no sql/<ext>--prev--new.sql: stranded, refused."""
    previous_release_state.forge([release("v1.1.0", DEMO_CELL)], "v1.1.0")
    previous_release_state.graph("demo_ext--1.0.0--1.1.0.sql")
    previous_release_state.enter(monkeypatch)
    status = previous_release.main(previous_release_state.previous("guard"))
    err = capfd.readouterr().err
    assert status == 1
    assert err == (
        "::error::previous-release: no upgrade path"
        " crates/demo-ext/sql/demo_ext--1.1.0--1.2.0.sql"
        " from published 1.1.0 (v1.1.0) — existing installations would be"
        " stranded\n"
    )


def test_a_present_upgrade_path_passes(
    previous_release_state: PreviousRelease,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The derived script is there: the release may proceed."""
    previous_release_state.forge([release("v1.1.0", DEMO_CELL)], "v1.1.0")
    previous_release_state.graph(
        "demo_ext--1.0.0--1.1.0.sql", "demo_ext--1.1.0--1.2.0.sql"
    )
    previous_release_state.enter(monkeypatch)
    status = previous_release.main(previous_release_state.previous("guard"))
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == "::notice::upgrade path 1.1.0 -> 1.2.0 present\n"


def test_a_failed_listing_read_is_red(
    previous_release_state: PreviousRelease,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gh failing on the listing is a red with gh's own words, never a pass."""
    args = ["guard", "--repo", "acme/demo", "--version", "1.2.0"]
    previous_release_state.enter(monkeypatch)
    status = previous_release.main([*args, "--crate-dir", previous_release_state.CRATE])
    out, err = capfd.readouterr()
    assert status == 1
    assert not out
    assert err == "HTTP 502\n"


CELL = ("--pg", "18", "--arch", "arm64")


class PreviousTarball(PreviousRelease):
    """`fetch`: the predecessor's tarball of this cell, or a skip."""

    def fetch(self) -> list[str]:
        """Fetch for pg18/arm64 into the row's directory.

        Returns:
            The helper's arguments.

        """
        dest = (
            "--tarball",
            str(self.root / "prev.tar.gz"),
            "--dest",
            str(self.root / "prevroot"),
        )
        return self.previous("fetch", *CELL, *dest)

    def tarball(self) -> Path:
        """Pack a one-file pkgroot archive beside the crate.

        Returns:
            The archive.

        """
        payload = self.root / "pkgroot" / "usr" / "lib" / "demo_ext.so"
        payload.parent.mkdir(parents=True)
        payload.write_bytes(b"\x7fELF")
        tarball = self.root / "asset.tar.gz"
        with tarfile.open(tarball, "w:gz") as tar:
            tar.add(self.root / "pkgroot", arcname="pkgroot")
        shutil.rmtree(self.root / "pkgroot")
        return tarball


@fixture
def previous_tarball(tmp_path: Path, env: support.Env) -> PreviousTarball:
    """Give each row its own PreviousTarball.

    Returns:
        The row's PreviousTarball.

    """
    return PreviousTarball(tmp_path, env=env)


def test_no_previous_release_or_a_rerun_skips(
    previous_tarball: PreviousTarball,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No release, none of this extension, or this version: said why."""
    rerun = release("v1.2.0", "demo_ext-1.2.0-pg18-linux-arm64.tar.gz")
    for releases in ([], [release("release-1")], [rerun]):
        previous_tarball.forge(releases)
        previous_tarball.graph("demo_ext--1.0.0--1.1.0.sql")
        previous_tarball.enter(monkeypatch)
        status = previous_release.main(previous_tarball.fetch())
        out, err = capfd.readouterr()
        assert status == 0
        assert not out
        assert err == "::notice::no previous release; upgrade execution skipped\n"


def test_no_tarball_for_this_cell_skips(
    previous_tarball: PreviousTarball,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The predecessor shipped no tarball of this pg and arch."""
    previous_tarball.forge([
        release("v1.1.0", "demo_ext-1.1.0-pg17-linux-arm64.tar.gz")
    ])
    previous_tarball.graph("demo_ext--1.0.0--1.1.0.sql")
    previous_tarball.enter(monkeypatch)
    status = previous_release.main(previous_tarball.fetch())
    out, err = capfd.readouterr()
    assert status == 0
    assert not out
    assert err == (
        "::notice::v1.1.0 shipped no demo_ext-1.1.0-pg18-linux-arm64.tar.gz;"
        " upgrade execution skipped\n"
    )
    assert (
        "release download v1.1.0 --repo acme/demo --pattern"
        " demo_ext-1.1.0-pg18-linux-arm64.tar.gz --output "
        f"{previous_tarball.root}/prev.tar.gz"
    ) == previous_tarball.gh_calls()[1]


def test_the_tarball_is_extracted_and_the_version_printed(
    previous_tarball: PreviousTarball,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cell's tarball lands under DEST, and stdout is the version."""
    previous_tarball.forge([release("v1.1.0", DEMO_CELL)])
    previous_tarball.graph("demo_ext--1.0.0--1.1.0.sql")
    previous_tarball.enter(monkeypatch, tarball=str(previous_tarball.tarball()))
    status = previous_release.main(previous_tarball.fetch())
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == "1.1.0\n"
    extracted = (
        previous_tarball.root / "prevroot" / "pkgroot" / "usr" / "lib" / "demo_ext.so"
    )
    assert extracted.read_bytes() == b"\x7fELF"


def test_a_corrupt_tarball_is_red(
    previous_tarball: PreviousTarball,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tar failing is tar's own red, and no version is printed."""
    previous_tarball.forge([release("v1.1.0", DEMO_CELL)])
    previous_tarball.graph("demo_ext--1.0.0--1.1.0.sql")
    tarball = previous_tarball.root / "asset.tar.gz"
    tarball.write_bytes(b"not a tarball")
    previous_tarball.enter(monkeypatch, tarball=str(tarball))
    status = previous_release.main(previous_tarball.fetch())
    out = capfd.readouterr().out
    assert status != 0
    assert not out


class OnePredecessor(PreviousRelease):
    """The function and the two build sites, from edtf's captures."""

    CRATE = "crates/edtf-postgres"

    def edtf(self, listing: str, sql_at: str, latest: str, *extra: str) -> None:
        """Serve a captured listing over a captured `sql/` directory."""
        self.env["STUB_LISTING"] = str(TESTDATA / listing)
        self.env["STUB_LATEST"] = latest
        names = (TESTDATA / f"edtf-sql-{sql_at}.txt").read_text().split()
        self.graph(*names, *extra)

    def predecessor(self, version: str) -> list[str]:
        """Ask for edtf_postgres's predecessor of `version`.

        Returns:
            The helper's arguments.

        """
        control = f"{self.CRATE}/edtf_postgres.control"
        args = ["predecessor", "--repo", "acme/demo", "--version", version]
        return [*args, "--control", control]


@fixture
def one_predecessor(tmp_path: Path, env: support.Env) -> OnePredecessor:
    """Give each row its own OnePredecessor.

    Returns:
        The row's OnePredecessor.

    """
    return OnePredecessor(tmp_path, env=env)


def test_an_imported_tag_scheme_yields_the_published_predecessor(
    one_predecessor: OnePredecessor,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At import, 1.2.3 sits on `edtf-postgres-v1.2.3` (main: none)."""
    one_predecessor.edtf("edtf-releases.json", "v1.2.3", "edtf-postgres-v1.2.3")
    one_predecessor.enter(monkeypatch)
    status = previous_release.main(one_predecessor.predecessor("1.3.0"))
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == "1.2.3\tedtf-postgres-v1.2.3\n"


def test_a_burned_head_yields_the_next_installable_version(
    one_predecessor: OnePredecessor,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At v1.3.1 the graph's next head, 1.3.0, burned: 1.2.3 it is."""
    one_predecessor.edtf("edtf-releases.json", "v1.3.1", "edtf-postgres-v1.2.3")
    one_predecessor.enter(monkeypatch)
    status = previous_release.main(one_predecessor.predecessor("1.3.1"))
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == "1.2.3\tedtf-postgres-v1.2.3\n"


def test_an_older_release_marked_latest_yields_the_newest(
    one_predecessor: OnePredecessor,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A maintenance release v1.3.4 published last: 1.3.5 is still newest."""
    one_predecessor.edtf("edtf-releases-v1.3.5.json", "v1.3.5", "v1.3.4")
    one_predecessor.enter(monkeypatch)
    status = previous_release.main(one_predecessor.predecessor("1.3.6"))
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == "1.3.5\tv1.3.5\n"


def test_an_empty_graph_yields_nothing(
    one_predecessor: OnePredecessor,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No upgrade script at all: first publish, whatever is published."""
    one_predecessor.env["STUB_LISTING"] = str(TESTDATA / "edtf-releases-v1.3.5.json")
    one_predecessor.graph("README.md")
    one_predecessor.enter(monkeypatch)
    status = previous_release.main(one_predecessor.predecessor("1.3.6"))
    out, err = capfd.readouterr()
    assert status == 0, err
    assert not out


def test_the_guard_refuses_from_the_imported_predecessor(
    one_predecessor: OnePredecessor,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No 1.2.3--1.3.0 path: refused, naming the release that carries 1.2.3."""
    one_predecessor.edtf("edtf-releases.json", "v1.2.3", "edtf-postgres-v1.2.3")
    one_predecessor.enter(monkeypatch)
    status = previous_release.main(one_predecessor.previous("guard", version="1.3.0"))
    err = capfd.readouterr().err
    assert status == 1, err
    assert err == (
        "::error::previous-release: no upgrade path"
        " crates/edtf-postgres/sql/edtf_postgres--1.2.3--1.3.0.sql from"
        " published 1.2.3 (edtf-postgres-v1.2.3) — existing installations"
        " would be stranded\n"
    )


def test_the_guard_checks_past_a_burned_head(
    one_predecessor: OnePredecessor,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At v1.3.1 the path is from 1.2.3, and it is there."""
    one_predecessor.edtf("edtf-releases.json", "v1.3.1", "edtf-postgres-v1.2.3")
    one_predecessor.enter(monkeypatch)
    status = previous_release.main(one_predecessor.previous("guard", version="1.3.1"))
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == "::notice::upgrade path 1.2.3 -> 1.3.1 present\n"


def test_the_guard_checks_from_the_newest_not_the_latest(
    one_predecessor: OnePredecessor,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Latest is v1.3.4; the path owed is from 1.3.5, and it is there."""
    one_predecessor.edtf(
        "edtf-releases-v1.3.5.json",
        "v1.3.5",
        "v1.3.4",
        "edtf_postgres--1.3.5--1.3.6.sql",
    )
    one_predecessor.enter(monkeypatch)
    status = previous_release.main(one_predecessor.previous("guard", version="1.3.6"))
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == "::notice::upgrade path 1.3.5 -> 1.3.6 present\n"


def test_the_fetch_downloads_the_imported_predecessor(
    one_predecessor: OnePredecessor,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The upgrade execution starts from 1.2.3, off its own release."""
    one_predecessor.edtf("edtf-releases.json", "v1.3.1", "edtf-postgres-v1.2.3")
    tarball = one_predecessor.root / "asset.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(one_predecessor.sql, arcname="pkgroot")
    dest: tuple[str, ...] = ("--tarball", str(one_predecessor.root / "prev.tar.gz"))
    dest += ("--dest", str(one_predecessor.root / "prevroot"))
    cell = ("--pg", "18", "--arch", "arm64", *dest)
    one_predecessor.enter(monkeypatch, tarball=str(tarball))
    status = previous_release.main(
        one_predecessor.previous("fetch", *cell, version="1.3.1")
    )
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == "1.2.3\n"
    assert (
        "release download edtf-postgres-v1.2.3 --repo acme/demo --pattern"
        " edtf_postgres-1.2.3-pg18-linux-arm64.tar.gz --output"
        f" {one_predecessor.root}/prev.tar.gz"
    ) == one_predecessor.gh_calls()[1]


class OlderLine(PreviousRelease):
    """The predecessor is LOWER than the version under release (#1592).

    1.2.4 and 1.3.0 are both published. A release on the older line,
    1.2.5, upgrades from 1.2.4: a path from 1.3.0 would be a downgrade.
    A release on the newer line, 1.3.1, upgrades from 1.3.0.
    """

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Publish 1.2.4 and 1.3.0, each on its own `v` release."""
        super().__init__(tmp_path, env=env)
        self.forge(
            [
                release("v1.3.0", "demo_ext-1.3.0-pg18-linux-arm64.tar.gz"),
                release("v1.2.4", "demo_ext-1.2.4-pg18-linux-arm64.tar.gz"),
            ],
            "v1.3.0",
        )
        self.graph("demo_ext--1.2.3--1.2.4.sql", "demo_ext--1.2.4--1.3.0.sql")

    def predecessor(self, version: str) -> list[str]:
        """Ask for demo_ext's predecessor of `version`.

        Returns:
            The helper's arguments.

        """
        control = f"{self.CRATE}/demo_ext.control"
        args = ["predecessor", "--repo", "acme/demo", "--version", version]
        return [*args, "--control", control]


@fixture
def older_line(tmp_path: Path, env: support.Env) -> OlderLine:
    """Give each row its own OlderLine.

    Returns:
        The row's OlderLine.

    """
    return OlderLine(tmp_path, env=env)


@pytest.mark.parametrize(
    ("version", "want"),
    [
        ("1.2.5", "1.2.4\tv1.2.4\n"),
        ("1.3.1", "1.3.0\tv1.3.0\n"),
    ],
)
def test_each_line_yields_its_own_predecessor(
    older_line: OlderLine,
    version: str,
    want: str,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1.2.5 comes from 1.2.4 on v1.2.4; 1.3.1 from 1.3.0 on v1.3.0."""
    older_line.enter(monkeypatch)
    status = previous_release.main(older_line.predecessor(version))
    out, err = capfd.readouterr()
    assert status == 0, err
    assert want == out


def test_the_record_names_what_the_forge_carries(
    older_line: OlderLine,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`record`: the carried versions written, the predecessor printed.

    1.2.3 is in the graph but on no release, so it is not recorded;
    releasing 1.3.1, the predecessor is 1.3.0 on v1.3.0 (#825, #1592).
    """
    record = older_line.root / ".pgrx-installable"
    control = f"{older_line.CRATE}/demo_ext.control"
    args = ["record", "--repo", "acme/demo", "--version", "1.3.1"]
    older_line.enter(monkeypatch)
    status = previous_release.main([*args, "--out", str(record), control])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == "demo_ext\t1.3.0\tv1.3.0\n"
    written = record.read_text(encoding="utf-8")
    assert written.startswith("# pgrx installable versions"), written
    assert written.endswith("# derived: v1.3.1\ndemo_ext 1.3.0 1.2.4\n"), written


def test_the_guard_owes_no_downgrade_path(
    older_line: OlderLine,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Releasing 1.2.5 with 1.2.4--1.2.5 present passes; 1.3.0 is not asked."""
    older_line.graph("demo_ext--1.2.4--1.2.5.sql")
    older_line.enter(monkeypatch)
    status = previous_release.main(older_line.previous("guard", version="1.2.5"))
    out = capfd.readouterr().out
    assert status == 0, out
    assert out == "::notice::upgrade path 1.2.4 -> 1.2.5 present\n"


# No tracked file under release/ finds a predecessor as "latest" (#1592).
#
# `gh release view` with no tag answers GitHub's latest release, which
# is the forge's opinion of newest and not what an installation can be
# sitting on. The predecessor is previous_release.py's to answer.
NOT_A_PREDECESSOR = ("release/repro_resolve.py",)
UNTAGGED = re.compile(r"""\brelease["',\s]*view["',]*\s+["']?--""")


def test_no_untagged_release_view() -> None:
    """Every `gh release view` in release/ names the release it reads."""
    root = CANON
    tracked = support.tracked(root, "release")
    found = []
    for path in tracked:
        name = Path(path).name
        if "/testdata/" in path or name.startswith(("test_", "prop_")):
            continue
        if path in NOT_A_PREDECESSOR:
            continue
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        found += [
            f"{path}:{text.count(chr(10), 0, hit.start()) + 1}"
            for hit in UNTAGGED.finditer(text)
        ]
    assert strings() == found


# --------------------------------------------------------------------
# smoke.sh, upgrade.sh, upgrade-cluster.sh: the container halves, with
# cp, ls, su and psql stubbed, since their paths are the image's own.
# --------------------------------------------------------------------

# Exported bash functions, never stub files: bash resolves a function
# before PATH, and a fresh executable per row is slow to first exec on a
# Mac (one named su hung the session outright).
CONTAINER_STUBS = {
    # The package copies are the image's paths; recorded, never run.
    "cp": 'printf "cp %s\\n" "$*" >> "${CALLS}"',
    "ls": (
        'printf "%s\\n"'
        ' "/pkgroot/usr/share/postgresql/${PG}/extension/demo_ext.control"'
    ),
    "su": 'printf "su %s\\n" "$*" >> "${CALLS}"',
    "psql": (
        'printf "psql %s\\n" "$*" >> "${CALLS}"; '
        'if [[ $1 == -tA ]]; then printf "%s\\n" "${STUB_GOT}"; fi'
    ),
}


class ContainerScripts:
    """The rows share the stub functions and a call log."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Stub functions; PG 18, demo_ext, 1.1.0 to 1.2.0."""
        self.make_env = env
        self.calls = Path(str(tmp_path)) / "calls"
        self.env = {
            **{
                f"BASH_FUNC_{n}%%": f"() {{ {body}; }}"
                for n, body in CONTAINER_STUBS.items()
            },
            "CALLS": str(self.calls),
            "PG": "18",
            "NAME": "demo_ext",
            "PREV": "1.1.0",
            "NEW": "1.2.0",
            "STUB_GOT": "1.2.0",
            "UNTRUSTED_SMOKE": "",
        }

    def script(
        self, name: str, *args: str, **env: str
    ) -> subprocess.CompletedProcess[str]:
        """Run release/pgrx/<name> under the stubs.

        Returns:
            The completed process.

        """
        return run(
            [BASH, str(PGRX / name), *args], {**self.env, **env}, make_env=self.make_env
        )

    def logged(self) -> list[str]:
        """Read back the stubbed calls, in order.

        Returns:
            One line per call.

        """
        return self.calls.read_text().splitlines()


@fixture
def container_scripts(tmp_path: Path, env: support.Env) -> ContainerScripts:
    """Give each row its own ContainerScripts.

    Returns:
        The row's ContainerScripts.

    """
    return ContainerScripts(tmp_path, env=env)


def test_smoke_without_a_script_creates_the_extension_only(
    container_scripts: ContainerScripts,
) -> None:
    """Package in, CREATE EXTENSION as postgres, nothing else."""
    result = container_scripts.script("smoke.sh")
    assert result.returncode == 0, result.stderr
    assert (
        strings(
            ("cp -a /pkgroot/usr/lib/postgresql/18/lib/. /usr/lib/postgresql/18/lib/"),
            (
                "cp -a /pkgroot/usr/share/postgresql/18/extension/."
                " /usr/share/postgresql/18/extension/"
            ),
            "su postgres -s /bin/bash /pgrx/smoke-cluster.sh",
        )
        == container_scripts.logged()
    )


def test_smoke_refuses_a_script_that_is_not_executable(
    container_scripts: ContainerScripts,
) -> None:
    """The caller's smoke-test must exist and be executable in /src."""
    result = container_scripts.script("smoke.sh", UNTRUSTED_SMOKE="ci/no-such-smoke.sh")
    assert result.returncode == 1
    assert (
        result.stderr
        == "::error::smoke.sh: smoke-test ci/no-such-smoke.sh is not executable\n"
    )
    # Stopped at the refusal, never tried: nothing else was said.
    assert not result.stdout


@pytest.mark.usefixtures("container_scripts")
def test_smoke_cluster_names_the_extension_from_its_control() -> None:
    """The cluster half reads EXT, which smoke.sh exports from the control."""
    text = (PGRX / "smoke-cluster.sh").read_text()
    assert 'psql -c "CREATE EXTENSION ${EXT};"' in text
    assert "export EXT" in (PGRX / "smoke.sh").read_text()


def test_upgrade_creates_at_prev_then_updates_over_the_new_package(
    container_scripts: ContainerScripts,
) -> None:
    """Old package, create, new package over it, update: in that order."""
    result = container_scripts.script("upgrade.sh")
    assert result.returncode == 0, result.stderr
    lib = "/usr/lib/postgresql/18/lib"
    ext = "/usr/share/postgresql/18/extension"
    assert (
        strings(
            f"cp -a /prev{lib}/. {lib}/",
            f"cp -a /prev{ext}/. {ext}/",
            "su postgres -s /bin/bash /pgrx/upgrade-cluster.sh create",
            f"cp -a /new{lib}/. {lib}/",
            f"cp -a /new{ext}/. {ext}/",
            "su postgres -s /bin/bash /pgrx/upgrade-cluster.sh update",
        )
        == container_scripts.logged()
    )


def test_update_reads_the_new_version_back(container_scripts: ContainerScripts) -> None:
    """ALTER EXTENSION UPDATE TO the new version, then the version read."""
    result = container_scripts.script("upgrade-cluster.sh", "update")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "upgraded 1.1.0 -> 1.2.0\n"
    assert (
        strings(
            "psql -c ALTER EXTENSION demo_ext UPDATE TO '1.2.0';",
            (
                "psql -tA -c SELECT extversion FROM pg_extension"
                " WHERE extname = 'demo_ext';"
            ),
        )
        == container_scripts.logged()
    )


def test_update_refuses_any_other_version(container_scripts: ContainerScripts) -> None:
    """An update that left the old version is a red, with both versions."""
    result = container_scripts.script("upgrade-cluster.sh", "update", STUB_GOT="1.1.0")
    assert result.returncode == 1
    assert result.stderr == (
        "::error::upgrade-cluster.sh: upgrade left extversion 1.1.0, wanted 1.2.0\n"
    )


def test_an_unknown_phase_is_refused(container_scripts: ContainerScripts) -> None:
    """Only create and update exist."""
    result = container_scripts.script("upgrade-cluster.sh", "drop")
    assert result.returncode == support.USAGE
    assert (
        result.stderr
        == "::error::upgrade-cluster.sh: unknown phase 'drop' (create or update)\n"
    )
    assert not container_scripts.calls.exists()


# --------------------------------------------------------------------
# debug-split.sh and glibc-floor.sh: real binutils over a real library.
# --------------------------------------------------------------------

ELF_TOOLS = ("cc", "readelf", "objcopy", "strip")
MISSING = [t for t in ELF_TOOLS if support.optional_tool(t) is None]


def elf_tools_present() -> bool:
    """Whether the ELF rows can run here; never a silent skip on the runner.

    Returns:
        True where every tool is on PATH and `cc` emits ELF.

    Raises:
        AssertionError: on the gate's runner with a tool missing.

    """
    if MISSING and ON_RUNNER:
        msg = f"the gate's runner lacks {', '.join(MISSING)}"
        raise AssertionError(msg)
    if MISSING:
        return False
    probe = subprocess.run(
        [support.tool("readelf"), "--version"], capture_output=True, check=False
    )
    return probe.returncode == 0


SKIP_ELF = (
    f"not run: debug-split and glibc rows need {', '.join(MISSING) or 'an ELF cc'}"
    " (GNU binutils and an ELF compiler)"
)
SOURCE = """\
#include <string.h>
int demo_one(const char *s) { return (int)strlen(s); }
int demo_two(int a) { return a * 2; }
"""
NO_LIBC = "int demo_one(int a) { return a + 1; }\n"


def build_library(
    path: Path, source: str, *, libc: bool = True, dep: bool = True
) -> None:
    """Compile `source` into a shared library at `path`, with debug info.

    `dep` adds a non-allocated `.dep-v0` section, the shape
    cargo-auditable's linker section has.
    """
    src = path.with_suffix(".c")
    src.write_text(source)
    flags = ["-g", "-O0", "-shared", "-fPIC"] + ([] if libc else ["-nostdlib"])
    subprocess.run([support.tool("cc"), *flags, "-o", str(path), str(src)], check=True)
    src.unlink()
    if dep:
        payload = path.with_suffix(".dep")
        payload.write_bytes(b"\x78\x9c\x03\x00\x00\x00\x00\x01")
        subprocess.run(
            [
                support.tool("objcopy"),
                "--add-section",
                f".dep-v0={payload}",
                str(path),
            ],
            check=True,
        )
        payload.unlink()


# Each shim stands in for one binutils step done wrong, so one check fires.
SHIMS = {
    "no .gnu_debuglink": (
        "objcopy",
        '[[ $1 == --add-gnu-debuglink=* ]] && exit 0\nexec "${REAL_objcopy}" "$@"\n',
    ),
    "still has .debug_info": (
        "strip",
        (
            '"${REAL_strip}" "$@"\n'
            "for so; do :; done\n"
            'printf x > "${so}.junk"\n'
            '"${REAL_objcopy}" --add-section ".debug_info=${so}.junk" "${so}"\n'
        ),
    ),
    "still has .symtab": ("strip", 'exec "${REAL_strip}" --strip-debug "${@: -1}"\n'),
    "dynamic functions": (
        "readelf",
        # The second --dyn-syms read, after the strip, loses one FUNC.
        (
            "if [[ $1 == --dyn-syms ]]; then\n"
            '  n=$(($(cat "${SEEN}" 2> /dev/null || echo 0) + 1))\n'
            '  echo "${n}" > "${SEEN}"\n'
            "  if ((n == 2)); then\n"
            '    "${REAL_readelf}" "$@" |\n'
            "      awk '!d && $4 == \"FUNC\" { d = 1; next } 1'\n"
            "    exit\n"
            "  fi\n"
            "fi\n"
            'exec "${REAL_readelf}" "$@"\n'
        ),
    ),
}

REL = "usr/lib/postgresql/18/lib/demo_ext.so"


class DebugSplit:
    """debug-split.sh over pkgroot/usr/lib/postgresql/18/lib/demo_ext.so."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Build a package root holding one real library with .dep-v0."""
        self.make_env = env
        if not elf_tools_present():
            pytest.skip(SKIP_ELF)
        self.root = Path(str(tmp_path))
        self.pkgroot = self.root / "pkgroot"
        self.so = self.pkgroot / REL
        self.so.parent.mkdir(parents=True)
        build_library(self.so, SOURCE)

    def split(
        self, shim: tuple[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the split, one binutils step replaced by `shim` if given.

        Returns:
            The completed process.

        """
        env = {"SEEN": str(self.root / "seen")}
        for tool in ("objcopy", "strip", "readelf"):
            env[f"REAL_{tool}"] = support.tool(tool)
        if shim:
            bindir = self.root / "bin"
            bindir.mkdir(exist_ok=True)
            stub(bindir, *shim)
            env["PATH"] = f"{bindir}{os.pathsep}{os.environ['PATH']}"
        script = str(PGRX / "debug-split.sh")
        return run(
            [BASH, script, str(self.pkgroot), str(self.root / "debugroot")],
            env,
            make_env=self.make_env,
        )


@fixture
def debug_split(tmp_path: Path, env: support.Env) -> DebugSplit:
    """Give each row its own DebugSplit.

    Returns:
        The row's DebugSplit.

    """
    return DebugSplit(tmp_path, env=env)


def sections(path: Path) -> str:
    """Read the section table, as the split reads it.

    Returns:
        readelf -S output.

    """
    return subprocess.run(
        [support.tool("readelf"), "-S", "--wide", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_a_good_split(debug_split: DebugSplit) -> None:
    """Debug info out to the sibling archive, .dep-v0 and the link kept."""
    result = debug_split.split()
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(
        rf"^::notice::{REL}: split and stripped, \d+ dynamic functions,"
        r" \.dep-v0 kept\n$",
        result.stdout,
    )
    stripped = sections(debug_split.so)
    assert " .dep-v0 " in stripped
    assert " .gnu_debuglink " in stripped
    assert " .debug_info " not in stripped
    assert " .symtab " not in stripped
    debug = debug_split.root / "debugroot" / "usr" / "lib" / "debug" / f"{REL}.debug"
    assert " .debug_info " in sections(debug)


def test_no_dep_section_is_refused(debug_split: DebugSplit) -> None:
    """A library cargo-auditable never reached (#1274)."""
    build_library(debug_split.so, SOURCE, dep=False)
    result = debug_split.split()
    assert result.returncode == 1
    assert (
        f"::error::debug-split.sh: {REL} after the split: no .dep-v0\n" == result.stderr
    )


@pytest.mark.parametrize(("name", "shim"), SHIMS.items())
def test_each_wrong_step_is_refused_by_name(
    debug_split: DebugSplit, name: str, shim: tuple[str, str]
) -> None:
    """No debuglink; debug info left; symbol table left; functions lost."""
    result = debug_split.split(shim)
    assert result.returncode == 1, result.stdout
    if name == "dynamic functions":
        assert re.search(
            rf"^::error::debug-split.sh: {REL} after the split:"
            r" \d+ dynamic functions, \d+ before the split\n$",
            result.stderr,
        )
    else:
        assert (
            f"::error::debug-split.sh: {REL} after the split: {name}\n"
        ) == result.stderr


def test_no_library_is_refused(debug_split: DebugSplit) -> None:
    """A package with nothing to split is a red, never a quiet pass."""
    debug_split.so.unlink()
    result = debug_split.split()
    assert result.returncode == 1
    assert (
        f"::error::debug-split.sh: no shared library under {debug_split.pkgroot} to"
        f" split\n"
    ) == result.stderr


class GlibcFloor:
    """glibc-floor.sh over a real library, against floors either side."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Make an empty package root."""
        self.make_env = env
        if not elf_tools_present():
            pytest.skip(SKIP_ELF)
        self.pkgroot = Path(str(tmp_path)) / "pkgroot"
        self.lib = self.pkgroot / "usr" / "lib" / "demo_ext.so"
        self.lib.parent.mkdir(parents=True)

    def floor(self, floor: str) -> subprocess.CompletedProcess[str]:
        """Run the assertion at `floor`.

        Returns:
            The completed process.

        """
        return run(
            [BASH, str(PGRX / "glibc-floor.sh"), str(self.pkgroot), floor],
            {},
            make_env=self.make_env,
        )


@fixture
def glibc_floor(tmp_path: Path, env: support.Env) -> GlibcFloor:
    """Give each row its own GlibcFloor.

    Returns:
        The row's GlibcFloor.

    """
    return GlibcFloor(tmp_path, env=env)


def test_within_the_floor(glibc_floor: GlibcFloor) -> None:
    """Every versioned symbol at or under a floor far above them."""
    build_library(glibc_floor.lib, SOURCE, dep=False)
    result = glibc_floor.floor("99.0")
    assert result.returncode == 0, result.stdout
    assert re.search(
        r"^::notice::glibc ceiling GLIBC_\d+\.\d+ within floor 99\.0\n$",
        result.stdout,
    )


def test_above_the_floor_is_refused(glibc_floor: GlibcFloor) -> None:
    """A symbol newer than the floor fails the release, not dlopen."""
    build_library(glibc_floor.lib, SOURCE, dep=False)
    result = glibc_floor.floor("1.0")
    assert result.returncode == 1
    assert re.search(
        r"^::error::glibc-floor.sh: demo_ext\.so needs GLIBC_\d+\.\d+, above the"
        r" 1\.0 floor"
        r" the class guarantees\n$",
        result.stderr,
    )


def test_no_versioned_symbols(glibc_floor: GlibcFloor) -> None:
    """A library that takes nothing from glibc has nothing to assert."""
    build_library(glibc_floor.lib, NO_LIBC, libc=False, dep=False)
    result = glibc_floor.floor("2.36")
    assert result.returncode == 0, result.stdout
    assert (
        result.stdout
        == "::notice::no versioned glibc symbols (static or unusual link)\n"
    )


# The one glibc row that needs no binutils.
def test_glibc_floor_no_library_no_library_is_refused(env: support.Env) -> None:
    """A package with no library has no floor to prove: a red."""
    with TemporaryDirectory() as d:
        result = run([BASH, str(PGRX / "glibc-floor.sh"), d, "2.36"], {}, make_env=env)
    assert result.returncode == 1
    assert result.stderr == "::error::glibc-floor.sh: no shared library packaged\n"
