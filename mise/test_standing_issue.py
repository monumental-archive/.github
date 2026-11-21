#!/usr/bin/env python3
"""standing_issue.py's report against a stub `gh` on PATH (#1887).

The stub records every argv it is called with and answers four reads
from the row: the run's job list (one JSON job per line, as `--jq
'.jobs[]'` prints it), the open issues, the labels, and each job's log
from a file named for its id. Each row calls `main` in this process
with the report job's arguments and asserts the exact calls gh
received and the message.

stdlib `unittest`, run through the gate as `mise run test`.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic
import standing_issue as standing

REPO = "example-owner/example-repo"
WORKFLOW = "slsa"
TITLE = "slsa is red"
RUN_ID = "200"
RUN = f"https://github.com/{REPO}/actions/runs/{RUN_ID}"
FIRST = f"https://github.com/{REPO}/actions/runs/100"
JOBS_API = f"repos/{REPO}/actions/runs/{RUN_ID}/jobs?filter=latest&per_page=100"

STUB = """\
import json, os, sys
with open(os.environ["STUB_CALLS"], "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
if args[:2] == ["issue", "list"]:
    if os.environ.get("STUB_LIST_FAIL"):
        print("HTTP 502: Bad Gateway", file=sys.stderr)
        sys.exit(1)
    print(os.environ["STUB_LIST"])
elif args[:2] == ["label", "list"]:
    print(os.environ["STUB_LABELS"])
elif args[:2] == ["api", "--paginate"]:
    print(os.environ["STUB_JOBS"])
elif args[0] == "api" and args[1].endswith("/logs"):
    job = args[1].split("/")[-2]
    path = os.path.join(os.environ["STUB_LOGS"], job + ".log")
    if not os.path.exists(path):
        print("HTTP 410: Gone", file=sys.stderr)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        sys.stdout.write(f.read())
"""

LIST: list[str] = [
    "issue", "list", "--repo", REPO, "--state", "open",
    "--limit", "1000", "--json", "number,title,body",
]  # fmt: skip
JOBS: list[str] = ["api", "--paginate", "--jq", ".jobs[]", JOBS_API]
LABELS: list[str] = [
    "label",
    "list",
    "--repo",
    REPO,
    "--limit",
    "1000",
    "--json",
    "name",
]


def job(
    number: int, name: str, conclusion: str | None, status: str = "completed"
) -> dict[str, object]:
    """Build one job as the job list gives it.

    Returns:
        The job's fields the report reads.

    """
    url = f"https://github.com/{REPO}/actions/runs/{RUN_ID}/job/{number}"
    return {
        "id": number,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "html_url": url,
    }


SELF = job(9, "Report red jobs", None, "in_progress")


def log_api(number: int) -> list[str]:
    """Build the call that fetches a job's log.

    Returns:
        gh's argv.

    """
    return ["api", f"repos/{REPO}/actions/jobs/{number}/logs"]


def previous(since: str, runs: int, latest: str = "https://example.invalid/old") -> str:
    """Build an earlier body the report wrote.

    Returns:
        A body with the lines an edit carries forward.

    """
    return (
        f"...\n\n- Latest red run: {latest}\n- Red since: {since}\n- Red runs: {runs}\n"
    )


def logged(*lines: str) -> str:
    """Write a job log as the forge serves it, timestamped.

    Returns:
        The log text.

    """
    return "".join(
        f"2026-11-20T07:00:{at % 60:02d}.1234567Z {line}\n"
        for at, line in enumerate(lines)
    )


class Rows(unittest.TestCase):
    """A stub gh, the run's job list, and the report run."""

    @override
    def setUp(self) -> None:
        """Lay out a stub gh and a run with one red job."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "gh"
        stub.write_text(f"#!{sys.executable}\n{STUB}", encoding="utf-8")
        stub.chmod(0o755)
        self.logs = self.dir / "logs"
        self.logs.mkdir()
        self.calls = self.dir / "calls.jsonl"
        self.env = hermetic.env(
            PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            GITHUB_REPOSITORY=REPO,
            GITHUB_SERVER_URL="https://github.com",
            STUB_CALLS=str(self.calls),
            STUB_LIST="[]",
            STUB_LABELS=json.dumps([{"name": "hold"}, {"name": "red-check"}]),
        )
        self.jobs([job(1, "plan", "success"), job(2, "caller permissions", "failure")])
        self.log(
            2,
            logged(
                "walking",
                "assert: edtf/release.yml: caller-grant",
                "##[error]Process completed with exit code 1.",
            ),
        )

    def jobs(self, jobs: list[dict[str, object]]) -> None:
        """Set the run's job list; the report job itself is always in it."""
        self.env["STUB_JOBS"] = "\n".join(json.dumps(j) for j in [*jobs, SELF])

    def log(self, number: int, text: str) -> None:
        """Serve a job's log."""
        (self.logs / f"{number}.log").write_text(text, encoding="utf-8")

    def run_report(
        self, *, ref: str = "refs/heads/main", default: str = "main"
    ) -> hermetic.Called:
        """Run the report as the job runs it.

        Returns:
            The status and what it printed.

        """
        argv = [
            "--workflow",
            WORKFLOW,
            "--run-id",
            RUN_ID,
            "--ref",
            ref,
            "--default-branch",
            default,
        ]
        env = {**self.env, "STUB_LOGS": str(self.logs)}
        return hermetic.call_main(lambda: standing.main(argv), env=env)

    def gh_calls(self) -> list[list[str]]:
        """Read every argv the stub gh received.

        Returns:
            One argv per call, in order.

        """
        if not self.calls.exists():
            return []
        lines = self.calls.read_text(encoding="utf-8").splitlines()
        return [
            [
                decoded.as_str(a, "arg")
                for a in decoded.as_array(decoded.load_json(line), "argv")
            ]
            for line in lines
        ]

    @staticmethod
    def body_of(call: list[str]) -> str:
        """Read the --body a create or edit sent.

        Returns:
            The body.

        """
        return call[call.index("--body") + 1]

    def test_red_creates_with_title_and_label(self) -> None:
        """Red, none open: one issue, exact title, the label."""
        proc = self.run_report()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = self.gh_calls()
        self.assertEqual(calls[:4], [JOBS, LIST, log_api(2), LABELS])
        self.assertEqual(len(calls), 5)
        create = calls[4]
        self.assertEqual(
            create[:6], ["issue", "create", "--repo", REPO, "--title", TITLE]
        )
        self.assertEqual(create[-2:], ["--label", "red-check"])
        text = self.body_of(create)
        self.assertIn(f"- Latest red run: {RUN}\n", text)
        self.assertIn(f"- Red since: {RUN}\n", text)
        self.assertIn("- Red runs: 1\n", text)
        self.assertIn("### caller permissions\n", text)
        self.assertIn("<summary>log tail</summary>", text)
        self.assertIn(
            "```text\nwalking\nassert: edtf/release.yml: caller-grant\n```", text
        )
        self.assertNotIn("##[error]", text)
        self.assertNotIn("2026-11-20T", text)
        self.assertIn(f"red-check: opened '{TITLE}'", proc.stdout)

    def test_red_with_one_open_replaces_and_keeps_since(self) -> None:
        """Red, one open: its body is replaced, Red since kept, the run counted."""
        self.env["STUB_LIST"] = json.dumps([
            {"number": 42, "title": TITLE, "body": previous(FIRST, 3)}
        ])
        proc = self.run_report()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = self.gh_calls()
        self.assertEqual(calls[:3], [JOBS, LIST, log_api(2)])
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[3][:5], ["issue", "edit", "42", "--repo", REPO])
        text = self.body_of(calls[3])
        self.assertIn(f"- Red since: {FIRST}\n", text)
        self.assertIn(f"- Latest red run: {RUN}\n", text)
        self.assertIn("- Red runs: 4\n", text)
        self.assertIn("updated #42 (4 red runs)", proc.stdout)

    def test_same_run_is_counted_once(self) -> None:
        """A second report in one run does not count the run twice."""
        self.env["STUB_LIST"] = json.dumps([
            {"number": 42, "title": TITLE, "body": previous(FIRST, 3, RUN)}
        ])
        proc = self.run_report()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("- Red runs: 3\n", self.body_of(self.gh_calls()[3]))

    def test_green_closes_with_the_run(self) -> None:
        """Every job green or skipped, one open: closed, naming the run."""
        self.jobs([job(1, "plan", "success"), job(2, "source-vsa", "skipped")])
        self.env["STUB_LIST"] = json.dumps([
            {"number": 42, "title": TITLE, "body": previous(FIRST, 3)}
        ])
        proc = self.run_report()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        comment = f"Green: {RUN} ran every job green or skipped."
        closed = ["issue", "close", "42", "--repo", REPO, "--comment", comment]
        self.assertEqual(self.gh_calls(), [JOBS, LIST, closed])
        self.assertIn("green: closed #42", proc.stdout)

    def test_green_with_none_open_writes_nothing(self) -> None:
        """Green, none open: the two reads and nothing else."""
        self.jobs([job(1, "plan", "success")])
        proc = self.run_report()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.gh_calls(), [JOBS, LIST])
        self.assertIn("green: no red-check issue open", proc.stdout)

    def test_cancelled_job_is_neither(self) -> None:
        """A cancelled job and no red one: nothing changes past the read.

        A cancelled RUN never reaches here: the step that runs the
        helper is `if: ${{ !cancelled() }}`, which test_report_job.py
        holds for every reporting workflow.
        """
        self.jobs([job(1, "plan", "success"), job(2, "drafts", "cancelled")])
        self.env["STUB_LIST"] = json.dumps([
            {"number": 42, "title": TITLE, "body": previous(FIRST, 3)}
        ])
        proc = self.run_report()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.gh_calls(), [JOBS])
        self.assertIn(
            "neither red nor green yet, nothing changed: drafts (cancelled)",
            proc.stdout,
        )

    def test_other_branch_calls_nothing(self) -> None:
        """A run on another branch changes nothing, and asks nothing."""
        proc = self.run_report(ref="refs/heads/feature")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.gh_calls(), [])
        self.assertIn(
            "refs/heads/feature is not the default branch (main)", proc.stdout
        )

    def test_failed_listing_files_nothing(self) -> None:
        """A listing that fails exits 2 by name and files nothing."""
        self.env["STUB_LIST_FAIL"] = "1"
        proc = self.run_report()
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.gh_calls(), [JOBS, LIST])
        self.assertIn(
            "gh issue list failed (exit 1): HTTP 502: Bad Gateway; nothing was filed",
            proc.stderr,
        )

    def test_two_open_is_a_named_error(self) -> None:
        """Two open issues with the title: a named error, never a third."""
        listing = [
            {"number": n, "title": TITLE, "body": previous(FIRST, 1)} for n in (41, 42)
        ]
        self.env["STUB_LIST"] = json.dumps(listing)
        proc = self.run_report()
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.gh_calls(), [JOBS, LIST])
        self.assertIn(
            f"2 open issues are titled exactly '{TITLE}' (#41, #42)", proc.stderr
        )

    def test_resembling_title_is_not_the_issue(self) -> None:
        """A title that only resembles is never edited."""
        self.env["STUB_LIST"] = json.dumps([
            {"number": 7, "title": f"{TITLE} again", "body": ""}
        ])
        proc = self.run_report()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.gh_calls()[-1][:2], ["issue", "create"])

    def test_missing_label_is_created_from_the_baseline(self) -> None:
        """No red-check label: created from settings/repo-baseline.json first."""
        self.env["STUB_LABELS"] = json.dumps([{"name": "hold"}])
        proc = self.run_report()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        label = standing.label_definition()
        made = [
            "label", "create", "red-check", "--repo", REPO,
            "--color", str(label["color"]), "--description", str(label["description"]),
        ]  # fmt: skip
        calls = self.gh_calls()
        self.assertEqual(calls[4], made)
        self.assertEqual(calls[5][:2], ["issue", "create"])
        self.assertIn("created the red-check label; opened", proc.stdout)

    def test_unfinished_sibling_changes_nothing(self) -> None:
        """Green so far with a caller's job still running: no close yet."""
        self.jobs([
            job(1, "records / links", "success"),
            job(3, "citations", None, "in_progress"),
        ])
        self.env["STUB_LIST"] = json.dumps([
            {"number": 42, "title": TITLE, "body": previous(FIRST, 3)}
        ])
        proc = self.run_report()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.gh_calls(), [JOBS])
        self.assertIn("neither red nor green yet", proc.stdout)

    def test_timed_out_is_red(self) -> None:
        """A job that timed out is red."""
        self.jobs([job(4, "watch (leg 1)", "timed_out")])
        self.log(4, logged("reading"))
        proc = self.run_report()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("```text\nreading\n```", self.body_of(self.gh_calls()[-1]))

    def test_unreadable_log_is_named(self) -> None:
        """A log the forge will not serve is named in the entry, not fatal."""
        self.jobs([job(5, "drafts", "failure")])
        proc = self.run_report()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(
            "_Log tail unknown: the log could not be read: gh api repos/",
            self.body_of(self.gh_calls()[-1]),
        )


