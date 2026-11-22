#!/usr/bin/env python3
"""The pgrx-extension build's tracked scripts, driven from Python (#1380).

build-pgrx-extension.yml carried this logic as shell three quotes deep
inside `docker run … bash -c '… su postgres -c "…"'`, with no tests.
It now lives in release/pgrx/ as plain scripts the containers run from
the pinned canon tree, and in release/pgrx/previous_release.py on the
runner. Each guard branch is a row here, and every refusal row asserts
its message, word for word, against the wording the workflow carried.
previous_release.py's rows call its `main` in this process (#1698);
one starts it as the workflow does instead, the script contract.

The debug-split and glibc rows run the real binutils over a small real
shared library built here, so they need `cc`, `readelf`, `objcopy` and
`strip` (GNU, ELF). Where those are absent (a Mac) the rows are skipped
by name; on the gate's runner a missing tool is a failure, never a skip
that reads like a pass (#364).

stdlib `unittest`, matching the other tests here.
"""

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest import mock

# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))
sys.path.append(str(Path(__file__).resolve().parent / "pgrx"))

import hermetic
import previous_release

CANON = Path(__file__).resolve().parent.parent
PGRX = CANON / "release" / "pgrx"
PREVIOUS = PGRX / "previous_release.py"
BASH = hermetic.tool("bash")
ON_RUNNER = os.environ.get("GITHUB_ACTIONS") == "true"
# What a helper's run answered: a child's, or an in-process call's.
type Done = subprocess.CompletedProcess[str] | hermetic.Called


_NAMED = contextlib.ExitStack()


def setUpModule() -> None:
    """Name the helper by its file, as a run outside mise does (#1934).

    An in-process call reads argv, whatever task runs this suite.
    """
    _NAMED.enter_context(mock.patch.object(sys, "argv", ["previous_release.py"]))
    for key in ("MISE_TASK_NAME", "ORG_FINDINGS"):
        if key in os.environ:
            _NAMED.callback(os.environ.__setitem__, key, os.environ.pop(key))
    # Empty, not unset: unset on a runner records to the job's file (#1976).
    os.environ["ORG_FINDINGS"] = ""
    _NAMED.callback(os.environ.pop, "ORG_FINDINGS", None)
    unittest.addModuleCleanup(_NAMED.close)


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
        env=hermetic.env(**env),
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


