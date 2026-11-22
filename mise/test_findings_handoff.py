#!/usr/bin/env python3
"""Every job hands its findings to the report job (#1933).

A finding's writer names its own records file on a runner, under
`${RUNNER_TEMP}/findings/` (docs/findings.md, #1976), whatever ran it:
`mise run`, a workflow step, a script run bare. Jobs of one run share
nothing unless the workflow hands it on, so in every workflow whose
`report` job runs mise/standing_issue.py:

- every job with `steps` that does not run the helper ends with the
  upload of `${{ runner.temp }}/findings/`, as `findings-<job>-<index>`,
  unless the run was cancelled (#1976). Which jobs write records is
  not read from their `run:` text: every job is in the set, so no way
  of writing a record falls outside it. A job that calls a reusable
  workflow has no steps and is not in the set;
- the report job downloads every `findings-*` artifact into one
  directory before the helper runs, and passes it as `--findings`.

The set is computed from the files, never listed. A planted workflow
missing either half is red by message, naming the workflow and the job.

stdlib `unittest`, run through the gate as `mise run test`.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import decoded
import workflow_yaml

CANON = Path(__file__).resolve().parent.parent
WORKFLOWS = Path(".github") / "workflows"
HELPER = "mise/standing_issue.py"
UPLOAD = "actions/upload-artifact@"
DOWNLOAD = "actions/download-artifact@"
NOT_CANCELLED = "${{ !cancelled() }}"
NAME = "findings-${{ github.job }}-${{ strategy.job-index }}"
UPLOAD_PATH = "${{ runner.temp }}/findings/"
PATTERN = "findings-*"
DOWNLOAD_PATH = "${{ runner.temp }}/findings"
FLAG = '--findings "${RUNNER_TEMP}/findings"'
NOT_LAST = "the findings upload is not the job's last step"

Step = dict[str, object]


def same[T](case: unittest.TestCase, got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    case.assertEqual(got, want, msg or None)


def run_text(step: Step) -> str:
    """Read a step's `run:` text.

    Returns:
        The text, or empty for a step that runs no command.

    """
    run = step.get("run")
    return run if isinstance(run, str) else ""


def uses(step: Step, action: str) -> bool:
    """Say whether a step uses an action, at any pin.

    Returns:
        True when its `uses:` names the action.

    """
    used = step.get("uses")
    return isinstance(used, str) and used.startswith(action)


def given(step: Step) -> dict[str, object]:
    """Read a step's `with:` inputs.

    Returns:
        The mapping, empty without one.

    """
    return decoded.as_object(step.get("with") or {}, "with")


def population(root: Path) -> list[Path]:
    """Compute the workflows whose report job runs the helper.

    Returns:
        Each workflow file under the tree, in name order.

    """
    found = []
    for path in sorted((root / WORKFLOWS).glob("*.yml")):
        jobs = workflow_yaml.jobs(workflow_yaml.document(path))
        report = jobs.get("report")
        if report is None:
            continue
        if any(HELPER in run_text(s) for s in workflow_yaml.steps(report)):
            found.append(path)
    return found


def upload_problems(where: str, steps: list[Step]) -> list[str]:
    """Check a job ends by handing on its findings.

    The upload is the step that uploads under the findings name or
    from the findings path, so one whose name or path differs is still
    found, and said.

    Returns:
        One message per way the job is not as built; empty when it is.

    """
    uploads = [
        i
        for i, s in enumerate(steps)
        if uses(s, UPLOAD)
        and (given(s).get("name") == NAME or given(s).get("path") == UPLOAD_PATH)
    ]
    if not uploads:
        return [f"{where}: uploads no {NAME}"]
    if uploads[-1] != len(steps) - 1:
        return [f"{where}: {NOT_LAST}"]
    step = steps[uploads[-1]]
    problems = []
    if given(step).get("name") != NAME:
        problems.append(f"{where}: the findings upload's name is not {NAME}")
    if given(step).get("path") != UPLOAD_PATH:
        problems.append(f"{where}: the findings upload's path is not {UPLOAD_PATH}")
    if step.get("if") != NOT_CANCELLED:
        problems.append(f"{where}: the findings upload is not if: {NOT_CANCELLED}")
    return problems


def download_problems(where: str, steps: list[Step]) -> list[str]:
    """Check the report job collects every job's findings and reads them.

    Returns:
        One message per way the job is not as built; empty when it is.

    """
    helper = next(i for i, s in enumerate(steps) if HELPER in run_text(s))
    downloads = [
        i
        for i, s in enumerate(steps[:helper])
        if uses(s, DOWNLOAD) and given(s).get("pattern") == PATTERN
    ]
    problems = []
    if not downloads:
        problems.append(f"{where}: downloads no {PATTERN} before {HELPER}")
    else:
        inputs = given(steps[downloads[-1]])
        if inputs.get("merge-multiple") is not True:
            problems.append(f"{where}: the findings download does not merge-multiple")
        if inputs.get("path") != DOWNLOAD_PATH:
            problems.append(
                f"{where}: the findings download's path is not {DOWNLOAD_PATH}"
            )
    if FLAG not in run_text(steps[helper]):
        problems.append(f"{where}: {HELPER} is not passed {FLAG}")
    return problems


def problems(root: Path) -> list[str]:
    """Check every workflow whose report job runs the helper.

    Returns:
        Every message, in file and job order.

    """
    found: list[str] = []
    for path in population(root):
        for name, job in workflow_yaml.jobs(workflow_yaml.document(path)).items():
            steps = workflow_yaml.steps(job)
            where = f"{path.name}: {name}"
            if name == "report":
                found += download_problems(where, steps)
            elif steps and not any(HELPER in run_text(s) for s in steps):
                found += upload_problems(where, steps)
    return found


UPLOAD_STEP = """\
      - if: ${{ !cancelled() }}
        uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9
        with:
          name: findings-${{ github.job }}-${{ strategy.job-index }}
          path: ${{ runner.temp }}/findings/
          if-no-files-found: ignore
          retention-days: 1
