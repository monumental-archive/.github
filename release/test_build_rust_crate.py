#!/usr/bin/env python3
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

WHAT RUNS. The Package step's helper, release/package-crates.py
(#1384), then the `run:` bodies of `Compute the subject manifest` and
`Emit the inventory plan`, cut out of
`.github/workflows/build-rust-crate.yml` as shipped, in order, the two
bodies under `bash -c`, with a stub `cargo` first on PATH. The helper
runs through a `.org-canon` link to this tree exactly as the canon
action lays it out, and so does crates-io-members.sh, which it calls;
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

stdlib `unittest`, matching the rest of release/. Run through the gate
as `mise run test`, which `ci` collects.
"""

import hashlib
import json
import os
import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

hermetic = SimpleNamespace(
    **runpy.run_path(str(Path(__file__).resolve().parents[1] / "mise" / "hermetic.py"))
)

CANON = Path(__file__).resolve().parent.parent
WORKFLOW = CANON / ".github" / "workflows" / "build-rust-crate.yml"
BASH = shutil.which("bash")
VERSION = "1.3.4"
MEMBERS = ("edtf-core", "edtf-cli", "edtf-postgres")
UNCOMPILABLE = "edtf-postgres"
HELPER = ".org-canon/release/package-crates.py"
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


def step_body(name: str) -> str:
    """Cut one step's `run:` block out of the shipped workflow.

    Returns:
        The block, dedented, exactly as the runner would hand it to bash;
        a one-line `run:` as its line.

    """
    lines = WORKFLOW.read_text().splitlines()
    step = lines.index(f"      - name: {name}")
    run = next(
        i for i in range(step, len(lines)) if lines[i].strip().startswith("run:")
    )
    if lines[run].strip() != "run: |":
        return lines[run].strip().removeprefix("run:").strip() + "\n"
    indent = len(lines[run]) - len(lines[run].lstrip()) + 2
    body = []
    for line in lines[run + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body) + "\n"


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


def run_steps(
    excludes: str,
    second: dict[str, bytes] | None = None,
    members: tuple[str, ...] = MEMBERS,
    publish: dict[str, list[str]] | None = None,
    unwritten: tuple[str, ...] = (),
) -> Run:
    """Run the shipped step bodies in order, as the job does.

    Args:
        excludes: EXCLUDE_FLAGS, as the resolve step writes them.
        second: bytes the --no-verify run writes instead, per member.
        members: the workspace members cargo packages.
        publish: each member's `publish` key as cargo metadata reports
            it, where the row sets one; unset (null) otherwise.
        unwritten: members cargo package writes no crate for.

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
        (work / ".org-canon").symlink_to(CANON)
        keys = {m: (publish or {}).get(m) for m in members}
        (tmp / "metadata.json").write_text(json.dumps(metadata(keys)))
        for m in members:
            (first / f"{m}-{VERSION}.crate").write_bytes(crate_bytes(m))
        for m, b in (second or {}).items():
            (planted / f"{m}-{VERSION}.crate").write_bytes(b)
        log, outputs = tmp / "cargo.log", tmp / "github_output"
        outputs.touch()
        env = hermetic.env(
            PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
            EXCLUDE_FLAGS=excludes,
            UNTRUSTED_VERSION=VERSION,
            GITHUB_OUTPUT=str(outputs),
            STUB_LOG=str(log),
            STUB_MEMBERS=" ".join(members),
            STUB_UNCOMPILABLE=UNCOMPILABLE,
            STUB_VERSION=VERSION,
            STUB_FIRST=str(first),
            STUB_SECOND=str(planted),
            STUB_METADATA=str(tmp / "metadata.json"),
            STUB_UNWRITTEN=" ".join(unwritten),
        )
        out, err, rc = "", "", 0
        for argv in [[HELPER], *([BASH, "-c", step_body(n)] for n in STEPS)]:
            result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                argv,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                cwd=work,
            )
            out, err, rc = out + result.stdout, err + result.stderr, result.returncode
            if rc != 0:
                break
        package = work / "target" / "package"
        plan = work / "plan" / "plan.json"
        return Run(
            rc,
            out,
            err,
            log.read_text().splitlines() if log.exists() else [],
            sorted(p.name for p in package.glob("*.crate")),
            outputs.read_text(),
            json.loads(plan.read_text()) if plan.exists() else [],
        )


ALL = sorted(f"{m}-{VERSION}.crate" for m in MEMBERS)
EXCLUDE = f" --exclude {UNCOMPILABLE}"


class PackageStepIsTheHelper(unittest.TestCase):
    """The job runs the helper these rows run (#1384)."""

    def test_the_shipped_step_calls_the_helper(self) -> None:
        """Nothing but the call is left in the workflow."""
        # Under the belt's Python (#1543).
        self.assertEqual(f"'\"${{ORG_PYTHON}}\" {HELPER}'\n", step_body("Package"))


