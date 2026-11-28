"""The pgrx-extension build's tracked scripts, driven from Python (#1380).

build-pgrx-extension.yml carried this logic as shell three quotes deep
inside `docker run … bash -c '… su postgres -c "…"'`, with no tests.
It now lives in belt/release/pgrx/ and release/pgrx/ as plain scripts the containers run from
the pinned canon tree, and in release/pgrx/previous_release.py on the
runner. Each guard branch is a row here, and every refusal row asserts
its message, word for word, against the wording the workflow carried.
previous_release.py's rows call its `main` in this process (#1698).

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
from tests import support

CANON = support.CANON
PGRX = CANON / "release" / "pgrx"
BASH = support.tool("bash")
ON_RUNNER = os.environ.get("GITHUB_ACTIONS") == "true"
# What a helper's call answered: its status, stdout and stderr.
type Done = tuple[int, str, str]


@pytest.fixture
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
    argv: list[str], env: dict[str, str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one script with `env` laid over the process environment.

    Returns:
        The completed process, output captured as text.

    """
    return subprocess.run(
        argv,
        env=support.env(**env),
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


@pytest.mark.usefixtures("_set_up_test_floor_block")
class TestFloorBlock:
    """floor.sh against an extension directory as the package lays it out."""

    @pytest.fixture
    def _set_up_test_floor_block(self, tmp_path: Path) -> None:
        """Lay out a control, the install script and the upgrade scripts."""
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
        )

    def test_install_and_upgrade_scripts_to_the_release_carry_it(self) -> None:
        """The install script and every upgrade TO the version, nothing else."""
        result = self.floor()
        assert result.returncode == 0, result.stdout + result.stderr
        assert (
            result.stdout
            == "::notice::demo_ext 1.2.0 was built against PostgreSQL 18.7\n"
        )
        block = filled("demo_ext", "1.2.0", "18.7", "180007")
        for script in (
            "demo_ext--1.2.0.sql",
            "demo_ext--1.0.0--1.2.0.sql",
            "demo_ext--1.1.0--1.2.0.sql",
        ):
            assert block + GENERATED == (self.extdir / script).read_text()
        for script in ("demo_ext--1.0.0--1.1.0.sql", "demo_ext--1.1.0.sql"):
            assert (self.extdir / script).read_text() == GENERATED

    def test_the_filled_block_names_the_floor(self) -> None:
        """No placeholder survives, and the number and X.Y are the build's."""
        self.floor()
        text = (self.extdir / "demo_ext--1.2.0.sql").read_text()
        assert "@" not in text
        assert text.startswith("DO $pgrx_floor$\n")
        assert "< 180007 THEN" in text
        assert "'demo_ext 1.2.0 was built against PostgreSQL 18.7;" in text

    @pytest.mark.parametrize(
        "script", ["demo_ext--1.2.0.sql", "demo_ext--1.1.0--1.2.0.sql"]
    )
    def test_a_script_already_using_the_tag_is_refused(self, script: str) -> None:
        """The dollar-quote tag must be one no generated script uses."""
        planted = "SELECT $pgrx_floor$ x $pgrx_floor$;\n"
        (self.extdir / script).write_text(planted)
        result = self.floor()
        assert result.returncode == 1
        assert (
            f"::error::floor.sh: {script} already uses the pgrx_floor tag\n"
            == result.stderr
        )
        assert planted == (self.extdir / script).read_text()

    def test_no_script_for_the_version_is_not_an_error(self) -> None:
        """A control whose version has no script yet leaves the tree alone."""
        for script in self.extdir.glob("*.sql"):
            script.unlink()
        result = self.floor()
        assert result.returncode == 0, result.stderr
        none: list[Path] = []
        left: list[Path] = list(self.extdir.glob("*.sql"))
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


