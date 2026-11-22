#!/usr/bin/env python3
"""Rows for the release steps #1660 moved out of their workflows.

Routing each step's workflow commands through the canon's writer grew
its `run:` block past the ceiling (#1352), and the one permitted
response moved the block into a canon script the step runs:

- release/pgrx/tag-version.sh: a tag whose version disagrees with the
  crate's manifest (or the workspace's) is refused by name;
- release/pgrx/build-in-container.sh and upgrade-previous.sh: the
  container runs, with the canon's release/pgrx and the writer mounted
  and named;
- release/wasm-inventory-plan.sh: the plan and one notice per entry;
- release/inventory-digests.sh: both per-arch inventories, or a
  refusal of a partial image.

Each row runs the script itself, by its path, with stub tools first on
PATH that record what they were given.

stdlib `unittest`, matching the rest of release/; run through the gate
as `mise run test`.
"""

import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import override

sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import decoded
import hermetic

HERE = Path(__file__).resolve().parent
CANON = HERE.parent
PGRX = (CANON / "release" / "pgrx").resolve()
WRITERS = (CANON / "mise").resolve()
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")

# A stub that appends its argv, one argument per line and a blank line
# after, to $STUB_LOG, reads and drops its stdin, and exits 0.
RECORDER = """#!/usr/bin/env bash
{ printf '%s\\n' "$(basename "$0")" "$@"; echo; } >> "${STUB_LOG}"
cat > /dev/null || true
"""


def strings(*items: str) -> list[str]:
    """List the given strings, typed as strings for the assertions.

    Returns:
        The strings, in order.

    """
    return list(items)


class Script(unittest.TestCase):
    """A scratch directory, stub tools, and the script run in it."""

    @override
    def setUp(self) -> None:
        """Make the scratch directory and its stub bin."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "stub.log"
        self.log.write_text("", "utf-8")

    def stub(self, name: str, body: str = RECORDER) -> None:
        """Put a stub tool first on PATH."""
        path = self.bin / name
        path.write_text(body, "utf-8")
        path.chmod(0o755)

    def run_script(self, script: Path, **env: str) -> subprocess.CompletedProcess[str]:
        """Run a script by its path in the scratch work directory.

        Returns:
            The finished process.

        """
        full = hermetic.env(
            self.root / "home",
            PATH=f"{self.bin}:{os.environ['PATH']}",
            STUB_LOG=str(self.log),
            **env,
        )
        return subprocess.run(
            [BASH, str(script)],
            cwd=self.work,
            env=full,
            stdin=subprocess.DEVNULL,
            timeout=60,
            capture_output=True,
            text=True,
            check=False,
        )

    def calls(self) -> list[list[str]]:
        """Read the stubs' recorded calls.

        Returns:
            One argv per call, the tool's name first.

        """
        text = self.log.read_text("utf-8")
        return [block.split("\n") for block in text.split("\n\n") if block.strip()]


class TagVersion(Script):
    """release/pgrx/tag-version.sh."""

    SCRIPT = PGRX / "tag-version.sh"

    def plant(self, crate: str, workspace: str | None = None) -> None:
        """Write the crate's manifest, and the workspace's when given."""
        (self.work / "ext").mkdir()
        (self.work / "ext" / "Cargo.toml").write_text(crate, "utf-8")
        if workspace is not None:
            (self.work / "Cargo.toml").write_text(workspace, "utf-8")

    def test_the_manifests_version_passes(self) -> None:
        """The tag and the manifest agree: nothing printed, exit 0."""
        self.plant('[package]\nname = "ext"\nversion = "1.2.3"\n')
        done = self.run_script(
            self.SCRIPT, UNTRUSTED_VERSION="1.2.3", UNTRUSTED_CRATE_DIR="ext"
        )
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)

    def test_another_version_is_refused_by_name(self) -> None:
        """Both versions in the one error command."""
        self.plant('[package]\nname = "ext"\nversion = "1.2.3"\n')
        done = self.run_script(
            self.SCRIPT, UNTRUSTED_VERSION="1.2.4", UNTRUSTED_CRATE_DIR="ext"
        )
        self.assertEqual(
            (done.returncode, done.stderr),
            (1, "::error::tag-version.sh: tag says 1.2.4, manifest says 1.2.3\n"),
        )

    def test_a_workspace_version_is_read_from_the_workspace(self) -> None:
        """`version.workspace = true` reads the workspace's version."""
        self.plant(
            '[package]\nname = "ext"\nversion.workspace = true\n',
            '[workspace.package]\nversion = "2.0.0"\n',
        )
        done = self.run_script(
            self.SCRIPT, UNTRUSTED_VERSION="2.0.0", UNTRUSTED_CRATE_DIR="ext"
        )
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)


