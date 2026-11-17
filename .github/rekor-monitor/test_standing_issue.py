#!/usr/bin/env python3
"""standing-issue.py against a stub `gh` on PATH (#1353, #1339).

The stub records every argv it is called with and answers `issue list`
from the row's canned listing (or fails, for the read-failure row).
Each row runs the helper as the workflow does, as a process, and
asserts the exact arguments gh received, the job's colour, the message
it prints and the dispatch it asks for.

The reports and checkpoints are stele v0.24.0's own output, captured
from ctfe.sigstore.dev/2022 on 2026-11-17 (testdata/SOURCE.md): a run
that judged to the head (PASS, exit 0) and a run whose budget ended
before its first chunk was done (CANNOT_JUDGE, exit 4, nothing judged).
A row that needs a shape no live run produced edits a copy of a real
one in code, and says which field.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import] - the helper is run as the workflow runs it
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELPER = HERE / "standing-issue.py"
DATA = HERE / "testdata"

TITLE = "rekor-monitor: the signer-identity watch is behind"
REPO = "example-owner/example-repo"
RUN = "https://github.com/example-owner/example-repo/actions/runs/200"
FIRST_RUN = "https://github.com/example-owner/example-repo/actions/runs/100"

STUB = """\
import json, os, sys
with open(os.environ["STUB_CALLS"], "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["issue", "list"]:
    if os.environ.get("STUB_LIST_FAIL"):
        print("HTTP 502: Bad Gateway", file=sys.stderr)
        sys.exit(1)
    print(os.environ["STUB_LIST"])
"""

CREATE = ["issue", "create", "--repo", REPO, "--title", TITLE]
NEXT = "- Checkpoint next index (everything before it is judged): "
LIST = [
    "issue", "list", "--repo", REPO, "--state", "open",
    "--limit", "1000", "--json", "number,title,body",
]  # fmt: skip

# The budget run: stele verified 448,222,427 and judged nothing from
# 448,196,522; its stopped fact, verbatim.
BUDGET_STOPPED = (
    "budget: ctlog: the run's budget ended waiting 5s on ctlog: get-entries"
    " 448196522..448206521: the log or the transport failed, which says"
    " nothing about its entries: context deadline exceeded"
)


NOT_ADVANCED = (
    f"the checkpoint did not advance: stele judged nothing ({BUDGET_STOPPED})"
)


def load(name: str) -> dict:
    """Read one captured stele document.

    Returns:
        The document.

    """
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def advanced_report() -> dict:
    """Make the budget run's report one whose run advanced.

    Edits `judged` (448196522..448206521, one chunk) and `checkpointNext`
    of the real CANNOT_JUDGE report: no live run advanced and stopped.

    Returns:
        The edited report.

    """
    doc = load("report-budget.json")
    for fact in doc["facts"]:
        if fact["name"] == "judged":
            fact["value"] = "448196522..448206521"
        if fact["name"] == "checkpointNext":
            fact["value"] = "448206522"
    return doc


def advanced_checkpoint(age: dt.timedelta) -> dict:
    """Make the budget run's checkpoint one chunk on, its head `age` old.

    Returns:
        The edited checkpoint (`next` and `treeHead.timestamp`).

    """
    doc = load("checkpoint-budget.json")
    doc["next"] = 448206522
    when = dt.datetime.now(dt.UTC) - age
    doc["treeHead"]["timestamp"] = int(when.timestamp() * 1000)
    return doc


def previous_body(since: str, runs: int) -> str:
    """Build an earlier body the helper wrote.

    Returns:
        A body with the two lines an edit carries forward.

    """
    return (
        "The signer-identity watch is behind ...\n\n"
        "- Checkpoint next index (everything before it is judged): 445,241,315\n"
        f"- Behind since: {since}\n"
        "- Latest run behind: https://example.invalid/old\n"
        f"- Runs behind since it opened: {runs}\n"
    )


class Rows(unittest.TestCase):
    """A stub gh, a captured report and checkpoints, and the helper run."""

    def setUp(self) -> None:
        """Lay out a stub gh, its call log, a report and checkpoints."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "gh"
        stub.write_text(f"#!{sys.executable}\n{STUB}", encoding="utf-8")
        stub.chmod(0o755)
        self.calls = self.dir / "calls.jsonl"
        self.output = self.dir / "github_output"
        self.report = self.dir / "report.json"
        self.checkpoint = self.dir / "checkpoint.json"
        self.before = self.dir / "checkpoint-before.json"
        self.write(self.report, load("report-budget.json"))
        self.write(self.checkpoint, load("checkpoint-budget.json"))
        self.write(self.before, load("checkpoint-budget.json"))
        self.env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_OUTPUT": str(self.output),
            "STUB_CALLS": str(self.calls),
            "STUB_LIST": "[]",
        }

    @staticmethod
    def write(path: Path, doc: dict) -> None:
        """Write a document where the helper reads it."""
        path.write_text(json.dumps(doc), encoding="utf-8")

    def run_helper(
        self,
        code: str = "4",
        listing: list[dict] | None = None,
        saved: str = "true",
        **env: str,
    ) -> subprocess.CompletedProcess[str]:
        """Run the helper as the workflow does.

        Returns:
            The finished process.

        """
        full = {**self.env, **env}
        if listing is not None:
            full["STUB_LIST"] = json.dumps(listing)
        argv = [
            sys.executable, str(HELPER), "--exit-code", code,
            "--report", str(self.report), "--checkpoint", str(self.checkpoint),
            "--before", str(self.before), "--saved", saved, "--run-url", RUN,
        ]  # fmt: skip
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
            argv, env=full, capture_output=True, text=True, check=False
        )

    def gh_calls(self) -> list[list[str]]:
        """Read what the stub gh was called with.

        Returns:
            Each call's argv, in order.

        """
        if not self.calls.exists():
            return []
        lines = self.calls.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def dispatch(self) -> str:
        """Read what the helper asked of the dispatch step.

        Returns:
            The `dispatch=` line it wrote, or "" when it wrote none.

        """
        if not self.output.exists():
            return ""
        return self.output.read_text(encoding="utf-8").strip()