class FloorBlock(unittest.TestCase):
    """floor.sh against an extension directory as the package lays it out."""

    @override
    def setUp(self) -> None:
        """Lay out a control, the install script and the upgrade scripts."""
        self.tmp = TemporaryDirectory()
        self.extdir = Path(self.tmp.name)
        (self.extdir / "demo_ext.control").write_text("default_version = '1.2.0'\n")
        for script in (
            "demo_ext--1.2.0.sql",
            "demo_ext--1.0.0--1.2.0.sql",
            "demo_ext--1.1.0--1.2.0.sql",
            "demo_ext--1.0.0--1.1.0.sql",
            "demo_ext--1.1.0.sql",
        ):
            (self.extdir / script).write_text(GENERATED)

    @override
    def tearDown(self) -> None:
        """Drop the directory."""
        self.tmp.cleanup()

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
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(
            "::notice::demo_ext 1.2.0 was built against PostgreSQL 18.7\n",
            result.stdout,
        )
        block = filled("demo_ext", "1.2.0", "18.7", "180007")
        for script in (
            "demo_ext--1.2.0.sql",
            "demo_ext--1.0.0--1.2.0.sql",
            "demo_ext--1.1.0--1.2.0.sql",
        ):
            with self.subTest(script=script):
                self.assertEqual(block + GENERATED, (self.extdir / script).read_text())
        for script in ("demo_ext--1.0.0--1.1.0.sql", "demo_ext--1.1.0.sql"):
            with self.subTest(untouched=script):
                self.assertEqual(GENERATED, (self.extdir / script).read_text())

    def test_the_filled_block_names_the_floor(self) -> None:
        """No placeholder survives, and the number and X.Y are the build's."""
        self.floor()
        text = (self.extdir / "demo_ext--1.2.0.sql").read_text()
        self.assertNotIn("@", text)
        self.assertTrue(text.startswith("DO $pgrx_floor$\n"))
        self.assertIn("< 180007 THEN", text)
        self.assertIn("'demo_ext 1.2.0 was built against PostgreSQL 18.7;", text)

    def test_a_script_already_using_the_tag_is_refused(self) -> None:
        """The dollar-quote tag must be one no generated script uses."""
        rows = ("demo_ext--1.2.0.sql", "demo_ext--1.1.0--1.2.0.sql")
        for script in rows:
            with self.subTest(script=script):
                self.setUp()
                planted = "SELECT $pgrx_floor$ x $pgrx_floor$;\n"
                (self.extdir / script).write_text(planted)
                result = self.floor()
                self.assertEqual(1, result.returncode)
                self.assertEqual(
                    f"::error::floor.sh: {script} already uses the pgrx_floor tag\n",
                    result.stderr,
                )
                self.assertEqual(planted, (self.extdir / script).read_text())

    def test_no_script_for_the_version_is_not_an_error(self) -> None:
        """A control whose version has no script yet leaves the tree alone."""
        for script in self.extdir.glob("*.sql"):
            script.unlink()
        result = self.floor()
        self.assertEqual(0, result.returncode, result.stderr)
        none: list[Path] = []
        left: list[Path] = list(self.extdir.glob("*.sql"))
        self.assertEqual(none, left)


# --------------------------------------------------------------------
# previous_release.py: the control-file refusal and the previous
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

JQ = hermetic.optional_tool("jq")
TESTDATA = CANON / "release" / "testdata"
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


class PreviousRelease(unittest.TestCase):
    """The rows share one directory: a stub gh, a crate, a log."""

    CRATE = "crates/demo-ext"

    @override
    def setUp(self) -> None:
        """Put a stub gh first on PATH and a crate at CRATE."""
        if not JQ:
            self.skipTest("jq is a belt tool and drives the gh stub")
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
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

    @override
    def tearDown(self) -> None:
        """Drop the directory."""
        self.tmp.cleanup()

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
        self, args: list[str], env: dict[str, str], *, script: bool = False
    ) -> Done:
        """Run the helper on `args` in the row's directory.

        In this process unless `script`, which starts it by its path. The
        helper resolves `gh` on PATH once, at import, so the call is
        given the stub the child would have found first on its PATH.

        Returns:
            What the run answered.

        """
        if script:
            return run([str(PREVIOUS), *args], env, cwd=self.root)

        def call() -> int:
            return previous_release.main(args)

        with mock.patch.object(previous_release, "GH", str(self.gh)):
            return hermetic.call_main(call, env=hermetic.env(**env), cwd=self.root)

    def previous(
        self,
        mode: str,
        *extra: str,
        version: str = "1.2.0",
        **stubs: str,
    ) -> Done:
        """Run the helper at `version` for CRATE.

        Returns:
            What the run answered.

        """
        env = {**self.env, **{f"STUB_{k.upper()}": v for k, v in stubs.items()}}
        args = [mode, "--repo", "acme/demo", "--version", version]
        return self.helper([*args, "--crate-dir", self.CRATE, *extra], env)

    def gh_calls(self) -> list[str]:
        """Each gh invocation, its arguments joined by spaces.

        Returns:
            The calls in order.

        """
        return self.log.read_text().splitlines() if self.log.exists() else []