class Containers(Script):
    """release/pgrx/build-in-container.sh and upgrade-previous.sh."""

    def mounts(self, argv: list[str]) -> None:
        """Hold one docker run to the canon's mounts and the writer's name."""
        self.assertIn(f"{PGRX}:/pgrx:ro", argv)
        self.assertIn(f"{WRITERS}/findings.sh:/opt/org/findings.sh:ro", argv)
        self.assertIn(
            f"{WRITERS}/workflow-command.sh:/opt/org/workflow-command.sh:ro", argv
        )
        self.assertIn("ORG_FINDINGS_WRITER=/opt/org/findings.sh", argv)

    def test_the_build_mounts_the_canon_and_the_writer(self) -> None:
        """One docker run, build-root.sh in the build image."""
        self.stub("docker")
        subprocess.run(
            [GIT, "init", "-q"], cwd=self.work, check=True, env=hermetic.env()
        )
        subprocess.run(
            [GIT, "commit", "-q", "--allow-empty", "-m", "c"],
            cwd=self.work,
            check=True,
            env=hermetic.env(),
        )
        done = self.run_script(
            PGRX / "build-in-container.sh",
            BUILD_IMAGE="postgres:17@sha256:planted",
            UNTRUSTED_VERSION="1.2.3",
            UNTRUSTED_CRATE_DIR="ext",
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        (argv,) = self.calls()
        self.assertEqual(argv[:3], strings("docker", "run", "--rm"))
        self.mounts(argv)
        self.assertIn("VERSION=1.2.3", argv)
        self.assertEqual(
            argv[-3:],
            strings("postgres:17@sha256:planted", "bash", "/pgrx/build-root.sh"),
        )

    def fetcher(self, prev: str) -> str:
        """Write a stand-in for ORG_PYTHON that prints the previous version.

        Returns:
            Its path.

        """
        path = self.bin / "python"
        path.write_text(f"#!/usr/bin/env bash\nprintf '%s' '{prev}'\n", "utf-8")
        path.chmod(0o755)
        return str(path)

    def upgrade(self, prev: str) -> subprocess.CompletedProcess[str]:
        """Run the upgrade with a previous release of `prev`.

        Returns:
            The finished process.

        """
        self.stub("docker")
        return self.run_script(
            PGRX / "upgrade-previous.sh",
            ORG_PYTHON=self.fetcher(prev),
            GITHUB_REPOSITORY="o/r",
            UNTRUSTED_VERSION="1.1.0",
            UNTRUSTED_CRATE_DIR="crates/edtf-pg",
            PG="16",
            ARCH="amd64",
            SMOKE_IMAGE="postgres:16@sha256:smoke",
        )

    def test_the_upgrade_runs_and_says_so(self) -> None:
        """The upgrade container, then one notice naming both versions."""
        done = self.upgrade("1.0.0")
        self.assertEqual(done.returncode, 0, done.stderr)
        (argv,) = self.calls()
        self.mounts(argv)
        self.assertIn("NAME=edtf_pg", argv)
        self.assertIn("PREV=1.0.0", argv)
        self.assertEqual(
            argv[-3:], strings("postgres:16@sha256:smoke", "bash", "/pgrx/upgrade.sh")
        )
        self.assertEqual(
            done.stdout, "::notice::pg16/amd64: 1.0.0 -> 1.1.0 upgrade executed\n"
        )

    def test_no_previous_release_skips_clean(self) -> None:
        """No version fetched: no container, nothing printed, exit 0."""
        done = self.upgrade("")
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)
        none: list[list[str]] = []
        self.assertEqual(self.calls(), none)


