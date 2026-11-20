#!/usr/bin/env python3
"""Run publish.yml's repro-gate call on a stub stele and read its argv (#1376).

The comparison is `stele verify repro` in its pre-release form
(stele#401); stele's own suite holds the verdicts. What the canon owns
is the wiring: which classes reach the call, and each class's two
outputs as stele takes them. These rows hold that wiring.

WHAT RUNS. The `run:` body of "Compare the two builds (stele verify
repro)", cut out of `.github/workflows/publish.yml` as shipped, under
bash, with a stub `stele` first on PATH that writes its argv one word
per line, and the contents of each file it was handed.

The verdicts themselves were measured against the real stele v0.25.0
and real artifacts (release-lab v0.28.5, edtf v1.3.5, the canon's
v1.69.3), and the pull request quotes them; these rows need no network
and no binary.

stdlib `unittest`, matching the rest of release/. Run through the gate
as `mise run test`, which `ci` collects.
"""

import os

# ruff: ignore[suspicious-subprocess-import]
import subprocess
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import decoded
import hermetic
import workflow_yaml

CANON = Path(__file__).resolve().parent.parent
WORKFLOW = CANON / ".github" / "workflows" / "publish.yml"
STEP = "Compare the two builds (stele verify repro)"
BASH = hermetic.tool("bash")
CRATES = "aa  edtf-core-1.3.5.crate\nbb  edtf-cli-1.3.5.crate"
WASM = "cc  lab-wasm-0.28.5.tgz"

STELE_STUB = """#!/usr/bin/env bash
# Stub `stele`: record argv, and the contents of every <class>=<file>.
printf '%s\\n' "$@" > "${STUB_ARGV}"
for a in "$@"; do
  if [[ ${a} == *=* && -f ${a#*=} ]]; then
    { echo "== ${a}"; cat "${a#*=}"; } >> "${STUB_FILES}"
  fi
done
exit "${STUB_EXIT:-0}"
"""


def step() -> dict[str, object]:
    """Read the step out of the shipped workflow.

    Returns:
        The step, as workflow_yaml reads it.

    """
    document = workflow_yaml.document(WORKFLOW)
    (found,) = [
        s
        for job in workflow_yaml.jobs(document).values()
        for s in workflow_yaml.steps(job)
        if s.get("name") == STEP
    ]
    return found


def step_body() -> str:
    """Read the step's `run:` value.

    Returns:
        The text the runner hands to bash.

    """
    return decoded.as_str(step()["run"], "run")


@dataclass
class Run:
    """What the body did, read back from the stub's files."""

    returncode: int
    stderr: str
    argv: list[str]
    files: str

    def inputs(self) -> list[str]:
        """Name every --first/--rebuild the call received, in order.

        Returns:
            `--first rust-crate=first-rust-crate`-shaped pairs.

        """
        return [
            f"{flag} {value}"
            for flag, value in zip(self.argv, self.argv[1:], strict=False)
            if flag in {"--first", "--rebuild"}
        ]


def run_step(classes: str, *, dry_run: bool = False, **outputs: str) -> Run:
    """Run the body with the guard's class list and each class's outputs.

    Args:
        classes: the guard's expanded `classes` output.
        dry_run: the caller's dry-run input.
        **outputs: FIRST_/REBUILD_<class> values as the step's env, and
            STUB_EXIT for the stub's status.

    Returns:
        Its status, stderr, the call's argv and the files it was given.

    """
    with TemporaryDirectory() as d:
        tmp = Path(d)
        stubs, work = tmp / "bin", tmp / "work"
        for p in (stubs, work):
            p.mkdir()
        (stubs / "stele").write_text(STELE_STUB)
        (stubs / "stele").chmod(0o755)
        argv, files = tmp / "argv", tmp / "files"
        inherited = hermetic.env()
        env = {
            **inherited,
            "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
            "CLASSES": classes,
            "VERSION": "1.3.5",
            "DRY_RUN": "true" if dry_run else "false",
            "GITHUB_REPOSITORY": "monumental-archive/edtf",
            "STUB_ARGV": str(argv),
            "STUB_FILES": str(files),
            **outputs,
        }
        # ruff: ignore[subprocess-without-shell-equals-true]
        result = subprocess.run(
            [BASH, "-c", step_body()],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            cwd=work,
        )
        return Run(
            result.returncode,
            result.stderr,
            argv.read_text().splitlines() if argv.exists() else [],
            files.read_text() if files.exists() else "",
        )


class TheCall(unittest.TestCase):
    """The pre-release form, the release's identity, the policy."""

    def test_the_call_is_the_pre_release_form(self) -> None:
        """--classes is the guard's list; repo, tag and policy are named."""
        run = run_step("rust-crate", FIRST_rust_crate=CRATES, REBUILD_rust_crate=CRATES)
        self.assertEqual(0, run.returncode, run.stderr)
        want: list[str] = [
            "verify",
            "repro",
            "--repo",
            "monumental-archive/edtf",
            "--tag",
            "v1.3.5",
            "--classes",
            "rust-crate",
            "--assert-policy",
            ".org-canon/slsa/assert-policy.json",
            "--rehearsal=false",
            "--first",
            "rust-crate=first-rust-crate",
            "--rebuild",
            "rust-crate=rebuild-rust-crate",
        ]
        self.assertEqual(want, run.argv)

    def test_a_failing_verdict_fails_the_step(self) -> None:
        """Stele's status is the step's."""
        run = run_step(
            "rust-crate",
            FIRST_rust_crate=CRATES,
            REBUILD_rust_crate=CRATES,
            STUB_EXIT="1",
        )
        self.assertEqual(1, run.returncode)