"""

DOWNLOAD_STEP = """\
      - if: ${{ !cancelled() }}
        uses: actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333
        with:
          pattern: findings-*
          merge-multiple: true
          path: ${{ runner.temp }}/findings
"""

CHECK = """\
      - run: mise run audit:thing
"""

# A step that records through a writer outside `mise run`.
PLAIN = """\
      - run: .org-canon/settings/repo-baseline.sh check
"""

HELPER_STEP = """\
      - if: ${{ !cancelled() }}
        run: .org-canon/mise/standing_issue.py --findings "${RUNNER_TEMP}/findings"
"""

BARE_HELPER_STEP = """\
      - if: ${{ !cancelled() }}
        run: .org-canon/mise/standing_issue.py
"""

PLANTED = """\
---
name: planted
"on":
  schedule:
    - cron: 0 6 * * 1
jobs:
  check:
    runs-on: ubuntu-26.04
    steps:
{check}  called:
    uses: ./.github/workflows/callee.yml
  report:
    needs: [check, called]
    if: ${{{{ always() }}}}
    runs-on: ubuntu-26.04
    steps:
{report}"""


class Canon(unittest.TestCase):
    """The canon's own workflows."""

    def test_every_job_hands_its_findings_on(self) -> None:
        """Every job uploads as its last step; every report reads them."""
        same(self, problems(CANON), [])

    def test_the_set_is_the_reporting_workflows(self) -> None:
        """The computation reaches a direct workflow, a callee and a stub."""
        names = {p.name for p in population(CANON)}
        self.assertIn("slsa.yml", names)
        self.assertIn("repo-mutants.yml", names)
        self.assertIn("self-records.yml", names)
        self.assertNotIn("gate.yml", names)