class WasmPlan(Script):
    """release/wasm-inventory-plan.sh."""

    def test_the_plan_and_its_notice(self) -> None:
        """One package, one entry, one notice."""
        crate = self.work / "crates" / "edtf-wasm"
        crate.mkdir(parents=True)
        (crate / "Cargo.toml").write_text("[package]\n", "utf-8")
        meta = {
            "packages": [
                {
                    "name": "edtf-wasm",
                    "manifest_path": str((crate / "Cargo.toml").resolve()),
                },
                {"name": "other", "manifest_path": "/elsewhere/Cargo.toml"},
            ]
        }
        (self.root / "meta.json").write_text(json.dumps(meta), "utf-8")
        self.stub("cargo", f"#!/usr/bin/env bash\ncat '{self.root / 'meta.json'}'\n")
        done = self.run_script(
            CANON / "release" / "wasm-inventory-plan.sh",
            UNTRUSTED_CRATE_DIR="crates/edtf-wasm",
            PLAN_DOC_PREFIX="sbom-npm-",
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        plan: list[object] = [
            {
                "class": "wasm-npm",
                "doc": "sbom-npm-edtf-wasm",
                "params": {"cargoPackage": "edtf-wasm"},
            }
        ]
        self.assertEqual(
            decoded.load_json((self.work / "plan" / "plan.json").read_text("utf-8")),
            plan,
        )
        self.assertEqual(
            done.stdout,
            "::notice::plans inventory sbom-npm-edtf-wasm for package edtf-wasm\n",
        )


TRIVY = """#!/usr/bin/env bash
{ printf '%s\\n' trivy "$@"; echo; } >> "${STUB_LOG}"
out="" ref=""
while (($#)); do
  case $1 in --output) out=$2; shift ;; *) ref=$1 ;; esac
  shift
done
printf '{"name": "%s"}' "${ref}" > "${out}"
"""
SHA = "sha256:" + "a" * 64


class Inventory(Script):
    """release/inventory-digests.sh."""

    def run_inventory(
        self, digests: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        """Inventory the planted per-arch digests.

        Returns:
            The finished process.

        """
        for name in ("docker", "stele"):
            self.stub(name)
        self.stub("trivy", TRIVY)
        (self.work / "digests").mkdir()
        for arch, digest in digests.items():
            (self.work / "digests" / arch).write_text(digest, "utf-8")
        output = self.root / "output"
        output.write_text("", "utf-8")
        self.output = output
        return self.run_script(
            CANON / "release" / "inventory-digests.sh",
            GITHUB_REPOSITORY="Org/Repo",
            GITHUB_ACTOR="bot",
            GH_TOKEN="planted",  # ruff: ignore[hardcoded-password-func-arg]
            GITHUB_OUTPUT=str(output),
        )

    def test_both_arches_are_inventoried_and_judged(self) -> None:
        """Each digest's document, then stele over both, then the subjects."""
        done = self.run_inventory({"amd64": SHA, "arm64": SHA})
        self.assertEqual(done.returncode, 0, done.stderr)
        tools = [argv[0] for argv in self.calls()]
        self.assertEqual(tools, strings("docker", "trivy", "trivy", "stele"))
        ref = f"ghcr.io/org/repo@{SHA}"
        self.assertEqual(
            self.output.read_text("utf-8"),
            f"subjects={ref}=dist/amd64.spdx.json,{ref}=dist/arm64.spdx.json\n",
        )

    def test_a_partial_image_is_refused_by_name(self) -> None:
        """A missing arm64 digest: one error command, no stele."""
        done = self.run_inventory({"amd64": SHA})
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            "::error::inventory-digests.sh: no arm64 digest to inventory; refusing"
            " to decide about a"
            " partial image\n",
        )
        called: list[str] = [argv[0] for argv in self.calls()]
        self.assertNotIn("stele", called)


# The runner's own directories, without the belt: a row that needs stele
# missing puts only these after its stub bin.
SYSTEM_PATH = "/usr/bin:/bin"
NO_STELE = "::error::{}: stele is not on the runner — it is a belt tool\n"


class Helper(Script):
    """A #1934 helper: its stele refusal, run with no stele on PATH."""

    def run_bare(self, script: Path, **env: str) -> subprocess.CompletedProcess[str]:
        """Run a script with no belt tool on PATH.

        Returns:
            The finished process.

        """
        full = hermetic.env(
            self.root / "home",
            PATH=f"{self.bin}:{SYSTEM_PATH}",
            STUB_LOG=str(self.log),
            **env,
        )
        return subprocess.run(
            [BASH, str(script)],
            cwd=self.work,
            env=full,
            stdin=subprocess.DEVNULL,
            timeout=60,
            capture_output=True,
            text=True,
            check=False,
        )


FACTS = """#!/usr/bin/env bash
{ printf '%s\\n' "$(basename "$0")" "$@"; echo; } >> "${STUB_LOG}"
printf 'facts=planted\\nepoch=1\\nlicence: MIT\\n'
"""


class DeriveFacts(Helper):
    """release/derive-facts.sh: publish.yml's facts step (#1934)."""

    SCRIPT = CANON / "release" / "derive-facts.sh"

    def env(self) -> dict[str, str]:
        """Name the step's inputs and its output file.

        Returns:
            The variables.

        """
        self.output = self.root / "output"
        self.output.write_text("", "utf-8")
        return {
            "VERSION": "1.2.3",
            "IMAGE_TITLE": "Repo",
            "IMAGE_DESCRIPTION": "an image",
            "GITHUB_REPOSITORY": "org/repo",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_OUTPUT": str(self.output),
        }

    def test_the_facts_are_derived_and_split(self) -> None:
        """Stele's facts and epoch are outputs; its other lines print."""
        self.stub("stele", FACTS)
        done = self.run_script(self.SCRIPT, **self.env())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.output.read_text("utf-8"), "facts=planted\nepoch=1\n")
        self.assertEqual(done.stdout, "licence: MIT\n")
        expected: list[list[str]] = [
            strings(
                "stele",
                "derive",
                "facts",
                "--archetype",
                "versioned",
                "--version",
                "1.2.3",
                "--repo",
                "org/repo",
                "--server-url",
                "https://github.com",
                "--git-dir",
                ".",
                "--title",
                "Repo",
                "--description",
                "an image",
            )
        ]
        self.assertEqual(self.calls(), expected)

    def test_no_stele_is_refused_by_name(self) -> None:
        """No stele on the runner: one finding, nothing written."""
        done = self.run_bare(self.SCRIPT, **self.env())
        self.assertEqual((done.returncode, done.stdout), (1, ""), done.stderr)
        self.assertEqual(done.stderr, NO_STELE.format("derive-facts.sh"))
        self.assertEqual(self.output.read_text("utf-8"), "")


