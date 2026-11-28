"""`belt inventory-plan`, the pgrx-extension plan step (#1371, #2177).

#1371 moved the step "Emit the inventory plan (what this cell built,
from its own inputs)" out of build-pgrx-extension.yml into the helper.
A build leg carries no belt, so the step runs the helper from the canon
tree, as the cell's other steps run release/pgrx/*.

WHAT RUNS. The helper's `main`, in this process, in a scratch directory
holding a planted crate. The golden is the step's own jq
output for crate `pg_edtf` and major 16, recorded byte for byte when the
step was still shell (the same bytes stele#396 holds as its producer
golden), so a passing row is the helper writing what the leg wrote.
"""

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pytest

from belt import decoded, plan_producers, workflow_yaml
from belt.release.pgrx import inventory_plan
from belt.typed_pytest import fixture
from tests import support

CANON = support.CANON
WORKFLOW = CANON / ".github" / "workflows" / "build-pgrx-extension.yml"
STEP = "Emit the inventory plan (what this cell built, from its own inputs)"

GOLDEN = """[
  {
    "class": "pgrx-extension",
    "doc": "sbom-pgrx-pg_edtf-pg16",
    "artifact": "pg_edtf-pg16",
    "params": {
      "cargoPackage": "pg_edtf",
      "features": [
        "pg16"
      ],
      "noDefaultFeatures": true
    }
  }
]
"""

MANIFEST_REFUSAL = "::error::inventory-plan: crate/Cargo.toml: "

CRATE = '[package]\nname = "pg_edtf"\nversion = "0.4.2"\n\n[features]\npg16 = []\n'


