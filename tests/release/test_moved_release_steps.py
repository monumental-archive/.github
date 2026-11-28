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
"""

import json
import os
import subprocess
import tarfile
from pathlib import Path

from belt import decoded
from belt.typed_pytest import fixture
from tests import support

HERE = support.RELEASE
CANON = HERE.parent
PGRX = (CANON / "release" / "pgrx").resolve()
WRITERS = (CANON / "mise").resolve()
BASH = support.tool("bash")
GIT = support.tool("git")

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


class Script:
    """A scratch directory, stub tools, and the script run in it."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Make the scratch directory and its stub bin."""
        self.make_env = env
        self.root = Path(str(tmp_path))
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
        full = self.make_env(
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


@fixture
def script_state(tmp_path: Path, env: support.Env) -> Script:
    """Give each row its own Script.

    Returns:
        The row's Script.

    """
    return Script(tmp_path, env=env)


SCRIPT = PGRX / "tag-version.sh"


class TagVersion(Script):
    """release/pgrx/tag-version.sh."""

    def plant(self, crate: str, workspace: str | None = None) -> None:
        """Write the crate's manifest, and the workspace's when given."""
        (self.work / "ext").mkdir()
        (self.work / "ext" / "Cargo.toml").write_text(crate, "utf-8")
        if workspace is not None:
            (self.work / "Cargo.toml").write_text(workspace, "utf-8")


@fixture
def tag_version(tmp_path: Path, env: support.Env) -> TagVersion:
    """Give each row its own TagVersion.

    Returns:
        The row's TagVersion.

    """
    return TagVersion(tmp_path, env=env)


def test_the_manifests_version_passes(tag_version: TagVersion) -> None:
    """The tag and the manifest agree: nothing printed, exit 0."""
    tag_version.plant('[package]\nname = "ext"\nversion = "1.2.3"\n')
    done = tag_version.run_script(
        SCRIPT, UNTRUSTED_VERSION="1.2.3", UNTRUSTED_CRATE_DIR="ext"
    )
    assert (done.returncode, done.stdout) == (0, ""), done.stderr


def test_another_version_is_refused_by_name(tag_version: TagVersion) -> None:
    """Both versions in the one error command."""
    tag_version.plant('[package]\nname = "ext"\nversion = "1.2.3"\n')
    done = tag_version.run_script(
        SCRIPT, UNTRUSTED_VERSION="1.2.4", UNTRUSTED_CRATE_DIR="ext"
    )
    assert (done.returncode, done.stderr) == (
        1,
        "::error::tag-version.sh: tag says 1.2.4, manifest says 1.2.3\n",
    )


def test_a_workspace_version_is_read_from_the_workspace(
    tag_version: TagVersion,
) -> None:
    """`version.workspace = true` reads the workspace's version."""
    tag_version.plant(
        '[package]\nname = "ext"\nversion.workspace = true\n',
        '[workspace.package]\nversion = "2.0.0"\n',
    )
    done = tag_version.run_script(
        SCRIPT, UNTRUSTED_VERSION="2.0.0", UNTRUSTED_CRATE_DIR="ext"
    )
    assert (done.returncode, done.stdout) == (0, ""), done.stderr


class Containers(Script):
    """release/pgrx/build-in-container.sh and upgrade-previous.sh."""

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


@fixture
def containers(tmp_path: Path, env: support.Env) -> Containers:
    """Give each row its own Containers.

    Returns:
        The row's Containers.

    """
    return Containers(tmp_path, env=env)


def mounts(argv: list[str]) -> None:
    """Hold one docker run to the canon's mounts and the writer's name."""
    assert f"{PGRX}:/pgrx:ro" in argv
    assert f"{WRITERS}/findings.sh:/opt/org/findings.sh:ro" in argv
    assert f"{WRITERS}/workflow-command.sh:/opt/org/workflow-command.sh:ro" in argv
    assert f"{WRITERS}/tracked.sh:/opt/org/tracked.sh:ro" in argv
    assert "ORG_FINDINGS_WRITER=/opt/org/findings.sh" in argv


def test_the_build_mounts_the_canon_and_the_writer(
    containers: Containers, env: support.Env
) -> None:
    """One docker run, build-root.sh in the build image."""
    containers.stub("docker")
    subprocess.run([GIT, "init", "-q"], cwd=containers.work, check=True, env=env())
    subprocess.run(
        [GIT, "commit", "-q", "--allow-empty", "-m", "c"],
        cwd=containers.work,
        check=True,
        env=env(),
    )
    done = containers.run_script(
        PGRX / "build-in-container.sh",
        BUILD_IMAGE="postgres:17@sha256:planted",
        UNTRUSTED_VERSION="1.2.3",
        UNTRUSTED_CRATE_DIR="ext",
    )
    assert done.returncode == 0, done.stderr
    (argv,) = containers.calls()
    assert argv[:3] == strings("docker", "run", "--rm")
    mounts(argv)
    assert "VERSION=1.2.3" in argv
    assert argv[-3:] == strings(
        "postgres:17@sha256:planted", "bash", "/pgrx/build-root.sh"
    )


def test_the_upgrade_runs_and_says_so(containers: Containers) -> None:
    """The upgrade container, then one notice naming both versions."""
    done = containers.upgrade("1.0.0")
    assert done.returncode == 0, done.stderr
    (argv,) = containers.calls()
    mounts(argv)
    assert "NAME=edtf_pg" in argv
    assert "PREV=1.0.0" in argv
    assert argv[-3:] == strings("postgres:16@sha256:smoke", "bash", "/pgrx/upgrade.sh")
    assert done.stdout == "::notice::pg16/amd64: 1.0.0 -> 1.1.0 upgrade executed\n"


def test_no_previous_release_skips_clean(containers: Containers) -> None:
    """No version fetched: no container, nothing printed, exit 0."""
    done = containers.upgrade("")
    assert (done.returncode, done.stdout) == (0, ""), done.stderr
    none: list[list[str]] = []
    assert containers.calls() == none


def test_the_plan_and_its_notice(script_state: Script) -> None:
    """One package, one entry, one notice."""
    crate = script_state.work / "crates" / "edtf-wasm"
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
    (script_state.root / "meta.json").write_text(json.dumps(meta), "utf-8")
    script_state.stub(
        "cargo", f"#!/usr/bin/env bash\ncat '{script_state.root / 'meta.json'}'\n"
    )
    done = script_state.run_script(
        CANON / "release" / "wasm-inventory-plan.sh",
        UNTRUSTED_CRATE_DIR="crates/edtf-wasm",
        PLAN_DOC_PREFIX="sbom-npm-",
    )
    assert done.returncode == 0, done.stderr
    plan: list[object] = [
        {
            "class": "wasm-npm",
            "doc": "sbom-npm-edtf-wasm",
            "params": {"cargoPackage": "edtf-wasm"},
        }
    ]
    assert (
        decoded.load_json((script_state.work / "plan" / "plan.json").read_text("utf-8"))
        == plan
    )
    assert (
        done.stdout
        == "::notice::plans inventory sbom-npm-edtf-wasm for package edtf-wasm\n"
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
            GH_TOKEN=support.FAKE_CREDENTIAL,
            GITHUB_OUTPUT=str(output),
        )


@fixture
def inventory(tmp_path: Path, env: support.Env) -> Inventory:
    """Give each row its own Inventory.

    Returns:
        The row's Inventory.

    """
    return Inventory(tmp_path, env=env)


def test_both_arches_are_inventoried_and_judged(inventory: Inventory) -> None:
    """Each digest's document, then stele over both, then the subjects."""
    done = inventory.run_inventory({"amd64": SHA, "arm64": SHA})
    assert done.returncode == 0, done.stderr
    tools = [argv[0] for argv in inventory.calls()]
    assert tools == strings("docker", "trivy", "trivy", "stele")
    ref = f"ghcr.io/org/repo@{SHA}"
    assert (
        inventory.output.read_text("utf-8")
        == f"subjects={ref}=dist/amd64.spdx.json,{ref}=dist/arm64.spdx.json\n"
    )


def test_a_partial_image_is_refused_by_name(inventory: Inventory) -> None:
    """A missing arm64 digest: one error command, no stele."""
    done = inventory.run_inventory({"amd64": SHA})
    assert done.returncode == 1
    assert done.stderr == (
        "::error::inventory-digests.sh: no arm64 digest to inventory; refusing"
        " to decide about a"
        " partial image\n"
    )
    called: list[str] = [argv[0] for argv in inventory.calls()]
    assert "stele" not in called


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
        full = self.make_env(
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


@fixture
def helper(tmp_path: Path, env: support.Env) -> Helper:
    """Give each row its own Helper.

    Returns:
        The row's Helper.

    """
    return Helper(tmp_path, env=env)


FACTS = """#!/usr/bin/env bash
{ printf '%s\\n' "$(basename "$0")" "$@"; echo; } >> "${STUB_LOG}"
printf 'facts=planted\\nepoch=1\\nlicence: MIT\\n'
"""

DERIVE_FACTS_SCRIPT = CANON / "release" / "derive-facts.sh"


class DeriveFacts(Helper):
    """release/derive-facts.sh: publish.yml's facts step (#1934)."""

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


@fixture
def derive_facts(tmp_path: Path, env: support.Env) -> DeriveFacts:
    """Give each row its own DeriveFacts.

    Returns:
        The row's DeriveFacts.

    """
    return DeriveFacts(tmp_path, env=env)


def test_the_facts_are_derived_and_split(derive_facts: DeriveFacts) -> None:
    """Stele's facts and epoch are outputs; its other lines print."""
    derive_facts.stub("stele", FACTS)
    done = derive_facts.run_script(DERIVE_FACTS_SCRIPT, **derive_facts.env())
    assert done.returncode == 0, done.stderr
    assert derive_facts.output.read_text("utf-8") == "facts=planted\nepoch=1\n"
    assert done.stdout == "licence: MIT\n"
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
    assert derive_facts.calls() == expected


def test_no_stele_is_refused_by_name(derive_facts: DeriveFacts) -> None:
    """No stele on the runner: one finding, nothing written."""
    done = derive_facts.run_bare(DERIVE_FACTS_SCRIPT, **derive_facts.env())
    assert (done.returncode, done.stdout) == (1, ""), done.stderr
    assert done.stderr == NO_STELE.format("derive-facts.sh")
    assert not derive_facts.output.read_text("utf-8")


UNPACK_GO_LEGS_SCRIPT = CANON / "release" / "unpack-go-legs.sh"


class UnpackGoLegs(Helper):
    """release/unpack-go-legs.sh: publish.yml's go-binary unpack (#1934)."""

    def unpack(self) -> subprocess.CompletedProcess[str]:
        """Run the unpack with a fresh GITHUB_ENV.

        Returns:
            The finished process.

        """
        self.github_env = self.root / "github-env"
        self.github_env.write_text("", "utf-8")
        (self.work / "shipped-archives").mkdir(exist_ok=True)
        return self.run_script(UNPACK_GO_LEGS_SCRIPT, GITHUB_ENV=str(self.github_env))

    def archive(self, leg: str) -> None:
        """Pack one leg's archive holding a `tool` command."""
        src = self.root / "src" / leg
        src.mkdir(parents=True)
        (src / "tool").write_text(leg, "utf-8")
        (self.work / "shipped-archives").mkdir(exist_ok=True)
        out = self.work / "shipped-archives" / f"{leg}.tar.gz"
        with tarfile.open(out, "w:gz") as packed:
            packed.add(src / "tool", arcname="tool")


@fixture
def unpack_go_legs(tmp_path: Path, env: support.Env) -> UnpackGoLegs:
    """Give each row its own UnpackGoLegs.

    Returns:
        The row's UnpackGoLegs.

    """
    return UnpackGoLegs(tmp_path, env=env)


def test_each_leg_gets_its_own_directory(unpack_go_legs: UnpackGoLegs) -> None:
    """Two legs shipping one basename both survive."""
    for leg in ("tool_linux_amd64", "tool_linux_arm64"):
        unpack_go_legs.archive(leg)
    done = unpack_go_legs.unpack()
    assert done.returncode == 0, done.stderr
    for leg in ("tool_linux_amd64", "tool_linux_arm64"):
        tool = unpack_go_legs.work / "shipped" / leg / "tool"
        assert tool.read_text("utf-8") == leg
    assert (
        unpack_go_legs.github_env.read_text("utf-8")
        == f"BINARIES_DIR={unpack_go_legs.work.resolve()}/shipped\n"
    )


def test_no_archive_is_refused_by_name(unpack_go_legs: UnpackGoLegs) -> None:
    """An artifact with no archive: one finding, no BINARIES_DIR."""
    done = unpack_go_legs.unpack()
    assert (done.returncode, done.stdout) == (1, ""), done.stderr
    assert done.stderr == (
        "::error::unpack-go-legs.sh: the go-binary build artifact holds no archives\n"
    )
    assert not unpack_go_legs.github_env.read_text("utf-8")


DERIVE_VEX_SCRIPT = CANON / "release" / "derive-vex.sh"


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


def test_the_policies_and_the_release_view_are_named(helper: Helper) -> None:
    """No basename: the repository's name, its leading dot dropped."""
    helper.stub("stele")
    done = helper.run_script(DERIVE_VEX_SCRIPT, **env(""))
    assert done.returncode == 0, done.stderr
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
    assert helper.calls() == expected


def test_a_basename_names_the_documents(helper: Helper) -> None:
    """A declared basename is the documents' name."""
    helper.stub("stele")
    done = helper.run_script(DERIVE_VEX_SCRIPT, **env("edtf"))
    assert done.returncode == 0, done.stderr
    argv = helper.calls()[0]
    assert "dist/edtf-1.2.3.spdx.json" in argv
    assert "dist/edtf-1.2.3.vex.openvex.json" in argv


def test_derive_vex_no_stele_is_refused_by_name(helper: Helper) -> None:
    """No stele on the runner: one finding."""
    done = helper.run_bare(DERIVE_VEX_SCRIPT, **env(""))
    assert (done.returncode, done.stdout) == (1, ""), done.stderr
    assert done.stderr == NO_STELE.format("derive-vex.sh")