class UnpackGoLegs(Helper):
    """release/unpack-go-legs.sh: publish.yml's go-binary unpack (#1934)."""

    SCRIPT = CANON / "release" / "unpack-go-legs.sh"

    def unpack(self) -> subprocess.CompletedProcess[str]:
        """Run the unpack with a fresh GITHUB_ENV.

        Returns:
            The finished process.

        """
        self.github_env = self.root / "github-env"
        self.github_env.write_text("", "utf-8")
        (self.work / "shipped-archives").mkdir(exist_ok=True)
        return self.run_script(self.SCRIPT, GITHUB_ENV=str(self.github_env))

    def archive(self, leg: str) -> None:
        """Pack one leg's archive holding a `tool` command."""
        src = self.root / "src" / leg
        src.mkdir(parents=True)
        (src / "tool").write_text(leg, "utf-8")
        (self.work / "shipped-archives").mkdir(exist_ok=True)
        out = self.work / "shipped-archives" / f"{leg}.tar.gz"
        with tarfile.open(out, "w:gz") as packed:
            packed.add(src / "tool", arcname="tool")

    def test_each_leg_gets_its_own_directory(self) -> None:
        """Two legs shipping one basename both survive."""
        for leg in ("tool_linux_amd64", "tool_linux_arm64"):
            self.archive(leg)
        done = self.unpack()
        self.assertEqual(done.returncode, 0, done.stderr)
        for leg in ("tool_linux_amd64", "tool_linux_arm64"):
            tool = self.work / "shipped" / leg / "tool"
            self.assertEqual(tool.read_text("utf-8"), leg)
        self.assertEqual(
            self.github_env.read_text("utf-8"),
            f"BINARIES_DIR={self.work.resolve()}/shipped\n",
        )

    def test_no_archive_is_refused_by_name(self) -> None:
        """An artifact with no archive: one finding, no BINARIES_DIR."""
        done = self.unpack()
        self.assertEqual((done.returncode, done.stdout), (1, ""), done.stderr)
        self.assertEqual(
            done.stderr,
            "::error::unpack-go-legs.sh: the go-binary build artifact holds no"
            " archives\n",
        )
        self.assertEqual(self.github_env.read_text("utf-8"), "")


