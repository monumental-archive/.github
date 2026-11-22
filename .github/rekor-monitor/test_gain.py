#!/usr/bin/env python3
"""gain.py, one row per outcome of a run (#1967, #1339).

Each row calls the helper's `main` in this process with the gaining
job's arguments, the row's environment in place for the call (#1673,
#1699), and asserts the exit status and what was recorded: a behind
outcome appends one finding to ORG_FINDINGS, read back by the belt's
strict reader over `json.loads`; a green one appends nothing. One row,
the script row, starts it as the gaining job does.

The checkpoints are stele v0.24.0's own files, captured from
ctfe.sigstore.dev/2022 on 2026-11-17 (testdata/SOURCE.md): one at the
head it verified, one 25,905 entries behind it. A row that needs another
position edits `next` or `treeHead.treeSize` of a copy in code. The legs'
reports are the budget run's own, its `stopped` fact edited per row.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest import mock

# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[2] / "mise"))

import decoded
import gain
import hermetic

HERE = Path(__file__).resolve().parent
HELPER = HERE / "gain.py"
DATA = HERE / "testdata"

MESSAGE = "the signer-identity watch is behind"
NEXT = "Checkpoint next index (everything before it is judged): "
HEAD = "Checkpoint signed tree head: "
STARTED = "Entries outstanding when this run started: "
NOW = "Entries outstanding now: "
THIS_RUN = "This run: "
# The budget run's stopped fact, verbatim, and the reason a throttled
# leg gives.
BUDGET = (
    "budget: ctlog: the run's budget ended waiting 5s on ctlog: get-entries"
    " 448196522..448206521: the log or the transport failed, which says"
    " nothing about its entries: context deadline exceeded"
)
THROTTLED = "throttled: ctlog: HTTP 429 from the log"

# checkpoint-budget.json: next 448,196,522 of a verified 448,222,427.
BEHIND_NEXT = 448196522
BEHIND_SIZE = 448222427
LEGS = 14


def checkpoint(name: str = "checkpoint-budget.json", **edits: int) -> dict[str, object]:
    """Read a captured checkpoint, `next` or `size` edited.

    Returns:
        The checkpoint document.

    """
    text = (DATA / name).read_text(encoding="utf-8")
    doc = decoded.as_object(decoded.load_json(text), name)
    if "next" in edits:
        doc["next"] = edits["next"]
    if "size" in edits:
        tree_head(doc)["treeSize"] = edits["size"]
    return doc


def tree_head(doc: dict[str, object]) -> dict[str, object]:
    """Reach a checkpoint's signed tree head, to edit it in place.

    Returns:
        The `treeHead` object.

    """
    return decoded.as_object(doc["treeHead"], "treeHead")


def report(stopped: str | None) -> dict[str, object]:
    """Make one leg's report: the budget run's, its `stopped` fact edited.

    Returns:
        The report; None removes the fact.

    """
    text = (DATA / "report-budget.json").read_text(encoding="utf-8")
    doc = decoded.as_object(decoded.load_json(text), "report-budget.json")
    facts = [
        decoded.as_object(f, "a fact") for f in decoded.as_array(doc["facts"], "facts")
    ]
    kept = [f for f in facts if f["name"] != "stopped"]
    if stopped is not None:
        kept.append({"name": "stopped", "value": stopped})
    doc["facts"] = kept
    return doc


def expect(*text: str) -> list[str]:
    """Write the lines a row expects, typed.

    Returns:
        The lines, in order.

    """
    return list(text)


class Rows(unittest.TestCase):
    """The run's two checkpoints, the legs' reports, and the helper run."""

    @override
    def setUp(self) -> None:
        """Lay out a run that did not advance, every leg stopped by its budget."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.start = self.dir / "run-start.json"
        self.end = self.dir / "checkpoint.json"
        self.reports = self.dir / "reports"
        self.reports.mkdir()
        self.findings = self.dir / "findings" / "gaining-0.jsonl"
        self.write(self.start, checkpoint())
        self.write(self.end, checkpoint())
        self.legs([BUDGET] * LEGS)
        self.env = hermetic.env(ORG_FINDINGS=str(self.findings))

    @staticmethod
    def write(path: Path, doc: dict[str, object]) -> None:
        """Write a checkpoint or report where the helper reads it."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")

    def legs(self, stops: list[str | None]) -> None:
        """Lay out one downloaded report per leg, as download-artifact does."""
        shutil.rmtree(self.reports, ignore_errors=True)
        self.reports.mkdir()
        for leg, stopped in enumerate(stops, start=1):
            self.write(
                self.reports / f"watch-report-{leg}" / "report.json", report(stopped)
            )

    def arguments(self, kept: str, legs: int = LEGS) -> list[str]:
        """Build the gaining job's command line.

        Returns:
            The arguments after the program name.

        """
        return [
            "--start", str(self.start), "--start-kept", kept,
            "--checkpoint", str(self.end), "--reports", str(self.reports),
            "--legs", str(legs),
        ]  # fmt: skip

    def run_helper(self, kept: str = "true", legs: int = LEGS) -> hermetic.Called:
        """Call the helper's main in this process, as the gaining job runs it.

        Returns:
            Its exit status and output, captured.

        """
        argv = self.arguments(kept, legs)
        # The gaining job runs the script by path with no mise task
        # around it, so the finding writer names the script (#1934).
        with mock.patch.object(sys, "argv", [str(HELPER), *argv]):
            return hermetic.call_main(lambda: gain.main(argv), env=self.env)

    def recorded(self) -> list[dict[str, object]]:
        """Read back every record the helper appended.

        Returns:
            Each record, decoded by decoded.load_json (strict `json.loads`).

        """
        if not self.findings.exists():
            return []
        lines = self.findings.read_text(encoding="utf-8").splitlines()
        return [
            decoded.as_object(decoded.load_json(line), "a record") for line in lines
        ]

    def detail(self) -> list[str]:
        """Read the one behind record's detail, asserting its frame.

        Returns:
            The detail lines.

        """
        records = self.recorded()
        self.assertEqual(len(records), 1, records)
        record = records[0]
        frame: dict[str, object] = {
            k: record[k] for k in ("v", "task", "subject", "message")
        }
        expected: dict[str, object] = {
            "v": 1,
            "task": "gain.py",
            "subject": "",
            "message": MESSAGE,
        }
        self.assertEqual(frame, expected)
        return [
            decoded.as_str(s, "a line")
            for s in decoded.as_array(record["detail"], "detail")
        ]


class Behind(Rows):
    """Each outcome the header calls behind: exit 1, one record."""

    def test_did_not_advance(self) -> None:
        """Not advanced: red, the detail lines in order, the reason counted."""
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        why = "the checkpoint did not advance in this run (next 448196522)"
        self.assertEqual(
            self.detail(),
            expect(
                f"{NEXT}448,196,522",
                f"{HEAD}448,222,427 at 2026-11-17T18:32:08Z",
                f"{STARTED}25,905",
                f"{NOW}25,905",
                f"{THIS_RUN}{why}",
                f"14 legs: {BUDGET}",
            ),
        )
        self.assertIn(f"::error::gain.py: {MESSAGE}\n", proc.stderr)
        self.assertIn(f"  {THIS_RUN}{why}\n", proc.stderr)

    def test_advanced_not_gaining(self) -> None:
        """Advanced, but the log grew faster: red, both counts (green before #1967)."""
        grown = checkpoint(next=BEHIND_NEXT + 1000, size=BEHIND_SIZE + 5000)
        self.write(self.end, grown)
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        lines = self.detail()
        self.assertEqual(lines[2:4], expect(f"{STARTED}25,905", f"{NOW}29,905"))
        self.assertEqual(
            lines[4],
            f"{THIS_RUN}advanced but not gaining: outstanding 25,905 at the start,"
            " 29,905 now",
        )

    def test_advanced_outstanding_equal(self) -> None:
        """Advanced, outstanding exactly as at the start: not smaller, red."""
        level = checkpoint(next=BEHIND_NEXT + 1000, size=BEHIND_SIZE + 1000)
        self.write(self.end, level)
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(self.detail()[3], f"{NOW}25,905")

    def test_no_newest_checkpoint(self) -> None:
        """No checkpoint at all (before the founding run): red, named."""
        self.start.unlink()
        self.end.unlink()
        proc = self.run_helper(kept="false")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        missing = "unknown: no checkpoint file"
        self.assertEqual(
            self.detail()[:5],
            expect(
                f"{NEXT}{missing}",
                f"{HEAD}{missing}",
                f"{STARTED}none counted: this run founded the checkpoint",
                f"{NOW}{missing}",
                f"{THIS_RUN}the newest checkpoint: no checkpoint file",
            ),
        )

    def test_start_not_kept(self) -> None:
        """The start job did not finish: red, the count named unknown."""
        proc = self.run_helper(kept="")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        lines = self.detail()
        self.assertEqual(lines[2], f"{STARTED}unknown: the start job did not finish")
        self.assertEqual(
            lines[4],
            f"{THIS_RUN}the checkpoint the run started from was not kept"
            " (the start job did not finish)",
        )

    def test_kept_but_not_restored(self) -> None:
        """The start was kept but its copy is gone: red, named."""
        self.start.unlink()
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(
            self.detail()[4],
            f"{THIS_RUN}the checkpoint the run started from was not kept"
            " (no checkpoint file)",
        )


class Reasons(Rows):
    """Why the legs stopped: one line per distinct reason, with its count."""

    def test_two_reasons_counted(self) -> None:
        """Three stopped for one reason, two for another: counts 3 and 2."""
        self.legs([THROTTLED, BUDGET, THROTTLED, BUDGET, THROTTLED])
        proc = self.run_helper(legs=5)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(
            self.detail()[5:], expect(f"3 legs: {THROTTLED}", f"2 legs: {BUDGET}")
        )

    def test_missing_report_is_counted(self) -> None:
        """Twelve reports for fourteen legs: two counted as "no report"."""
        self.legs([THROTTLED] * 12)
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(
            self.detail()[5:], expect(f"12 legs: {THROTTLED}", "2 legs: no report")
        )

    def test_empty_report_is_no_report(self) -> None:
        """A leg whose stele wrote nothing hands on an empty file: no report."""
        self.legs([THROTTLED] * 13)
        empty = self.reports / "watch-report-14" / "report.json"
        empty.parent.mkdir()
        empty.write_text("", encoding="utf-8")
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(
            self.detail()[5:], expect(f"13 legs: {THROTTLED}", "1 leg: no report")
        )

    def test_report_without_stopped_fact(self) -> None:
        """A report with no `stopped` fact: named, never dropped."""
        self.legs([None] * LEGS)
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(self.detail()[5:], expect("14 legs: no reason given"))

    def test_report_at_the_head(self) -> None:
        """A leg that judged to the head gave no reason: said so."""
        self.legs([])
        at_head = self.reports / "watch-report-1" / "report.json"
        self.write(
            at_head,
            decoded.as_object(
                decoded.load_json((DATA / "report-pass.json").read_bytes()), "pass"
            ),
        )
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(
            self.detail()[5:],
            expect("13 legs: no report", "1 leg: judged to the head of the log"),
        )

    def test_unreadable_report(self) -> None:
        """A report that is not stele's: the reader's own reason."""
        self.legs([BUDGET] * 13)
        bad = self.reports / "watch-report-14" / "report.json"
        bad.parent.mkdir()
        bad.write_text("[", encoding="utf-8")
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(
            self.detail()[5:],
            expect(f"13 legs: {BUDGET}", "1 leg: the report could not be read"),
        )

    def test_no_reports_directory(self) -> None:
        """No report reached the job at all: every leg counted."""
        shutil.rmtree(self.reports)
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(self.detail()[5:], expect("14 legs: no report"))


class Green(Rows):
    """Each outcome the header calls green: exit 0, nothing appended."""

    def test_gaining(self) -> None:
        """Advanced, outstanding smaller than at the start: green."""
        self.write(self.end, checkpoint(next=BEHIND_NEXT + 10000))
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.recorded(), list[dict[str, object]]())
        self.assertIn(
            "gain: green: gaining: outstanding 25,905 at the start, 15,905 now",
            proc.stdout,
        )

    def test_caught_up(self) -> None:
        """Newest checkpoint at its head: green, whatever the start."""
        self.write(self.end, checkpoint("checkpoint-pass.json"))
        proc = self.run_helper(kept="")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.recorded(), list[dict[str, object]]())
        self.assertIn("gain: the newest checkpoint: next index 448221522", proc.stdout)
        self.assertIn("gain: green: judged to the head of the log", proc.stdout)

    def test_founded(self) -> None:
        """No checkpoint at the start: no earlier count, by name, green."""
        self.start.unlink()
        proc = self.run_helper(kept="false")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.recorded(), list[dict[str, object]]())
        self.assertIn(
            "gain: green: advanced to 448196522; no earlier count, this run founded it",
            proc.stdout,
        )