class Inputs(unittest.TestCase):
    """Each declared class's outputs, in the shape stele reads."""

    def test_a_manifest_is_written_to_a_file_whole(self) -> None:
        """Every line of a file class's manifest reaches stele."""
        run = run_step("rust-crate", FIRST_rust_crate=CRATES, REBUILD_rust_crate=CRATES)
        self.assertEqual(
            f"== rust-crate=first-rust-crate\n{CRATES}\n"
            f"== rust-crate=rebuild-rust-crate\n{CRATES}\n",
            run.files,
        )

    def test_a_digest_directory_is_passed_as_it_is(self) -> None:
        """A registry class's downloads are named, never rewritten."""
        run = run_step(
            "oci-image-crate",
            FIRST_oci_image_crate="oci-a",
            REBUILD_oci_image_crate="oci-b",
        )
        want: list[str] = [
            "--first oci-image-crate=oci-a",
            "--rebuild oci-image-crate=oci-b",
        ]
        self.assertEqual(want, run.inputs())
        self.assertEqual("", run.files)

    def test_only_the_declared_classes_reach_the_call(self) -> None:
        """A job's output for a class the evidence did not declare stays out."""
        run = run_step(
            "wasm-npm",
            FIRST_wasm_npm=WASM,
            REBUILD_wasm_npm=WASM,
            FIRST_rust_crate=CRATES,
            REBUILD_rust_crate=CRATES,
        )
        want: list[str] = [
            "--first wasm-npm=first-wasm-npm",
            "--rebuild wasm-npm=rebuild-wasm-npm",
        ]
        self.assertEqual(want, run.inputs())

    def test_an_empty_side_is_not_named(self) -> None:
        """Stele refuses the class by name; no empty file stands in."""
        run = run_step("rust-crate", FIRST_rust_crate=CRATES, REBUILD_rust_crate="")
        want: list[str] = ["--first rust-crate=first-rust-crate"]
        self.assertEqual(want, run.inputs())

    def test_every_class_in_the_expanded_list_is_wired(self) -> None:
        """The lab's real expansion: five classes, ten inputs, in order."""
        classes = (
            "rust-binary,oci-image-crate,wasm-npm,pgrx-extension,pgrx-extension-image"
        )
        outputs = {
            "FIRST_rust_binary": "dd  a.tar.gz",
            "REBUILD_rust_binary": "dd  a.tar.gz",
            "FIRST_oci_image_crate": "oci-a",
            "REBUILD_oci_image_crate": "oci-b",
            "FIRST_wasm_npm": WASM,
            "REBUILD_wasm_npm": WASM,
            "FIRST_pgrx_extension": "ee  b.tar.gz",
            "REBUILD_pgrx_extension": "ee  b.tar.gz",
            "FIRST_pgrx_extension_image": "pgrx-a",
            "REBUILD_pgrx_extension_image": "pgrx-b",
        }
        run = run_step(classes, dry_run=False, **outputs)
        want: list[str] = [
            "--first rust-binary=first-rust-binary",
            "--rebuild rust-binary=rebuild-rust-binary",
            "--first oci-image-crate=oci-a",
            "--rebuild oci-image-crate=oci-b",
            "--first wasm-npm=first-wasm-npm",
            "--rebuild wasm-npm=rebuild-wasm-npm",
            "--first pgrx-extension=first-pgrx-extension",
            "--rebuild pgrx-extension=rebuild-pgrx-extension",
            "--first pgrx-extension-image=pgrx-a",
            "--rebuild pgrx-extension-image=pgrx-b",
        ]
        self.assertEqual(want, run.inputs())


class Rehearsal(unittest.TestCase):
    """A dry run pushed no image."""

    def test_a_rehearsal_names_no_digest_directory(self) -> None:
        """The workflow leaves the registry values empty; stele says so."""
        run = run_step(
            "rust-crate,oci-image",
            dry_run=True,
            FIRST_rust_crate=CRATES,
            REBUILD_rust_crate=CRATES,
            FIRST_oci_image="",
            REBUILD_oci_image="",
        )
        self.assertIn("--rehearsal=true", run.argv)
        want: list[str] = [
            "--first rust-crate=first-rust-crate",
            "--rebuild rust-crate=rebuild-rust-crate",
        ]
        self.assertEqual(want, run.inputs())

    def test_a_rehearsal_of_images_alone_still_calls_stele(self) -> None:
        """No inputs at all: the call is made, and stele refuses it."""
        run = run_step("oci-image", dry_run=True)
        self.assertEqual(0, run.returncode, run.stderr)
        self.assertEqual(list[str](), run.inputs())
        self.assertIn("--rehearsal=true", run.argv)

    def test_the_workflow_empties_the_registry_values_on_a_dry_run(self) -> None:
        """Every digest directory is named only when the run pushed."""
        env = decoded.as_object(step()["env"], "env")
        for var, path in (
            ("oci_image", "oci"),
            ("oci_image_crate", "oci"),
            ("pgrx_extension_image", "pgrx"),
        ):
            for side, suffix in (("FIRST", "a"), ("REBUILD", "b")):
                with self.subTest(var=f"{side}_{var}"):
                    self.assertEqual(
                        f"${{{{ !inputs.dry-run && '{path}-{suffix}' || '' }}}}",
                        env[f"{side}_{var}"],
                    )


if __name__ == "__main__":
    unittest.main()
