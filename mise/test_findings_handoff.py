#!/usr/bin/env python3
"""Every job hands its findings to the report job (#1933).

A belt check appends its findings to the file ORG_FINDINGS names
(docs/findings.md), and the belt's `[env]` names that file on every
`mise run` a runner makes.
Jobs of one run share nothing unless the workflow hands it on, so in
every workflow whose `report` job runs mise/standing_issue.py:

- every job with a step that runs `mise run` uploads
  `${{ runner.temp }}/findings/` after its last such step, as
  `findings-<job>-<index>`, unless the run was cancelled;
- the report job downloads every `findings-*` artifact into one
  directory before the helper runs, and passes it as `--findings`.

The set is computed from the files, never listed. A planted workflow
missing either half is red by message, naming the workflow and the job.
The item-1 rows run `mise run` under the belt and read what it named.

stdlib `unittest`, run through the gate as `mise run test`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic
import workflow_yaml

CANON = Path(__file__).resolve().parent.parent
BELT = CANON / "mise" / "config.toml"
WORKFLOWS = Path(".github") / "workflows"
HELPER = "mise/standing_issue.py"
MISE_RUN = re.compile(r"\bmise run\b")
UPLOAD = "actions/upload-artifact@"
DOWNLOAD = "actions/download-artifact@"
NOT_CANCELLED = "${{ !cancelled() }}"
NAME = "findings-${{ github.job }}-${{ strategy.job-index }}"
UPLOAD_PATH = "${{ runner.temp }}/findings/"
PATTERN = "findings-*"
DOWNLOAD_PATH = "${{ runner.temp }}/findings"
FLAG = '--findings "${RUNNER_TEMP}/findings"'
EARLY = "uploads its findings before its last `mise run` step"

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
    """Check a job that runs `mise run` hands on its findings.

    Returns:
        One message per way the job is not as built; empty when it is.

    """
    last = max(i for i, s in enumerate(steps) if MISE_RUN.search(run_text(s)))
    uploads = [
        i
        for i, s in enumerate(steps)
        if uses(s, UPLOAD) and given(s).get("name") == NAME
    ]
    if not uploads:
        return [f"{where}: runs `mise run` and uploads no {NAME}"]
    if all(i < last for i in uploads):
        return [f"{where}: {EARLY}"]
    step = steps[next(i for i in uploads if i > last)]
    problems = []
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
            elif any(MISE_RUN.search(run_text(s)) for s in steps):
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
{check}  quiet:
    runs-on: ubuntu-26.04
    steps:
      - run: "true"
  report:
    needs: [check, quiet]
    if: ${{{{ always() }}}}
    runs-on: ubuntu-26.04
    steps:
{report}"""


class Canon(unittest.TestCase):
    """The canon's own workflows."""

    def test_every_job_hands_its_findings_on(self) -> None:
        """Every job that runs `mise run` uploads; every report reads them."""
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
            [f"planted.yml: check: runs `mise run` and uploads no {NAME}"],
        )

    def test_an_upload_before_the_last_check(self) -> None:
        """An upload placed before the last `mise run` step is red by name."""
        same(
            self,
            self.plant(CHECK + UPLOAD_STEP + CHECK, DOWNLOAD_STEP + HELPER_STEP),
            [f"planted.yml: check: {EARLY}"],
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


SHOW = """\
[tasks.show]
run = 'printf "%s\\n" "${ORG_FINDINGS-<unset>}"'
"""


class NameTheFile(unittest.TestCase):
    """The belt's `[env]` names the job's findings file, run as a job runs it."""

    @override
    def setUp(self) -> None:
        """Plant a tree with one task that prints ORG_FINDINGS."""
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.tree = self.tmp / "tree"
        self.tree.mkdir()
        (self.tree / "mise.toml").write_text(SHOW, encoding="utf-8")
        self.config = self.tmp / "config"
        self.config.mkdir()
        self.temp = self.tmp / "runner-temp"
        self.temp.mkdir()

    def named(self, **given: str) -> str:
        """Run `mise run show` with the belt as the global config.

        Returns:
            What the task printed for ORG_FINDINGS.

        """
        done = subprocess.run(
            [hermetic.tool("mise"), "run", "show"],
            cwd=self.tree,
            env=hermetic.env(
                MISE_CONFIG_DIR=str(self.config),
                XDG_CONFIG_HOME=str(self.config),
                MISE_GLOBAL_CONFIG_FILE=str(BELT),
                MISE_TRUSTED_CONFIG_PATHS=f"{self.tmp}:{CANON}",
                MISE_OFFLINE="1",
                MISE_YES="1",
                MISE_AUTO_INSTALL="0",
                MISE_TASK_RUN_AUTO_INSTALL="0",
                **given,
            ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        same(self, done.returncode, 0, done.stdout + done.stderr)
        return done.stdout.removesuffix("\n")

    def on_a_runner(self) -> str:
        """Run the task as a matrix leg of the job `mutants-rust` does.

        Returns:
            The file the belt named.

        """
        return self.named(RUNNER_TEMP=str(self.temp), GITHUB_JOB="mutants-rust")

    def test_the_file_is_the_jobs_own_under_the_runner_temp(self) -> None:
        """The path is under findings/, named for the job, and ends .jsonl."""
        path = Path(self.on_a_runner())
        same(self, path.parent, self.temp / "findings")
        self.assertRegex(path.name, r"^mutants-rust-[0-9a-f]{16}\.jsonl$")
        self.assertFalse(path.exists(), "the file is a writer's to create")

    def test_two_runs_name_two_files(self) -> None:
        """Two legs of one matrix share GITHUB_JOB and never a file."""
        self.assertNotEqual(self.on_a_runner(), self.on_a_runner())

    def test_off_a_runner_it_is_empty(self) -> None:
        """No RUNNER_TEMP: empty, so a writer prints and records nothing."""
        same(self, self.named(), "")

    def test_a_value_the_caller_set_is_kept(self) -> None:
        """A caller's own ORG_FINDINGS wins, on a runner or off one."""
        mine = str(self.tmp / "mine.jsonl")
        same(
            self,
            self.named(
                ORG_FINDINGS=mine, RUNNER_TEMP=str(self.temp), GITHUB_JOB="badges"
            ),
            mine,
        )


if __name__ == "__main__":
    unittest.main()
