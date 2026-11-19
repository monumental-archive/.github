#!/usr/bin/env python3
"""rekor-monitor.yml holds the shape the legs depend on (#1339).

The checkpoint passes between legs and between runs through one
mechanism, the Actions cache. That only works if every leg restores the
newest save by one prefix and saves under a key no other leg uses, the
legs run one at a time, the run's starting checkpoint is kept aside
before the first leg, and the alert runs once after the last. None of it
can run here, so this reads the workflow's text and names each part that
is not as built; a planted copy per rule proves the message.

The workflow is read as text, line by line under each job: stdlib only.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKFLOW = HERE.parent / "workflows" / "rekor-monitor.yml"

RUN = "${{ github.run_id }}-${{ github.run_attempt }}"
LEG_KEY = f"key: ct-watch-{RUN}-${{{{ matrix.leg }}}}"
START_KEY = f"key: ct-start-{RUN}"
NEWEST = "restore-keys: ct-watch-"
LEGS = 14
ALERT_NEEDS = "    needs:\n      - start\n      - watch\n"
IDENTITY = re.compile(r"actions:\s*write|gh workflow run|id-token:\s*write")


def jobs(text: str) -> dict[str, str]:
    """Split the workflow into its jobs.

    Returns:
        Job id to the job's text.

    """
    lines = text.splitlines(keepends=True)
    top = lines.index("jobs:\n")
    starts = [
        i for i in range(top + 1, len(lines)) if re.match(r"^  [\w-]+:\s*$", lines[i])
    ]
    ends = [*starts[1:], len(lines)]
    return {
        lines[a].strip().rstrip(":"): "".join(lines[a:b])
        for a, b in zip(starts, ends, strict=True)
    }


def steps(job: str) -> list[str]:
    """Split a job into its steps.

    Returns:
        Each step's text.

    """
    return re.split(r"\n      - ", job)[1:]


def legs(job: str) -> list[int]:
    """Read the matrix's legs, written one per line.

    Returns:
        The legs, in order; none when the list is not there.

    """
    block = re.search(r"\n        leg:\n((?:          - \d+\n)+)", job)
    return [int(n) for n in re.findall(r"\d+", block.group(1))] if block else []


def run_wide(text: str) -> list[str]:
    """Name what is wrong across the whole workflow.

    Returns:
        One line per problem.

    """
    found = []
    lines = text.splitlines(keepends=True)
    code = "".join(line for line in lines if not line.lstrip().startswith("#"))
    if IDENTITY.search(code):
        found.append("a job can start workflows or mint an identity")
    if "cancel-in-progress: false" not in text:
        found.append("a delivered run can overlap a running one")
    return found


def leg_wide(watch: str) -> list[str]:
    """Name what is wrong with the legs.

    Returns:
        One line per problem.

    """
    found = []
    if "max-parallel: 1" not in watch or "fail-fast: false" not in watch:
        found.append("the legs do not run one at a time to the last")
    if legs(watch) != list(range(1, LEGS + 1)):
        found.append(f"the legs are not 1 to {LEGS}")
    if "    needs: start\n" not in watch:
        found.append("a leg can start before the run's checkpoint is kept aside")
    if "create-github-app-token" not in watch:
        found.append("a leg reads with a token it did not mint")
    if "--budget 40m" not in watch or "timeout-minutes: 50" not in watch:
        found.append("a leg reads longer than its token lives")
    if "matrix.leg == 1 && inputs.start-at" not in watch:
        found.append("start-at reaches a leg other than the first")
    restores = [s for s in steps(watch) if "actions/cache/restore" in s]
    saves = [s for s in steps(watch) if "actions/cache/save" in s]
    if not (restores and LEG_KEY in restores[0] and NEWEST in restores[0]):
        found.append("a leg does not restore the newest save by its prefix")
    if not (saves and LEG_KEY in saves[0]):
        found.append("a leg does not save under its own key")
    elif "steps.findings.outcome == 'success'" not in saves[0]:
        found.append("a leg saves before its findings are filed")
    return found


def ends(start: str, alert: str) -> list[str]:
    """Name what is wrong with the start job and the alert job.

    Returns:
        One line per problem.

    """
    found = []
    if not (START_KEY in start and NEWEST in start):
        found.append("the run's starting checkpoint is not kept aside")
    if ALERT_NEEDS not in alert or "if: ${{ always() }}" not in alert:
        found.append("the alert does not run once after the last leg")
    restores = [s for s in steps(alert) if "actions/cache/restore" in s]
    kept = [s for s in restores if START_KEY in s and "restore-keys" not in s]
    if not kept or not any(NEWEST in s for s in restores):
        found.append("the alert does not read both checkpoints")
    if "actions/cache/save" in alert:
        found.append("the alert saves a checkpoint")
    return found


def problems(text: str) -> list[str]:
    """Name every part of the shape that is not as built.

    Returns:
        One line per problem; none when the shape holds.

    """
    found = run_wide(text)
    by_id = jobs(text)
    if sorted(by_id) != ["alert", "start", "watch"]:
        return [*found, f"the jobs are {sorted(by_id)}, not start, watch and alert"]
    found += leg_wide(by_id["watch"])
    return found + ends(by_id["start"], by_id["alert"])


# Each row: what is planted (old, new) and the one message it must earn.
ROWS = [
    (
        "      contents: read # the helper",
        "      id-token: write\n      contents: read # the helper",
        "a job can start workflows or mint an identity",
    ),
    (
        "      contents: read # the canon's policy",
        "      actions: write\n      contents: read # the canon's policy",
        "a job can start workflows or mint an identity",
    ),
    (
        "cancel-in-progress: false",
        "cancel-in-progress: true",
        "a delivered run can overlap a running one",
    ),
    (
        "max-parallel: 1",
        "max-parallel: 2",
        "the legs do not run one at a time to the last",
    ),
    (
        "fail-fast: false",
        "fail-fast: true",
        "the legs do not run one at a time to the last",
    ),
    ("          - 14\n", "", f"the legs are not 1 to {LEGS}"),
    (
        "    needs: start\n",
        "",
        "a leg can start before the run's checkpoint is kept aside",
    ),
    (
        "uses: actions/create-github-app-token",
        "uses: example/other-token",
        "a leg reads with a token it did not mint",
    ),
    ("--budget 40m", "--budget 90m", "a leg reads longer than its token lives"),
    (
        "matrix.leg == 1 && inputs.start-at",
        "inputs.start-at",
        "start-at reaches a leg other than the first",
    ),
    (
        f"{NEWEST}\n\n      # Read-only",
        "\n      # Read-only",
        "a leg does not restore the newest save by its prefix",
    ),
    (
        f"{LEG_KEY}\n\n      # The leg's colour",
        f"key: ct-watch-{RUN}\n\n      # The leg's colour",
        "a leg does not save under its own key",
    ),
    (
        "steps.findings.outcome == 'success' && ",
        "",
        "a leg saves before its findings are filed",
    ),
    (
        f"{START_KEY}\n\n  watch:",
        "key: x\n\n  watch:",
        "the run's starting checkpoint is not kept aside",
    ),
    (
        ALERT_NEEDS,
        "    needs:\n      - watch\n",
        "the alert does not run once after the last leg",
    ),
    (
        f"{START_KEY}\n\n      # unattested-path: the newest checkpoint, the last",
        "key: x\n\n      # unattested-path: the newest checkpoint, the last",
        "the alert does not read both checkpoints",
    ),
    (
        "\n...\n",
        "\n      - uses: actions/cache/save@x\n...\n",
        "the alert saves a checkpoint",
    ),
    (
        "\n  alert:\n",
        "\n  alerts:\n",
        "the jobs are ['alerts', 'start', 'watch'], not start, watch and alert",
    ),
]


class Shape(unittest.TestCase):
    """The workflow as built, and one planted copy per rule."""

    def setUp(self) -> None:
        """Read the workflow."""
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_the_workflow_holds(self) -> None:
        """The workflow as built names no problem.

        The alert's `always()` is #1149's: a leg ended by its timeout is
        cancelled, which `failure()` does not match, and nothing was filed.
        """
        self.assertEqual(problems(self.text), [])

    def test_rows(self) -> None:
        """Each planted change is named by its own message, and only it."""
        for old, new, message in ROWS:
            with self.subTest(message=message, old=old):
                self.assertEqual(self.text.count(old), 1)
                self.assertEqual(problems(self.text.replace(old, new)), [message])


if __name__ == "__main__":
    unittest.main()
