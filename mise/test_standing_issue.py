#!/usr/bin/env python3
"""standing_issue.py's report against a stub `gh` on PATH (#1887).

The stub records every argv it is called with and answers three reads
from the row: the run's job list (one JSON job per line, as `--jq
'.jobs[]'` prints it), the open issues and the labels. Job logs come
from a local HTTP server standing in for the API (#1925): the job-log
path answers 302 to a blob path, which serves the file named for the
job's id, or 410 when there is none; it records each request's
Authorization header. Each row calls `main` in this process with the
report job's arguments and asserts the exact calls gh received and the
message.

stdlib `unittest`, run through the gate as `mise run test`.
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import tempfile
import threading
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
TESTDATA = Path(__file__).resolve().parent / "testdata" / "job-log"

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
"""
BEARER = "stub-bearer"

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


def issues(*found: tuple[int, str, str]) -> str:
    """Write an `issue list` answer.

    Returns:
        The JSON gh would print.

    """
    listing: list[dict[str, object]] = [
        {"number": n, "title": title, "body": body} for n, title, body in found
    ]
    return json.dumps(listing)


def labels(*names: str) -> str:
    """Write a `label list` answer.

    Returns:
        The JSON gh would print.

    """
    listing: list[dict[str, str]] = [{"name": name} for name in names]
    return json.dumps(listing)