@pytest.mark.usefixtures("_set_up_test_previous_release")
class TestPreviousRelease:
    """The rows share one directory: a stub gh, a crate, a log."""

    CRATE = "crates/demo-ext"

    @pytest.fixture
    def _set_up_test_previous_release(self, tmp_path: Path) -> None:
        """Put a stub gh first on PATH and a crate at CRATE."""
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

    def helper(
        self,
        args: list[str],
        env: dict[str, str],
        *,
        capfd: pytest.CaptureFixture[str],
    ) -> Done:
        """Call the helper on `args` in the row's directory.

        The helper resolves `gh` on PATH once, at import, so the call is
        given the stub a step would have found first on its PATH.

        Returns:
            The status and both streams.

        """
        with pytest.MonkeyPatch.context() as patched:
            patched.setattr(previous_release, "GH", str(self.gh))
            for name, value in support.env(**env).items():
                patched.setenv(name, value)
            patched.chdir(self.root)
            status = previous_release.main(args)
        out, err = capfd.readouterr()
        return status, out, err

    def previous(
        self,
        mode: str,
        *extra: str,
        version: str = "1.2.0",
        capfd: pytest.CaptureFixture[str],
        **stubs: str,
    ) -> Done:
        """Run the helper at `version` for CRATE.

        Returns:
            What the run answered.

        """
        env = {**self.env, **{f"STUB_{k.upper()}": v for k, v in stubs.items()}}
        args = [mode, "--repo", "acme/demo", "--version", version]
        return self.helper([*args, "--crate-dir", self.CRATE, *extra], env, capfd=capfd)

    def gh_calls(self) -> list[str]:
        """Each gh invocation, its arguments joined by spaces.

        Returns:
            The calls in order.

        """
        return self.log.read_text().splitlines() if self.log.exists() else []