@fixture
def _outside_mise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name the helper by its command, as a run outside mise does (#1934).

    An in-process call reads argv, whatever task runs this suite; an
    empty ORG_FINDINGS records nothing (unset on a runner records to the
    job's file, #1976).
    """
    monkeypatch.setattr(sys, "argv", ["inventory-plan"])
    monkeypatch.delenv("MISE_TASK_NAME", raising=False)
    monkeypatch.setenv("ORG_FINDINGS", "")


pytestmark = pytest.mark.usefixtures("_outside_mise")


@dataclass
class Run:
    """What the helper did."""

    returncode: int
    stdout: str
    stderr: str
    plan: str | None


@dataclass
class Planted:
    """One crate planted for the helper, in its own working tree."""

    work: Path

    def ran(self, status: int, said: tuple[str, str]) -> Run:
        """Read what the helper's run did.

        Returns:
            Its status, output, and plan/plan.json when one was written.

        """
        out, err = said
        plan = self.work / "plan" / "plan.json"
        return Run(status, out, err, plan.read_text("utf-8") if plan.exists() else None)


class Plant(Protocol):
    """What the `plant` fixture hands a row."""

    def __call__(self, manifest: str | None = ..., prefix: str | None = ...) -> Planted:
        """Run plant."""
        ...


@fixture
def plant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Plant:
    """Plant a crate for the helper and step into its tree.

    Returns:
        A function that plants one crate (manifest None plants none)
        under the plan step's PLAN_DOC_PREFIX (None unsets it).

    """

    def lay_out(
        manifest: str | None = CRATE, prefix: str | None = "sbom-pgrx-"
    ) -> Planted:
        work = Path(tempfile.mkdtemp(dir=tmp_path))
        (work / "crate").mkdir()
        if manifest is not None:
            (work / "crate" / "Cargo.toml").write_text(manifest)
        if prefix is None:
            monkeypatch.delenv("PLAN_DOC_PREFIX", raising=False)
        else:
            monkeypatch.setenv("PLAN_DOC_PREFIX", prefix)
        monkeypatch.chdir(work)
        return Planted(work)

    return lay_out


def step() -> dict[str, object]:
    """Read the plan step of the shipped workflow.

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


# The cell runs the helper these rows run, and the join can see it.
def test_the_shipped_step_calls_the_helper() -> None:
    """The run: is the call alone, from the canon tree."""
    assert step()["run"] == (
        '"${ORG_PYTHON}" -I .org-canon/belt inventory-plan'
        ' -- "${UNTRUSTED_CRATE_DIR}" "${PG}"\n'
    )


def test_the_step_states_its_prefix_and_its_marker() -> None:
    """belt/plan_producers.py joins the marker to PLAN_DOC_PREFIX."""
    declared, _ = plan_producers.markers(WORKFLOW.read_text("utf-8"))
    assert ("pgrx-extension", "sbom-pgrx-") in declared
    env = decoded.as_object(step()["env"], "env")
    assert env["PLAN_DOC_PREFIX"] == "sbom-pgrx-"


# The plan the step's jq wrote, byte for byte.
def test_the_cell_plan_is_the_shells_bytes(
    plant: Plant, capfd: pytest.CaptureFixture[str]
) -> None:
    """Crate pg_edtf, major 16: the recorded golden."""
    planted = plant()
    status = inventory_plan.main(["crate", "16"])
    run = planted.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.plan == GOLDEN
    assert (
        run.stdout
        == "::notice::plans inventory sbom-pgrx-pg_edtf-pg16 for package pg_edtf\n"
    )


def test_the_major_names_the_artifact_and_the_feature(
    plant: Plant, capfd: pytest.CaptureFixture[str]
) -> None:
    """Another major is another artifact, built with its own feature."""
    planted = plant()
    status = inventory_plan.main(["crate", "18"])
    run = planted.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout + run.stderr
    assert GOLDEN.replace("16", "18") == run.plan


# One row per refusal, each message asserted; nothing is written.
def assert_refused(run: Run, want: str) -> None:
    """Hold one refusal to its status, its line, and no plan."""
    assert run.returncode == 1, run.stderr
    assert f"::error::inventory-plan: {want}\n" == run.stderr
    assert run.plan is None


def test_no_prefix_is_refused(plant: Plant, capfd: pytest.CaptureFixture[str]) -> None:
    """The class's prefix names the document; the leg never invents one."""
    planted = plant(prefix=None)
    status = inventory_plan.main(["crate", "16"])
    run = planted.ran(status, capfd.readouterr())
    assert_refused(
        run,
        "PLAN_DOC_PREFIX is unset — the class's planned prefix names the document",
    )


def test_a_major_that_is_not_a_number_is_refused(
    plant: Plant, capfd: pytest.CaptureFixture[str]
) -> None:
    """A matrix value that is not a Postgres major."""
    planted = plant()
    status = inventory_plan.main(["crate", "pg16"])
    run = planted.ran(status, capfd.readouterr())
    assert_refused(run, "major 'pg16' is not a Postgres major")


def test_no_manifest_is_refused(
    plant: Plant, capfd: pytest.CaptureFixture[str]
) -> None:
    """The crate directory holds no Cargo.toml."""
    planted = plant(manifest=None)
    status = inventory_plan.main(["crate", "16"])
    run = planted.ran(status, capfd.readouterr())
    assert run.returncode == 1
    assert run.stderr.startswith(MANIFEST_REFUSAL), run.stderr
    assert run.plan is None


def test_a_manifest_that_is_not_toml_is_refused(
    plant: Plant, capfd: pytest.CaptureFixture[str]
) -> None:
    """A manifest the reader cannot parse."""
    planted = plant(manifest="[package\n")
    status = inventory_plan.main(["crate", "16"])
    run = planted.ran(status, capfd.readouterr())
    assert run.returncode == 1
    assert run.stderr.startswith(MANIFEST_REFUSAL), run.stderr
    assert run.plan is None


def test_a_workspace_manifest_is_refused(
    plant: Plant, capfd: pytest.CaptureFixture[str]
) -> None:
    """A manifest with no [package] names no crate this cell built."""
    planted = plant(manifest="[workspace]\n")
    status = inventory_plan.main(["crate", "16"])
    run = planted.ran(status, capfd.readouterr())
    assert_refused(
        run,
        "crate/Cargo.toml names no package — a cell plans the crate it built",
    )


def test_a_nameless_package_is_refused(
    plant: Plant, capfd: pytest.CaptureFixture[str]
) -> None:
    """A [package] table with no name."""
    planted = plant(manifest='[package]\nversion = "1.0.0"\n')
    status = inventory_plan.main(["crate", "16"])
    run = planted.ran(status, capfd.readouterr())
    assert_refused(
        run,
        "crate/Cargo.toml names no package — a cell plans the crate it built",
    )


def test_a_package_that_is_not_a_table_is_refused(
    plant: Plant, capfd: pytest.CaptureFixture[str]
) -> None:
    """`package = false` (#1556's shrunk example): an AttributeError before."""
    planted = plant(manifest="package = false\n")
    status = inventory_plan.main(["crate", "16"])
    run = planted.ran(status, capfd.readouterr())
    assert_refused(
        run,
        "crate/Cargo.toml names no package — a cell plans the crate it built",
    )


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param(
            ["crate"],
            "belt inventory-plan: error: the following arguments are required: MAJOR",
            id='["crate"]',
        ),
    ],
)
def test_a_wrong_argument_count_is_usage(
    argv: list[str], error: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """Two positional arguments, never fewer: argparse's exit 2 and its error."""
    with pytest.raises(SystemExit) as ended:
        inventory_plan.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt inventory-plan "), err
    assert err.splitlines()[-1] == error


# The workflow passes the caller's crate dir after `--`: a value that reads
# as an option is a crate dir, refused by name, never argparse's help.
def test_a_crate_dir_that_reads_as_an_option_is_refused(
    plant: Plant, capfd: pytest.CaptureFixture[str]
) -> None:
    """`-h` after `--`: exit 1 naming it, no plan and no help."""
    planted = plant()
    status = inventory_plan.main(["--", "-h", "16"])
    run = planted.ran(status, capfd.readouterr())
    assert (run.returncode, run.stdout) == (1, "")
    assert run.stderr == (
        "::error::inventory-plan: -h/Cargo.toml: [Errno 2] No such file or"
        " directory: '-h/Cargo.toml'\n"
    )