class ControlFileRefusal(PreviousRelease):
    """`guard`: the backstop for a tag minted around the upgrade path."""

    def test_no_previous_release(self) -> None:
        """No release at all, or none carrying this extension's tarballs."""
        for releases, latest in (([], ""), ([release("release-1")], "release-1")):
            with self.subTest(latest=latest):
                self.forge(releases, latest)
                self.graph("demo_ext--1.0.0--1.1.0.sql")
                result = self.previous("guard")
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(
                    "::notice::no previous release; first publish needs no"
                    " upgrade path\n",
                    result.stdout,
                )

    def test_a_rerun(self) -> None:
        """A release already carries THIS version: nothing to upgrade from."""
        self.forge([release("v1.2.0", "demo_ext-1.2.0-pg18-linux-arm64.tar.gz")])
        result = self.previous("guard")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "::notice::re-run of 1.2.0; upgrade path not applicable\n", result.stdout
        )
        self.assertEqual(1, len(self.gh_calls()))

    def test_the_extensions_first_publish(self) -> None:
        """A previous release that shipped no tarball of this extension (#87).

        One meaning of first publish now (#1592): no version in the graph
        is installable. It used to be said per tag, naming GitHub's latest
        release, which is no longer what anything asks.
        """
        self.forge([release("v1.1.0", "demo-cli.tar.gz")], "v1.1.0")
        self.graph("demo_ext--1.0.0--1.1.0.sql")
        result = self.previous("guard")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "::notice::no previous release; first publish needs no upgrade path\n",
            result.stdout,
        )
        self.assertEqual(
            "api repos/acme/demo/releases --paginate --jq"
            " .[] | select(.draft | not) | {tag: .tag_name, asset: .assets[]?.name}"
            ' | "\\(.tag)\\t\\(.asset)"',
            self.gh_calls()[0],
        )

    def test_a_missing_upgrade_path_is_refused(self) -> None:
        """Shipped before, no sql/<ext>--prev--new.sql: stranded, refused."""
        self.forge([release("v1.1.0", DEMO_CELL)], "v1.1.0")
        self.graph("demo_ext--1.0.0--1.1.0.sql")
        result = self.previous("guard")
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            "::error::previous_release.py: no upgrade path"
            " crates/demo-ext/sql/demo_ext--1.1.0--1.2.0.sql"
            " from published 1.1.0 (v1.1.0) — existing installations would be"
            " stranded\n",
            result.stderr,
        )

    def test_a_present_upgrade_path_passes(self) -> None:
        """The derived script is there: the release may proceed."""
        self.forge([release("v1.1.0", DEMO_CELL)], "v1.1.0")
        self.graph("demo_ext--1.0.0--1.1.0.sql", "demo_ext--1.1.0--1.2.0.sql")
        result = self.previous("guard")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "::notice::upgrade path 1.1.0 -> 1.2.0 present\n", result.stdout
        )

    def test_a_failed_listing_read_is_red(self) -> None:
        """Gh failing on the listing is a red with gh's own words, never a pass.

        The script row: gh's words are on the child's own stderr.
        """
        args = ["guard", "--repo", "acme/demo", "--version", "1.2.0"]
        result = self.helper([*args, "--crate-dir", self.CRATE], self.env, script=True)
        self.assertEqual(1, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual("HTTP 502\n", result.stderr)


class PreviousTarball(PreviousRelease):
    """`fetch`: the predecessor's tarball of this cell, or a skip."""

    CELL = ("--pg", "18", "--arch", "arm64")

    def fetch(self, **stubs: str) -> Done:
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
        return self.previous("fetch", *self.CELL, *dest, **stubs)

    def test_no_previous_release_or_a_rerun_skips(self) -> None:
        """No release, none of this extension, or this version: said why."""
        rerun = release("v1.2.0", "demo_ext-1.2.0-pg18-linux-arm64.tar.gz")
        for releases in ([], [release("release-1")], [rerun]):
            with self.subTest(releases=releases):
                self.forge(releases)
                self.graph("demo_ext--1.0.0--1.1.0.sql")
                result = self.fetch()
                self.assertEqual(0, result.returncode)
                self.assertEqual("", result.stdout)
                self.assertEqual(
                    "::notice::no previous release; upgrade execution skipped\n",
                    result.stderr,
                )

    def test_no_tarball_for_this_cell_skips(self) -> None:
        """The predecessor shipped no tarball of this pg and arch."""
        self.forge([release("v1.1.0", "demo_ext-1.1.0-pg17-linux-arm64.tar.gz")])
        self.graph("demo_ext--1.0.0--1.1.0.sql")
        result = self.fetch()
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual(
            "::notice::v1.1.0 shipped no demo_ext-1.1.0-pg18-linux-arm64.tar.gz;"
            " upgrade execution skipped\n",
            result.stderr,
        )
        self.assertEqual(
            f"release download v1.1.0 --repo acme/demo --pattern"
            f" demo_ext-1.1.0-pg18-linux-arm64.tar.gz --output {self.root}/prev.tar.gz",
            self.gh_calls()[1],
        )

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

    def test_the_tarball_is_extracted_and_the_version_printed(self) -> None:
        """The cell's tarball lands under DEST, and stdout is the version."""
        self.forge([release("v1.1.0", DEMO_CELL)])
        self.graph("demo_ext--1.0.0--1.1.0.sql")
        result = self.fetch(tarball=str(self.tarball()))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("1.1.0\n", result.stdout)
        extracted = self.root / "prevroot" / "pkgroot" / "usr" / "lib" / "demo_ext.so"
        self.assertEqual(b"\x7fELF", extracted.read_bytes())

    def test_a_corrupt_tarball_is_red(self) -> None:
        """Tar failing is tar's own red, and no version is printed."""
        self.forge([release("v1.1.0", DEMO_CELL)])
        self.graph("demo_ext--1.0.0--1.1.0.sql")
        tarball = self.root / "asset.tar.gz"
        tarball.write_bytes(b"not a tarball")
        result = self.fetch(tarball=str(tarball))
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout)


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