class TestControlFileRefusal(TestPreviousRelease):
    """`guard`: the backstop for a tag minted around the upgrade path."""

    @pytest.mark.parametrize(
        ("releases", "latest"), [([], ""), ([release("release-1")], "release-1")]
    )
    def test_no_previous_release(
        self, releases: object, latest: object, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """No release at all, or none carrying this extension's tarballs."""
        self.forge(releases, latest)
        self.graph("demo_ext--1.0.0--1.1.0.sql")
        status, out, err = self.previous("guard", capfd=capfd)
        assert status == 0, err
        assert out == (
            "::notice::no previous release; first publish needs no upgrade path\n"
        )

    def test_a_rerun(self, capfd: pytest.CaptureFixture[str]) -> None:
        """A release already carries THIS version: nothing to upgrade from."""
        self.forge([release("v1.2.0", "demo_ext-1.2.0-pg18-linux-arm64.tar.gz")])
        status, out, err = self.previous("guard", capfd=capfd)
        assert status == 0, err
        assert out == "::notice::re-run of 1.2.0; upgrade path not applicable\n"
        assert len(self.gh_calls()) == 1

    def test_the_extensions_first_publish(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A previous release that shipped no tarball of this extension (#87).

        One meaning of first publish now (#1592): no version in the graph
        is installable. It used to be said per tag, naming GitHub's latest
        release, which is no longer what anything asks.
        """
        self.forge([release("v1.1.0", "demo-cli.tar.gz")], "v1.1.0")
        self.graph("demo_ext--1.0.0--1.1.0.sql")
        status, out, err = self.previous("guard", capfd=capfd)
        assert status == 0, err
        assert (
            out
            == "::notice::no previous release; first publish needs no upgrade path\n"
        )
        assert self.gh_calls()[0] == (
            "api repos/acme/demo/releases --paginate --jq"
            " .[] | select(.draft | not) | {tag: .tag_name, asset: .assets[]?.name}"
            ' | "\\(.tag)\\t\\(.asset)"'
        )

    def test_a_missing_upgrade_path_is_refused(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Shipped before, no sql/<ext>--prev--new.sql: stranded, refused."""
        self.forge([release("v1.1.0", DEMO_CELL)], "v1.1.0")
        self.graph("demo_ext--1.0.0--1.1.0.sql")
        status, _out, err = self.previous("guard", capfd=capfd)
        assert status == 1
        assert err == (
            "::error::previous-release: no upgrade path"
            " crates/demo-ext/sql/demo_ext--1.1.0--1.2.0.sql"
            " from published 1.1.0 (v1.1.0) — existing installations would be"
            " stranded\n"
        )

    def test_a_present_upgrade_path_passes(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The derived script is there: the release may proceed."""
        self.forge([release("v1.1.0", DEMO_CELL)], "v1.1.0")
        self.graph("demo_ext--1.0.0--1.1.0.sql", "demo_ext--1.1.0--1.2.0.sql")
        status, out, err = self.previous("guard", capfd=capfd)
        assert status == 0, err
        assert out == "::notice::upgrade path 1.1.0 -> 1.2.0 present\n"

    def test_a_failed_listing_read_is_red(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Gh failing on the listing is a red with gh's own words, never a pass."""
        args = ["guard", "--repo", "acme/demo", "--version", "1.2.0"]
        status, out, err = self.helper(
            [*args, "--crate-dir", self.CRATE], self.env, capfd=capfd
        )
        assert status == 1
        assert out == ""
        assert err == "HTTP 502\n"


class TestPreviousTarball(TestPreviousRelease):
    """`fetch`: the predecessor's tarball of this cell, or a skip."""

    CELL = ("--pg", "18", "--arch", "arm64")

    def fetch(self, *, capfd: pytest.CaptureFixture[str], **stubs: str) -> Done:
        """Fetch for pg18/arm64 into the row's directory.

        Returns:
            What the run answered.

        """
        dest = (
            "--tarball",
            str(self.root / "prev.tar.gz"),
            "--dest",
            str(self.root / "prevroot"),
        )
        return self.previous("fetch", *self.CELL, *dest, **stubs, capfd=capfd)

    def test_no_previous_release_or_a_rerun_skips(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """No release, none of this extension, or this version: said why."""
        rerun = release("v1.2.0", "demo_ext-1.2.0-pg18-linux-arm64.tar.gz")
        for releases in ([], [release("release-1")], [rerun]):
            self.forge(releases)
            self.graph("demo_ext--1.0.0--1.1.0.sql")
            status, out, err = self.fetch(capfd=capfd)
            assert status == 0
            assert out == ""
            assert err == "::notice::no previous release; upgrade execution skipped\n"

    def test_no_tarball_for_this_cell_skips(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The predecessor shipped no tarball of this pg and arch."""
        self.forge([release("v1.1.0", "demo_ext-1.1.0-pg17-linux-arm64.tar.gz")])
        self.graph("demo_ext--1.0.0--1.1.0.sql")
        status, out, err = self.fetch(capfd=capfd)
        assert status == 0
        assert out == ""
        assert err == (
            "::notice::v1.1.0 shipped no demo_ext-1.1.0-pg18-linux-arm64.tar.gz;"
            " upgrade execution skipped\n"
        )
        assert (
            f"release download v1.1.0 --repo acme/demo --pattern"
            f" demo_ext-1.1.0-pg18-linux-arm64.tar.gz --output {self.root}/prev.tar.gz"
        ) == self.gh_calls()[1]

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

    def test_the_tarball_is_extracted_and_the_version_printed(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The cell's tarball lands under DEST, and stdout is the version."""
        self.forge([release("v1.1.0", DEMO_CELL)])
        self.graph("demo_ext--1.0.0--1.1.0.sql")
        status, out, err = self.fetch(tarball=str(self.tarball()), capfd=capfd)
        assert status == 0, err
        assert out == "1.1.0\n"
        extracted = self.root / "prevroot" / "pkgroot" / "usr" / "lib" / "demo_ext.so"
        assert extracted.read_bytes() == b"\x7fELF"

    def test_a_corrupt_tarball_is_red(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Tar failing is tar's own red, and no version is printed."""
        self.forge([release("v1.1.0", DEMO_CELL)])
        self.graph("demo_ext--1.0.0--1.1.0.sql")
        tarball = self.root / "asset.tar.gz"
        tarball.write_bytes(b"not a tarball")
        status, out, _err = self.fetch(tarball=str(tarball), capfd=capfd)
        assert status != 0
        assert out == ""


# --------------------------------------------------------------------
# The one predecessor (#1592), on edtf's real history: its release
# listing captured 2026-08-21 after v1.3.0 burned
# (testdata/edtf-releases.json) and 2026-11-19 after v1.3.5 published
# (testdata/edtf-releases-v1.3.5.json), and its extension's `sql/`
# directory at three tags (testdata/edtf-sql-<tag>.txt, the names under
# crates/edtf-postgres/sql/, read from the tag's git tree). v1.2.3 is the
# tree the canon's first edtf release started from: every published
# extension version sits on an `edtf-postgres-v*` release, and `v1.2.3`
# is a tag with no release object. Only GitHub's "latest" is authored
# (STUB_LATEST), because a listing does not carry it.
# --------------------------------------------------------------------


class TestOnePredecessor(TestPreviousRelease):
    """The function and the two build sites, from edtf's captures."""

    CRATE = "crates/edtf-postgres"

    def edtf(self, listing: str, sql_at: str, latest: str, *extra: str) -> None:
        """Serve a captured listing over a captured `sql/` directory."""
        self.env["STUB_LISTING"] = str(TESTDATA / listing)
        self.env["STUB_LATEST"] = latest
        names = (TESTDATA / f"edtf-sql-{sql_at}.txt").read_text().split()
        self.graph(*names, *extra)

    def predecessor(self, version: str, *, capfd: pytest.CaptureFixture[str]) -> Done:
        """Ask the helper for edtf_postgres's predecessor of `version`.

        Returns:
            What the run answered.

        """
        control = f"{self.CRATE}/edtf_postgres.control"
        args = ["predecessor", "--repo", "acme/demo", "--version", version]
        return self.helper([*args, "--control", control], self.env, capfd=capfd)

    def test_an_imported_tag_scheme_yields_the_published_predecessor(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """At import, 1.2.3 sits on `edtf-postgres-v1.2.3` (main: none)."""
        self.edtf("edtf-releases.json", "v1.2.3", "edtf-postgres-v1.2.3")
        status, out, err = self.predecessor("1.3.0", capfd=capfd)
        assert status == 0, err
        assert out == "1.2.3\tedtf-postgres-v1.2.3\n"

    def test_a_burned_head_yields_the_next_installable_version(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """At v1.3.1 the graph's next head, 1.3.0, burned: 1.2.3 it is."""
        self.edtf("edtf-releases.json", "v1.3.1", "edtf-postgres-v1.2.3")
        status, out, err = self.predecessor("1.3.1", capfd=capfd)
        assert status == 0, err
        assert out == "1.2.3\tedtf-postgres-v1.2.3\n"

    def test_an_older_release_marked_latest_yields_the_newest(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A maintenance release v1.3.4 published last: 1.3.5 is still newest."""
        self.edtf("edtf-releases-v1.3.5.json", "v1.3.5", "v1.3.4")
        status, out, err = self.predecessor("1.3.6", capfd=capfd)
        assert status == 0, err
        assert out == "1.3.5\tv1.3.5\n"

    def test_an_empty_graph_yields_nothing(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """No upgrade script at all: first publish, whatever is published."""
        self.env["STUB_LISTING"] = str(TESTDATA / "edtf-releases-v1.3.5.json")
        self.graph("README.md")
        status, out, err = self.predecessor("1.3.6", capfd=capfd)
        assert status == 0, err
        assert out == ""

    def test_the_guard_refuses_from_the_imported_predecessor(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """No 1.2.3--1.3.0 path: refused, naming the release that carries 1.2.3."""
        self.edtf("edtf-releases.json", "v1.2.3", "edtf-postgres-v1.2.3")
        status, _out, err = self.previous("guard", version="1.3.0", capfd=capfd)
        assert status == 1, err
        assert err == (
            "::error::previous-release: no upgrade path"
            " crates/edtf-postgres/sql/edtf_postgres--1.2.3--1.3.0.sql from"
            " published 1.2.3 (edtf-postgres-v1.2.3) — existing installations"
            " would be stranded\n"
        )

    def test_the_guard_checks_past_a_burned_head(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """At v1.3.1 the path is from 1.2.3, and it is there."""
        self.edtf("edtf-releases.json", "v1.3.1", "edtf-postgres-v1.2.3")
        status, out, err = self.previous("guard", version="1.3.1", capfd=capfd)
        assert status == 0, err
        assert out == "::notice::upgrade path 1.2.3 -> 1.3.1 present\n"

    def test_the_guard_checks_from_the_newest_not_the_latest(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Latest is v1.3.4; the path owed is from 1.3.5, and it is there."""
        self.edtf(
            "edtf-releases-v1.3.5.json",
            "v1.3.5",
            "v1.3.4",
            "edtf_postgres--1.3.5--1.3.6.sql",
        )
        status, out, err = self.previous("guard", version="1.3.6", capfd=capfd)
        assert status == 0, err
        assert out == "::notice::upgrade path 1.3.5 -> 1.3.6 present\n"

    def test_the_fetch_downloads_the_imported_predecessor(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The upgrade execution starts from 1.2.3, off its own release."""
        self.edtf("edtf-releases.json", "v1.3.1", "edtf-postgres-v1.2.3")
        tarball = self.root / "asset.tar.gz"
        with tarfile.open(tarball, "w:gz") as tar:
            tar.add(self.sql, arcname="pkgroot")
        dest: tuple[str, ...] = ("--tarball", str(self.root / "prev.tar.gz"))
        dest += ("--dest", str(self.root / "prevroot"))
        cell = ("--pg", "18", "--arch", "arm64", *dest)
        status, out, err = self.previous(
            "fetch", *cell, version="1.3.1", tarball=str(tarball), capfd=capfd
        )
        assert status == 0, err
        assert out == "1.2.3\n"
        assert (
            "release download edtf-postgres-v1.2.3 --repo acme/demo --pattern"
            " edtf_postgres-1.2.3-pg18-linux-arm64.tar.gz --output"
            f" {self.root}/prev.tar.gz"
        ) == self.gh_calls()[1]


@pytest.mark.usefixtures("_set_up_test_older_line")
class TestOlderLine(TestPreviousRelease):
    """The predecessor is LOWER than the version under release (#1592).

    1.2.4 and 1.3.0 are both published. A release on the older line,
    1.2.5, upgrades from 1.2.4: a path from 1.3.0 would be a downgrade.
    A release on the newer line, 1.3.1, upgrades from 1.3.0.
    """

    @pytest.fixture
    def _set_up_test_older_line(self) -> None:
        """Publish 1.2.4 and 1.3.0, each on its own `v` release."""
        self.forge(
            [
                release("v1.3.0", "demo_ext-1.3.0-pg18-linux-arm64.tar.gz"),
                release("v1.2.4", "demo_ext-1.2.4-pg18-linux-arm64.tar.gz"),
            ],
            "v1.3.0",
        )
        self.graph("demo_ext--1.2.3--1.2.4.sql", "demo_ext--1.2.4--1.3.0.sql")

    def predecessor(self, version: str, *, capfd: pytest.CaptureFixture[str]) -> Done:
        """Ask the helper for demo_ext's predecessor of `version`.

        Returns:
            What the run answered.

        """
        control = f"{self.CRATE}/demo_ext.control"
        args = ["predecessor", "--repo", "acme/demo", "--version", version]
        return self.helper([*args, "--control", control], self.env, capfd=capfd)

    @pytest.mark.parametrize(
        ("version", "want"),
        [
            ("1.2.5", "1.2.4\tv1.2.4\n"),
            ("1.3.1", "1.3.0\tv1.3.0\n"),
        ],
    )
    def test_each_line_yields_its_own_predecessor(
        self, version: object, want: object, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """1.2.5 comes from 1.2.4 on v1.2.4; 1.3.1 from 1.3.0 on v1.3.0."""
        status, out, err = self.predecessor(version, capfd=capfd)
        assert status == 0, err
        assert want == out

    def test_the_record_names_what_the_forge_carries(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """`record`: the carried versions written, the predecessor printed.

        1.2.3 is in the graph but on no release, so it is not recorded;
        releasing 1.3.1, the predecessor is 1.3.0 on v1.3.0 (#825, #1592).
        """
        record = self.root / ".pgrx-installable"
        control = f"{self.CRATE}/demo_ext.control"
        args = ["record", "--repo", "acme/demo", "--version", "1.3.1"]
        status, out, err = self.helper(
            [*args, "--out", str(record), control], self.env, capfd=capfd
        )
        assert status == 0, err
        assert out == "demo_ext\t1.3.0\tv1.3.0\n"
        written = record.read_text(encoding="utf-8")
        assert written.startswith("# pgrx installable versions"), written
        assert written.endswith("# derived: v1.3.1\ndemo_ext 1.3.0 1.2.4\n"), written

    def test_the_guard_owes_no_downgrade_path(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Releasing 1.2.5 with 1.2.4--1.2.5 present passes; 1.3.0 is not asked."""
        self.graph("demo_ext--1.2.4--1.2.5.sql")
        status, out, _err = self.previous("guard", version="1.2.5", capfd=capfd)
        assert status == 0, out
        assert out == "::notice::upgrade path 1.2.4 -> 1.2.5 present\n"


class TestNoSiteAsksForTheLatestRelease:
    """No tracked file under release/ finds a predecessor as "latest" (#1592).

    `gh release view` with no tag answers GitHub's latest release, which
    is the forge's opinion of newest and not what an installation can be
    sitting on. The predecessor is previous_release.py's to answer.
    """

    # The one other caller, and why it is not a predecessor search:
    # it proves the latest release itself, and asks nothing before it.
    NOT_A_PREDECESSOR = ("release/repro_resolve.py",)
    UNTAGGED = re.compile(r"""\brelease["',\s]*view["',]*\s+["']?--""")

    def test_no_untagged_release_view(self) -> None:
        """Every `gh release view` in release/ names the release it reads."""
        root = CANON
        tracked = support.tracked(root, "release")
        found = []
        for path in tracked:
            name = Path(path).name
            if "/testdata/" in path or name.startswith(("test_", "prop_")):
                continue
            if path in self.NOT_A_PREDECESSOR:
                continue
            text = (root / path).read_text(encoding="utf-8", errors="replace")
            found += [
                f"{path}:{text.count(chr(10), 0, hit.start()) + 1}"
                for hit in self.UNTAGGED.finditer(text)
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


@pytest.mark.usefixtures("_set_up_test_container_scripts")
class TestContainerScripts:
    """The rows share the stub functions and a call log."""

    @pytest.fixture
    def _set_up_test_container_scripts(self, tmp_path: Path) -> None:
        """Stub functions; PG 18, demo_ext, 1.1.0 to 1.2.0."""
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
        return run([BASH, str(PGRX / name), *args], {**self.env, **env})

    def logged(self) -> list[str]:
        """Read back the stubbed calls, in order.

        Returns:
            One line per call.

        """
        return self.calls.read_text().splitlines()

    def test_smoke_without_a_script_creates_the_extension_only(self) -> None:
        """Package in, CREATE EXTENSION as postgres, nothing else."""
        result = self.script("smoke.sh")
        assert result.returncode == 0, result.stderr
        assert (
            strings(
                (
                    "cp -a /pkgroot/usr/lib/postgresql/18/lib/."
                    " /usr/lib/postgresql/18/lib/"
                ),
                (
                    "cp -a /pkgroot/usr/share/postgresql/18/extension/."
                    " /usr/share/postgresql/18/extension/"
                ),
                "su postgres -s /bin/bash /pgrx/smoke-cluster.sh",
            )
            == self.logged()
        )

    def test_smoke_refuses_a_script_that_is_not_executable(self) -> None:
        """The caller's smoke-test must exist and be executable in /src."""
        result = self.script("smoke.sh", UNTRUSTED_SMOKE="ci/no-such-smoke.sh")
        assert result.returncode == 1
        assert (
            result.stderr
            == "::error::smoke.sh: smoke-test ci/no-such-smoke.sh is not executable\n"
        )
        # Stopped at the refusal, never tried: nothing else was said.
        assert result.stdout == ""

    def test_smoke_cluster_names_the_extension_from_its_control(self) -> None:
        """The cluster half reads EXT, which smoke.sh exports from the control."""
        text = (PGRX / "smoke-cluster.sh").read_text()
        assert 'psql -c "CREATE EXTENSION ${EXT};"' in text
        assert "export EXT" in (PGRX / "smoke.sh").read_text()

    def test_upgrade_creates_at_prev_then_updates_over_the_new_package(self) -> None:
        """Old package, create, new package over it, update: in that order."""
        result = self.script("upgrade.sh")
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
            == self.logged()
        )

    def test_update_reads_the_new_version_back(self) -> None:
        """ALTER EXTENSION UPDATE TO the new version, then the version read."""
        result = self.script("upgrade-cluster.sh", "update")
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
            == self.logged()
        )

    def test_update_refuses_any_other_version(self) -> None:
        """An update that left the old version is a red, with both versions."""
        result = self.script("upgrade-cluster.sh", "update", STUB_GOT="1.1.0")
        assert result.returncode == 1
        assert result.stderr == (
            "::error::upgrade-cluster.sh: upgrade left extversion 1.1.0, wanted 1.2.0\n"
        )

    def test_an_unknown_phase_is_refused(self) -> None:
        """Only create and update exist."""
        result = self.script("upgrade-cluster.sh", "drop")
        assert result.returncode == 2
        assert (
            result.stderr
            == "::error::upgrade-cluster.sh: unknown phase 'drop' (create or update)\n"
        )
        assert not self.calls.exists()


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


@pytest.mark.usefixtures("_set_up_test_debug_split")
class TestDebugSplit:
    """debug-split.sh over pkgroot/usr/lib/postgresql/18/lib/demo_ext.so."""

    REL = "usr/lib/postgresql/18/lib/demo_ext.so"

    @pytest.fixture
    def _set_up_test_debug_split(self, tmp_path: Path) -> None:
        """Build a package root holding one real library with .dep-v0."""
        if not elf_tools_present():
            pytest.skip(SKIP_ELF)
        self.root = Path(str(tmp_path))
        self.pkgroot = self.root / "pkgroot"
        self.so = self.pkgroot / self.REL
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
        return run([BASH, script, str(self.pkgroot), str(self.root / "debugroot")], env)

    @staticmethod
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

    def test_a_good_split(self) -> None:
        """Debug info out to the sibling archive, .dep-v0 and the link kept."""
        result = self.split()
        assert result.returncode == 0, result.stdout + result.stderr
        assert re.search(
            rf"^::notice::{self.REL}: split and stripped, \d+ dynamic functions,"
            r" \.dep-v0 kept\n$",
            result.stdout,
        )
        stripped = self.sections(self.so)
        assert " .dep-v0 " in stripped
        assert " .gnu_debuglink " in stripped
        assert " .debug_info " not in stripped
        assert " .symtab " not in stripped
        debug = self.root / "debugroot" / "usr" / "lib" / "debug" / f"{self.REL}.debug"
        assert " .debug_info " in self.sections(debug)

    def test_no_dep_section_is_refused(self) -> None:
        """A library cargo-auditable never reached (#1274)."""
        build_library(self.so, SOURCE, dep=False)
        result = self.split()
        assert result.returncode == 1
        assert (
            f"::error::debug-split.sh: {self.REL} after the split: no .dep-v0\n"
            == result.stderr
        )

    @pytest.mark.parametrize(("name", "shim"), SHIMS.items())
    def test_each_wrong_step_is_refused_by_name(
        self, name: object, shim: object
    ) -> None:
        """No debuglink; debug info left; symbol table left; functions lost."""
        self.tearDown()
        self.setUp()
        result = self.split(shim)
        assert result.returncode == 1, result.stdout
        if name == "dynamic functions":
            assert re.search(
                rf"^::error::debug-split.sh: {self.REL} after the split:"
                r" \d+ dynamic functions, \d+ before the split\n$",
                result.stderr,
            )
        else:
            assert (
                f"::error::debug-split.sh: {self.REL} after the split: {name}\n"
            ) == result.stderr

    def test_no_library_is_refused(self) -> None:
        """A package with nothing to split is a red, never a quiet pass."""
        self.so.unlink()
        result = self.split()
        assert result.returncode == 1
        assert (
            f"::error::debug-split.sh: no shared library under {self.pkgroot} to"
            f" split\n"
        ) == result.stderr


@pytest.mark.usefixtures("_set_up_test_glibc_floor")
class TestGlibcFloor:
    """glibc-floor.sh over a real library, against floors either side."""

    @pytest.fixture
    def _set_up_test_glibc_floor(self, tmp_path: Path) -> None:
        """Make an empty package root."""
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
        return run([BASH, str(PGRX / "glibc-floor.sh"), str(self.pkgroot), floor], {})

    def test_within_the_floor(self) -> None:
        """Every versioned symbol at or under a floor far above them."""
        build_library(self.lib, SOURCE, dep=False)
        result = self.floor("99.0")
        assert result.returncode == 0, result.stdout
        assert re.search(
            r"^::notice::glibc ceiling GLIBC_\d+\.\d+ within floor 99\.0\n$",
            result.stdout,
        )

    def test_above_the_floor_is_refused(self) -> None:
        """A symbol newer than the floor fails the release, not dlopen."""
        build_library(self.lib, SOURCE, dep=False)
        result = self.floor("1.0")
        assert result.returncode == 1
        assert re.search(
            r"^::error::glibc-floor.sh: demo_ext\.so needs GLIBC_\d+\.\d+, above the"
            r" 1\.0 floor"
            r" the class guarantees\n$",
            result.stderr,
        )

    def test_no_versioned_symbols(self) -> None:
        """A library that takes nothing from glibc has nothing to assert."""
        build_library(self.lib, NO_LIBC, libc=False, dep=False)
        result = self.floor("2.36")
        assert result.returncode == 0, result.stdout
        assert (
            result.stdout
            == "::notice::no versioned glibc symbols (static or unusual link)\n"
        )


class TestGlibcFloorNoLibrary:
    """The one glibc row that needs no binutils."""

    def test_no_library_is_refused(self) -> None:
        """A package with no library has no floor to prove: a red."""
        with TemporaryDirectory() as d:
            result = run([BASH, str(PGRX / "glibc-floor.sh"), d, "2.36"], {})
        assert result.returncode == 1
        assert result.stderr == "::error::glibc-floor.sh: no shared library packaged\n"