class DeriveVex(Helper):
    """release/derive-vex.sh: publish.yml's VEX gate (#1934)."""

    SCRIPT = CANON / "release" / "derive-vex.sh"

    @staticmethod
    def env(basename: str) -> dict[str, str]:
        """Name the step's inputs.

        Returns:
            The variables.

        """
        return {
            "VERSION": "1.2.3",
            "SBOM_BASENAME": basename,
            "SBOM_CREATED": "2026-11-22T00:00:00Z",
            "GITHUB_REPOSITORY": "org/.repo",
        }

    def test_the_policies_and_the_release_view_are_named(self) -> None:
        """No basename: the repository's name, its leading dot dropped."""
        self.stub("stele")
        done = self.run_script(self.SCRIPT, **self.env(""))
        self.assertEqual(done.returncode, 0, done.stderr)
        canon = str(CANON.resolve())
        expected: list[list[str]] = [
            strings(
                "stele",
                "derive",
                "vex",
                "--policy",
                f"{canon}/slsa/assert-policy.json",
                "--verify-policy",
                f"{canon}/slsa/verify-policy.json",
                "--inventories",
                "dist",
                "--release-view",
                "dist/repo-1.2.3.spdx.json",
                "--vex",
                f"{canon}/security/vex",
                "--repo",
                "org/.repo",
                "--version",
                "1.2.3",
                "--released",
                "2026-11-22T00:00:00Z",
                "--out",
                "dist/repo-1.2.3.vex.openvex.json",
            )
        ]
        self.assertEqual(self.calls(), expected)

    def test_a_basename_names_the_documents(self) -> None:
        """A declared basename is the documents' name."""
        self.stub("stele")
        done = self.run_script(self.SCRIPT, **self.env("edtf"))
        self.assertEqual(done.returncode, 0, done.stderr)
        argv = self.calls()[0]
        self.assertIn("dist/edtf-1.2.3.spdx.json", argv)
        self.assertIn("dist/edtf-1.2.3.vex.openvex.json", argv)

    def test_no_stele_is_refused_by_name(self) -> None:
        """No stele on the runner: one finding."""
        done = self.run_bare(self.SCRIPT, **self.env(""))
        self.assertEqual((done.returncode, done.stdout), (1, ""), done.stderr)
        self.assertEqual(done.stderr, NO_STELE.format("derive-vex.sh"))


if __name__ == "__main__":
    unittest.main()