class PackageAnExcludedMember(unittest.TestCase):
    """One row per branch of the Package step (#1273)."""

    def test_an_excluded_member_is_packaged_hashed_and_planned(self) -> None:
        """Ship the extension edtf excludes, uncompiled."""
        run = run_steps(EXCLUDE)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual(
            [
                f"package --workspace --locked --exclude {UNCOMPILABLE}",
                "package --workspace --locked --no-verify",
            ],
            run.cargo,
        )
        self.assertEqual(ALL, run.packaged)
        self.assertEqual(
            "".join(f"{digest(m)}  {m}-{VERSION}.crate\n" for m in sorted(MEMBERS)),
            run.manifest(),
        )
        self.assertIn(
            {
                "class": "rust-crate",
                "doc": f"sbom-cargo-{UNCOMPILABLE}-crate",
                "artifact": f"{UNCOMPILABLE}-crate",
                "params": {"cargoPackage": UNCOMPILABLE},
            },
            run.plan,
        )
        self.assertEqual(len(MEMBERS), len(run.plan))

    def test_a_crate_rewritten_differently_fails_naming_both(self) -> None:
        """The verified bytes must be the bytes hashed and published."""
        planted = b"not the bytes that were verified\n"
        run = run_steps(EXCLUDE, second={"edtf-core": planted})
        self.assertNotEqual(0, run.returncode)
        self.assertEqual(
            f"::error::edtf-core-{VERSION}.crate changed when packaged again"
            f" without verifying: {digest('edtf-core')} verified,"
            f" {hashlib.sha256(planted).hexdigest()} now\n",
            run.stdout,
        )
        self.assertEqual("", run.outputs)

    def test_a_changed_excluded_crate_alone_is_not_a_mismatch(self) -> None:
        """Only crates the verified run wrote are compared."""
        run = run_steps(EXCLUDE, second={UNCOMPILABLE: b"first written here\n"})
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertNotIn("::error::", run.stdout)

    def test_a_verified_run_that_wrote_nothing_is_refused(self) -> None:
        """Every member excluded leaves nothing to compare (#1384)."""
        flags = "".join(f" --exclude {m}" for m in MEMBERS)
        run = run_steps(flags)
        self.assertEqual(1, run.returncode)
        self.assertEqual(
            "::error::cargo package wrote no crate to verify\n", run.stdout
        )
        self.assertEqual([f"package --workspace --locked{flags}"], run.cargo)
        self.assertEqual("", run.outputs)

    def test_a_failing_cargo_fails_the_step_with_its_status(self) -> None:
        """The uncompilable member, not excluded: cargo's 101 is the step's."""
        run = run_steps("")
        self.assertEqual(101, run.returncode)
        self.assertIn("failed to compile pgrx-pg-sys", run.stderr)
        self.assertEqual(["package --workspace --locked"], run.cargo)
        self.assertEqual("", run.outputs)


class PackageWithoutExclusion(unittest.TestCase):
    """A workspace the job compiles entirely packages once."""

    def test_one_verified_run_and_every_crate_hashed(self) -> None:
        """No --no-verify run when nothing is excluded."""
        compiles = ("edtf-core", "edtf-cli")
        run = run_steps("", members=compiles)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual(["package --workspace --locked"], run.cargo)
        self.assertEqual(sorted(f"{m}-{VERSION}.crate" for m in compiles), run.packaged)
        self.assertEqual(
            "".join(f"{digest(m)}  {m}-{VERSION}.crate\n" for m in sorted(compiles)),
            run.manifest(),
        )

    def test_a_blank_exclusion_is_no_exclusion(self) -> None:
        """Whitespace-only flags take the one-run branch too."""
        run = run_steps("  ", members=("edtf-core",))
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual(["package --workspace --locked"], run.cargo)


class SubjectsAreWhatCratesIoReceives(unittest.TestCase):
    """The Package step keeps exactly the helper's set (#1310)."""

    def test_a_publish_false_member_leaves_every_subject_list(self) -> None:
        """Packaged and verified, then removed before anything hashes it."""
        run = run_steps(
            "",
            members=("edtf-core", "edtf-cli", "edtf-internal"),
            publish={"edtf-cli": ["crates-io"], "edtf-internal": []},
        )
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual(["package --workspace --locked"], run.cargo)
        self.assertEqual(
            [
                (
                    f"edtf-internal-{VERSION}.crate is not published to crates.io"
                    " (publish key); not a release subject"
                )
            ],
            [x for x in run.stdout.splitlines() if "release subject" in x],
        )
        ships = ("edtf-cli", "edtf-core")
        self.assertEqual([f"{m}-{VERSION}.crate" for m in ships], run.packaged)
        self.assertEqual(
            "".join(f"{digest(m)}  {m}-{VERSION}.crate\n" for m in ships),
            run.manifest(),
        )
        self.assertEqual(list(ships), [x["params"]["cargoPackage"] for x in run.plan])

    def test_another_registry_only_is_not_a_subject(self) -> None:
        """A member publishing elsewhere is not crates.io's to serve."""
        run = run_steps(
            "", members=("edtf-core", "edtf-mirror"), publish={"edtf-mirror": ["x"]}
        )
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual([f"edtf-core-{VERSION}.crate"], run.packaged)
        self.assertEqual(
            [
                (
                    f"edtf-mirror-{VERSION}.crate is not published to crates.io"
                    " (publish key); not a release subject"
                )
            ],
            [x for x in run.stdout.splitlines() if "release subject" in x],
        )

    def test_an_excluded_publish_false_member_is_removed_too(self) -> None:
        """Packaged by the --no-verify run, then cut like any other."""
        run = run_steps(EXCLUDE, publish={UNCOMPILABLE: []})
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual(
            [f"edtf-cli-{VERSION}.crate", f"edtf-core-{VERSION}.crate"], run.packaged
        )
        self.assertNotIn(UNCOMPILABLE, run.manifest())

    def test_a_named_crate_cargo_did_not_write_fails_by_name(self) -> None:
        """Nothing the publisher will upload goes unhashed."""
        run = run_steps("", members=("edtf-core", "edtf-cli"), unwritten=("edtf-cli",))
        self.assertNotEqual(0, run.returncode)
        self.assertEqual(
            f"::error::edtf-cli {VERSION} publishes to crates.io,"
            f" but cargo package wrote no edtf-cli-{VERSION}.crate\n",
            run.stdout,
        )
        self.assertEqual("", run.outputs)


if __name__ == "__main__":
    unittest.main()
