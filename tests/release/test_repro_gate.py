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
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from belt import decoded, workflow_yaml
from tests import support

CANON = support.CANON
WORKFLOW = CANON / ".github" / "workflows" / "publish.yml"
STEP = "Compare the two builds (stele verify repro)"
BASH = support.tool("bash")
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


def run_step(
    classes: str, *, dry_run: bool = False, env: support.Env, **outputs: str
) -> Run:
    """Run the body with the guard's class list and each class's outputs.

    Args:
        classes: the guard's expanded `classes` output.
        dry_run: the caller's dry-run input.
        env: the `env` fixture, which builds the step's environment.
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
        inherited = env()
        environ = {
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
        result = subprocess.run(
            [BASH, "-c", step_body()],
            env=environ,
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


# The pre-release form, the release's identity, the policy.
def test_the_call_is_the_pre_release_form(env: support.Env) -> None:
    """--classes is the guard's list; repo, tag and policy are named."""
    run = run_step(
        "rust-crate", FIRST_rust_crate=CRATES, REBUILD_rust_crate=CRATES, env=env
    )
    assert run.returncode == 0, run.stderr
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
    assert want == run.argv


def test_a_failing_verdict_fails_the_step(env: support.Env) -> None:
    """Stele's status is the step's."""
    run = run_step(
        "rust-crate",
        FIRST_rust_crate=CRATES,
        REBUILD_rust_crate=CRATES,
        STUB_EXIT="1",
        env=env,
    )
    assert run.returncode == 1


# Each declared class's outputs, in the shape stele reads.
def test_a_manifest_is_written_to_a_file_whole(env: support.Env) -> None:
    """Every line of a file class's manifest reaches stele."""
    run = run_step(
        "rust-crate", FIRST_rust_crate=CRATES, REBUILD_rust_crate=CRATES, env=env
    )
    assert (
        f"== rust-crate=first-rust-crate\n{CRATES}\n"
        f"== rust-crate=rebuild-rust-crate\n{CRATES}\n"
    ) == run.files


def test_a_digest_directory_is_passed_as_it_is(env: support.Env) -> None:
    """A registry class's downloads are named, never rewritten."""
    run = run_step(
        "oci-image-crate",
        FIRST_oci_image_crate="oci-a",
        REBUILD_oci_image_crate="oci-b",
        env=env,
    )
    want: list[str] = [
        "--first oci-image-crate=oci-a",
        "--rebuild oci-image-crate=oci-b",
    ]
    assert want == run.inputs()
    assert not run.files


def test_only_the_declared_classes_reach_the_call(env: support.Env) -> None:
    """A job's output for a class the evidence did not declare stays out."""
    run = run_step(
        "wasm-npm",
        FIRST_wasm_npm=WASM,
        REBUILD_wasm_npm=WASM,
        FIRST_rust_crate=CRATES,
        REBUILD_rust_crate=CRATES,
        env=env,
    )
    want: list[str] = [
        "--first wasm-npm=first-wasm-npm",
        "--rebuild wasm-npm=rebuild-wasm-npm",
    ]
    assert want == run.inputs()


def test_an_empty_side_is_not_named(env: support.Env) -> None:
    """Stele refuses the class by name; no empty file stands in."""
    run = run_step(
        "rust-crate", FIRST_rust_crate=CRATES, REBUILD_rust_crate="", env=env
    )
    want: list[str] = ["--first rust-crate=first-rust-crate"]
    assert want == run.inputs()


def test_every_class_in_the_expanded_list_is_wired(env: support.Env) -> None:
    """The lab's real expansion: five classes, ten inputs, in order."""
    classes = "rust-binary,oci-image-crate,wasm-npm,pgrx-extension,pgrx-extension-image"
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
    run = run_step(classes, dry_run=False, **outputs, env=env)
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
    assert want == run.inputs()


# A dry run pushed no image.
def test_a_rehearsal_names_no_digest_directory(env: support.Env) -> None:
    """The workflow leaves the registry values empty; stele says so."""
    run = run_step(
        "rust-crate,oci-image",
        dry_run=True,
        FIRST_rust_crate=CRATES,
        REBUILD_rust_crate=CRATES,
        FIRST_oci_image="",
        REBUILD_oci_image="",
        env=env,
    )
    assert "--rehearsal=true" in run.argv
    want: list[str] = [
        "--first rust-crate=first-rust-crate",
        "--rebuild rust-crate=rebuild-rust-crate",
    ]
    assert want == run.inputs()


def test_a_rehearsal_of_images_alone_still_calls_stele(env: support.Env) -> None:
    """No inputs at all: the call is made, and stele refuses it."""
    run = run_step("oci-image", dry_run=True, env=env)
    assert run.returncode == 0, run.stderr
    assert list[str]() == run.inputs()
    assert "--rehearsal=true" in run.argv


def test_the_workflow_empties_the_registry_values_on_a_dry_run() -> None:
    """Every digest directory is named only when the run pushed."""
    env = decoded.as_object(step()["env"], "env")
    for var, path in (
        ("oci_image", "oci"),
        ("oci_image_crate", "oci"),
        ("pgrx_extension_image", "pgrx"),
    ):
        for side, suffix in (("FIRST", "a"), ("REBUILD", "b")):
            assert (
                f"${{{{ !inputs.dry-run && '{path}-{suffix}' || '' }}}}"
                == env[f"{side}_{var}"]
            )