def same[T](case: unittest.TestCase, got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    case.assertEqual(got, want, msg or None)


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


class Forge(http.server.BaseHTTPRequestHandler):
    """The API's job-log path and the blob host it redirects to."""

    logs: Path
    seen: list[tuple[str, str | None]]

    def do_GET(self) -> None:
        """Redirect a job-log read to its blob, and serve the blob."""
        type(self).seen.append((self.path, self.headers.get("Authorization")))
        prefix = f"/repos/{REPO}/actions/jobs/"
        if self.path.startswith(prefix) and self.path.endswith("/logs"):
            number = self.path.removeprefix(prefix).removesuffix("/logs")
            self.send_response(302)
            self.send_header("Location", f"/blob/{number}")
            self.end_headers()
            return
        found = self.logs / f"{self.path.removeprefix('/blob/')}.log"
        if not self.path.startswith("/blob/") or not found.exists():
            self.send_response(410)
            self.end_headers()
            return
        data = found.read_bytes()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @override
    def log_message(self, *_: object) -> None:
        """Keep the test output quiet."""


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


class Stubbed(unittest.TestCase):
    """A stub gh, a stub forge for logs, the run's job list, the report run."""

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
        self.seen: list[tuple[str, str | None]] = []

        class Served(Forge):
            """The row's own forge: its logs and what it saw."""

        Served.logs = self.logs
        Served.seen = self.seen
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Served)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.calls = self.dir / "calls.jsonl"
        self.env = hermetic.env(
            PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            GITHUB_REPOSITORY=REPO,
            GITHUB_SERVER_URL="https://github.com",
            GITHUB_API_URL=f"http://127.0.0.1:{server.server_address[1]}",
            GH_TOKEN=BEARER,
            STUB_CALLS=str(self.calls),
            STUB_LIST="[]",
            STUB_LABELS=labels("hold", "red-check"),
        )
        self.jobs([job(1, "plan", "success"), job(2, "caller permissions", "failure")])
        self.log(
            2,
            logged(
                "##[group]Run mise run lint:caller",
                "mise run lint:caller",
                "##[endgroup]",
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
        return hermetic.call_main(lambda: standing.main(argv), env=self.env)

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


class Rows(Stubbed):
    """The report's outcomes, by the calls gh received."""

    def test_red_creates_with_title_and_label(self) -> None:
        """Red, none open: one issue, exact title, the label."""
        proc = self.run_report()
        same(self, proc.returncode, 0, proc.stderr)
        calls = self.gh_calls()
        same(self, calls[:3], [JOBS, LIST, LABELS])
        same(self, len(calls), 4)
        create = calls[3]
        same(self, create[:6], ["issue", "create", "--repo", REPO, "--title", TITLE])
        same(self, create[-2:], ["--label", "red-check"])
        text = self.body_of(create)
        self.assertIn(f"- Latest red run: {RUN}\n", text)
        self.assertIn(f"- Red since: {RUN}\n", text)
        self.assertIn("- Red runs: 1\n", text)
        self.assertIn("### caller permissions\n", text)
        self.assertIn(
            "<summary>failing step: <code>mise run lint:caller</code></summary>", text
        )
        self.assertIn(
            "```text\nwalking\nassert: edtf/release.yml: caller-grant\n```", text
        )
        self.assertNotIn("##[error]", text)
        self.assertNotIn("2026-11-20T", text)
        self.assertIn(f"red-check: opened '{TITLE}'", proc.stdout)

    def test_red_with_one_open_replaces_and_keeps_since(self) -> None:
        """Red, one open: its body is replaced, Red since kept, the run counted."""
        self.env["STUB_LIST"] = issues((42, TITLE, previous(FIRST, 3)))
        proc = self.run_report()
        same(self, proc.returncode, 0, proc.stderr)
        calls = self.gh_calls()
        same(self, calls[:2], [JOBS, LIST])
        same(self, len(calls), 3)
        same(self, calls[2][:5], ["issue", "edit", "42", "--repo", REPO])
        text = self.body_of(calls[2])
        self.assertIn(f"- Red since: {FIRST}\n", text)
        self.assertIn(f"- Latest red run: {RUN}\n", text)
        self.assertIn("- Red runs: 4\n", text)
        self.assertIn("updated #42 (4 red runs)", proc.stdout)

    def test_same_run_is_counted_once(self) -> None:
        """A second report in one run does not count the run twice."""
        self.env["STUB_LIST"] = issues((42, TITLE, previous(FIRST, 3, RUN)))
        proc = self.run_report()
        same(self, proc.returncode, 0, proc.stderr)
        self.assertIn("- Red runs: 3\n", self.body_of(self.gh_calls()[2]))

    def test_green_closes_with_the_run(self) -> None:
        """Every job green or skipped, one open: closed, naming the run."""
        self.jobs([job(1, "plan", "success"), job(2, "source-vsa", "skipped")])
        self.env["STUB_LIST"] = issues((42, TITLE, previous(FIRST, 3)))
        proc = self.run_report()
        same(self, proc.returncode, 0, proc.stderr)
        comment = f"Green: {RUN} ran every job green or skipped."
        closed = ["issue", "close", "42", "--repo", REPO, "--comment", comment]
        same(self, self.gh_calls(), [JOBS, LIST, closed])
        self.assertIn("green: closed #42", proc.stdout)

    def test_green_with_none_open_writes_nothing(self) -> None:
        """Green, none open: the two reads and nothing else."""
        self.jobs([job(1, "plan", "success")])
        proc = self.run_report()
        same(self, proc.returncode, 0, proc.stderr)
        same(self, self.gh_calls(), [JOBS, LIST])
        self.assertIn("green: no red-check issue open", proc.stdout)

    def test_cancelled_job_is_neither(self) -> None:
        """A cancelled job and no red one: nothing changes past the read.

        A cancelled RUN never reaches here: the step that runs the
        helper is `if: ${{ !cancelled() }}`, which test_report_job.py
        holds for every reporting workflow.
        """
        self.jobs([job(1, "plan", "success"), job(2, "drafts", "cancelled")])
        self.env["STUB_LIST"] = issues((42, TITLE, previous(FIRST, 3)))
        proc = self.run_report()
        same(self, proc.returncode, 0, proc.stderr)
        same(self, self.gh_calls(), [JOBS])
        self.assertIn(
            "neither red nor green yet, nothing changed: drafts (cancelled)",
            proc.stdout,
        )

    def test_other_branch_calls_nothing(self) -> None:
        """A run on another branch changes nothing, and asks nothing."""
        proc = self.run_report(ref="refs/heads/feature")
        same(self, proc.returncode, 0, proc.stderr)
        same(self, self.gh_calls(), [])
        self.assertIn(
            "refs/heads/feature is not the default branch (main)", proc.stdout
        )

    def test_failed_listing_files_nothing(self) -> None:
        """A listing that fails exits 2 by name and files nothing."""
        self.env["STUB_LIST_FAIL"] = "1"
        proc = self.run_report()
        same(self, proc.returncode, 2)
        same(self, self.gh_calls(), [JOBS, LIST])
        self.assertIn(
            "gh issue list failed (exit 1): HTTP 502: Bad Gateway; nothing was filed",
            proc.stderr,
        )

    def test_two_open_is_a_named_error(self) -> None:
        """Two open issues with the title: a named error, never a third."""
        both = [(n, TITLE, previous(FIRST, 1)) for n in (41, 42)]
        self.env["STUB_LIST"] = issues(*both)
        proc = self.run_report()
        same(self, proc.returncode, 2)
        same(self, self.gh_calls(), [JOBS, LIST])
        self.assertIn(
            f"2 open issues are titled exactly '{TITLE}' (#41, #42)", proc.stderr
        )

    def test_resembling_title_is_not_the_issue(self) -> None:
        """A title that only resembles is never edited."""
        self.env["STUB_LIST"] = issues((7, f"{TITLE} again", ""))
        proc = self.run_report()
        same(self, proc.returncode, 0, proc.stderr)
        same(self, self.gh_calls()[-1][:2], ["issue", "create"])

    def test_missing_label_is_created_from_the_baseline(self) -> None:
        """No red-check label: created from settings/repo-baseline.json first."""
        self.env["STUB_LABELS"] = labels("hold")
        proc = self.run_report()
        same(self, proc.returncode, 0, proc.stderr)
        label = standing.label_definition()
        made = [
            "label", "create", "red-check", "--repo", REPO,
            "--color", str(label["color"]), "--description", str(label["description"]),
        ]  # fmt: skip
        calls = self.gh_calls()
        same(self, calls[3], made)
        same(self, calls[4][:2], ["issue", "create"])
        self.assertIn("created the red-check label; opened", proc.stdout)

    def test_unfinished_sibling_changes_nothing(self) -> None:
        """Green so far with a caller's job still running: no close yet."""
        self.jobs([
            job(1, "records / links", "success"),
            job(3, "citations", None, "in_progress"),
        ])
        self.env["STUB_LIST"] = issues((42, TITLE, previous(FIRST, 3)))
        proc = self.run_report()
        same(self, proc.returncode, 0, proc.stderr)
        same(self, self.gh_calls(), [JOBS])
        self.assertIn("neither red nor green yet", proc.stdout)

    def test_timed_out_is_red(self) -> None:
        """A job that timed out is red."""
        self.jobs([job(4, "watch (leg 1)", "timed_out")])
        self.log(4, logged("reading"))
        proc = self.run_report()
        same(self, proc.returncode, 0, proc.stderr)
        self.assertIn("```text\nreading\n```", self.body_of(self.gh_calls()[-1]))


class Logs(Stubbed):
    """How a red job's log is read (#1925)."""

    def test_unreadable_log_is_named(self) -> None:
        """A log the forge will not serve is named in the entry, not fatal."""
        self.jobs([job(5, "drafts", "failure")])
        proc = self.run_report()
        same(self, proc.returncode, 0, proc.stderr)
        self.assertIn(
            "_Log tail unknown: the log could not be read: GET"
            f" repos/{REPO}/actions/jobs/5/logs answered HTTP 410_",
            self.body_of(self.gh_calls()[-1]),
        )

    def test_no_token_is_named(self) -> None:
        """No GH_TOKEN: named in the entry, not fatal, nothing requested."""
        del self.env["GH_TOKEN"]
        proc = self.run_report()
        same(self, proc.returncode, 0, proc.stderr)
        self.assertIn("GH_TOKEN is not set_", self.body_of(self.gh_calls()[-1]))
        same(self, self.seen, [])

    def test_log_is_read_without_gh(self) -> None:
        """The log comes from the endpoint; gh never reads one."""
        proc = self.run_report()
        same(self, proc.returncode, 0, proc.stderr)
        reads = [
            call
            for call in self.gh_calls()
            if call[:2] == ["run", "view"]
            or (call[:1] == ["api"] and call[-1].endswith("/logs"))
        ]
        same(self, reads, [])
        self.assertIn("caller-grant", self.body_of(self.gh_calls()[-1]))

    def test_token_never_reaches_the_blob_host(self) -> None:
        """The API read carries the token; the redirected read does not."""
        proc = self.run_report()
        same(self, proc.returncode, 0, proc.stderr)
        same(
            self,
            self.seen,
            [
                (f"/repos/{REPO}/actions/jobs/2/logs", f"Bearer {BEARER}"),
                ("/blob/2", None),
            ],
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
            standing.Excerpt(tail, "step"),
            None,
        )

    def test_two_red_jobs_two_entries(self) -> None:
        """Two red jobs give two entries, each with its tail."""
        text = self.red([self.entry(1, "first tail"), self.entry(2, "second tail")])
        same(self, text.count("<summary>failing step: <code>step</code></summary>"), 2)
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

    def test_recorded_log_shows_the_failing_step(self) -> None:
        """The endpoint's own answer: the step's 11 lines, no codes."""
        want = [
            (
                "[audit:bestpractices] $ git ls-files --error-unmatch"
                " .bestpractices.json > /dev/null 2>&1 || {"
            ),
            (
                "audit:bestpractices: monumental-archive/release-lab (bestpractices.dev"
                " project 14067): the published entry differs from .bestpractices.json:"
            ),
            "  osps_br_02_01: justification differs",
            "  osps_br_04_01: justification differs",
            "  release_notes: justification differs",
            "  repo_distributed: justification differs",
            "  repo_track: justification differs",
            (
                '  test_statement_coverage80: justification differs; status "Unmet" on'
                ' the site, "Met" in the file'
            ),
            "  re-enter the form from the file (docs/best-practices.md); the file is",
            "  the source, never edit it to match the site (#1536)",
            "[audit:bestpractices] ERROR task failed",
        ]
        log = (TESTDATA / "release-lab-136940475864.log").read_text(encoding="utf-8")
        self.assertIn("\x1b[", log)
        cut = standing.tail(log)
        same(self, cut.step, "mise run audit:bestpractices")
        same(self, cut.text.splitlines(), want)
        self.assertNotIn("\x1b", cut.text)

    def test_error_line_is_kept_unmarked(self) -> None:
        """A step's own error line and the two after it are all shown."""
        log = logged(
            "##[group]Run check", "check", "##[endgroup]",
            "##[error]x", "y", "z",
        )  # fmt: skip
        cut = standing.tail(log)
        same(self, cut.text.splitlines(), ["x", "y", "z"])
        same(self, cut.step, "check")

    def test_step_own_group_keeps_what_follows(self) -> None:
        """A failing step's own group does not end its output."""
        log = logged(
            "##[group]Run build", "build", "  CI: true", "##[endgroup]",
            "before", "##[group]Inner", "inside", "##[endgroup]", "after",
            "##[error]Process completed with exit code 2.",
            "##[group]Run next", "next", "##[endgroup]", "Post job cleanup.",
        )  # fmt: skip
        same(
            self,
            standing.tail(log).text.splitlines(),
            ["before", "##[group]Inner", "inside", "##[endgroup]", "after"],
        )

    def test_long_step_shows_its_last_two_hundred(self) -> None:
        """A 300-line step shows its last 200 under the count of the rest."""
        out = [f"out {n}" for n in range(300)]
        log = logged(
            "##[group]Run long", "long", "##[endgroup]", *out,
            "##[error]Process completed with exit code 1.",
        )  # fmt: skip
        cut = standing.tail(log).text.splitlines()
        same(self, cut[0], "(100 earlier lines not shown)")
        same(self, cut[1:], out[100:])

    def test_long_lines_keep_twelve_thousand(self) -> None:
        """Output past 12,000 characters keeps its last whole lines."""
        out = ["w" * 199 for _ in range(100)]
        log = logged(
            "##[group]Run wide", "wide", "##[endgroup]", *out,
            "##[error]Process completed with exit code 1.",
        )  # fmt: skip
        cut = standing.tail(log).text.splitlines()
        same(self, cut[0], "(40 earlier lines not shown)")
        same(self, cut[1:], out[40:])

    def test_no_error_shows_the_last_sixty(self) -> None:
        """A log with no error line shows its last 60, no colour codes."""
        lines = [f"\x1b[36mline {n}\x1b[0m" for n in range(100)]
        cut = standing.tail(logged(*lines))
        same(self, cut.step, None)
        same(self, cut.text.splitlines(), [f"line {n}" for n in range(40, 100)])

    def test_error_without_a_step_shows_the_last_sixty(self) -> None:
        """An error with no step marker before it keeps the last-lines rule."""
        lines = [f"line {n}" for n in range(99)]
        cut = standing.tail(logged(*lines, "##[error]boom"))
        same(self, cut.step, None)
        same(self, len(cut.text.splitlines()), standing.TAIL_LINES)

    def test_tail_is_at_most_four_thousand(self) -> None:
        """A tail longer than 4,000 characters keeps its last whole lines."""
        width = 199
        cut = standing.tail(logged(*["y" * width for _ in range(60)])).text
        self.assertLessEqual(len(cut), standing.TAIL_CHARS)
        same(self, {len(line) for line in cut.splitlines()}, {width})


if __name__ == "__main__":
    unittest.main()
