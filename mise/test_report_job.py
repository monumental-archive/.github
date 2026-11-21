#!/usr/bin/env python3
"""Every scheduled workflow ends with a `report` job (#1887).

The set is computed from the files, never listed: every workflow in
.github/workflows/ with a `schedule:` trigger, and every reusable
workflow that a scheduled workflow calls, from .github/workflows/
(`./.github/workflows/<file>`) or from a stub in workflow-templates/
(`<owner>/.github/.github/workflows/<file>@<sha>`). A scheduled
workflow whose every job is such a call is a stub: the job lives in
the workflow it calls. One with a job of its own carries the job
itself, so a stub that also has one (self-records.yml) holds both.

Each one's last job is `report`, its `needs:` names every other job,
it runs `if: ${{ always() }}` and holds the two grants. Each scheduled
stub grants the same two at its calling job: in a reusable workflow
the caller's grant is the ceiling. A planted workflow with no `report`
job, and one whose `needs:` misses a job, are each red by message.

stdlib `unittest`, run through the gate as `mise run test`.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

import decoded
import workflow_yaml

CANON = Path(__file__).resolve().parent.parent
WORKFLOWS = Path(".github") / "workflows"
TEMPLATES = Path("workflow-templates")
LOCAL = re.compile(r"^\./\.github/workflows/([^/@]+)$")
REMOTE = re.compile(r"^[^/]+/\.github/\.github/workflows/([^/@]+)@")
GRANTS = {"issues": "write", "actions": "read"}
ALWAYS = "${{ always() }}"
NOT_CANCELLED = "${{ !cancelled() }}"
HELPER = "mise/standing_issue.py"

Mapping = dict[str, object]


def scheduled(document: Mapping) -> bool:
    """Say whether a workflow has a `schedule:` trigger.

    Returns:
        True when `on:` names `schedule`.

    """
    on = document.get("on")
    if isinstance(on, str):
        return on == "schedule"
    if decoded.is_array(on):
        return "schedule" in on
    return decoded.is_object(on) and "schedule" in on


def callee(job: Mapping) -> str | None:
    """Name the canon workflow a job calls, if it calls one.

    Returns:
        The file name under .github/workflows/, or None.

    """
    uses = job.get("uses")
    if not isinstance(uses, str):
        return None
    match = LOCAL.match(uses) or REMOTE.match(uses)
    return match.group(1) if match else None


def population(root: Path) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Compute the workflows that must report, and the stubs that must grant.

    Returns:
        The workflows that end with `report`, and each (stub, job) that
        calls one of them on a schedule.

    """
    targets: set[Path] = set()
    stubs: list[tuple[Path, str]] = []
    files = sorted((root / WORKFLOWS).glob("*.yml")) + sorted(
        (root / TEMPLATES).glob("*.yml")
    )
    for path in files:
        document = workflow_yaml.document(path)
        if not scheduled(document):
            continue
        own = False
        for name, job in workflow_yaml.jobs(document).items():
            called = callee(job)
            if called is None:
                own = True
                continue
            targets.add(root / WORKFLOWS / called)
            stubs.append((path, name))
        if own and path.parent == root / WORKFLOWS:
            targets.add(path)
    return sorted(targets), stubs


def report_problems(path: Path) -> list[str]:
    """Check one workflow's `report` job.

    Returns:
        One message per way the job is not as built; empty when it is.

    """
    jobs = workflow_yaml.jobs(workflow_yaml.document(path))
    names = list(jobs)
    if not names or names[-1] != "report":
        return [
            f"{path.name}: the last job is not `report` (it is {names[-1:] or 'none'})"
        ]
    job = jobs["report"]
    problems: list[str] = []
    needs = job.get("needs")
    listed = (
        [needs]
        if isinstance(needs, str)
        else list(decoded.as_array(needs or [], "needs"))
    )
    missing = [n for n in names[:-1] if n not in listed]
    if missing:
        problems.append(f"{path.name}: report's needs: misses {', '.join(missing)}")
    if job.get("if") != ALWAYS:
        problems.append(f"{path.name}: report runs if: {job.get('if')!r}, not {ALWAYS}")
    helper = [s for s in workflow_yaml.steps(job) if HELPER in str(s.get("run", ""))]
    if not helper:
        problems.append(f"{path.name}: report runs no {HELPER}")
    elif any(s.get("if") != NOT_CANCELLED for s in helper):
        problems.append(
            f"{path.name}: report's {HELPER} step is not if: {NOT_CANCELLED}"
        )
    problems += grant_problems(path, "report", job)
    return problems


