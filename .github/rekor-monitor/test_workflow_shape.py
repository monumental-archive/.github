#!/usr/bin/env python3
"""rekor-monitor.yml holds the shape the legs depend on (#1339).

The checkpoint passes between legs and between runs through one
mechanism, the Actions cache. That only works if every leg restores the
newest save by one prefix and saves under a key no other leg uses, the
legs run one at a time, the run's starting checkpoint is kept aside
before the first leg, and the gaining job runs once after the last,
reading every leg's report and counting all the legs. None of it
can run here, so this reads the workflow and names each part that is not
as built; a planted copy per rule proves the message.

The workflow is read through mise/workflow_yaml.py (#1840), the belt's
one YAML parser; a planted copy is written to a scratch file and read
the same way.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[2] / "mise"))

import decoded
import workflow_yaml

HERE = Path(__file__).resolve().parent
WORKFLOW = HERE.parent / "workflows" / "rekor-monitor.yml"

RUN = "${{ github.run_id }}-${{ github.run_attempt }}"
LEG_KEY = f"ct-watch-{RUN}-${{{{ matrix.leg }}}}"
START_KEY = f"ct-start-{RUN}"
NEWEST = "ct-watch-"
REPORT = "watch-report-${{ matrix.leg }}"
REPORTS = "watch-report-*"
LEGS = 14
# A leg's bound, longer than its 40-minute read and inside its token's hour.
TIMEOUT = 50
WRITES = ("actions", "id-token")

Mapping = dict[str, object]


def mapping(node: Mapping, key: str) -> Mapping:
    """Read one mapping under a node.

    Returns:
        The mapping; empty where the key is absent or null.

    """
    return decoded.as_object(node.get(key) or {}, key)


def inputs(step: Mapping) -> Mapping:
    """Read a step's `with:` inputs.

    Returns:
        The inputs; empty where there are none.

    """
    return mapping(step, "with")


def using(job: Mapping, action: str) -> list[Mapping]:
    """Name a job's steps that use an action.

    Returns:
        Each step whose `uses:` names the action, in order.

    """
    return [s for s in workflow_yaml.steps(job) if action in str(s.get("uses", ""))]


def runs(job: Mapping) -> str:
    """Read every `run:` value in a job.

    Returns:
        The values, joined.

    """
    return "\n".join(str(s.get("run", "")) for s in workflow_yaml.steps(job))


def mints(document: Mapping) -> bool:
    """Say whether any job can start workflows or mint an identity.

    Returns:
        True where the workflow or a job grants `actions` or `id-token`
        write, or a step runs `gh workflow run`.

    """
    granted = [mapping(document, "permissions")]
    granted += [
        mapping(j, "permissions") for j in workflow_yaml.jobs(document).values()
    ]
    if any(g.get(scope) == "write" for g in granted for scope in WRITES):
        return True
    return any(
        "gh workflow run" in runs(j) for j in workflow_yaml.jobs(document).values()
    )


def run_wide(document: Mapping) -> list[str]:
    """Name what is wrong across the whole workflow.

    Returns:
        One line per problem.

    """
    found = []
    if mints(document):
        found.append("a job can start workflows or mint an identity")
    if mapping(document, "concurrency").get("cancel-in-progress") is not False:
        found.append("a delivered run can overlap a running one")
    return found


def leg_wide(watch: Mapping) -> list[str]:
    """Name what is wrong with the legs.

    Returns:
        One line per problem.

    """
    found = []
    strategy = mapping(watch, "strategy")
    if strategy.get("max-parallel") != 1 or strategy.get("fail-fast") is not False:
        found.append("the legs do not run one at a time to the last")
    if mapping(strategy, "matrix").get("leg") != list(range(1, LEGS + 1)):
        found.append(f"the legs are not 1 to {LEGS}")
    if watch.get("needs") != "start":
        found.append("a leg can start before the run's checkpoint is kept aside")
    if not using(watch, "actions/create-github-app-token"):
        found.append("a leg reads with a token it did not mint")
    if "--budget 40m" not in runs(watch) or watch.get("timeout-minutes") != TIMEOUT:
        found.append("a leg reads longer than its token lives")
    envs = [mapping(s, "env") for s in workflow_yaml.steps(watch)]
    if not any(
        "matrix.leg == 1 && inputs.start-at" in str(v) for e in envs for v in e.values()
    ):
        found.append("start-at reaches a leg other than the first")
    restores = [inputs(s) for s in using(watch, "actions/cache/restore")]
    saves = using(watch, "actions/cache/save")
    if not (
        restores
        and restores[0].get("key") == LEG_KEY
        and restores[0].get("restore-keys") == NEWEST
    ):
        found.append("a leg does not restore the newest save by its prefix")
    if not (saves and inputs(saves[0]).get("key") == LEG_KEY):
        found.append("a leg does not save under its own key")
    elif "steps.findings.outcome == 'success'" not in str(saves[0].get("if", "")):
        found.append("a leg saves before its findings are filed")
    return found


def ends(start: Mapping, gaining: Mapping, watch: Mapping) -> list[str]:
    """Name what is wrong with the start job and the gaining job.

    Returns:
        One line per problem.

    """
    found = []
    kept = [inputs(s).get("key") for s in using(start, "actions/cache/save")]
    newest = [
        inputs(s).get("restore-keys") for s in using(start, "actions/cache/restore")
    ]
    if not (START_KEY in kept and NEWEST in newest):
        found.append("the run's starting checkpoint is not kept aside")
    after_legs = gaining.get("needs") == ["start", "watch"]
    if not after_legs or gaining.get("if") != "${{ always() }}":
        found.append("the gaining job does not run once after the last leg")
    restores = [inputs(s) for s in using(gaining, "actions/cache/restore")]
    aside = [
        r for r in restores if r.get("key") == START_KEY and "restore-keys" not in r
    ]
    if not aside or not any(r.get("restore-keys") == NEWEST for r in restores):
        found.append("the gaining job does not read both checkpoints")
    if using(gaining, "actions/cache/save"):
        found.append("the gaining job saves a checkpoint")
    handed = [inputs(s).get("name") for s in using(watch, "actions/upload-artifact")]
    taken = [
        inputs(s).get("pattern") for s in using(gaining, "actions/download-artifact")
    ]
    if REPORT not in handed or REPORTS not in taken:
        found.append("the legs' reports do not reach the gaining job")
    if f"--legs {LEGS}" not in runs(gaining):
        found.append(f"the gaining job does not count {LEGS} legs")
    return found


def problems(text: str) -> list[str]:
    """Name every part of the shape that is not as built.

    Returns:
        One line per problem; none when the shape holds.

    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "rekor-monitor.yml"
        path.write_text(text, encoding="utf-8")
        document = workflow_yaml.document(path)
    found = run_wide(document)
    by_id = workflow_yaml.jobs(document)
    # `report` is every scheduled workflow's last job (#1887), held by
    # mise/test_report_job.py; this file holds the legs' shape.
    if sorted(by_id) != ["gaining", "report", "start", "watch"]:
        expected = "start, watch, gaining and report"
        return [*found, f"the jobs are {sorted(by_id)}, not {expected}"]
    found += leg_wide(by_id["watch"])
    return found + ends(by_id["start"], by_id["gaining"], by_id["watch"])