class Planted(unittest.TestCase):
    """A planted reporting workflow, red by message."""

    @staticmethod
    def plant(check: str, report: str) -> list[str]:
        """Write a planted workflow and check its tree.

        Returns:
            The messages.

        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / WORKFLOWS).mkdir(parents=True)
            (root / WORKFLOWS / "planted.yml").write_text(
                PLANTED.format(check=check, report=report), encoding="utf-8"
            )
            return problems(root)

    def test_as_built_passes(self) -> None:
        """An upload after the check and a download the helper reads pass."""
        same(self, self.plant(CHECK + UPLOAD_STEP, DOWNLOAD_STEP + HELPER_STEP), [])

    def test_a_job_with_no_upload(self) -> None:
        """A job that runs `mise run` and uploads nothing is red by name."""
        same(
            self,
            self.plant(CHECK, DOWNLOAD_STEP + HELPER_STEP),
            [f"planted.yml: check: uploads no {NAME}"],
        )

    def test_a_plain_run_job_with_no_upload(self) -> None:
        """A job that records only through a plain `run:` step is in the set."""
        same(
            self,
            self.plant(PLAIN, DOWNLOAD_STEP + HELPER_STEP),
            [f"planted.yml: check: uploads no {NAME}"],
        )

    def test_a_job_with_no_steps_is_not_in_the_set(self) -> None:
        """A job that calls a reusable workflow uploads nothing and passes."""
        same(self, self.plant(PLAIN + UPLOAD_STEP, DOWNLOAD_STEP + HELPER_STEP), [])

    def test_an_upload_that_is_not_last(self) -> None:
        """An upload with a step after it is red by name."""
        same(
            self,
            self.plant(PLAIN + UPLOAD_STEP + PLAIN, DOWNLOAD_STEP + HELPER_STEP),
            [f"planted.yml: check: {NOT_LAST}"],
        )

    def test_an_upload_under_another_name(self) -> None:
        """An upload of the findings path under another name is red by name."""
        renamed = UPLOAD_STEP.replace(NAME, "findings-${{ github.job }}")
        same(
            self,
            self.plant(PLAIN + renamed, DOWNLOAD_STEP + HELPER_STEP),
            [f"planted.yml: check: the findings upload's name is not {NAME}"],
        )

    def test_an_upload_from_another_path(self) -> None:
        """An upload under the findings name from another path is red by name."""
        moved = UPLOAD_STEP.replace(UPLOAD_PATH, "${{ runner.temp }}/out/")
        same(
            self,
            self.plant(PLAIN + moved, DOWNLOAD_STEP + HELPER_STEP),
            [f"planted.yml: check: the findings upload's path is not {UPLOAD_PATH}"],
        )

    def test_an_upload_that_runs_only_on_success(self) -> None:
        """An upload a failed check would skip is red by name."""
        skipped = UPLOAD_STEP.replace(NOT_CANCELLED, "${{ success() }}")
        same(
            self,
            self.plant(CHECK + skipped, DOWNLOAD_STEP + HELPER_STEP),
            [f"planted.yml: check: the findings upload is not if: {NOT_CANCELLED}"],
        )

    def test_a_report_with_no_download(self) -> None:
        """A report job that downloads nothing is red by name."""
        same(
            self,
            self.plant(CHECK + UPLOAD_STEP, HELPER_STEP),
            [f"planted.yml: report: downloads no {PATTERN} before {HELPER}"],
        )

    def test_a_download_after_the_helper(self) -> None:
        """A download the helper runs before is no download."""
        same(
            self,
            self.plant(CHECK + UPLOAD_STEP, HELPER_STEP + DOWNLOAD_STEP),
            [f"planted.yml: report: downloads no {PATTERN} before {HELPER}"],
        )

    def test_a_report_that_does_not_pass_the_flag(self) -> None:
        """A report job whose helper is not passed `--findings` is red by name."""
        same(
            self,
            self.plant(CHECK + UPLOAD_STEP, DOWNLOAD_STEP + BARE_HELPER_STEP),
            [f"planted.yml: report: {HELPER} is not passed {FLAG}"],
        )


if __name__ == "__main__":
    unittest.main()
