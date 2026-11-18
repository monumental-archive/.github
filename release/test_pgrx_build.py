#!/usr/bin/env python3
"""The pgrx-extension build's tracked scripts, driven from Python (#1380).

build-pgrx-extension.yml carried this logic as shell three quotes deep
inside `docker run … bash -c '… su postgres -c "…"'`, with no tests.
It now lives in release/pgrx/ as plain scripts the containers run from
the pinned canon tree, and in release/pgrx/previous-release.py on the
runner. Each guard branch is a row here, and every refusal row asserts
its message, word for word, against the wording the workflow carried.

The debug-split and glibc rows run the real binutils over a small real
shared library built here, so they need `cc`, `readelf`, `objcopy` and
`strip` (GNU, ELF). Where those are absent (a Mac) the rows are skipped
by name; on the gate's runner a missing tool is a failure, never a skip
that reads like a pass (#364).

stdlib `unittest`, matching the other tests here.
"""

import os
import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

hermetic = SimpleNamespace(
    **runpy.run_path(str(Path(__file__).resolve().parents[1] / "mise" / "hermetic.py"))
)


CANON = Path(__file__).resolve().parent.parent
PGRX = CANON / "release" / "pgrx"
PREVIOUS = PGRX / "previous-release.py"
BASH = shutil.which("bash")
ON_RUNNER = os.environ.get("GITHUB_ACTIONS") == "true"


def run(
    argv: list[str], env: dict[str, str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one script with `env` laid over the process environment.

    Returns:
        The completed process, output captured as text.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
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
                    f"::error::{script} already uses the pgrx_floor tag\n",
                    result.stdout,
                )
                self.assertEqual(planted, (self.extdir / script).read_text())

    def test_no_script_for_the_version_is_not_an_error(self) -> None:
        """A control whose version has no script yet leaves the tree alone."""
        for script in self.extdir.glob("*.sql"):
            script.unlink()
        result = self.floor()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], list(self.extdir.glob("*.sql")))


# --------------------------------------------------------------------
# previous-release.py: the control-file refusal and the previous
# tarball, against a stub `gh`.
# --------------------------------------------------------------------

GH_STUB = """
printf '%s\\n' "$*" >> "${GH_LOG}"
case "$1 $2" in
  "release view")
    if [[ $3 == --repo ]]; then
      # The newest release's tag: none means gh's own error.
      [[ -n ${STUB_TAG} ]] || { echo "release not found" >&2; exit 1; }
      printf '%s\\n' "${STUB_TAG}"
    else
      # The previous release's assets, already counted by --jq.
      [[ -n ${STUB_SHIPPED} ]] || { echo "HTTP 502" >&2; exit 1; }
      printf '%s\\n' "${STUB_SHIPPED}"
    fi
    ;;
  "release download")
    [[ -n ${STUB_TARBALL} ]] || { echo "no assets match" >&2; exit 1; }
    while (($#)); do
      [[ $1 == --output ]] && cp "${STUB_TARBALL}" "$2"
      shift
    done
    ;;
esac
"""


class PreviousRelease(unittest.TestCase):
    """The rows share one directory: a stub gh, a crate, a log."""

    def setUp(self) -> None:
        """Put a stub gh first on PATH and a crate at crates/demo-ext."""
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        bindir = self.root / "bin"
        bindir.mkdir()
        stub(bindir, "gh", GH_STUB)
        (self.root / "crates" / "demo-ext" / "sql").mkdir(parents=True)
        self.log = self.root / "gh.log"
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "GH_LOG": str(self.log),
            "STUB_TAG": "",
            "STUB_SHIPPED": "",
            "STUB_TARBALL": "",
        }

    def tearDown(self) -> None:
        """Drop the directory."""
        self.tmp.cleanup()

    def previous(
        self, mode: str, *extra: str, **stubs: str
    ) -> subprocess.CompletedProcess[str]:
        """Run the helper at version 1.2.0 for crates/demo-ext.

        Returns:
            The completed process.

        """
        env = {**self.env, **{f"STUB_{k.upper()}": v for k, v in stubs.items()}}
        argv = [str(PREVIOUS), mode, "--repo", "acme/demo", "--version", "1.2.0"]
        return run(
            [*argv, "--crate-dir", "crates/demo-ext", *extra], env, cwd=self.root
        )

    def gh_calls(self) -> list[str]:
        """Each gh invocation, its arguments joined by spaces.

        Returns:
            The calls in order.

        """
        return self.log.read_text().splitlines() if self.log.exists() else []


