#!/usr/bin/env python3
"""verdict.py, one row per outcome of a leg (#1339).

Each row calls the helper's `main` in this process with the leg's last
step's arguments (#1673, #1699): mutation, coverage and the type checker
see the call. One row, the script row, starts it as the workflow does,
as a process, and holds the script contract. Each asserts the colour
and the message. The reports are stele v0.24.0's own
(testdata/SOURCE.md): a run that judged to the head (PASS, exit 0) and a
run whose budget ended before its first chunk was done (CANNOT_JUDGE,
exit 4, nothing judged). A row that needs a shape no live run produced
edits a copy in code and says which field.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[2] / "mise"))

import decoded
import hermetic
import verdict

HERE = Path(__file__).resolve().parent
HELPER = HERE / "verdict.py"
DATA = HERE / "testdata"

# The budget run's stopped fact, verbatim.
STOPPED = (
    "budget: ctlog: the run's budget ended waiting 5s on ctlog: get-entries"
    " 448196522..448206521: the log or the transport failed, which says"
    " nothing about its entries: context deadline exceeded"
)


def load(name: str) -> dict[str, object]:
    """Read one captured report.

    Returns:
        The report.

    """
    text = (DATA / name).read_text(encoding="utf-8")
    return decoded.as_object(decoded.load_json(text), name)


def advanced() -> dict[str, object]:
    """Make the budget run's report one whose leg advanced one chunk.

    Edits `judged` of the real CANNOT_JUDGE report: no live run advanced
    and then stopped.

    Returns:
        The edited report.

    """
    doc = load("report-budget.json")
    for listed in decoded.as_array(doc["facts"], "facts"):
        fact = decoded.as_object(listed, "a fact")
        if fact["name"] == "judged":
            fact["value"] = "448196522..448206521"
    return doc


def finding(index: int, condition: str) -> dict[str, object]:
    """Write one finding as stele records it (internal/assert/reconcile.go).

    Returns:
        The finding.

    """
    return {"subject": f"CT index {index}", "assertion": condition, "detail": "x"}


class Verdict(unittest.TestCase):
    """One row per branch of verdict.judge()."""

    @override
    def setUp(self) -> None:
        """Lay out a report file."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.report = Path(tmp.name) / "report.json"

    def run_helper(
        self, code: str, doc: dict[str, object] | None, saved: str = "true"
    ) -> hermetic.Called:
        """Write the report (None: stele wrote none) and call the helper.

        Returns:
            Its exit status and output.

        """
        text = json.dumps(doc) if doc is not None else ""
        self.report.write_text(text, encoding="utf-8")
        argv = ["--exit-code", code, "--report", str(self.report), "--saved", saved]
        return hermetic.call_main(lambda: verdict.main(argv))

    def test_judged_to_the_head(self) -> None:
        """Exit 0, saved: green."""
        proc = self.run_helper("0", load("report-pass.json"))
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual(
            proc.stdout.strip(), "verdict: green: judged to the head of the log"
        )

    def test_fail_is_red(self) -> None:
        """Exit 1, saved: red by the findings."""
        doc = load("report-pass.json")
        doc["verdict"] = "FAIL"
        doc["findings"] = [finding(448221200, "foreign"), finding(448221200, "chain")]
        proc = self.run_helper("1", doc)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(
            proc.stdout.strip(),
            "::error::2 finding(s): a certificate did not reconcile;"
            " each has its issue",
        )

    def test_advanced_is_green(self) -> None:
        """Exit 4, advanced, no finding: green, the outstanding range named."""
        proc = self.run_helper("4", advanced())
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual(
            proc.stdout.strip(),
            f"verdict: green: advanced; outstanding 448196522..448222426 ({STOPPED})",
        )

    def test_finding_under_cannot_judge_is_red(self) -> None:
        """Exit 4, advanced, a finding: red whatever the exit code."""
        doc = advanced()
        doc["findings"] = [finding(448200000, "run")]
        proc = self.run_helper("4", doc)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(
            proc.stdout.strip(),
            "::error::1 finding(s): a certificate did not reconcile;"
            " each has its issue",
        )

    def test_not_advanced_is_red(self) -> None:
        """Exit 4, nothing judged: red, stele's reason quoted."""
        proc = self.run_helper("4", load("report-budget.json"))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(
            proc.stdout.strip(),
            "::error::the checkpoint did not advance: stele judged nothing"
            f" ({STOPPED})",
        )

    def test_killed_is_red(self) -> None:
        """No exit status (cancelled or timed out): red."""
        proc = self.run_helper("", None, saved="false")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(
            proc.stdout.strip(),
            "::error::stele did not finish: no exit status was recorded (a step"
            " before it failed, or the leg was cancelled or timed out)",
        )

    def test_no_report_is_red(self) -> None:
        """Stele refused (exit 2, empty stdout), nothing saved: red, named."""
        proc = self.run_helper("2", None, saved="false")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(
            proc.stdout.strip(),
            "::error::stele exited 2 and the checkpoint was not saved:"
            " stele wrote no report",
        )

    def test_filing_failed_is_red(self) -> None:
        """A report, but the checkpoint not saved: a finding was not filed."""
        proc = self.run_helper("0", load("report-pass.json"), saved="false")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(
            proc.stdout.strip(),
            "::error::stele exited 0 and the checkpoint was not saved:"
            " a finding could not be filed",
        )

    def test_other_exit_is_red(self) -> None:
        """A saved leg that exited 3: red, named."""
        proc = self.run_helper("3", load("report-budget.json"))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip(), "::error::stele exited 3")

    def test_facts_not_stele_shape_is_red(self) -> None:
        """Saved, a finding, facts that are not stele's: red, not green.

        prop_verdict.py's example (#1557): the facts could not be read,
        so the finding was not counted and exit 0 judged the leg green.
        """
        doc = load("report-pass.json")
        doc["findings"] = [finding(448221200, "chain")]
        doc["facts"] = [1]
        proc = self.run_helper("0", doc)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(
            proc.stdout.strip(),
            "::error::stele exited 0 and its report could not be judged:"
            " the report could not be read",
        )

    def test_report_nested_past_the_parser(self) -> None:
        """JSON nested past the parser's depth: red by name, no traceback.

        The script row (#1673): the helper started as the leg starts it,
        reading its arguments and exiting with the documented status.
        """
        self.report.write_bytes(b"[" * 100_000)
        argv = [
            sys.executable, str(HELPER), "--exit-code", "0",
            "--report", str(self.report), "--saved", "true",
        ]  # fmt: skip
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(
            proc.stdout.strip(),
            "::error::stele exited 0 and its report could not be judged:"
            " the report could not be read",
        )

    def test_a_report_not_of_stele_s_shape_is_unreadable(self) -> None:
        """Each wrong shape, read where the report is decoded: one refusal (#1683)."""
        target = json.dumps("assert signer-certificates")
        rows = {
            "a fact's value is not a string": (
                f'{{"target": {target}, "facts": [{{"name": "judged", "value": 3}}]}}'
            ),
            "a fact has no value": (
                f'{{"target": {target}, "facts": [{{"name": "judged"}}]}}'
            ),
            "the facts are not a list": f'{{"target": {target}, "facts": {{}}}}',
            "the findings are not a list": f'{{"target": {target}, "findings": "x"}}',
            "the report is not an object": "[]",
            "a key appears twice": f'{{"target": {target}, "target": {target}}}',
            "a number JSON does not have": f'{{"target": {target}, "findings": [NaN]}}',
        }
        argv = ["--exit-code", "0", "--report", str(self.report), "--saved", "true"]
        for why, text in rows.items():
            with self.subTest(why=why):
                self.report.write_text(text, encoding="utf-8")
                proc = hermetic.call_main(lambda: verdict.main(argv))
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertEqual(proc.stderr, "")
                self.assertEqual(
                    proc.stdout.strip(),
                    "::error::stele exited 0 and its report could not be judged:"
                    " the report could not be read",
                )

    def test_report_from_another_target(self) -> None:
        """Another verb's report: named, never read on."""
        doc = load("report-budget.json")
        doc["target"] = "assert tags"
        proc = self.run_helper("4", doc, saved="false")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(
            proc.stdout.strip(),
            "::error::stele exited 4 and the checkpoint was not saved:"
            " the report is not stele's assert signer-certificates report",
        )


if __name__ == "__main__":
    unittest.main()
