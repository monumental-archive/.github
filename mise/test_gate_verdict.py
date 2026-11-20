#!/usr/bin/env python3
"""The required check's verdict step, every branch (#1386).

`ci.yml`'s "Every part of the gate passed" step is `ci / ci`, the one
context the org ruleset names, so it must be red unless every part of
the gate was green. It stays shell in the workflow (it only prints what
other jobs computed), so these rows lift its `run:` block out of the
YAML by step name and run it under bash. Reading it out of the YAML is
what keeps the test on the code that ships.

stdlib `unittest`, matching the other tests here.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import unittest

import hermetic

WORKFLOW = pathlib.Path(__file__).resolve().parent.parent / ".github/workflows/ci.yml"
STEP = "- name: Every part of the gate passed"


def step_script() -> str:
    """Lift the verdict step's `run: |` block out of the workflow.

    Returns:
        The block as bash, dedented to column zero.

    Raises:
        AssertionError: No single step of that name, no `run: |` under
            it, or an empty block. Loud, never a skip: a test that found
            nothing to run reads as a passing one.

    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == STEP]
    if len(starts) != 1:
        msg = f"expected one {STEP!r} in {WORKFLOW}, found {len(starts)}"
        raise AssertionError(msg)
    runs = [i for i in range(starts[0] + 1, len(lines)) if lines[i].strip() == "run: |"]
    if not runs:
        msg = f"no 'run: |' under {STEP!r}"
        raise AssertionError(msg)
    key = lines[runs[0]]
    key_indent = len(key) - len(key.lstrip())
    block: list[str] = []
    for line in lines[runs[0] + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= key_indent:
            break
        block.append(line)
    while block and not block[-1].strip():
        block.pop()
    if not block:
        msg = f"empty run block under {STEP!r}"
        raise AssertionError(msg)
    indent = len(block[0]) - len(block[0].lstrip())
    return "\n".join(line[indent:] for line in block) + "\n"


class EveryPartPassed(unittest.TestCase):
    """Each row: the jobs' results, and the verdict printed."""

    @staticmethod
    def verdict(
        plan: str, gate: str, pgrx: str, majors: str = "[16, 17]"
    ) -> subprocess.CompletedProcess[str]:
        """Run the lifted step on one set of job results.

        Returns:
            The run.

        """
        bash = hermetic.tool("bash")
        return subprocess.run(
            [bash, "-c", step_script()],
            env=hermetic.env(
                PATH=os.environ["PATH"], PLAN=plan, GATE=gate, PGRX=pgrx, MAJORS=majors
            ),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def test_every_part_green(self) -> None:
        """Plan, gate and every planned leg passed."""
        done = self.verdict("success", "success", "success")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout, "ci: plan, gate and every pgrx leg (\\[16\\,\\ 17\\]) passed\n"
        )

    def test_no_legs_planned_and_none_ran(self) -> None:
        """A tree with no extension skips the legs, and that is green."""
        done = self.verdict("success", "success", "skipped", "[]")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout, "ci: plan, gate and every pgrx leg (\\[\\]) passed\n"
        )

    def test_planned_legs_that_were_skipped(self) -> None:
        """A skipped required leg must not count as passing."""
        done = self.verdict("success", "success", "skipped")
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(
            done.stdout,
            "::error::pgrx legs were skipped although majors were planned\n"
            "  planned: \\[16\\,\\ 17\\]\n",
        )

    def test_a_failed_plan(self) -> None:
        """The plan's result is named."""
        done = self.verdict("failure", "skipped", "skipped", "")
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(
            done.stdout,
            "::error::a job the gate needs did not succeed\n  job plan: failure\n"
            "::error::a job the gate needs did not succeed\n  job gate: skipped\n"
            "::error::pgrx legs were skipped although majors were planned\n"
            "  planned: ''\n",
        )

    def test_a_cancelled_gate(self) -> None:
        """The gate's result is named."""
        done = self.verdict("success", "cancelled", "success")
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(
            done.stdout,
            "::error::a job the gate needs did not succeed\n  job gate: cancelled\n",
        )

    def test_a_failed_leg(self) -> None:
        """A leg's failure is named."""
        done = self.verdict("success", "success", "failure")
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(
            done.stdout,
            "::error::a job the gate needs did not succeed\n"
            "  job pgrx\\ legs: failure\n",
        )


if __name__ == "__main__":
    unittest.main()
