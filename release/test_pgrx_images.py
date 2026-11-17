#!/usr/bin/env python3
"""The pgrx images' staging and consumer proof, out of the workflow (#1381).

build-pgrx-images.yml staged the per-architecture trees and proved the
pushed image in 82 lines of inline shell with no test. The staging is
release/pgrx-images.py; the proof is release/pgrx-image-proof.sh running
the tracked docker/pgrx-consumer*.Dockerfile and docker/pgrx-*-proof.sh.

The staging rows plant real gzip tarballs and run the helper as the
workflow does, asserting each refusal's exact `::error::` line (the
wording the inline shell had). The proof rows put a recording `docker`
first on PATH and run the real script: what it builds, with which
arguments, and the Postgres-major branch (below 18, no path proof).

stdlib `unittest`, matching the other release/test_*.py.
"""

import io
import os
import shutil
import stat
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tarfile
import tempfile
import tomllib
import unittest
from pathlib import Path

RELEASE = Path(__file__).resolve().parent
CANON = RELEASE.parent
STAGE = RELEASE / "pgrx-images.py"
PROOF = RELEASE / "pgrx-image-proof.sh"
TABLE = CANON / "docker" / "pgrx-base-images.toml"
BASH = shutil.which("bash") or "bash"

VERSION = "1.2.3"
CRATE = "crates/edtf-postgres"


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
    args: list[str], cwd: Path, env: dict | None = None
) -> subprocess.CompletedProcess:
    """Run a helper the way the workflow step does.

    Returns:
        The finished process, output captured as text.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, check=False, env=env
    )


class Stage(unittest.TestCase):
    """`pgrx-images.py stage`: every branch, messages asserted."""

    def setUp(self) -> None:
        """Make a workspace with a tarballs/ directory, removed after."""
        self.root = Path(tempfile.mkdtemp(prefix="pgrx-stage-"))
        self.addCleanup(shutil.rmtree, self.root)
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

    def stage(self, pg: str) -> subprocess.CompletedProcess:
        """Run the stage verb for one major.

        Returns:
            The finished process.

        """
        return run(
            [
                sys.executable,
                str(STAGE),
                "stage",
                "--pg",
                pg,
                "--version",
                VERSION,
                "--crate-dir",
                CRATE,
            ],
            self.root,
        )

    def test_both_architectures_staged(self) -> None:
        """The one layout, for amd64 and arm64, from pkgroot only."""
        for arch in ("amd64", "arm64"):
            self.plant("17", arch, link=True)
        result = self.stage("17")
        self.assertEqual(0, result.returncode, result.stderr)
        for arch in ("amd64", "arm64"):
            image = self.root / "staged" / arch / "image"
            with self.subTest(arch=arch):
                found = sorted(
                    str(p.relative_to(image))
                    for p in image.rglob("*")
                    if not p.is_dir()
                )
                self.assertEqual(
                    [
                        "lib/edtf.so",
                        "lib/edtf_postgres.so",
                        "share/extension/edtf_postgres--1.2.3.sql",
                        "share/extension/edtf_postgres.control",
                    ],
                    found,
                )
                so = image / "lib" / "edtf_postgres.so"
                self.assertEqual(0o755, stat.S_IMODE(so.stat().st_mode))
                self.assertTrue((image / "lib" / "edtf.so").is_symlink())

    def test_a_missing_tarball(self) -> None:
        """No amd64 tarball: the build did not produce the cell."""
        self.plant("18", "arm64")
        result = self.stage("18")
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            "::error::missing tarballs/edtf_postgres-1.2.3-pg18-linux-amd64.tar.gz"
            " — the extension build did not produce this cell\n",
            result.stderr,
        )

    def test_a_missing_second_architecture(self) -> None:
        """amd64 stages, arm64 is missing and named."""
        self.plant("18", "amd64")
        result = self.stage("18")
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            "::error::missing tarballs/edtf_postgres-1.2.3-pg18-linux-arm64.tar.gz"
            " — the extension build did not produce this cell\n",
            result.stderr,
        )

    def test_a_tarball_without_lib(self) -> None:
        """Only the second copy's lib: refused, never read (#1274)."""
        shown = self.plant("16", "amd64", lib=False)
        self.plant("16", "arm64")
        result = self.stage("16")
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            f"::error::{shown} has no pkgroot/usr/lib/postgresql/16/lib\n",
            result.stderr,
        )
        image_lib = self.root / "staged" / "amd64" / "image" / "lib"
        self.assertFalse(image_lib.exists())

    def test_a_tarball_without_extension(self) -> None:
        """No share/postgresql/<major>/extension: refused by path."""
        shown = self.plant("14", "amd64", share=False)
        result = self.stage("14")
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            f"::error::{shown} has no pkgroot/usr/share/postgresql/14/extension\n",
            result.stderr,
        )