class OnePredecessor(PreviousRelease):
    """The function and the two build sites, from edtf's captures."""

    CRATE = "crates/edtf-postgres"

    def edtf(self, listing: str, sql_at: str, latest: str, *extra: str) -> None:
        """Serve a captured listing over a captured `sql/` directory."""
        self.env["STUB_LISTING"] = str(TESTDATA / listing)
        self.env["STUB_LATEST"] = latest
        names = (TESTDATA / f"edtf-sql-{sql_at}.txt").read_text().split()
        self.graph(*names, *extra)

    def predecessor(self, version: str) -> Done:
        """Ask the helper for edtf_postgres's predecessor of `version`.

        Returns:
            What the run answered.

        """
        control = f"{self.CRATE}/edtf_postgres.control"
        args = ["predecessor", "--repo", "acme/demo", "--version", version]
        return self.helper([*args, "--control", control], self.env)

    def test_an_imported_tag_scheme_yields_the_published_predecessor(self) -> None:
        """At import, 1.2.3 sits on `edtf-postgres-v1.2.3` (main: none)."""
        self.edtf("edtf-releases.json", "v1.2.3", "edtf-postgres-v1.2.3")
        result = self.predecessor("1.3.0")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("1.2.3\tedtf-postgres-v1.2.3\n", result.stdout)

    def test_a_burned_head_yields_the_next_installable_version(self) -> None:
        """At v1.3.1 the graph's next head, 1.3.0, burned: 1.2.3 it is."""
        self.edtf("edtf-releases.json", "v1.3.1", "edtf-postgres-v1.2.3")
        result = self.predecessor("1.3.1")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("1.2.3\tedtf-postgres-v1.2.3\n", result.stdout)

    def test_an_older_release_marked_latest_yields_the_newest(self) -> None:
        """A maintenance release v1.3.4 published last: 1.3.5 is still newest."""
        self.edtf("edtf-releases-v1.3.5.json", "v1.3.5", "v1.3.4")
        result = self.predecessor("1.3.6")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("1.3.5\tv1.3.5\n", result.stdout)

    def test_an_empty_graph_yields_nothing(self) -> None:
        """No upgrade script at all: first publish, whatever is published."""
        self.env["STUB_LISTING"] = str(TESTDATA / "edtf-releases-v1.3.5.json")
        self.graph("README.md")
        result = self.predecessor("1.3.6")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stdout)

    def test_the_guard_refuses_from_the_imported_predecessor(self) -> None:
        """No 1.2.3--1.3.0 path: refused, naming the release that carries 1.2.3."""
        self.edtf("edtf-releases.json", "v1.2.3", "edtf-postgres-v1.2.3")
        result = self.previous("guard", version="1.3.0")
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertEqual(
            "::error::previous_release.py: no upgrade path"
            " crates/edtf-postgres/sql/edtf_postgres--1.2.3--1.3.0.sql from"
            " published 1.2.3 (edtf-postgres-v1.2.3) — existing installations"
            " would be stranded\n",
            result.stderr,
        )

    def test_the_guard_checks_past_a_burned_head(self) -> None:
        """At v1.3.1 the path is from 1.2.3, and it is there."""
        self.edtf("edtf-releases.json", "v1.3.1", "edtf-postgres-v1.2.3")
        result = self.previous("guard", version="1.3.1")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "::notice::upgrade path 1.2.3 -> 1.3.1 present\n", result.stdout
        )

    def test_the_guard_checks_from_the_newest_not_the_latest(self) -> None:
        """Latest is v1.3.4; the path owed is from 1.3.5, and it is there."""
        self.edtf(
            "edtf-releases-v1.3.5.json",
            "v1.3.5",
            "v1.3.4",
            "edtf_postgres--1.3.5--1.3.6.sql",
        )
        result = self.previous("guard", version="1.3.6")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "::notice::upgrade path 1.3.5 -> 1.3.6 present\n", result.stdout
        )

    def test_the_fetch_downloads_the_imported_predecessor(self) -> None:
        """The upgrade execution starts from 1.2.3, off its own release."""
        self.edtf("edtf-releases.json", "v1.3.1", "edtf-postgres-v1.2.3")
        tarball = self.root / "asset.tar.gz"
        with tarfile.open(tarball, "w:gz") as tar:
            tar.add(self.sql, arcname="pkgroot")
        dest: tuple[str, ...] = ("--tarball", str(self.root / "prev.tar.gz"))
        dest += ("--dest", str(self.root / "prevroot"))
        cell = ("--pg", "18", "--arch", "arm64", *dest)
        result = self.previous("fetch", *cell, version="1.3.1", tarball=str(tarball))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("1.2.3\n", result.stdout)
        self.assertEqual(
            "release download edtf-postgres-v1.2.3 --repo acme/demo --pattern"
            " edtf_postgres-1.2.3-pg18-linux-arm64.tar.gz --output"
            f" {self.root}/prev.tar.gz",
            self.gh_calls()[1],
        )