class Body(unittest.TestCase):
    """The body's entries, fence and size, without gh."""

    @staticmethod
    def red(entries: list[standing.Entry]) -> str:
        """Write a body for a run.

        Returns:
            The body.

        """
        return standing.body(standing.Red(WORKFLOW, "main", RUN, FIRST, 2), entries)

    @staticmethod
    def entry(number: int, tail: str) -> standing.Entry:
        """Build a red job's entry.

        Returns:
            The entry.

        """
        made = job(number, f"job {number}", "failure")
        url = decoded.as_str(made["html_url"], "url")
        return standing.Entry(
            standing.Job(number, f"job {number}", "completed", "failure", url),
            tail,
            None,
        )

    def test_two_red_jobs_two_entries(self) -> None:
        """Two red jobs give two entries, each with its tail."""
        text = self.red([self.entry(1, "first tail"), self.entry(2, "second tail")])
        self.assertEqual(text.count("<summary>log tail</summary>"), 2)
        self.assertLess(text.index("### job 1"), text.index("first tail"))
        self.assertLess(text.index("first tail"), text.index("### job 2"))
        self.assertLess(text.index("### job 2"), text.index("second tail"))

    def test_backticks_cannot_close_the_fence(self) -> None:
        """A log line of three backticks sits inside a longer fence."""
        text = self.red([self.entry(1, "before\n```\nafter")])
        self.assertIn("````text\nbefore\n```\nafter\n````", text)

    def test_long_body_drops_tails_from_the_last(self) -> None:
        """Over 60,000 characters, the last jobs' tails go first, said so."""
        tails = [self.entry(n, f"tail {n}\n" + "x" * 3990) for n in range(1, 21)]
        text = self.red(tails)
        self.assertLess(len(text), standing.BODY_LIMIT)
        self.assertIn("tail 1\n", text)
        self.assertNotIn("tail 20\n", text)
        self.assertIn(
            "_Log tail dropped: the body would pass 60,000 characters._", text
        )
        self.assertRegex(text, r"Log tails dropped for the last \d+ job\(s\)")

    def test_tail_is_sixty_lines_before_the_first_error(self) -> None:
        """The tail ends before the first error, 60 lines, no colour codes."""
        lines = [f"\x1b[36mline {n}\x1b[0m" for n in range(100)]
        log = logged(*lines, "##[error]boom", "after", "##[error]again")
        cut = standing.tail(log).splitlines()
        self.assertEqual(cut, [f"line {n}" for n in range(40, 100)])

    def test_tail_is_at_most_four_thousand(self) -> None:
        """A tail longer than 4,000 characters keeps its last whole lines."""
        width = 199
        cut = standing.tail(logged(*["y" * width for _ in range(60)]))
        self.assertLessEqual(len(cut), standing.TAIL_CHARS)
        self.assertEqual({len(line) for line in cut.splitlines()}, {width})


if __name__ == "__main__":
    unittest.main()