class Decoded(Rows):
    """Each checkpoint gain.py reads, decoded where it is read (#1683)."""

    def test_a_checkpoint_field_that_is_not_an_integer(self) -> None:
        """next, treeSize or timestamp of another kind: named unreadable."""
        rows: dict[str, tuple[str, object]] = {
            "next as text": ("next", "448196522"),
            "next as a boolean": ("next", True),
            "a tree size with a fraction": ("treeSize", 448222427.5),
            "a timestamp as text": ("timestamp", "1794940328655"),
        }
        for why, (field, value) in rows.items():
            with self.subTest(why=why):
                self.findings.unlink(missing_ok=True)
                doc = checkpoint()
                if field == "next":
                    doc["next"] = value
                else:
                    tree_head(doc)[field] = value
                self.write(self.end, doc)
                proc = self.run_helper()
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertEqual(
                    self.detail()[4],
                    f"{THIS_RUN}the newest checkpoint:"
                    " the checkpoint file could not be read",
                )

    def test_next_infinity_is_unreadable(self) -> None:
        """A `next` of Infinity: named unreadable, never a traceback (#1557)."""
        text = json.dumps(checkpoint())
        self.end.write_text(
            text.replace(f'"next": {BEHIND_NEXT}', '"next": Infinity'),
            encoding="utf-8",
        )
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(
            self.detail()[0], f"{NEXT}unknown: the checkpoint file could not be read"
        )

    def test_timestamp_out_of_range_is_unreadable(self) -> None:
        """A head time past the platform's range: named, never a traceback.

        prop_gain.py's shrunk example (#1557): the start's timestamp
        9.223372036854776e+21.
        """
        doc = checkpoint()
        tree_head(doc)["timestamp"] = 9.223372036854776e21
        self.write(self.start, doc)
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(
            self.detail()[4],
            f"{THIS_RUN}the checkpoint the run started from was not kept"
            " (the checkpoint file could not be read)",
        )

    def test_nested_past_the_parser_is_unreadable(self) -> None:
        """JSON nested past the parser's depth: named, never a traceback.

        The script row (#1673): the helper started as the gaining job
        starts it, reading its arguments and exiting with the
        documented status.
        """
        self.end.write_bytes(b"[" * 100_000)
        argv = [sys.executable, str(HELPER), *self.arguments("true")]
        proc = subprocess.run(
            argv, env=self.env, capture_output=True, text=True, check=False
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(
            self.detail()[4],
            f"{THIS_RUN}the newest checkpoint: the checkpoint file could not be read",
        )
        self.assertIn(f"::error::gain.py: {MESSAGE}\n", proc.stderr)


if __name__ == "__main__":
    unittest.main()