class ControlFileRefusal(PreviousRelease):
    """`guard`: the backstop for a tag minted around the upgrade path."""

    def test_no_previous_release(self) -> None:
        """Gh errors (no release) or answers a tag that is not v*."""
        for tag in ("", "release-1"):
            with self.subTest(tag=tag):
                result = self.previous("guard", tag=tag)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(
                    "::notice::no previous release; first publish needs no"
                    " upgrade path\n",
                    result.stdout,
                )

    def test_a_rerun(self) -> None:
        """The newest release IS this version: nothing to upgrade from."""
        result = self.previous("guard", tag="v1.2.0")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "::notice::re-run of 1.2.0; upgrade path not applicable\n", result.stdout
        )
        self.assertEqual(1, len(self.gh_calls()))

    def test_the_extensions_first_publish(self) -> None:
        """A previous release that shipped no tarball of this extension."""
        result = self.previous("guard", tag="v1.1.0", shipped="0")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "::notice::v1.1.0 shipped no demo_ext tarballs; first publish of this"
            " extension, no upgrade path needed\n",
            result.stdout,
        )
        self.assertEqual(
            "release view v1.1.0 --repo acme/demo --json assets --jq"
            ' [.assets[].name | select(startswith("demo_ext-1.1.0-pg"))] | length',
            self.gh_calls()[1],
        )

    def test_a_missing_upgrade_path_is_refused(self) -> None:
        """Shipped before, no sql/<ext>--prev--new.sql: stranded, refused."""
        result = self.previous("guard", tag="v1.1.0", shipped="10")
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            "::error::no upgrade path crates/demo-ext/sql/demo_ext--1.1.0--1.2.0.sql"
            " from published 1.1.0 — existing installations would be stranded\n",
            result.stdout,
        )

    def test_a_present_upgrade_path_passes(self) -> None:
        """The derived script is there: the release may proceed."""
        upgrade = (
            self.root / "crates" / "demo-ext" / "sql" / "demo_ext--1.1.0--1.2.0.sql"
        )
        upgrade.write_text("SELECT 1;\n")
        result = self.previous("guard", tag="v1.1.0", shipped="10")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "::notice::upgrade path 1.1.0 -> 1.2.0 present\n", result.stdout
        )

    def test_a_failed_asset_read_is_red(self) -> None:
        """Gh failing on the assets is a red with gh's own words, never a pass."""
        result = self.previous("guard", tag="v1.1.0")
        self.assertEqual(1, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual("HTTP 502\n", result.stderr)


class PreviousTarball(PreviousRelease):
    """`fetch`: the previous release's tarball of this cell, or a skip."""

    CELL = ("--pg", "18", "--arch", "arm64")

    def fetch(self, **stubs: str) -> subprocess.CompletedProcess[str]:
        """Fetch for pg18/arm64 into the row's directory.

        Returns:
            The completed process.

        """
        dest = (
            "--tarball",
            str(self.root / "prev.tar.gz"),
            "--dest",
            str(self.root / "prevroot"),
        )
        return self.previous("fetch", *self.CELL, *dest, **stubs)

    def test_no_previous_release_or_a_rerun_skips(self) -> None:
        """No tag, a tag not v*, or this version: nothing printed, said why."""
        for tag in ("", "release-1", "v1.2.0"):
            with self.subTest(tag=tag):
                result = self.fetch(tag=tag)
                self.assertEqual(0, result.returncode)
                self.assertEqual("", result.stdout)
                self.assertEqual(
                    "::notice::no previous release; upgrade execution skipped\n",
                    result.stderr,
                )

    def test_no_tarball_for_this_cell_skips(self) -> None:
        """The previous release shipped no tarball of this pg and arch."""
        result = self.fetch(tag="v1.1.0")
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

    def test_the_tarball_is_extracted_and_the_version_printed(self) -> None:
        """The cell's tarball lands under DEST, and stdout is the version."""
        payload = self.root / "pkgroot" / "usr" / "lib" / "demo_ext.so"
        payload.parent.mkdir(parents=True)
        payload.write_bytes(b"\x7fELF")
        tarball = self.root / "asset.tar.gz"
        with tarfile.open(tarball, "w:gz") as tar:
            tar.add(self.root / "pkgroot", arcname="pkgroot")
        shutil.rmtree(self.root / "pkgroot")
        result = self.fetch(tag="v1.1.0", tarball=str(tarball))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("1.1.0\n", result.stdout)
        extracted = self.root / "prevroot" / "pkgroot" / "usr" / "lib" / "demo_ext.so"
        self.assertEqual(b"\x7fELF", extracted.read_bytes())

    def test_a_corrupt_tarball_is_red(self) -> None:
        """Tar failing is tar's own red, and no version is printed."""
        tarball = self.root / "asset.tar.gz"
        tarball.write_bytes(b"not a tarball")
        result = self.fetch(tag="v1.1.0", tarball=str(tarball))
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout)


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
            [
                (
                    "cp -a /pkgroot/usr/lib/postgresql/18/lib/."
                    " /usr/lib/postgresql/18/lib/"
                ),
                (
                    "cp -a /pkgroot/usr/share/postgresql/18/extension/."
                    " /usr/share/postgresql/18/extension/"
                ),
                "su postgres -s /bin/bash /pgrx/smoke-cluster.sh",
            ],
            self.logged(),
        )

    def test_smoke_refuses_a_script_that_is_not_executable(self) -> None:
        """The caller's smoke-test must exist and be executable in /src."""
        result = self.script("smoke.sh", UNTRUSTED_SMOKE="ci/no-such-smoke.sh")
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            "::error::smoke-test ci/no-such-smoke.sh is not executable\n", result.stdout
        )
        # Stopped at the refusal, never tried: nothing else was said.
        self.assertEqual("", result.stderr)

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
            [
                f"cp -a /prev{lib}/. {lib}/",
                f"cp -a /prev{ext}/. {ext}/",
                "su postgres -s /bin/bash /pgrx/upgrade-cluster.sh create",
                f"cp -a /new{lib}/. {lib}/",
                f"cp -a /new{ext}/. {ext}/",
                "su postgres -s /bin/bash /pgrx/upgrade-cluster.sh update",
            ],
            self.logged(),
        )

    def test_update_reads_the_new_version_back(self) -> None:
        """ALTER EXTENSION UPDATE TO the new version, then the version read."""
        result = self.script("upgrade-cluster.sh", "update")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("upgraded 1.1.0 -> 1.2.0\n", result.stdout)
        self.assertEqual(
            [
                "psql -c ALTER EXTENSION demo_ext UPDATE TO '1.2.0';",
                (
                    "psql -tA -c SELECT extversion FROM pg_extension"
                    " WHERE extname = 'demo_ext';"
                ),
            ],
            self.logged(),
        )

    def test_update_refuses_any_other_version(self) -> None:
        """An update that left the old version is a red, with both versions."""
        result = self.script("upgrade-cluster.sh", "update", STUB_GOT="1.1.0")
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            "::error::upgrade left extversion 1.1.0, wanted 1.2.0\n", result.stdout
        )

    def test_an_unknown_phase_is_refused(self) -> None:
        """Only create and update exist."""
        result = self.script("upgrade-cluster.sh", "drop")
        self.assertEqual(2, result.returncode)
        self.assertEqual(
            "::error::upgrade-cluster.sh: unknown phase 'drop' (create or update)\n",
            result.stdout,
        )
        self.assertFalse(self.calls.exists())