# The gaining job's `needs:` and the line after it; the report job's
# (#1887) lists more jobs, so this reaches the gaining job's alone.
AFTER_LEGS = "    if: ${{ always() }}\n    runs-on"

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
        f"restore-keys: {NEWEST}\n\n      # Read-only",
        "\n      # Read-only",
        "a leg does not restore the newest save by its prefix",
    ),
    (
        f"key: {LEG_KEY}\n\n      # The leg's report",
        f"key: ct-watch-{RUN}\n\n      # The leg's report",
        "a leg does not save under its own key",
    ),
    (
        "steps.findings.outcome == 'success' && ",
        "",
        "a leg saves before its findings are filed",
    ),
    (
        f"path: run-start.json\n          key: {START_KEY}\n\n      # This job's",
        "path: run-start.json\n          key: x\n\n      # This job's",
        "the run's starting checkpoint is not kept aside",
    ),
    (
        f"    needs:\n      - start\n      - watch\n{AFTER_LEGS}",
        f"    needs:\n      - watch\n{AFTER_LEGS}",
        "the gaining job does not run once after the last leg",
    ),
    (
        f"key: {START_KEY}\n\n      # unattested-path: the newest checkpoint, the last",
        "key: x\n\n      # unattested-path: the newest checkpoint, the last",
        "the gaining job does not read both checkpoints",
    ),
    (
        f"--legs {LEGS}\n",
        f"--legs {LEGS}\n      - uses: actions/cache/save@x\n",
        "the gaining job saves a checkpoint",
    ),
    (
        f"--legs {LEGS}\n",
        f"--legs {LEGS - 1}\n",
        f"the gaining job does not count {LEGS} legs",
    ),
    (
        f"name: {REPORT}\n",
        "name: watch-report\n",
        "the legs' reports do not reach the gaining job",
    ),
    (
        f"pattern: {REPORTS}\n",
        "pattern: findings-*\n",
        "the legs' reports do not reach the gaining job",
    ),
    (
        "\n  gaining:\n",
        "\n  gains:\n",
        (
            "the jobs are ['gains', 'report', 'start', 'watch'],"
            " not start, watch, gaining and report"
        ),
    ),
]


class Shape(unittest.TestCase):
    """The workflow as built, and one planted copy per rule."""

    @override
    def setUp(self) -> None:
        """Read the workflow."""
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_the_workflow_holds(self) -> None:
        """The workflow as built names no problem.

        The gaining job's `always()` is #1149's: a leg ended by its timeout is
        cancelled, which `failure()` does not match, and nothing was filed.
        """
        self.assertEqual(problems(self.text), list[str]())

    def test_rows(self) -> None:
        """Each planted change is named by its own message, and only it."""
        for old, new, message in ROWS:
            with self.subTest(message=message, old=old):
                self.assertEqual(self.text.count(old), 1)
                named: list[str] = [message]
                self.assertEqual(problems(self.text.replace(old, new)), named)


if __name__ == "__main__":
    unittest.main()