class StandingIssue(Rows):
    """The standing issue (#1353), row for row."""

    def test_no_open_issue_creates_it(self) -> None:
        """Not advanced, no open issue: red, one create, under the exact title."""
        proc = self.run_helper("4", [])
        self.assertEqual(proc.returncode, 1, proc.stderr)
        calls = self.gh_calls()
        self.assertEqual(calls[0], LIST)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][:6], CREATE)
        self.assertEqual(calls[1][6], "--body")
        text = calls[1][7]
        self.assertIn(f"{NEXT}448,196,522\n", text)
        head = "- Checkpoint signed tree head: 448,222,427 at 2026-11-17T18:32:08Z\n"
        self.assertIn(head, text)
        self.assertIn("- Log size this run: 448,222,427\n", text)
        self.assertIn("- Entries outstanding: 25,905\n", text)
        self.assertIn(
            f"- This run: {NOT_ADVANCED}\n",
            text,
        )
        self.assertIn(f"- Behind since: {RUN}\n", text)
        self.assertIn(f"- Latest run behind: {RUN}\n", text)
        self.assertIn("- Runs behind since it opened: 1\n", text)
        self.assertIn("standing-issue: opened the standing issue", proc.stdout)
        self.assertIn(
            f"::error::{NOT_ADVANCED}",
            proc.stdout,
        )
        self.assertEqual(self.dispatch(), "dispatch=false")

    def test_one_open_edits_it(self) -> None:
        """One open: edit that number, keep "behind since", count the run."""
        listing = [
            {"number": 7, "title": "unrelated", "body": ""},
            {"number": 42, "title": TITLE, "body": previous_body(FIRST_RUN, 3)},
        ]
        proc = self.run_helper("4", listing)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        calls = self.gh_calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][:5], ["issue", "edit", "42", "--repo", REPO])
        self.assertEqual(calls[1][5], "--body")
        text = calls[1][6]
        self.assertIn(f"- Behind since: {FIRST_RUN}\n", text)
        self.assertIn(f"- Latest run behind: {RUN}\n", text)
        self.assertIn("- Runs behind since it opened: 4\n", text)
        self.assertNotIn("create", [c[1] for c in calls])
        self.assertIn("updated the standing issue #42 (4 runs behind)", proc.stdout)

    def test_resembling_title_is_ignored(self) -> None:
        """A title that only resembles the fixed one is not the issue."""
        listing = [
            {"number": 9, "title": f"{TITLE} (run 1)", "body": ""},
            {"number": 10, "title": "rekor-monitor: failure (run 1)", "body": ""},
            {"number": 11, "title": TITLE.upper(), "body": ""},
        ]
        proc = self.run_helper("4", listing)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        calls = self.gh_calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][:6], CREATE)
        self.assertIn(f"- Behind since: {RUN}\n", calls[1][7])

    def test_list_failure_files_nothing(self) -> None:
        """Gh issue list failing: exit 2 by name, nothing created."""
        proc = self.run_helper("4", STUB_LIST_FAIL="1")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.gh_calls(), [LIST])
        self.assertEqual(
            proc.stderr.strip(),
            "standing-issue: gh issue list failed (exit 1): HTTP 502: Bad Gateway;"
            " nothing was filed",
        )

    def test_caught_up_closes_open_issue(self) -> None:
        """Exit 0, saved, one open: green, close it with one comment."""
        self.write(self.report, load("report-pass.json"))
        self.write(self.checkpoint, load("checkpoint-pass.json"))
        listing = [{"number": 42, "title": TITLE, "body": previous_body(FIRST_RUN, 3)}]
        proc = self.run_helper("0", listing)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            self.gh_calls(),
            [
                LIST,
                [
                    "issue", "close", "42", "--repo", REPO, "--comment",
                    (
                        f"Caught up: {RUN} judged the log to its head,"
                        " tree size 448,221,522."
                    ),
                ],
            ],
        )  # fmt: skip
        self.assertIn("standing-issue: closed #42", proc.stdout)
        self.assertIn(
            "standing-issue: the checkpoint that persists (saved by this run):"
            " next index 448221522",
            proc.stdout,
        )
        green = "standing-issue: green: judged to the head of the log"
        self.assertIn(green, proc.stdout)
        self.assertEqual(self.dispatch(), "dispatch=false")

    def test_caught_up_with_none_open_does_nothing(self) -> None:
        """Exit 0 with none open: list only, green."""
        self.write(self.report, load("report-pass.json"))
        self.write(self.checkpoint, load("checkpoint-pass.json"))
        proc = self.run_helper("0", [{"number": 7, "title": "x", "body": ""}])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.gh_calls(), [LIST])
        self.assertIn("no standing issue open", proc.stdout)

    def test_no_checkpoint_is_named(self) -> None:
        """Stele refused (no checkpoint, no start-at): red, the body says so."""
        self.checkpoint.unlink()
        self.before.unlink()
        self.report.write_text("", encoding="utf-8")
        proc = self.run_helper("2", [], saved="false")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        text = self.gh_calls()[1][7]
        missing = "unknown: no checkpoint file\n"
        self.assertIn(f"{NEXT}{missing}", text)
        self.assertIn(f"- Checkpoint signed tree head: {missing}", text)
        self.assertIn(f"- Entries outstanding: {missing}", text)
        self.assertIn(
            "::error::stele exited 2 and the checkpoint was not saved:"
            " stele wrote no report",
            proc.stdout,
        )

    def test_unreadable_checkpoint_is_named(self) -> None:
        """A checkpoint that is not stele's: named as unreadable."""
        self.before.write_text("not json\n", encoding="utf-8")
        proc = self.run_helper("4", [], saved="false")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        text = self.gh_calls()[1][7]
        unreadable = "unknown: the checkpoint file could not be read\n"
        self.assertIn(f"{NEXT}{unreadable}", text)
        self.assertIn(f"- Checkpoint signed tree head: {unreadable}", text)

    def test_no_verified_head_is_named(self) -> None:
        """No report, so no head verified this run: named, never a blank."""
        self.report.unlink()
        proc = self.run_helper("4", [], saved="false")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        text = self.gh_calls()[1][7]
        gone = "unknown: stele verified no tree head this run\n"
        self.assertIn(f"- Log size this run: {gone}", text)
        self.assertIn(f"- Entries outstanding: {gone}", text)

    def test_previous_body_unreadable(self) -> None:
        """An edited-by-hand body: "behind since" named as lost, count restarts."""
        listing = [{"number": 42, "title": TITLE, "body": "hand-written"}]
        proc = self.run_helper("4", listing)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        text = self.gh_calls()[1][6]
        self.assertIn("- Behind since: unknown: not in the previous body\n", text)
        self.assertIn("- Runs behind since it opened: 1\n", text)

    def test_no_repository(self) -> None:
        """GITHUB_REPOSITORY unset: exit 2 by name, gh never called."""
        proc = self.run_helper("4", [], GITHUB_REPOSITORY="")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.gh_calls(), [])
        self.assertEqual(
            proc.stderr.strip(), "standing-issue: GITHUB_REPOSITORY is not set"
        )