class OlderLine(PreviousRelease):
    """The predecessor is LOWER than the version under release (#1592).

    1.2.4 and 1.3.0 are both published. A release on the older line,
    1.2.5, upgrades from 1.2.4: a path from 1.3.0 would be a downgrade.
    A release on the newer line, 1.3.1, upgrades from 1.3.0.
    """

    @override
    def setUp(self) -> None:
        """Publish 1.2.4 and 1.3.0, each on its own `v` release."""
        super().setUp()
        self.forge(
            [
                release("v1.3.0", "demo_ext-1.3.0-pg18-linux-arm64.tar.gz"),
                release("v1.2.4", "demo_ext-1.2.4-pg18-linux-arm64.tar.gz"),
            ],
            "v1.3.0",
        )
        self.graph("demo_ext--1.2.3--1.2.4.sql", "demo_ext--1.2.4--1.3.0.sql")

    def predecessor(self, version: str) -> Done:
        """Ask the helper for demo_ext's predecessor of `version`.

        Returns:
            What the run answered.

        """
        control = f"{self.CRATE}/demo_ext.control"
        args = ["predecessor", "--repo", "acme/demo", "--version", version]
        return self.helper([*args, "--control", control], self.env)

    def test_each_line_yields_its_own_predecessor(self) -> None:
        """1.2.5 comes from 1.2.4 on v1.2.4; 1.3.1 from 1.3.0 on v1.3.0."""
        for version, want in (
            ("1.2.5", "1.2.4\tv1.2.4\n"),
            ("1.3.1", "1.3.0\tv1.3.0\n"),
        ):
            with self.subTest(version=version):
                result = self.predecessor(version)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(want, result.stdout)

    def test_the_record_names_what_the_forge_carries(self) -> None:
        """`record`: the carried versions written, the predecessor printed.

        1.2.3 is in the graph but on no release, so it is not recorded;
        releasing 1.3.1, the predecessor is 1.3.0 on v1.3.0 (#825, #1592).
        """
        out = self.root / ".pgrx-installable"
        control = f"{self.CRATE}/demo_ext.control"
        args = ["record", "--repo", "acme/demo", "--version", "1.3.1"]
        result = self.helper([*args, "--out", str(out), control], self.env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("demo_ext\t1.3.0\tv1.3.0\n", result.stdout)
        written = out.read_text(encoding="utf-8")
        self.assertTrue(written.startswith("# pgrx installable versions"), written)
        self.assertTrue(
            written.endswith("# derived: v1.3.1\ndemo_ext 1.3.0 1.2.4\n"), written
        )

    def test_the_guard_owes_no_downgrade_path(self) -> None:
        """Releasing 1.2.5 with 1.2.4--1.2.5 present passes; 1.3.0 is not asked."""
        self.graph("demo_ext--1.2.4--1.2.5.sql")
        result = self.previous("guard", version="1.2.5")
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertEqual(
            "::notice::upgrade path 1.2.4 -> 1.2.5 present\n", result.stdout
        )


class NoSiteAsksForTheLatestRelease(unittest.TestCase):
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
        tracked = hermetic.tracked(root, "release")
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
        self.assertEqual(strings(), found)


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


class ContainerScripts(unittest.TestCase):
    """The rows share the stub functions and a call log."""

    @override
    def setUp(self) -> None:
        """Stub functions; PG 18, demo_ext, 1.1.0 to 1.2.0."""
        self.tmp = TemporaryDirectory()
        self.calls = Path(self.tmp.name) / "calls"
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

    @override
    def tearDown(self) -> None:
        """Drop the directory."""
        self.tmp.cleanup()

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
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
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
            ),
            self.logged(),
        )

    def test_smoke_refuses_a_script_that_is_not_executable(self) -> None:
        """The caller's smoke-test must exist and be executable in /src."""
        result = self.script("smoke.sh", UNTRUSTED_SMOKE="ci/no-such-smoke.sh")
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            "::error::smoke.sh: smoke-test ci/no-such-smoke.sh is not executable\n",
            result.stderr,
        )
        # Stopped at the refusal, never tried: nothing else was said.
        self.assertEqual("", result.stdout)

    def test_smoke_cluster_names_the_extension_from_its_control(self) -> None:
        """The cluster half reads EXT, which smoke.sh exports from the control."""
        text = (PGRX / "smoke-cluster.sh").read_text()
        self.assertIn('psql -c "CREATE EXTENSION ${EXT};"', text)
        self.assertIn("export EXT", (PGRX / "smoke.sh").read_text())

    def test_upgrade_creates_at_prev_then_updates_over_the_new_package(self) -> None:
        """Old package, create, new package over it, update: in that order."""
        result = self.script("upgrade.sh")
        self.assertEqual(0, result.returncode, result.stderr)
        lib = "/usr/lib/postgresql/18/lib"
        ext = "/usr/share/postgresql/18/extension"
        self.assertEqual(
            strings(
                f"cp -a /prev{lib}/. {lib}/",
                f"cp -a /prev{ext}/. {ext}/",
                "su postgres -s /bin/bash /pgrx/upgrade-cluster.sh create",
                f"cp -a /new{lib}/. {lib}/",
                f"cp -a /new{ext}/. {ext}/",
                "su postgres -s /bin/bash /pgrx/upgrade-cluster.sh update",
            ),
            self.logged(),
        )

    def test_update_reads_the_new_version_back(self) -> None:
        """ALTER EXTENSION UPDATE TO the new version, then the version read."""
        result = self.script("upgrade-cluster.sh", "update")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("upgraded 1.1.0 -> 1.2.0\n", result.stdout)
        self.assertEqual(
            strings(
                "psql -c ALTER EXTENSION demo_ext UPDATE TO '1.2.0';",
                (
                    "psql -tA -c SELECT extversion FROM pg_extension"
                    " WHERE extname = 'demo_ext';"
                ),
            ),
            self.logged(),
        )

    def test_update_refuses_any_other_version(self) -> None:
        """An update that left the old version is a red, with both versions."""
        result = self.script("upgrade-cluster.sh", "update", STUB_GOT="1.1.0")
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            "::error::upgrade-cluster.sh: upgrade left extversion 1.1.0, wanted"
            " 1.2.0\n",
            result.stderr,
        )

    def test_an_unknown_phase_is_refused(self) -> None:
        """Only create and update exist."""
        result = self.script("upgrade-cluster.sh", "drop")
        self.assertEqual(2, result.returncode)
        self.assertEqual(
            "::error::upgrade-cluster.sh: unknown phase 'drop' (create or update)\n",
            result.stderr,
        )
        self.assertFalse(self.calls.exists())


