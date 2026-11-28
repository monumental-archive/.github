"""Run the shipped crate build's Package, hash and plan steps on stubs.

#1273: `exclude` meant two things. edtf passes `exclude: edtf-postgres`
because the crate job cannot compile the extension, and the class also
read that one list as "never publish it" - so edtf-postgres sat at
1.2.3 on crates.io, pointing readers at a repository the crate had
left. `exclude` now means only "this job cannot compile it": the
member is left out of `cargo test` and the verified `cargo package`,
then packaged by a second `cargo package --workspace --no-verify` run,
which must rewrite every verified crate byte for byte.

#1310: `cargo package` does not read the `publish` key, so a
`publish = false` member's crate was a release subject - hashed,
planned, and fetched back from static.crates.io by the verifier, which
would fail on a crate the registry never received. The Package step
now keeps exactly the crates release/crates-io-members.sh names (the
helper the publisher reads too) and fails by name on one it names that
cargo did not write.

WHAT RUNS. The Package step's helper, release/package_crates.py
(#1384), its `main` called in this process (#1698), then the `run:`
bodies of `Compute the subject manifest` and `Emit the inventory
plan`, cut out of `.github/workflows/build-rust-crate.yml` as shipped,
in order, the two bodies under `bash -c`, with a stub `cargo` first on
PATH. One row starts the helper as the job does instead, through a
`.org-canon` link to this tree exactly as the canon action lays it
out: the script contract (it starts, and exits with cargo's own
status and message). crates-io-members.sh, which it calls, runs from
this tree;
`sha256sum`, `sed` and `jq` are the real ones; GITHUB_OUTPUT is a real
file the hash step writes and the test reads back. One row holds the
shipped Package step to the helper call, so what runs here is what the
job runs.

THE STUB IS CARGO'S MEASURED SHAPE. Measured 2026-11-16 on a fixture
workspace with real cargo 1.97.1 (three members: one plain, one whose
lib.rs is `compile_error!`, one with `publish = false`):

  cargo package --workspace --locked --exclude fx-bad
      writes fx-good and fx-priv (`publish = false` is packaged too)
  cargo package --workspace --locked --no-verify
      writes all three, fx-bad included, and the two crates of the
      first run come out byte-identical (`shasum -c` OK)

and the issue's own measurement on edtf 32a8e84: the verified run of
`edtf-postgres` fails compiling `pgrx-pg-sys`, the `--no-verify` run
writes six crates. So the stub refuses a verified run that reaches
the uncompilable member, writes every member not `--exclude`d, and on
`--no-verify` writes every member - from STUB_SECOND when a row plants
different bytes there, which is the one way the equality check can be
reached at all.
"""

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from belt import decoded, workflow_yaml
from belt.release import package_crates
from belt.typed_pytest import fixture
from tests import support

CANON = support.CANON
WORKFLOW = CANON / ".github" / "workflows" / "build-rust-crate.yml"
BASH = support.tool("bash")
VERSION = "1.3.4"
MEMBERS = ("edtf-core", "edtf-cli", "edtf-postgres")
UNCOMPILABLE = "edtf-postgres"
COMMAND = "package-crates"
STEPS = (
    "Compute the subject manifest",
    "Emit the inventory plan (which packages these crates ship)",
)