def grant_problems(path: Path, name: str, job: Mapping) -> list[str]:
    """Check a job holds the report's two grants.

    Returns:
        One message per grant the job does not hold.

    """
    granted = decoded.as_object(job.get("permissions") or {}, f"{name}: permissions")
    return [
        f"{path.name}: {name} does not grant {key}: {level}"
        for key, level in GRANTS.items()
        if granted.get(key) not in {level, "write"}
    ]


def problems(root: Path) -> list[str]:
    """Check every scheduled workflow and stub under a tree.

    Returns:
        Every message, in file order.

    """
    targets, stubs = population(root)
    found: list[str] = []
    for path in targets:
        found += report_problems(path)
    for path, name in stubs:
        job = workflow_yaml.jobs(workflow_yaml.document(path))[name]
        found += grant_problems(path, name, job)
    return found


REPORT = """\
  report:
    needs:
{needs}
    if: ${{{{ always() }}}}
    runs-on: ubuntu-26.04
    permissions:
      issues: write
      actions: read
    steps:
      - if: ${{{{ !cancelled() }}}}
        run: .org-canon/mise/standing_issue.py
"""

PLANTED = """\
---
name: planted
"on":
  schedule:
    - cron: 0 6 * * 1
jobs:
  one:
    runs-on: ubuntu-26.04
    steps:
      - run: "true"
  two:
    runs-on: ubuntu-26.04
    steps:
      - run: "true"
{report}"""


class Canon(unittest.TestCase):
    """The canon's own tree."""

    def test_every_scheduled_workflow_reports(self) -> None:
        """Every workflow the set computes ends with `report`; every stub grants."""
        self.assertEqual(problems(CANON), [])

    def test_the_set_is_not_empty(self) -> None:
        """The computation reaches direct workflows and the stubs' callees."""
        targets, stubs = population(CANON)
        names = {p.name for p in targets}
        self.assertIn("slsa.yml", names)
        self.assertIn("repo-records.yml", names)
        self.assertIn("self-records.yml", names)
        self.assertNotIn("self-deep-tests.yml", names)
        self.assertIn((CANON / TEMPLATES / "records.yml", "records"), stubs)


class Planted(unittest.TestCase):
    """A planted scheduled workflow, red by message."""

    @staticmethod
    def plant(report: str) -> list[str]:
        """Write a planted workflow and check its tree.

        Returns:
            The messages.

        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / WORKFLOWS).mkdir(parents=True)
            (root / WORKFLOWS / "planted.yml").write_text(
                PLANTED.format(report=report), encoding="utf-8"
            )
            return problems(root)

    def test_as_built_passes(self) -> None:
        """A report needing both jobs passes."""
        self.assertEqual(
            self.plant(REPORT.format(needs="      - one\n      - two")), []
        )

    def test_no_report_job(self) -> None:
        """A scheduled workflow with no report job is red by name."""
        self.assertEqual(
            self.plant(""),
            ["planted.yml: the last job is not `report` (it is ['two'])"],
        )

    def test_needs_misses_a_job(self) -> None:
        """A report whose needs: misses a job names the job."""
        found = self.plant(REPORT.format(needs="      - one"))
        self.assertEqual(found, ["planted.yml: report's needs: misses two"])

    def test_cancelled_run_reaches_the_helper(self) -> None:
        """A helper step without the cancelled guard is red by name."""
        report = REPORT.format(needs="      - one\n      - two").replace(
            "      - if: ${{ !cancelled() }}\n        run", "      - run"
        )
        found = self.plant(report)
        self.assertEqual(
            found,
            [f"planted.yml: report's {HELPER} step is not if: {NOT_CANCELLED}"],
        )

    def test_stub_without_grants(self) -> None:
        """A scheduled stub that calls a reporting workflow must grant both."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / WORKFLOWS).mkdir(parents=True)
            text = PLANTED.format(
                report=REPORT.format(needs="      - one\n      - two")
            )
            (root / WORKFLOWS / "callee.yml").write_text(
                text.replace("schedule:\n    - cron: 0 6 * * 1", "workflow_call: {}"),
                encoding="utf-8",
            )
            stub = (
                '---\nname: stub\n"on":\n  schedule:\n    - cron: 0 6 * * 1\n'
                "jobs:\n  call:\n    uses: ./.github/workflows/callee.yml\n"
            )
            (root / WORKFLOWS / "stub.yml").write_text(stub, encoding="utf-8")
            self.assertEqual(
                problems(root),
                [
                    "stub.yml: call does not grant issues: write",
                    "stub.yml: call does not grant actions: read",
                ],
            )


if __name__ == "__main__":
    unittest.main()