# --------------------------------------------------------------------
# debug-split.sh and glibc-floor.sh: real binutils over a real library.
# --------------------------------------------------------------------

ELF_TOOLS = ("cc", "readelf", "objcopy", "strip")
MISSING = [t for t in ELF_TOOLS if hermetic.optional_tool(t) is None]


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
        [hermetic.tool("readelf"), "--version"], capture_output=True, check=False
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
    subprocess.run([hermetic.tool("cc"), *flags, "-o", str(path), str(src)], check=True)
    src.unlink()
    if dep:
        payload = path.with_suffix(".dep")
        payload.write_bytes(b"\x78\x9c\x03\x00\x00\x00\x00\x01")
        subprocess.run(
            [
                hermetic.tool("objcopy"),
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


class DebugSplit(unittest.TestCase):
    """debug-split.sh over pkgroot/usr/lib/postgresql/18/lib/demo_ext.so."""

    REL = "usr/lib/postgresql/18/lib/demo_ext.so"

    @override
    def setUp(self) -> None:
        """Build a package root holding one real library with .dep-v0."""
        if not elf_tools_present():
            self.skipTest(SKIP_ELF)
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pkgroot = self.root / "pkgroot"
        self.so = self.pkgroot / self.REL
        self.so.parent.mkdir(parents=True)
        build_library(self.so, SOURCE)

    @override
    def tearDown(self) -> None:
        """Drop the directory."""
        self.tmp.cleanup()

    def split(
        self, shim: tuple[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the split, one binutils step replaced by `shim` if given.

        Returns:
            The completed process.

        """
        env = {"SEEN": str(self.root / "seen")}
        for tool in ("objcopy", "strip", "readelf"):
            env[f"REAL_{tool}"] = hermetic.tool(tool)
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
            [hermetic.tool("readelf"), "-S", "--wide", str(path)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def test_a_good_split(self) -> None:
        """Debug info out to the sibling archive, .dep-v0 and the link kept."""
        result = self.split()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertRegex(
            result.stdout,
            rf"^::notice::{self.REL}: split and stripped, \d+ dynamic functions,"
            r" \.dep-v0 kept\n$",
        )
        stripped = self.sections(self.so)
        self.assertIn(" .dep-v0 ", stripped)
        self.assertIn(" .gnu_debuglink ", stripped)
        self.assertNotIn(" .debug_info ", stripped)
        self.assertNotIn(" .symtab ", stripped)
        debug = self.root / "debugroot" / "usr" / "lib" / "debug" / f"{self.REL}.debug"
        self.assertIn(" .debug_info ", self.sections(debug))

    def test_no_dep_section_is_refused(self) -> None:
        """A library cargo-auditable never reached (#1274)."""
        build_library(self.so, SOURCE, dep=False)
        result = self.split()
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            f"::error::debug-split.sh: {self.REL} after the split: no .dep-v0\n",
            result.stderr,
        )

    def test_each_wrong_step_is_refused_by_name(self) -> None:
        """No debuglink; debug info left; symbol table left; functions lost."""
        for name, shim in SHIMS.items():
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                result = self.split(shim)
                self.assertEqual(1, result.returncode, result.stdout)
                if name == "dynamic functions":
                    self.assertRegex(
                        result.stderr,
                        rf"^::error::debug-split.sh: {self.REL} after the split:"
                        r" \d+ dynamic functions, \d+ before the split\n$",
                    )
                else:
                    self.assertEqual(
                        f"::error::debug-split.sh: {self.REL} after the split:"
                        f" {name}\n",
                        result.stderr,
                    )

    def test_no_library_is_refused(self) -> None:
        """A package with nothing to split is a red, never a quiet pass."""
        self.so.unlink()
        result = self.split()
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            f"::error::debug-split.sh: no shared library under {self.pkgroot} to"
            f" split\n",
            result.stderr,
        )


class GlibcFloor(unittest.TestCase):
    """glibc-floor.sh over a real library, against floors either side."""

    @override
    def setUp(self) -> None:
        """Make an empty package root."""
        if not elf_tools_present():
            self.skipTest(SKIP_ELF)
        self.tmp = TemporaryDirectory()
        self.pkgroot = Path(self.tmp.name) / "pkgroot"
        self.lib = self.pkgroot / "usr" / "lib" / "demo_ext.so"
        self.lib.parent.mkdir(parents=True)

    @override
    def tearDown(self) -> None:
        """Drop the directory."""
        self.tmp.cleanup()

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
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertRegex(
            result.stdout,
            r"^::notice::glibc ceiling GLIBC_\d+\.\d+ within floor 99\.0\n$",
        )

    def test_above_the_floor_is_refused(self) -> None:
        """A symbol newer than the floor fails the release, not dlopen."""
        build_library(self.lib, SOURCE, dep=False)
        result = self.floor("1.0")
        self.assertEqual(1, result.returncode)
        self.assertRegex(
            result.stderr,
            r"^::error::glibc-floor.sh: demo_ext\.so needs GLIBC_\d+\.\d+, above the"
            r" 1\.0 floor"
            r" the class guarantees\n$",
        )

    def test_no_versioned_symbols(self) -> None:
        """A library that takes nothing from glibc has nothing to assert."""
        build_library(self.lib, NO_LIBC, libc=False, dep=False)
        result = self.floor("2.36")
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertEqual(
            "::notice::no versioned glibc symbols (static or unusual link)\n",
            result.stdout,
        )


class GlibcFloorNoLibrary(unittest.TestCase):
    """The one glibc row that needs no binutils."""

    def test_no_library_is_refused(self) -> None:
        """A package with no library has no floor to prove: a red."""
        with TemporaryDirectory() as d:
            result = run([BASH, str(PGRX / "glibc-floor.sh"), d, "2.36"], {})
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            "::error::glibc-floor.sh: no shared library packaged\n", result.stderr
        )


if __name__ == "__main__":
    unittest.main()