class BaseImage(unittest.TestCase):
    """`pgrx-images.py base-image`: the [build] row, or a refusal."""

    def test_every_major_names_its_build_image(self) -> None:
        """Each [build] row is what the verb prints."""
        rows = tomllib.loads(TABLE.read_text("utf-8"))["build"]
        for pg, ref in rows.items():
            with self.subTest(pg=pg):
                result = run(
                    [
                        sys.executable,
                        str(STAGE),
                        "base-image",
                        "--pg",
                        pg,
                        "--table",
                        str(TABLE),
                    ],
                    CANON,
                )
                self.assertEqual((0, f"{ref}\n"), (result.returncode, result.stdout))
                self.assertIn("@sha256:", ref)

    def test_a_major_with_no_row(self) -> None:
        """A major the table does not pin is refused by name."""
        result = run(
            [
                sys.executable,
                str(STAGE),
                "base-image",
                "--pg",
                "13",
                "--table",
                str(TABLE),
            ],
            CANON,
        )
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            f"::error::pg13 has no build image in {TABLE}\n", result.stderr
        )


def build_image(pg: str) -> str:
    """Read the [build] row the proof must use.

    Returns:
        The digest-pinned reference.

    """
    return tomllib.loads(TABLE.read_text("utf-8"))["build"][pg]


IMAGE = "ghcr.io/example/edtf-postgres"
PINNED = IMAGE + "@sha256:" + "a" * 64


def render(recipe: str, image: str, base: str, pg: str) -> subprocess.CompletedProcess:
    """Run the consumer-dockerfile verb.

    Returns:
        The finished process.

    """
    return run(
        [
            sys.executable,
            str(STAGE),
            "consumer-dockerfile",
            "--recipe",
            recipe,
            "--image",
            image,
            "--base",
            base,
            "--pg",
            pg,
        ],
        CANON,
    )