class Outcome(Rows):
    """Stele's outcome (#1339), one row per branch of judge()."""

    def test_advanced_is_green_and_reads_on(self) -> None:
        """Exit 4, advanced, head fresh: green, no issue, dispatch the next run."""
        self.write(self.report, advanced_report())
        self.write(self.checkpoint, advanced_checkpoint(dt.timedelta(minutes=40)))
        proc = self.run_helper("4", [])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.gh_calls(), [])
        self.assertIn(
            "standing-issue: green: advanced; outstanding 448196522..448222426"
            f" ({BUDGET_STOPPED})",
            proc.stdout,
        )
        self.assertEqual(self.dispatch(), "dispatch=true")

    def test_advanced_with_a_stale_head_is_behind(self) -> None:
        """Exit 4, advanced, head over 24 hours old: green, behind, dispatch."""
        self.write(self.report, advanced_report())
        self.write(self.checkpoint, advanced_checkpoint(dt.timedelta(hours=25)))
        proc = self.run_helper("4", [])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = self.gh_calls()
        self.assertEqual(calls[1][:6], CREATE)
        self.assertIn(f"{NEXT}448,206,522\n", calls[1][7])
        self.assertIn("- Entries outstanding: 15,905\n", calls[1][7])
        self.assertEqual(self.dispatch(), "dispatch=true")

    def test_advanced_with_a_finding_is_red(self) -> None:
        """Exit 4, advanced, a finding in the report: red, still reads on."""
        doc = advanced_report()
        doc["findings"] = [
            {"subject": "CT index 448200000", "assertion": "run", "detail": "x"}
        ]
        self.write(self.report, doc)
        self.write(self.checkpoint, advanced_checkpoint(dt.timedelta(minutes=40)))
        proc = self.run_helper("4", [])
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(self.gh_calls(), [])
        self.assertIn(
            "::error::1 finding(s) in the chunks judged; each has its issue",
            proc.stdout,
        )
        self.assertEqual(self.dispatch(), "dispatch=true")

    def test_fail_is_red_without_the_standing_issue(self) -> None:
        """Exit 1, saved: red by the findings, no standing issue, no dispatch."""
        doc = load("report-pass.json")
        doc["verdict"] = "FAIL"
        doc["findings"] = [
            {"subject": "CT index 448221200", "assertion": "foreign", "detail": "x"},
            {"subject": "CT index 448221200", "assertion": "chain", "detail": "y"},
        ]
        self.write(self.report, doc)
        self.write(self.checkpoint, load("checkpoint-pass.json"))
        proc = self.run_helper("1", [])
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(self.gh_calls(), [])
        self.assertIn(
            "::error::2 finding(s): a certificate did not reconcile;"
            " each has its issue",
            proc.stdout,
        )
        self.assertEqual(self.dispatch(), "dispatch=false")

    def test_not_saved_names_the_restored_checkpoint(self) -> None:
        """Exit 0 but not saved: red, behind, the body reads the restored file."""
        self.write(self.report, load("report-pass.json"))
        self.write(self.checkpoint, load("checkpoint-pass.json"))
        proc = self.run_helper("0", [], saved="false")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        text = self.gh_calls()[1][7]
        self.assertIn(f"{NEXT}448,196,522\n", text)
        self.assertIn(
            "standing-issue: the checkpoint that persists"
            " (restored, not saved by this run): next index 448196522",
            proc.stdout,
        )
        self.assertIn(
            "::error::stele exited 0 and the checkpoint was not saved:"
            " a finding could not be filed",
            proc.stdout,
        )
        self.assertEqual(self.dispatch(), "dispatch=false")

    def test_killed_run_is_behind(self) -> None:
        """No exit status (cancelled or timed out): red, behind, restored file."""
        proc = self.run_helper("", [], saved="false")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        text = self.gh_calls()[1][7]
        self.assertIn(f"{NEXT}448,196,522\n", text)
        self.assertIn(
            "::error::stele did not finish: no exit status was recorded"
            " (a step before it failed, or the job was cancelled or timed out)",
            proc.stdout,
        )

    def test_other_exit_is_behind(self) -> None:
        """A saved run that exited 3: red, behind, named."""
        proc = self.run_helper("3", [])
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(self.gh_calls()[1][:6], CREATE)
        self.assertIn("::error::stele exited 3", proc.stdout)
        self.assertEqual(self.dispatch(), "dispatch=false")

    def test_founding_run_with_no_before(self) -> None:
        """The start-at run: no restored file, advanced and saved, reads on."""
        self.before.unlink()
        self.write(self.report, advanced_report())
        self.write(self.checkpoint, advanced_checkpoint(dt.timedelta(minutes=40)))
        proc = self.run_helper("4", [])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.gh_calls(), [])
        self.assertEqual(self.dispatch(), "dispatch=true")

    def test_report_from_another_target(self) -> None:
        """A report that is not signer-certificates': named, never read on."""
        doc = load("report-budget.json")
        doc["target"] = "assert tags"
        self.write(self.report, doc)
        proc = self.run_helper("4", [], saved="false")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn(
            "::error::stele exited 4 and the checkpoint was not saved:"
            " the report is not stele's assert signer-certificates report",
            proc.stdout,
        )


if __name__ == "__main__":
    unittest.main()
