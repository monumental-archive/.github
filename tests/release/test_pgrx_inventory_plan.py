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


@pytest.fixture
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


class Helper(Protocol):
    """The `run_helper` fixture's caller."""

    def __call__(
        self,
        manifest: str | None = CRATE,
        args: tuple[str, ...] = ("crate", "16"),
        prefix: str | None = "sbom-pgrx-",
    ) -> Run:
        """Run the helper over a planted crate (manifest None plants none)."""
        ...


@pytest.fixture
def run_helper(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> Helper:
    """Call the helper's `main` in process, over a crate it plants.

    Returns:
        The caller, giving its status, output, and plan/plan.json when
        one was written.

    """

    def call(
        manifest: str | None = CRATE,
        args: tuple[str, ...] = ("crate", "16"),
        prefix: str | None = "sbom-pgrx-",
    ) -> Run:
        work = Path(tempfile.mkdtemp(dir=tmp_path))
        (work / "crate").mkdir()
        if manifest is not None:
            (work / "crate" / "Cargo.toml").write_text(manifest)
        with pytest.MonkeyPatch.context() as patched:
            if prefix is None:
                patched.delenv("PLAN_DOC_PREFIX", raising=False)
            else:
                patched.setenv("PLAN_DOC_PREFIX", prefix)
            patched.chdir(work)
            status = inventory_plan.main(list(args))
        out, err = capfd.readouterr()
        plan = work / "plan" / "plan.json"
        return Run(status, out, err, plan.read_text("utf-8") if plan.exists() else None)

    return call


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


class TestPlanStepIsTheHelper:
    """The cell runs the helper these rows run, and the join can see it."""

    def test_the_shipped_step_calls_the_helper(self) -> None:
        """The run: is the call alone, from the canon tree."""
        assert step()["run"] == (
            '"${ORG_PYTHON}" -I .org-canon/belt inventory-plan'
            ' "${UNTRUSTED_CRATE_DIR}" "${PG}"\n'
        )

    def test_the_step_states_its_prefix_and_its_marker(self) -> None:
        """mise/plan_producers.py joins the marker to PLAN_DOC_PREFIX."""
        declared, _ = plan_producers.markers(WORKFLOW.read_text("utf-8"))
        assert ("pgrx-extension", "sbom-pgrx-") in declared
        env = decoded.as_object(step()["env"], "env")
        assert env["PLAN_DOC_PREFIX"] == "sbom-pgrx-"


class TestWrites:
    """The plan the step's jq wrote, byte for byte."""

    def test_the_cell_plan_is_the_shells_bytes(self, run_helper: Helper) -> None:
        """Crate pg_edtf, major 16: the recorded golden."""
        run = run_helper()
        assert run.returncode == 0, run.stdout + run.stderr
        assert run.plan == GOLDEN
        assert (
            run.stdout
            == "::notice::plans inventory sbom-pgrx-pg_edtf-pg16 for package pg_edtf\n"
        )

    def test_the_major_names_the_artifact_and_the_feature(
        self, run_helper: Helper
    ) -> None:
        """Another major is another artifact, built with its own feature."""
        run = run_helper(args=("crate", "18"))
        assert run.returncode == 0, run.stdout + run.stderr
        assert GOLDEN.replace("16", "18") == run.plan


class TestRefuses:
    """One row per refusal, each message asserted; nothing is written."""

    def assert_refused(self, run: Run, want: str) -> None:
        """Hold one refusal to its status, its line, and no plan."""
        assert run.returncode == 1, run.stderr
        assert f"::error::inventory-plan: {want}\n" == run.stderr
        assert run.plan is None

    def test_no_prefix_is_refused(self, run_helper: Helper) -> None:
        """The class's prefix names the document; the leg never invents one."""
        self.assert_refused(
            run_helper(prefix=None),
            "PLAN_DOC_PREFIX is unset — the class's planned prefix names the document",
        )

    def test_a_major_that_is_not_a_number_is_refused(self, run_helper: Helper) -> None:
        """A matrix value that is not a Postgres major."""
        self.assert_refused(
            run_helper(args=("crate", "pg16")), "major 'pg16' is not a Postgres major"
        )

    def test_no_manifest_is_refused(self, run_helper: Helper) -> None:
        """The crate directory holds no Cargo.toml."""
        run = run_helper(manifest=None)
        assert run.returncode == 1
        assert run.stderr.startswith(MANIFEST_REFUSAL), run.stderr
        assert run.plan is None

    def test_a_manifest_that_is_not_toml_is_refused(self, run_helper: Helper) -> None:
        """A manifest the reader cannot parse."""
        run = run_helper(manifest="[package\n")
        assert run.returncode == 1
        assert run.stderr.startswith(MANIFEST_REFUSAL), run.stderr
        assert run.plan is None

    def test_a_workspace_manifest_is_refused(self, run_helper: Helper) -> None:
        """A manifest with no [package] names no crate this cell built."""
        self.assert_refused(
            run_helper(manifest="[workspace]\n"),
            "crate/Cargo.toml names no package — a cell plans the crate it built",
        )

    def test_a_nameless_package_is_refused(self, run_helper: Helper) -> None:
        """A [package] table with no name."""
        self.assert_refused(
            run_helper(manifest='[package]\nversion = "1.0.0"\n'),
            "crate/Cargo.toml names no package — a cell plans the crate it built",
        )

    def test_a_package_that_is_not_a_table_is_refused(self, run_helper: Helper) -> None:
        """`package = false` (#1556's shrunk example): an AttributeError before."""
        self.assert_refused(
            run_helper(manifest="package = false\n"),
            "crate/Cargo.toml names no package — a cell plans the crate it built",
        )

    def test_a_wrong_argument_count_is_usage(self, run_helper: Helper) -> None:
        """Two positional arguments, never fewer."""
        run = run_helper(args=("crate",))
        assert run.returncode == 2
        assert (
            run.stderr == "inventory-plan: usage: belt inventory-plan CRATE_DIR MAJOR\n"
        )