class ConsumerDockerfile(unittest.TestCase):
    """`pgrx-images.py consumer-dockerfile`: the recipes, pinned or refused."""

    def test_the_copy_recipe(self) -> None:
        """The text the workflow's first heredoc wrote, byte for byte."""
        result = render("copy", PINNED, build_image("17"), "17")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            f"FROM {PINNED} AS ext\n"
            f"FROM {build_image('17')}\n"
            "COPY --from=ext /lib/ /usr/lib/postgresql/17/lib/\n"
            "COPY --from=ext /share/extension/ /usr/share/postgresql/17/extension/\n",
            result.stdout,
        )

    def test_the_path_recipe(self) -> None:
        """The text the workflow's second heredoc wrote, byte for byte."""
        result = render("path", PINNED, build_image("18"), "18")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            f"FROM {PINNED} AS ext\n"
            f"FROM {build_image('18')}\n"
            "COPY --from=ext / /extensions/ext/\n",
            result.stdout,
        )

    def test_every_rendered_from_is_pinned(self) -> None:
        """Every recipe on every [build] row: each FROM carries a digest."""
        rows = tomllib.loads(TABLE.read_text("utf-8"))["build"]
        for pg, base in rows.items():
            for recipe in ("copy", "path"):
                with self.subTest(pg=pg, recipe=recipe):
                    result = render(recipe, PINNED, base, pg)
                    self.assertEqual(0, result.returncode, result.stderr)
                    from_lines = [
                        ln for ln in result.stdout.splitlines() if ln.startswith("FROM")
                    ]
                    self.assertEqual(2, len(from_lines))
                    for line in from_lines:
                        self.assertIn("@sha256:", line.split()[1])

    def test_an_unpinned_extension_image(self) -> None:
        """A tag, not a digest: refused, nothing rendered."""
        result = render("copy", IMAGE + ":1.2.3-pg17-artifact", build_image("17"), "17")
        self.assertEqual((1, ""), (result.returncode, result.stdout))
        self.assertEqual(
            f"::error::the extension image {IMAGE}:1.2.3-pg17-artifact is not"
            " pinned by digest (@sha256:); the consumer proof builds only from"
            " pinned references\n",
            result.stderr,
        )

    def test_an_unpinned_base_image(self) -> None:
        """A stock postgres by tag: refused, nothing rendered."""
        result = render("path", PINNED, "postgres:18-bookworm", "18")
        self.assertEqual((1, ""), (result.returncode, result.stdout))
        self.assertEqual(
            "::error::the base image postgres:18-bookworm is not pinned by digest"
            " (@sha256:); the consumer proof builds only from pinned references\n",
            result.stderr,
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


class Proof(unittest.TestCase):
    """`pgrx-image-proof.sh`: the recipes it runs, per Postgres major."""

    def setUp(self) -> None:
        """Put a recording docker first on PATH, beside a digest record."""
        self.root = Path(tempfile.mkdtemp(prefix="pgrx-proof-"))
        self.addCleanup(shutil.rmtree, self.root)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(DOCKER, "utf-8")
        docker.chmod(0o755)
        self.log = self.root / "docker.log"
        self.record = self.root / "pg.json"
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "DOCKER_LOG": str(self.log),
        }

    def prove(
        self, pg: str, digest: str = "sha256:" + "a" * 64
    ) -> tuple[subprocess.CompletedProcess, list[str]]:
        """Run the proof for one major against the canon tree.

        Returns:
            The finished process and the recorder's lines: each docker
            call, and under a build the Dockerfile it built, `  | `-led.

        """
        self.record.write_text(f'{{"pg": "{pg}", "digest": "{digest}"}}\n')
        result = run(
            [BASH, str(PROOF), IMAGE, str(self.record), pg, str(CANON)],
            self.root,
            self.env,
        )
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return result, lines

    def test_below_18_runs_the_copy_recipe_only(self) -> None:
        """pg17: one build, one run, and the branch says why it stops."""
        result, lines = self.prove("17")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "pg17: below 18, no extension_control_path; path proof not applicable\n",
            result.stdout,
        )
        calls = [ln for ln in lines if not ln.startswith("  | ")]
        built = [ln[4:] for ln in lines if ln.startswith("  | ")]
        self.assertEqual(2, len(calls), lines)
        self.assertRegex(
            calls[0],
            r"^buildx build --file \S+/copy\.Dockerfile --load"
            r" --tag consumer-proof:pg17 \S+$",
        )
        self.assertEqual(
            [
                f"FROM {PINNED} AS ext",
                f"FROM {build_image('17')}",
                "COPY --from=ext /lib/ /usr/lib/postgresql/17/lib/",
                "COPY --from=ext /share/extension/ /usr/share/postgresql/17/extension/",
            ],
            built,
        )
        docker_dir = (CANON / "docker").resolve()
        self.assertEqual(
            f"run --rm -v {docker_dir}:/proof:ro consumer-proof:pg17"
            " bash /proof/pgrx-copy-proof.sh",
            calls[1],
        )

    def test_18_adds_the_path_recipe(self) -> None:
        """pg18: the copy recipe, then the image mounted whole, as postgres."""
        result, lines = self.prove("18")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stdout)
        calls = [ln for ln in lines if not ln.startswith("  | ")]
        built = [ln[4:] for ln in lines if ln.startswith("  | ")]
        self.assertEqual(4, len(calls), lines)
        self.assertRegex(
            calls[2],
            r"^buildx build --file \S+/path\.Dockerfile --load"
            r" --tag consumer-path-proof:pg18 \S+$",
        )
        self.assertEqual(
            [
                f"FROM {PINNED} AS ext",
                f"FROM {build_image('18')}",
                "COPY --from=ext / /extensions/ext/",
            ],
            built[4:],
        )
        docker_dir = (CANON / "docker").resolve()
        self.assertEqual(
            f"run --rm --user postgres -v {docker_dir}:/proof:ro"
            " consumer-path-proof:pg18 bash /proof/pgrx-path-proof.sh",
            calls[3],
        )

    def test_a_major_with_no_build_image_builds_nothing(self) -> None:
        """No [build] row: refused before any docker call."""
        result, lines = self.prove("13")
        self.assertEqual(1, result.returncode)
        self.assertIn("::error::pg13 has no build image in", result.stderr)
        self.assertEqual([], lines)

    def test_a_record_without_a_digest_builds_nothing(self) -> None:
        """An empty digest renders no Dockerfile, so docker never runs."""
        result, lines = self.prove("18", digest="")
        self.assertEqual(1, result.returncode)
        self.assertIn(
            f"::error::the extension image {IMAGE}@ is not pinned by digest",
            result.stderr,
        )
        self.assertEqual([], lines)

    def test_the_in_image_scripts_are_tracked(self) -> None:
        """What runs inside the images is files a person can read and run."""
        for name in ("pgrx-copy-proof.sh", "pgrx-path-proof.sh"):
            with self.subTest(name=name):
                self.assertTrue(os.access(CANON / "docker" / name, os.X_OK))


if __name__ == "__main__":
    unittest.main()