# --------------------------------------------------------------------
# debug-split.sh and glibc-floor.sh: real binutils over a real library.
# --------------------------------------------------------------------

ELF_TOOLS = ("cc", "readelf", "objcopy", "strip")
MISSING = [t for t in ELF_TOOLS if shutil.which(t) is None]
CC = shutil.which("cc") or "cc"
READELF = shutil.which("readelf") or "readelf"
OBJCOPY = shutil.which("objcopy") or "objcopy"


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
    # ruff: ignore[subprocess-without-shell-equals-true]
    probe = subprocess.run([READELF, "--version"], capture_output=True, check=False)
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
    # ruff: ignore[subprocess-without-shell-equals-true]
    subprocess.run([CC, *flags, "-o", str(path), str(src)], check=True)
    src.unlink()
    if dep:
        payload = path.with_suffix(".dep")
        payload.write_bytes(b"\x78\x9c\x03\x00\x00\x00\x00\x01")
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run(
            [OBJCOPY, "--add-section", f".dep-v0={payload}", str(path)],
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


@unittest.skipUnless(elf_tools_present(), SKIP_ELF)
class DebugSplit(unittest.TestCase):
    """debug-split.sh over pkgroot/usr/lib/postgresql/18/lib/demo_ext.so."""

    REL = "usr/lib/postgresql/18/lib/demo_ext.so"

    def setUp(self) -> None:
        """Build a package root holding one real library with .dep-v0."""
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pkgroot = self.root / "pkgroot"
        self.so = self.pkgroot / self.REL
        self.so.parent.mkdir(parents=True)
        build_library(self.so, SOURCE)

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
            env[f"REAL_{tool}"] = shutil.which(tool) or tool
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
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [READELF, "-S", "--wide", str(path)],
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
            f"::error::{self.REL} after the split: no .dep-v0\n", result.stdout
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
                        result.stdout,
                        rf"^::error::{self.REL} after the split:"
                        r" \d+ dynamic functions, \d+ before the split\n$",
                    )
                else:
                    self.assertEqual(
                        f"::error::{self.REL} after the split: {name}\n", result.stdout
                    )

    def test_no_library_is_refused(self) -> None:
        """A package with nothing to split is a red, never a quiet pass."""
        self.so.unlink()
        result = self.split()
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            f"::error::no shared library under {self.pkgroot} to split\n", result.stdout
        )


@unittest.skipUnless(elf_tools_present(), SKIP_ELF)
class GlibcFloor(unittest.TestCase):
    """glibc-floor.sh over a real library, against floors either side."""

    def setUp(self) -> None:
        """Make an empty package root."""
        self.tmp = TemporaryDirectory()
        self.pkgroot = Path(self.tmp.name) / "pkgroot"
        self.lib = self.pkgroot / "usr" / "lib" / "demo_ext.so"
        self.lib.parent.mkdir(parents=True)

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
            result.stdout,
            r"^::error::demo_ext\.so needs GLIBC_\d+\.\d+, above the 1\.0 floor"
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
        self.assertEqual("::error::no shared library packaged\n", result.stdout)


if __name__ == "__main__":
    unittest.main()