CARGO_STUB = """#!/usr/bin/env bash
# Stub `cargo`, in cargo's measured shape. `metadata` answers
# STUB_METADATA. `package`: a verified run that reaches
# STUB_UNCOMPILABLE fails the way the pgrx crate does; every member not
# --exclude'd is written whatever its `publish` key says (cargo package
# does not read it); --no-verify writes from STUB_SECOND when the row
# planted bytes there; a member in STUB_UNWRITTEN is never written.
set -euo pipefail
if [[ $1 == metadata ]]; then cat "${STUB_METADATA}"; exit 0; fi
printf '%s\\n' "$*" >> "${STUB_LOG}"
[[ $1 == package ]] || { echo "cargo stub: refusing $*" >&2; exit 101; }
excluded=" "
verify=1
while [[ $# -gt 0 ]]; do
  case $1 in
    --exclude) excluded="${excluded}$2 "; shift ;;
    --no-verify) verify=0 ;;
  esac
  shift
done
mkdir -p target/package
for m in ${STUB_MEMBERS}; do
  if [[ ${excluded} == *" ${m} "* ]]; then continue; fi
  if [[ " ${STUB_UNWRITTEN} " == *" ${m} "* ]]; then continue; fi
  if [[ ${verify} -eq 1 && ${m} == "${STUB_UNCOMPILABLE}" ]]; then
    echo "error: failed to compile pgrx-pg-sys" >&2
    exit 101
  fi
  src="${STUB_FIRST}/${m}-${STUB_VERSION}.crate"
  if [[ ${verify} -eq 0 && -f ${STUB_SECOND}/${m}-${STUB_VERSION}.crate ]]; then
    src="${STUB_SECOND}/${m}-${STUB_VERSION}.crate"
  fi
  cp "${src}" target/package/
done
"""


