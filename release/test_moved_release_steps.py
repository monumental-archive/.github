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
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import decoded
import hermetic

HERE = Path(__file__).resolve().parent
CANON = HERE.parent
PGRX = (CANON / "release" / "pgrx").resolve()
WRITER = (CANON / "mise" / "workflow-command.sh").resolve()
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")

# A stub that appends its argv, one argument per line and a blank line
# after, to $STUB_LOG, reads and drops its stdin, and exits 0.
RECORDER = """#!/usr/bin/env bash
{ printf '%s\\n' "$(basename "$0")" "$@"; echo; } >> "${STUB_LOG}"
cat > /dev/null || true
"""


class Script(unittest.TestCase):
    """A scratch directory, stub tools, and the script run in it."""

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
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
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
            (done.returncode, done.stdout),
            (1, "::error::tag says 1.2.4, manifest says 1.2.3\n"),
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
        """Hold one docker run to the canon's two mounts and the name."""
        self.assertIn(f"{PGRX}:/pgrx:ro", argv)
        self.assertIn(f"{WRITER}:/opt/org/workflow-command.sh:ro", argv)
        self.assertIn("ORG_WORKFLOW_COMMAND=/opt/org/workflow-command.sh", argv)

    def test_the_build_mounts_the_canon_and_the_writer(self) -> None:
        """One docker run, build-root.sh in the build image."""
        self.stub("docker")
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [GIT, "init", "-q"], cwd=self.work, check=True, env=hermetic.env()
        )
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
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
        self.assertEqual(argv[:3], ["docker", "run", "--rm"])
        self.mounts(argv)
        self.assertIn("VERSION=1.2.3", argv)
        self.assertEqual(
            argv[-3:], ["postgres:17@sha256:planted", "bash", "/pgrx/build-root.sh"]
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
            argv[-3:], ["postgres:16@sha256:smoke", "bash", "/pgrx/upgrade.sh"]
        )
        self.assertEqual(
            done.stdout, "::notice::pg16/amd64: 1.0.0 -> 1.1.0 upgrade executed\n"
        )

    def test_no_previous_release_skips_clean(self) -> None:
        """No version fetched: no container, nothing printed, exit 0."""
        done = self.upgrade("")
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)
        self.assertEqual(self.calls(), [])


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
        self.assertEqual(
            decoded.load_json((self.work / "plan" / "plan.json").read_text("utf-8")),
            [
                {
                    "class": "wasm-npm",
                    "doc": "sbom-npm-edtf-wasm",
                    "params": {"cargoPackage": "edtf-wasm"},
                }
            ],
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
        self.assertEqual(tools, ["docker", "trivy", "trivy", "stele"])
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
            done.stdout,
            "::error::no arm64 digest to inventory; refusing to decide about a"
            " partial image\n",
        )
        self.assertNotIn("stele", [argv[0] for argv in self.calls()])


if __name__ == "__main__":
    unittest.main()
