#!/usr/bin/env python3
"""Run release/pgrx/inventory_plan.py, the pgrx-extension plan step.

#1371 moved the step "Emit the inventory plan (what this cell built,
from its own inputs)" out of build-pgrx-extension.yml into the helper.
A build leg carries no belt, so the step runs the helper from the canon
tree, as the cell's other steps run release/pgrx/*.

WHAT RUNS. The helper itself, by its path in this tree, in a scratch
directory holding a planted crate. The golden is the step's own jq
output for crate `pg_edtf` and major 16, recorded byte for byte when the
step was still shell (the same bytes stele#396 holds as its producer
golden), so a passing row is the helper writing what the leg wrote.

stdlib `unittest`, matching the rest of release/. Run through the gate
as `mise run test`, which `ci` collects.
"""

import contextlib
import os
import subprocess
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import decoded
import hermetic
import plan_producers
import workflow_yaml

CANON = Path(__file__).resolve().parent.parent
WORKFLOW = CANON / ".github" / "workflows" / "build-pgrx-extension.yml"
HELPER = CANON / "release" / "pgrx" / "inventory_plan.py"
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

MANIFEST_REFUSAL = "::error::inventory_plan.py: crate/Cargo.toml: "

CRATE = '[package]\nname = "pg_edtf"\nversion = "0.4.2"\n\n[features]\npg16 = []\n'


_NAMED = contextlib.ExitStack()


def setUpModule() -> None:
    """Name the helper by its file, as a run outside mise does (#1934).

    An in-process call reads argv, whatever task runs this suite.
    """
    _NAMED.enter_context(mock.patch.object(sys, "argv", ["inventory_plan.py"]))
    for key in ("MISE_TASK_NAME", "ORG_FINDINGS"):
        if key in os.environ:
            _NAMED.callback(os.environ.__setitem__, key, os.environ.pop(key))
    # Empty, not unset: unset on a runner records to the job's file (#1976).
    os.environ["ORG_FINDINGS"] = ""
    _NAMED.callback(os.environ.pop, "ORG_FINDINGS", None)
    unittest.addModuleCleanup(_NAMED.close)


@dataclass
class Run:
    """What the helper did."""

    returncode: int
    stdout: str
    stderr: str
    plan: str | None


def run_helper(
    manifest: str | None = CRATE,
    args: tuple[str, ...] = ("crate", "16"),
    prefix: str | None = "sbom-pgrx-",
) -> Run:
    """Run the helper over a planted crate (manifest None plants none).

    Returns:
        Its status, output, and plan/plan.json when one was written.

    """
    with TemporaryDirectory() as d:
        work = Path(d)
        (work / "crate").mkdir()
        if manifest is not None:
            (work / "crate" / "Cargo.toml").write_text(manifest)
        env = hermetic.env()
        if prefix is not None:
            env["PLAN_DOC_PREFIX"] = prefix
        result = subprocess.run(
            [str(HELPER), *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            cwd=work,
        )
        out = work / "plan" / "plan.json"
        return Run(
            result.returncode,
            result.stdout,
            result.stderr,
            out.read_text("utf-8") if out.exists() else None,
        )


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


class PlanStepIsTheHelper(unittest.TestCase):
    """The cell runs the helper these rows run, and the join can see it."""

    def test_the_shipped_step_calls_the_helper(self) -> None:
        """The run: is the call alone, from the canon tree."""
        self.assertEqual(
            '"${ORG_PYTHON}" .org-canon/release/pgrx/inventory_plan.py'
            ' "${UNTRUSTED_CRATE_DIR}" "${PG}"\n',
            step()["run"],
        )

    def test_the_step_states_its_prefix_and_its_marker(self) -> None:
        """mise/plan_producers.py joins the marker to PLAN_DOC_PREFIX."""
        declared, _ = plan_producers.markers(WORKFLOW.read_text("utf-8"))
        self.assertIn(("pgrx-extension", "sbom-pgrx-"), declared)
        env = decoded.as_object(step()["env"], "env")
        self.assertEqual("sbom-pgrx-", env["PLAN_DOC_PREFIX"])


class Writes(unittest.TestCase):
    """The plan the step's jq wrote, byte for byte."""

    def test_the_cell_plan_is_the_shells_bytes(self) -> None:
        """Crate pg_edtf, major 16: the recorded golden."""
        run = run_helper()
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual(GOLDEN, run.plan)
        self.assertEqual(
            "::notice::plans inventory sbom-pgrx-pg_edtf-pg16 for package pg_edtf\n",
            run.stdout,
        )

    def test_the_major_names_the_artifact_and_the_feature(self) -> None:
        """Another major is another artifact, built with its own feature."""
        run = run_helper(args=("crate", "18"))
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual(GOLDEN.replace("16", "18"), run.plan)


class Refuses(unittest.TestCase):
    """One row per refusal, each message asserted; nothing is written."""

    def assert_refused(self, run: Run, want: str) -> None:
        """Hold one refusal to its status, its line, and no plan."""
        self.assertEqual(1, run.returncode, run.stderr)
        self.assertEqual(f"::error::inventory_plan.py: {want}\n", run.stderr)
        self.assertIsNone(run.plan)

    def test_no_prefix_is_refused(self) -> None:
        """The class's prefix names the document; the leg never invents one."""
        self.assert_refused(
            run_helper(prefix=None),
            "PLAN_DOC_PREFIX is unset — the class's planned prefix names the document",
        )

    def test_a_major_that_is_not_a_number_is_refused(self) -> None:
        """A matrix value that is not a Postgres major."""
        self.assert_refused(
            run_helper(args=("crate", "pg16")), "major 'pg16' is not a Postgres major"
        )

    def test_no_manifest_is_refused(self) -> None:
        """The crate directory holds no Cargo.toml."""
        run = run_helper(manifest=None)
        self.assertEqual(1, run.returncode)
        self.assertTrue(run.stderr.startswith(MANIFEST_REFUSAL), run.stderr)
        self.assertIsNone(run.plan)

    def test_a_manifest_that_is_not_toml_is_refused(self) -> None:
        """A manifest the reader cannot parse."""
        run = run_helper(manifest="[package\n")
        self.assertEqual(1, run.returncode)
        self.assertTrue(run.stderr.startswith(MANIFEST_REFUSAL), run.stderr)
        self.assertIsNone(run.plan)

    def test_a_workspace_manifest_is_refused(self) -> None:
        """A manifest with no [package] names no crate this cell built."""
        self.assert_refused(
            run_helper(manifest="[workspace]\n"),
            "crate/Cargo.toml names no package — a cell plans the crate it built",
        )

    def test_a_nameless_package_is_refused(self) -> None:
        """A [package] table with no name."""
        self.assert_refused(
            run_helper(manifest='[package]\nversion = "1.0.0"\n'),
            "crate/Cargo.toml names no package — a cell plans the crate it built",
        )

    def test_a_package_that_is_not_a_table_is_refused(self) -> None:
        """`package = false` (#1556's shrunk example): an AttributeError before."""
        self.assert_refused(
            run_helper(manifest="package = false\n"),
            "crate/Cargo.toml names no package — a cell plans the crate it built",
        )

    def test_a_wrong_argument_count_is_usage(self) -> None:
        """Two positional arguments, never fewer."""
        run = run_helper(args=("crate",))
        self.assertEqual(2, run.returncode)
        self.assertEqual(
            "inventory_plan.py: usage: inventory_plan.py CRATE_DIR MAJOR\n", run.stderr
        )


if __name__ == "__main__":
    unittest.main()