@fixture
def _outside_mise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name the helper by its command, as a run outside mise does (#1934).

    An in-process call reads argv, whatever task runs this suite; an
    empty ORG_FINDINGS records nothing (unset on a runner records to the
    job's file, #1976).
    """
    monkeypatch.setattr(sys, "argv", ["package-crates"])
    monkeypatch.delenv("MISE_TASK_NAME", raising=False)
    monkeypatch.setenv("ORG_FINDINGS", "")


pytestmark = pytest.mark.usefixtures("_outside_mise")


def step_body(name: str) -> str:
    """Read one step's `run:` value from the shipped workflow (#1840).

    Returns:
        The value, as the runner hands it to bash: a one-line `run:` is
        its text, its quotes YAML's, not the command's.

    """
    document = workflow_yaml.document(WORKFLOW)
    (run,) = [
        s["run"]
        for job in workflow_yaml.jobs(document).values()
        for s in workflow_yaml.steps(job)
        if s.get("name") == name
    ]
    return decoded.as_str(run, "run")


def crate_bytes(member: str) -> bytes:
    """Make the stand-in bytes the first run writes for one member.

    Returns:
        Distinct bytes per member, so each digest names its crate.

    """
    return f"{member} {VERSION} packaged\n".encode()


def digest(member: str) -> str:
    """Hash a member's first-run crate.

    Returns:
        The hex digest the subject manifest should carry.

    """
    return hashlib.sha256(crate_bytes(member)).hexdigest()


@dataclass
class Run:
    """What the three steps did, read back from their files."""

    returncode: int
    stdout: str
    stderr: str
    cargo: list[str]
    packaged: list[str]
    outputs: str
    plan: list[dict[str, object]]

    def manifest(self) -> str:
        """Read the `subjects` output, as the next job would.

        Returns:
            The body between the heredoc markers.

        """
        body = self.outputs.split("subjects<<SUBJECTS_EOF\n", 1)[1]
        return body.split("SUBJECTS_EOF\n", 1)[0]


def metadata(publish: dict[str, list[str] | None]) -> dict[str, object]:
    """Build `cargo metadata --no-deps` for a workspace, as cargo shapes it.

    Returns:
        The fields the helper reads: members, ids, versions, `publish`.

    """
    ids = {m: f"path+file:///work/{m}#{VERSION}" for m in publish}
    return {
        "packages": [
            {"name": m, "version": VERSION, "id": ids[m], "publish": key}
            for m, key in publish.items()
        ],
        "workspace_members": list(ids.values()),
        "target_directory": "/work/target",
    }


@dataclass(frozen=True)
class Job:
    """One row's workspace and the resolve step's flags.

    Attributes:
        excludes: EXCLUDE_FLAGS, as the resolve step writes them.
        second: bytes the --no-verify run writes instead, per member.
        members: the workspace members cargo packages.
        publish: each member's `publish` key as cargo metadata reports
            it, where the row sets one; unset (null) otherwise.
        unwritten: members cargo package writes no crate for.

    """

    excludes: str
    second: dict[str, bytes] | None = None
    members: tuple[str, ...] = MEMBERS
    publish: dict[str, list[str]] | None = None
    unwritten: tuple[str, ...] = ()


def run_steps(
    excludes: str,
    second: dict[str, bytes] | None = None,
    members: tuple[str, ...] = MEMBERS,
    publish: dict[str, list[str]] | None = None,
    unwritten: tuple[str, ...] = (),
    *,
    capfd: pytest.CaptureFixture[str],
) -> Run:
    """Run the shipped step bodies in order, the helper in this process.

    The arguments are `Job`'s fields.

    Returns:
        The run's status, output, cargo log and what it left behind.

    """
    return steps(Job(excludes, second, members, publish, unwritten), capfd=capfd)


def steps(job: Job, *, capfd: pytest.CaptureFixture[str]) -> Run:
    """Run the shipped step bodies in order, as the job does.

    Returns:
        The run's status, output, cargo log and what it left behind.

    """
    with TemporaryDirectory() as d:
        tmp = Path(d)
        stubs, first, planted, work = (
            tmp / n for n in ("bin", "first", "second", "work")
        )
        for p in (stubs, first, planted, work):
            p.mkdir()
        (stubs / "cargo").write_text(CARGO_STUB)
        (stubs / "cargo").chmod(0o755)
        # The inventory step reads the policy the release asserts.
        support.canon(work / ".org-canon", "slsa/assert-policy.json")
        keys = {m: (job.publish or {}).get(m) for m in job.members}
        (tmp / "metadata.json").write_text(json.dumps(metadata(keys)))
        for m in job.members:
            (first / f"{m}-{VERSION}.crate").write_bytes(crate_bytes(m))
        for m, b in (job.second or {}).items():
            (planted / f"{m}-{VERSION}.crate").write_bytes(b)
        log, outputs = tmp / "cargo.log", tmp / "github_output"
        outputs.touch()
        env = support.env(
            PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
            EXCLUDE_FLAGS=job.excludes,
            UNTRUSTED_VERSION=VERSION,
            GITHUB_OUTPUT=str(outputs),
            STUB_LOG=str(log),
            STUB_MEMBERS=" ".join(job.members),
            STUB_UNCOMPILABLE=UNCOMPILABLE,
            STUB_VERSION=VERSION,
            STUB_FIRST=str(first),
            STUB_SECOND=str(planted),
            STUB_METADATA=str(tmp / "metadata.json"),
            STUB_UNWRITTEN=" ".join(job.unwritten),
        )
        with pytest.MonkeyPatch.context() as patched:
            for name, value in env.items():
                patched.setenv(name, value)
            patched.chdir(work)
            patched.setattr(sys, "argv", [COMMAND])
            rc = package_crates.main([])
        out, err = capfd.readouterr()
        for name in STEPS:
            if rc != 0:
                break
            result = subprocess.run(
                [BASH, "-c", step_body(name)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
                cwd=work,
            )
            out, err, rc = out + result.stdout, err + result.stderr, result.returncode
        package = work / "target" / "package"
        plan = work / "plan" / "plan.json"
        return Run(
            rc,
            out,
            err,
            log.read_text().splitlines() if log.exists() else [],
            sorted(p.name for p in package.glob("*.crate")),
            outputs.read_text(),
            read_plan(plan),
        )


def read_plan(plan: Path) -> list[dict[str, object]]:
    """Read the plan the step wrote, if it wrote one.

    Returns:
        Its entries; none when there is no plan.

    """
    if not plan.exists():
        return []
    listed = decoded.as_array(
        decoded.load_json(plan.read_text(encoding="utf-8")), "the plan"
    )
    return [decoded.as_object(e, "a plan entry") for e in listed]


ALL = sorted(f"{m}-{VERSION}.crate" for m in MEMBERS)
EXCLUDE = f" --exclude {UNCOMPILABLE}"
# The one verified run of a workspace packaged with no exclusion.
ONE_RUN: list[str] = ["package --workspace --locked"]


def subject_lines(stdout: str) -> list[str]:
    """Keep the lines that say a crate is not a release subject.

    Returns:
        Those lines, in order.

    """
    return [x for x in stdout.splitlines() if "release subject" in x]


class TestPackageStepIsTheHelper:
    """The job runs the helper these rows run (#1384)."""

    def test_the_shipped_step_calls_the_helper(self) -> None:
        """Nothing but the call is left in the workflow."""
        # Under the belt's Python (#1543).
        assert f'"${{ORG_PYTHON}}" -I .org-canon/belt {COMMAND}' == step_body("Package")


class TestPackageAnExcludedMember:
    """One row per branch of the Package step (#1273)."""

    def test_an_excluded_member_is_packaged_hashed_and_planned(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Ship the extension edtf excludes, uncompiled."""
        run = run_steps(EXCLUDE, capfd=capfd)
        assert run.returncode == 0, run.stdout + run.stderr
        cargo: list[str] = [
            f"package --workspace --locked --exclude {UNCOMPILABLE}",
            "package --workspace --locked --no-verify",
        ]
        assert cargo == run.cargo
        assert run.packaged == ALL
        assert (
            "".join(f"{digest(m)}  {m}-{VERSION}.crate\n" for m in sorted(MEMBERS))
            == run.manifest()
        )
        entry: dict[str, object] = {
            "class": "rust-crate",
            "doc": f"sbom-cargo-{UNCOMPILABLE}-crate",
            "artifact": f"{UNCOMPILABLE}-crate",
            "params": {"cargoPackage": UNCOMPILABLE},
        }
        assert entry in run.plan
        assert len(MEMBERS) == len(run.plan)

    def test_a_crate_rewritten_differently_fails_naming_both(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The verified bytes must be the bytes hashed and published."""
        planted = b"not the bytes that were verified\n"
        run = run_steps(EXCLUDE, second={"edtf-core": planted}, capfd=capfd)
        assert run.returncode != 0
        assert (
            f"::error::package-crates: edtf-core-{VERSION}.crate changed when"
            f" packaged again"
            f" without verifying: {digest('edtf-core')} verified,"
            f" {hashlib.sha256(planted).hexdigest()} now\n"
        ) == run.stderr
        assert run.outputs == ""

    def test_a_changed_excluded_crate_alone_is_not_a_mismatch(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Only crates the verified run wrote are compared."""
        run = run_steps(
            EXCLUDE, second={UNCOMPILABLE: b"first written here\n"}, capfd=capfd
        )
        assert run.returncode == 0, run.stdout + run.stderr
        assert "::error::package-crates: " not in run.stderr

    def test_a_verified_run_that_wrote_nothing_is_refused(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Every member excluded leaves nothing to compare (#1384)."""
        flags = "".join(f" --exclude {m}" for m in MEMBERS)
        run = run_steps(flags, capfd=capfd)
        assert run.returncode == 1
        assert (
            run.stderr
            == "::error::package-crates: cargo package wrote no crate to verify\n"
        )
        cargo: list[str] = [f"package --workspace --locked{flags}"]
        assert cargo == run.cargo
        assert run.outputs == ""

    def test_a_failing_cargo_fails_the_step_with_its_status(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The uncompilable member, not excluded: cargo's 101 is the step's."""
        run = steps(Job(""), capfd=capfd)
        assert run.returncode == 101
        assert "failed to compile pgrx-pg-sys" in run.stderr
        assert run.cargo == ONE_RUN
        assert run.outputs == ""


class TestPackageWithoutExclusion:
    """A workspace the job compiles entirely packages once."""

    def test_one_verified_run_and_every_crate_hashed(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """No --no-verify run when nothing is excluded."""
        compiles = ("edtf-core", "edtf-cli")
        run = run_steps("", members=compiles, capfd=capfd)
        assert run.returncode == 0, run.stdout + run.stderr
        assert run.cargo == ONE_RUN
        packaged: list[str] = sorted(f"{m}-{VERSION}.crate" for m in compiles)
        assert packaged == run.packaged
        assert (
            "".join(f"{digest(m)}  {m}-{VERSION}.crate\n" for m in sorted(compiles))
            == run.manifest()
        )

    def test_a_blank_exclusion_is_no_exclusion(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Whitespace-only flags take the one-run branch too."""
        run = run_steps("  ", members=("edtf-core",), capfd=capfd)
        assert run.returncode == 0, run.stdout + run.stderr
        assert run.cargo == ONE_RUN


class TestSubjectsAreWhatCratesIoReceives:
    """The Package step keeps exactly the helper's set (#1310)."""

    def test_a_publish_false_member_leaves_every_subject_list(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Packaged and verified, then removed before anything hashes it."""
        run = run_steps(
            "",
            members=("edtf-core", "edtf-cli", "edtf-internal"),
            publish={"edtf-cli": ["crates-io"], "edtf-internal": []},
            capfd=capfd,
        )
        assert run.returncode == 0, run.stdout + run.stderr
        assert run.cargo == ONE_RUN
        said: list[str] = [
            (
                f"edtf-internal-{VERSION}.crate is not published to crates.io"
                " (publish key); not a release subject"
            )
        ]
        assert said == subject_lines(run.stdout)
        ships = ("edtf-cli", "edtf-core")
        packaged: list[str] = [f"{m}-{VERSION}.crate" for m in ships]
        assert packaged == run.packaged
        assert (
            "".join(f"{digest(m)}  {m}-{VERSION}.crate\n" for m in ships)
            == run.manifest()
        )
        planned: list[object] = [
            decoded.as_object(x["params"], "params")["cargoPackage"] for x in run.plan
        ]
        shipped: list[object] = list(ships)
        assert shipped == planned

    def test_another_registry_only_is_not_a_subject(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A member publishing elsewhere is not crates.io's to serve."""
        run = run_steps(
            "",
            members=("edtf-core", "edtf-mirror"),
            publish={"edtf-mirror": ["x"]},
            capfd=capfd,
        )
        assert run.returncode == 0, run.stdout + run.stderr
        packaged: list[str] = [f"edtf-core-{VERSION}.crate"]
        assert packaged == run.packaged
        said: list[str] = [
            (
                f"edtf-mirror-{VERSION}.crate is not published to crates.io"
                " (publish key); not a release subject"
            )
        ]
        assert said == subject_lines(run.stdout)

    def test_an_excluded_publish_false_member_is_removed_too(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Packaged by the --no-verify run, then cut like any other."""
        run = run_steps(EXCLUDE, publish={UNCOMPILABLE: []}, capfd=capfd)
        assert run.returncode == 0, run.stdout + run.stderr
        packaged: list[str] = [
            f"edtf-cli-{VERSION}.crate",
            f"edtf-core-{VERSION}.crate",
        ]
        assert packaged == run.packaged
        assert UNCOMPILABLE not in run.manifest()

    def test_a_named_crate_cargo_did_not_write_fails_by_name(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Nothing the publisher will upload goes unhashed."""
        run = run_steps(
            "", members=("edtf-core", "edtf-cli"), unwritten=("edtf-cli",), capfd=capfd
        )
        assert run.returncode != 0
        assert (
            f"::error::package-crates: edtf-cli {VERSION} publishes to crates.io,"
            f" but cargo package wrote no edtf-cli-{VERSION}.crate\n"
        ) == run.stderr
        assert run.outputs == ""
