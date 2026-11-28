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
"""

from __future__ import annotations

import http.server
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import override

import pytest

from belt import decoded, typed_std
from belt import findings as writer
from belt import standing_issue as standing
from belt.typed_pytest import fixture
from tests import support

REPO = "example-owner/example-repo"
WORKFLOW = "slsa"
TITLE = "red: caller permissions (slsa)"
OLD = "slsa is red"
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
    "--limit", "1000", "--json", "number,title,body,labels",
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


def issues(*found: tuple[int, str, str], label: bool = True) -> str:
    """Write an `issue list` answer, each issue labelled red-check.

    Returns:
        The JSON gh would print.

    """
    names = [{"name": "red-check"}] if label else [{"name": "bug"}]
    listing: list[dict[str, object]] = [
        {"number": n, "title": title, "body": body, "labels": names}
        for n, title, body in found
    ]
    return json.dumps(listing)


def labels(*names: str) -> str:
    """Write a `label list` answer.

    Returns:
        The JSON gh would print.

    """
    listing: list[dict[str, str]] = [{"name": name} for name in names]
    return json.dumps(listing)


def same[T](got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    assert got == want, msg or None


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


class Stubbed:
    """A stub gh, a stub forge for logs, the run's job list, the report run."""

    def __init__(
        self, tmp_path: Path, request: pytest.FixtureRequest, *, env: support.Env
    ) -> None:
        """Lay out a stub gh and a run with one red job."""
        self.make_env = env
        self.dir = Path(str(tmp_path))
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
        request.addfinalizer(server.server_close)
        request.addfinalizer(server.shutdown)
        self.calls = self.dir / "calls.jsonl"
        self.env = self.make_env(
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
        self,
        *,
        ref: str = "refs/heads/main",
        default: str = "main",
        findings: Path | None = None,
        workflow: str = WORKFLOW,
        capfd: pytest.CaptureFixture[str],
    ) -> tuple[int, str, str]:
        """Run the report as the job runs it.

        Returns:
            The status and what it printed.

        """
        argv = [
            "--workflow",
            workflow,
            "--run-id",
            RUN_ID,
            "--ref",
            ref,
            "--default-branch",
            default,
        ]
        if findings is not None:
            argv += ["--findings", str(findings)]
        with pytest.MonkeyPatch.context() as patched:
            for name, value in self.env.items():
                patched.setenv(name, value)
            status = standing.main(argv)
        out, err = capfd.readouterr()
        return status, out, err

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


@fixture
def stubbed(
    tmp_path: Path, request: pytest.FixtureRequest, env: support.Env
) -> Stubbed:
    """Give each row its own Stubbed."""
    return Stubbed(tmp_path, request, env=env)


def body_of(call: list[str]) -> str:
    """Read the --body a create or edit sent.

    Returns:
        The body.

    """
    return call[call.index("--body") + 1]


def test_red_creates_with_title_and_label(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """Red, none open: one issue, the job's title, the label."""
    status, out, err = stubbed.run_report(capfd=capfd)
    same(status, 0, err)
    calls = stubbed.gh_calls()
    same(calls[:3], [JOBS, LIST, LABELS])
    same(len(calls), 4)
    create = calls[3]
    same(create[:6], ["issue", "create", "--repo", REPO, "--title", TITLE])
    same(create[-2:], ["--label", "red-check"])
    text = body_of(create)
    assert "The job **caller permissions** of slsa on main" in text
    assert f"- Latest red run: {RUN}\n" in text
    assert f"- Red since: {RUN}\n" in text
    assert "- Red runs: 1\n" in text
    assert "## Red legs\n\n### caller permissions\n" in text
    assert f"- [caller permissions]({RUN}/job/2): failure\n" in text
    assert "<summary>failing step: <code>mise run lint:caller</code></summary>" in text
    assert "```text\nwalking\nassert: edtf/release.yml: caller-grant\n```" in text
    assert "##[error]" not in text
    assert "2026-11-20T" not in text
    assert f"red-check: opened '{TITLE}'" in out


def test_red_with_one_open_replaces_and_keeps_since(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """Red, one open: its body is replaced, Red since kept, the run counted."""
    stubbed.env["STUB_LIST"] = issues((42, TITLE, previous(FIRST, 3)))
    status, out, err = stubbed.run_report(capfd=capfd)
    same(status, 0, err)
    calls = stubbed.gh_calls()
    same(calls[:2], [JOBS, LIST])
    same(len(calls), 3)
    same(calls[2][:5], ["issue", "edit", "42", "--repo", REPO])
    text = body_of(calls[2])
    assert f"- Red since: {FIRST}\n" in text
    assert f"- Latest red run: {RUN}\n" in text
    assert "- Red runs: 4\n" in text
    assert "updated #42 (4 red runs)" in out


def test_same_run_is_counted_once(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """A second report in one run does not count the run twice."""
    stubbed.env["STUB_LIST"] = issues((42, TITLE, previous(FIRST, 3, RUN)))
    status, _out, err = stubbed.run_report(capfd=capfd)
    same(status, 0, err)
    assert "- Red runs: 3\n" in body_of(stubbed.gh_calls()[2])


def test_green_closes_with_the_run(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """A job green, its issue open: closed, naming the run."""
    stubbed.jobs([job(1, "plan", "success"), job(2, "source-vsa", "skipped")])
    open_one = (42, "red: source-vsa (slsa)", previous(FIRST, 3))
    stubbed.env["STUB_LIST"] = issues(open_one)
    status, out, err = stubbed.run_report(capfd=capfd)
    same(status, 0, err)
    comment = f"Green: {RUN} ran every leg of this job green or skipped."
    closed = ["issue", "close", "42", "--repo", REPO, "--comment", comment]
    same(stubbed.gh_calls(), [JOBS, LIST, closed])
    assert "green: closed #42" in out


def test_green_with_none_open_writes_nothing(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """Green, none open: the two reads and nothing else."""
    stubbed.jobs([job(1, "plan", "success")])
    status, out, err = stubbed.run_report(capfd=capfd)
    same(status, 0, err)
    same(stubbed.gh_calls(), [JOBS, LIST])
    assert "red-check: nothing to change" in out


def test_other_branch_calls_nothing(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """A run on another branch changes nothing, and asks nothing."""
    status, out, err = stubbed.run_report(ref="refs/heads/feature", capfd=capfd)
    same(status, 0, err)
    same(stubbed.gh_calls(), [])
    assert "refs/heads/feature is not the default branch (main)" in out


def test_failed_listing_files_nothing(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """A listing that fails exits 2 by name and files nothing."""
    stubbed.env["STUB_LIST_FAIL"] = "1"
    status, _out, err = stubbed.run_report(capfd=capfd)
    same(status, 2)
    same(stubbed.gh_calls(), [JOBS, LIST])
    assert (
        "gh issue list failed (exit 1): HTTP 502: Bad Gateway; nothing was filed" in err
    )


def test_two_open_is_a_named_error(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """Two open issues with one job's title: a named error, nothing filed."""
    stubbed.jobs([job(1, "plan", "failure"), job(2, "caller permissions", "failure")])
    both = [(n, TITLE, previous(FIRST, 1)) for n in (41, 42)]
    stubbed.env["STUB_LIST"] = issues(*both)
    status, _out, err = stubbed.run_report(capfd=capfd)
    same(status, 2)
    same(stubbed.gh_calls(), [JOBS, LIST])
    assert f"2 open issues are titled exactly '{TITLE}' (#41, #42)" in err


def test_resembling_title_is_not_the_issue(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """A title that only resembles is never edited."""
    stubbed.env["STUB_LIST"] = issues((7, f"{TITLE} again", ""))
    status, _out, err = stubbed.run_report(capfd=capfd)
    same(status, 0, err)
    same(stubbed.gh_calls()[-1][:2], ["issue", "create"])


def test_missing_label_is_created_from_the_baseline(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """No red-check label: created from settings/repo-baseline.json first."""
    stubbed.env["STUB_LABELS"] = labels("hold")
    status, out, err = stubbed.run_report(capfd=capfd)
    same(status, 0, err)
    label = standing.label_definition()
    made = [
        "label", "create", "red-check", "--repo", REPO,
        "--color", str(label["color"]), "--description", str(label["description"]),
    ]  # fmt: skip
    calls = stubbed.gh_calls()
    same(calls[3], made)
    same(calls[4][:2], ["issue", "create"])
    assert "created the red-check label; opened" in out


def test_unfinished_sibling_changes_nothing(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """Green so far with a caller's job still running: nothing is read."""
    stubbed.jobs([
        job(1, "records / links", "success"),
        job(3, "citations", None, "in_progress"),
    ])
    stubbed.env["STUB_LIST"] = issues((42, "red: links (records)", previous(FIRST, 3)))
    status, out, err = stubbed.run_report(capfd=capfd)
    same(status, 0, err)
    same(stubbed.gh_calls(), [JOBS])
    assert "neither red nor green yet" in out


def test_unfinished_sibling_keeps_red_and_closes_nothing(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """A red job is kept with a sibling running; no green job is closed."""
    stubbed.jobs([
        job(1, "plan", "success"),
        job(2, "caller permissions", "failure"),
        job(3, "citations", None, "in_progress"),
    ])
    stubbed.env["STUB_LIST"] = issues((41, "red: plan (slsa)", previous(FIRST, 3)))
    status, _out, err = stubbed.run_report(capfd=capfd)
    same(status, 0, err)
    calls = stubbed.gh_calls()
    same([c[:2] for c in calls[2:]], [["label", "list"], ["issue", "create"]])


def test_timed_out_is_red(stubbed: Stubbed, capfd: pytest.CaptureFixture[str]) -> None:
    """A job that timed out is red."""
    stubbed.jobs([job(4, "watch (leg 1)", "timed_out")])
    stubbed.log(4, logged("reading"))
    status, _out, err = stubbed.run_report(capfd=capfd)
    same(status, 0, err)
    create = stubbed.gh_calls()[-1]
    same(create[5], "red: watch (slsa)")
    assert "```text\nreading\n```" in body_of(create)


def failing(task: str, *output: str) -> str:
    """Write a log whose failing step's first line is `task`'s command.

    Returns:
        The log text.

    """
    return logged(
        f"##[group]Run {task}",
        task,
        "##[endgroup]",
        *output,
        "##[error]Process completed with exit code 1.",
    )


class PerJob(Stubbed):
    """One issue per red job (#1966): its title, its legs, its closes."""

    def made(self) -> dict[str, str]:
        """Read every issue the report created.

        Returns:
            Each created issue's body, by its title.

        """
        return {
            c[5]: body_of(c) for c in self.gh_calls() if c[:2] == ["issue", "create"]
        }


@fixture
def per_job(tmp_path: Path, request: pytest.FixtureRequest, env: support.Env) -> PerJob:
    """Give each row its own PerJob."""
    return PerJob(tmp_path, request, env=env)


def test_two_red_jobs_open_two_issues(
    per_job: PerJob, capfd: pytest.CaptureFixture[str]
) -> None:
    """Two red jobs in one run: two issues, each titled for its job."""
    per_job.jobs([
        job(1, "rust branch coverage holds its floor", "failure"),
        job(2, "every source chain link verifies", "failure"),
    ])
    per_job.log(1, failing("mise run coverage", "below the floor"))
    per_job.log(2, failing("mise run chain", "link 3 does not verify"))
    status, _out, err = per_job.run_report(capfd=capfd)
    same(status, 0, err)
    calls = per_job.gh_calls()
    same(calls[:3], [JOBS, LIST, LABELS])
    same(
        [c[5] for c in calls[3:]],
        [
            "red: rust branch coverage holds its floor (slsa)",
            "red: every source chain link verifies (slsa)",
        ],
    )
    made = per_job.made()
    first = made["red: rust branch coverage holds its floor (slsa)"]
    second = made["red: every source chain link verifies (slsa)"]
    assert "below the floor" in first
    assert "link 3" not in first
    assert "link 3 does not verify" in second
    assert "below the floor" not in second


def test_a_reusable_workflow_leg_is_named_for_its_job(
    per_job: PerJob, capfd: pytest.CaptureFixture[str]
) -> None:
    """`deep-tests / x (3/8)` under deep-tests is a leg of the job `x`."""
    same(standing.job_name("deep-tests / x (3/8)", "deep-tests"), "x")
    per_job.jobs([
        job(1, "deep-tests / x (3/8)", "failure"),
        job(2, "deep-tests / x (4/8)", "success"),
    ])
    per_job.log(1, failing("mise run x", "boom"))
    status, _out, err = per_job.run_report(workflow="deep-tests", capfd=capfd)
    same(status, 0, err)
    create = per_job.gh_calls()[-1]
    same(create[5], "red: x (deep-tests)")
    text = body_of(create)
    assert "with 1 of its 2 legs red" in text
    assert f"- [deep-tests / x (3/8)]({RUN}/job/1): failure\n" in text
    assert "x (4/8)" not in text


def test_fourteen_legs_with_one_output_are_one_excerpt(
    per_job: PerJob, capfd: pytest.CaptureFixture[str]
) -> None:
    """Fourteen red legs, one output: one issue, one excerpt, 14 links."""
    name = "fuzz targets survive a bounded run"
    per_job.jobs([job(n, f"{name} (leg {n})", "failure") for n in range(1, 15)])
    for n in range(1, 15):
        per_job.log(n, failing("mise run fuzz", "llvm-tools is not installed"))
    status, _out, err = per_job.run_report(capfd=capfd)
    same(status, 0, err)
    creates = [c for c in per_job.gh_calls() if c[:2] == ["issue", "create"]]
    same(len(creates), 1)
    same(creates[0][5], f"red: {name} (slsa)")
    text = body_of(creates[0])
    same(text.count("llvm-tools is not installed"), 1)
    same(text.count("<details>"), 1)
    same(text.count(f"- [{name} (leg "), 14)
    assert f"### {name} (leg 1) and 13 more legs\n" in text


def test_one_red_leg_of_fourteen_is_red(
    per_job: PerJob, capfd: pytest.CaptureFixture[str]
) -> None:
    """One leg red and thirteen green: the job is red."""
    legs = [job(1, "shard (1)", "failure")]
    legs += [job(n, f"shard ({n})", "success") for n in range(2, 15)]
    per_job.jobs(legs)
    per_job.log(1, failing("mise run shard", "one shard failed"))
    status, _out, err = per_job.run_report(capfd=capfd)
    same(status, 0, err)
    create = per_job.gh_calls()[-1]
    same(create[:2], ["issue", "create"])
    same(create[5], "red: shard (slsa)")
    assert "with 1 of its 14 legs red" in body_of(create)


def test_every_leg_green_or_skipped_closes(
    per_job: PerJob, capfd: pytest.CaptureFixture[str]
) -> None:
    """Every leg green or skipped: the job's issue is closed."""
    legs = [job(n, f"shard ({n})", "success") for n in range(1, 14)]
    per_job.jobs([*legs, job(14, "shard (14)", "skipped")])
    per_job.env["STUB_LIST"] = issues((42, "red: shard (slsa)", previous(FIRST, 2)))
    status, _out, err = per_job.run_report(capfd=capfd)
    same(status, 0, err)
    comment = f"Green: {RUN} ran every leg of this job green or skipped."
    closed = ["issue", "close", "42", "--repo", REPO, "--comment", comment]
    same(per_job.gh_calls(), [JOBS, LIST, closed])


def test_a_cancelled_leg_with_no_red_leg_changes_nothing(
    per_job: PerJob, capfd: pytest.CaptureFixture[str]
) -> None:
    """A cancelled leg, the rest green: the job's issue is unchanged."""
    per_job.jobs([job(1, "shard (1)", "success"), job(2, "shard (2)", "cancelled")])
    per_job.env["STUB_LIST"] = issues((42, "red: shard (slsa)", previous(FIRST, 2)))
    status, out, err = per_job.run_report(capfd=capfd)
    same(status, 0, err)
    same(per_job.gh_calls(), [JOBS, LIST])
    assert "neither red nor green, unchanged: shard" in out


def test_green_job_closed_and_red_job_replaced(
    per_job: PerJob, capfd: pytest.CaptureFixture[str]
) -> None:
    """Job A green and job B red: A's issue closed, B's body replaced."""
    per_job.jobs([job(1, "a", "success"), job(2, "b", "failure")])
    per_job.log(2, failing("mise run b", "b broke"))
    per_job.env["STUB_LIST"] = issues(
        (41, "red: a (slsa)", previous(FIRST, 1)),
        (42, "red: b (slsa)", previous(FIRST, 1)),
    )
    status, _out, err = per_job.run_report(capfd=capfd)
    same(status, 0, err)
    calls = per_job.gh_calls()
    same(len(calls), 4)
    same(calls[2][:5], ["issue", "edit", "42", "--repo", REPO])
    assert "b broke" in body_of(calls[2])
    comment = f"Green: {RUN} ran every leg of this job green or skipped."
    closed = ["issue", "close", "41", "--repo", REPO, "--comment", comment]
    same(calls[3], closed)


def test_a_job_not_in_the_run_is_closed_saying_so(
    per_job: PerJob, capfd: pytest.CaptureFixture[str]
) -> None:
    """`red: gone` labelled is closed; the same shape unlabelled is not."""
    per_job.jobs([job(1, "plan", "success")])
    labelled = issues((50, "red: gone (slsa)", previous(FIRST, 1)))
    unlabelled = issues((51, "red: gone (slsa)", ""), label=False)
    listing = [
        *decoded.as_array(decoded.load_json(labelled), "labelled"),
        *decoded.as_array(decoded.load_json(unlabelled), "unlabelled"),
    ]
    per_job.env["STUB_LIST"] = json.dumps(listing)
    status, _out, err = per_job.run_report(capfd=capfd)
    same(status, 0, err)
    comment = (
        f"Not in the run: {RUN} lists no job named `gone`; it was renamed or removed."
    )
    closed = ["issue", "close", "50", "--repo", REPO, "--comment", comment]
    same(per_job.gh_calls(), [JOBS, LIST, closed])


@pytest.mark.parametrize("red", [True, False])
def test_the_old_issue_is_closed_red_or_green(
    per_job: PerJob, red: object, capfd: pytest.CaptureFixture[str]
) -> None:
    """An open `<workflow> is red` is closed on a red run and a green one."""
    comment = (
        f"{RUN} reported this workflow with one issue per red job"
        " (monumental-archive/.github#1966), titled `red: <job> (slsa)`:"
        " each red job now has its own issue."
    )
    closed = ["issue", "close", "30", "--repo", REPO, "--comment", comment]
    per_job.calls.unlink(missing_ok=True)
    if not red:
        per_job.jobs([job(1, "plan", "success")])
    old = (30, OLD, previous(FIRST, 5))
    per_job.env["STUB_LIST"] = issues(old, label=False)
    status, _out, err = per_job.run_report(capfd=capfd)
    same(status, 0, err)
    calls = per_job.gh_calls()
    same(calls[-1], closed)
    creates = [c for c in calls if c[:2] == ["issue", "create"]]
    same(len(creates), 1 if red else 0)


def test_red_since_and_runs_carry_per_issue(
    per_job: PerJob, capfd: pytest.CaptureFixture[str]
) -> None:
    """Each job's issue carries its own Red since and run count."""
    per_job.jobs([job(1, "a", "failure"), job(2, "b", "failure")])
    per_job.log(1, failing("mise run a", "a broke"))
    per_job.log(2, failing("mise run b", "b broke"))
    per_job.env["STUB_LIST"] = issues((41, "red: a (slsa)", previous(FIRST, 3)))
    status, _out, err = per_job.run_report(capfd=capfd)
    same(status, 0, err)
    calls = per_job.gh_calls()
    edit = next(c for c in calls if c[:3] == ["issue", "edit", "41"])
    assert f"- Red since: {FIRST}\n" in body_of(edit)
    assert "- Red runs: 4\n" in body_of(edit)
    b = per_job.made()["red: b (slsa)"]
    assert f"- Red since: {RUN}\n" in b
    assert "- Red runs: 1\n" in b


def test_unreadable_log_is_named(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """A log the forge will not serve is named in the entry, not fatal."""
    stubbed.jobs([job(5, "drafts", "failure")])
    status, _out, err = stubbed.run_report(capfd=capfd)
    same(status, 0, err)
    assert (
        "_Log tail unknown: the log could not be read: GET"
        f" repos/{REPO}/actions/jobs/5/logs answered HTTP 410_"
    ) in body_of(stubbed.gh_calls()[-1])


def test_no_token_is_named(stubbed: Stubbed, capfd: pytest.CaptureFixture[str]) -> None:
    """No GH_TOKEN: named in the entry, not fatal, nothing requested."""
    del stubbed.env["GH_TOKEN"]
    status, _out, err = stubbed.run_report(capfd=capfd)
    same(status, 0, err)
    assert "GH_TOKEN is not set_" in body_of(stubbed.gh_calls()[-1])
    same(stubbed.seen, [])


def test_log_is_read_without_gh(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """The log comes from the endpoint; gh never reads one."""
    status, _out, err = stubbed.run_report(capfd=capfd)
    same(status, 0, err)
    reads = [
        call
        for call in stubbed.gh_calls()
        if call[:2] == ["run", "view"]
        or (call[:1] == ["api"] and call[-1].endswith("/logs"))
    ]
    same(reads, [])
    assert "caller-grant" in body_of(stubbed.gh_calls()[-1])


def test_token_never_reaches_the_blob_host(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """The API read carries the token; the redirected read does not."""
    status, _out, err = stubbed.run_report(capfd=capfd)
    same(status, 0, err)
    same(
        stubbed.seen,
        [
            (f"/repos/{REPO}/actions/jobs/2/logs", f"Bearer {BEARER}"),
            ("/blob/2", None),
        ],
    )


def red_of(legs: int) -> standing.Red:
    """Describe a red job with `legs` legs, for a body written without gh.

    Returns:
        What the body says beside the legs.

    """
    return standing.Red(WORKFLOW, "job", "main", RUN, FIRST, 2, legs)


# The body's entries, fence and size, without gh.
def red(entries: list[standing.Entry]) -> str:
    """Write a body for a run.

    Returns:
        The body.

    """
    return standing.body(red_of(len(entries)), entries)


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


def test_two_outputs_give_two_excerpts() -> None:
    """Two red legs with different output give two excerpts, in order."""
    text = red([entry(1, "first tail"), entry(2, "second tail")])
    same(text.count("<summary>failing step: <code>step</code></summary>"), 2)
    assert text.index("### job 1") < text.index("first tail")
    assert text.index("first tail") < text.index("### job 2")
    assert text.index("### job 2") < text.index("second tail")


def test_backticks_cannot_close_the_fence() -> None:
    """A log line of three backticks sits inside a longer fence."""
    text = red([entry(1, "before\n```\nafter")])
    assert "````text\nbefore\n```\nafter\n````" in text


def test_long_body_drops_tails_from_the_last() -> None:
    """Over 60,000 characters, the last jobs' tails go first, said so."""
    tails = [entry(n, f"tail {n}\n" + "x" * 3990) for n in range(1, 21)]
    text = red(tails)
    assert len(text) < standing.BODY_LIMIT
    assert "tail 1\n" in text
    assert "tail 20\n" not in text
    assert "_Log tail dropped: the body would pass 60,000 characters._" in text
    assert re.search(r"Log tails dropped for the last \d+ excerpt\(s\)", text)


def test_recorded_log_shows_the_failing_step() -> None:
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
    assert "\x1b[" in log
    cut = standing.tail(log)
    same(cut.step, "mise run audit:bestpractices")
    same(cut.text.splitlines(), want)
    assert "\x1b" not in cut.text


def test_error_line_is_kept_unmarked() -> None:
    """A step's own error line and the two after it are all shown."""
    log = logged(
        "##[group]Run check", "check", "##[endgroup]",
        "##[error]x", "y", "z",
    )  # fmt: skip
    cut = standing.tail(log)
    same(cut.text.splitlines(), ["x", "y", "z"])
    same(cut.step, "check")


def test_step_own_group_keeps_what_follows() -> None:
    """A failing step's own group does not end its output."""
    log = logged(
        "##[group]Run build", "build", "  CI: true", "##[endgroup]",
        "before", "##[group]Inner", "inside", "##[endgroup]", "after",
        "##[error]Process completed with exit code 2.",
        "##[group]Run next", "next", "##[endgroup]", "Post job cleanup.",
    )  # fmt: skip
    same(
        standing.tail(log).text.splitlines(),
        ["before", "##[group]Inner", "inside", "##[endgroup]", "after"],
    )


def test_long_step_shows_its_last_two_hundred() -> None:
    """A 300-line step shows its last 200 under the count of the rest."""
    out = [f"out {n}" for n in range(300)]
    log = logged(
        "##[group]Run long", "long", "##[endgroup]", *out,
        "##[error]Process completed with exit code 1.",
    )  # fmt: skip
    cut = standing.tail(log).text.splitlines()
    same(cut[0], "(100 earlier lines not shown)")
    same(cut[1:], out[100:])


def test_long_lines_keep_twelve_thousand() -> None:
    """Output past 12,000 characters keeps its last whole lines."""
    out = ["w" * 199 for _ in range(100)]
    log = logged(
        "##[group]Run wide", "wide", "##[endgroup]", *out,
        "##[error]Process completed with exit code 1.",
    )  # fmt: skip
    cut = standing.tail(log).text.splitlines()
    same(cut[0], "(40 earlier lines not shown)")
    same(cut[1:], out[40:])


def test_no_error_shows_the_last_sixty() -> None:
    """A log with no error line shows its last 60, no colour codes."""
    lines = [f"\x1b[36mline {n}\x1b[0m" for n in range(100)]
    cut = standing.tail(logged(*lines))
    same(cut.step, None)
    same(cut.text.splitlines(), [f"line {n}" for n in range(40, 100)])


def test_error_without_a_step_shows_the_last_sixty() -> None:
    """An error with no step marker before it keeps the last-lines rule."""
    lines = [f"line {n}" for n in range(99)]
    cut = standing.tail(logged(*lines, "##[error]boom"))
    same(cut.step, None)
    same(len(cut.text.splitlines()), standing.TAIL_LINES)


def test_tail_is_at_most_four_thousand() -> None:
    """A tail longer than 4,000 characters keeps its last whole lines."""
    width = 199
    cut = standing.tail(logged(*["y" * width for _ in range(60)])).text
    assert len(cut) <= standing.TAIL_CHARS
    same({len(line) for line in cut.splitlines()}, {width})


def records(directory: Path, name: str, *found: standing.Finding) -> None:
    """Write a findings file as the writer appends it.

    The record is mise/findings.py's own encoding, so the report reads
    what the writer writes.
    """
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / name).open("a", encoding="utf-8") as out:
        for f in found:
            out.write(writer.record(f.task, f.subject, f.message, f.detail) + "\n")


def red_job(number: int, tail: standing.Excerpt | None) -> standing.Entry:
    """Build a red job's entry with a given excerpt.

    Returns:
        The entry.

    """
    url = f"https://github.com/{REPO}/actions/runs/{RUN_ID}/job/{number}"
    job_of = standing.Job(number, f"job {number}", "completed", "failure", url)
    return standing.Entry(job_of, tail, None if tail else "the log could not be read")


def ran(*tasks: str) -> standing.Entry:
    """Build a red leg whose failing step ran `tasks`.

    Returns:
        The entry.

    """
    return red_job(1, standing.Excerpt("out", "step", frozenset(tasks)))


DENY = standing.Finding(
    "audit:deny",
    "chrono@0.4.45",
    "RUSTSEC-2026-0543 is undecided",
    ("reached through object_store 0.14.2",),
)
SPELL = standing.Finding("lint:typos", "README.md", "a word is misspelt")
LOOSE = standing.Finding("audit:deny", "", "a pin is loose")


class FindingsBody:
    """The body written from the run's findings files (#1890)."""

    def __init__(self, tmp_path: Path) -> None:
        """Give each row a findings directory."""
        self.dir = Path(str(tmp_path)) / "findings"

    def red(
        self,
        entries: list[standing.Entry],
        previous: str | None = None,
    ) -> str:
        """Write a body from this row's findings files.

        Returns:
            The body.

        """
        before = None if previous is None else standing.Issue(42, previous)
        return standing.body(
            red_of(len(entries)),
            entries,
            standing.read_findings(self.dir),
            standing.seen_hashes(before),
        )


@fixture
def findings_body(tmp_path: Path) -> FindingsBody:
    """Give each row its own FindingsBody."""
    return FindingsBody(tmp_path)


def test_two_tasks_give_two_sections_with_counts(findings_body: FindingsBody) -> None:
    """Two tasks' records: one `###` per task, in name order, counted."""
    records(findings_body.dir, "a.jsonl", SPELL, DENY)
    records(findings_body.dir, "b.jsonl", LOOSE)
    text = findings_body.red([ran("audit:deny", "lint:typos")])
    assert "## Findings\n" in text
    assert "### audit:deny (2)\n" in text
    assert "### lint:typos (1)\n" in text
    assert text.index("### audit:deny") < text.index("### lint:typos")
    assert text.index("## Findings") < text.index("## Red legs")
    assert standing.OUTSIDE not in text
    assert "- chrono@0.4.45: RUSTSEC-2026-0543 is undecided\n" in text
    assert "- a pin is loose\n" in text
    assert (
        "  <details><summary>detail</summary>\n\n"
        "  ```text\n  reached through object_store 0.14.2\n  ```\n\n"
        "  </details>\n"
    ) in text
    last = text.splitlines()[-1]
    assert re.search(
        r"^<!-- findings: [0-9a-f]{16} [0-9a-f]{16} [0-9a-f]{16} -->$", last
    )


def test_a_hash_in_the_previous_body_is_still_red(findings_body: FindingsBody) -> None:
    """One of three hashes carried: one still red, two new."""
    third = standing.Finding("audit:deny", "time@0.3.0", "RUSTSEC-2026-0001")
    records(findings_body.dir, "a.jsonl", DENY, LOOSE, third)
    before = f"...\n<!-- findings: 0123456789abcdef {LOOSE.digest} -->"
    text = findings_body.red([ran("audit:deny")], before)
    section = text[text.index("### audit:deny (3)") : text.index("## Red legs")]
    new, still = section.split("#### Still red")
    assert "#### New in this run" in new
    same(new.count("\n- "), 2)
    assert "- chrono@0.4.45:" in new
    assert "- time@0.3.0:" in new
    same(still.count("\n- "), 1)
    assert "- a pin is loose" in still


def test_no_previous_line_makes_every_finding_new(findings_body: FindingsBody) -> None:
    """A previous body with no findings line: every finding is new."""
    records(findings_body.dir, "a.jsonl", DENY, LOOSE)
    text = findings_body.red([red_job(1, None)], previous(FIRST, 1))
    assert "#### New in this run" in text
    assert "#### Still red" not in text


def test_a_leg_with_a_record_points_and_shows_its_output(
    findings_body: FindingsBody,
) -> None:
    """A step with a record points above; both show their output."""
    records(findings_body.dir, "a.jsonl", DENY)
    deny = standing.tail(
        logged(
            "##[group]Run mise run audit:deny",
            "mise run audit:deny",
            "##[endgroup]",
            "denied",
            "##[error]Process completed with exit code 1.",
        )
    )
    other = standing.tail(
        logged(
            "##[group]Run mise run lint:caller",
            "mise run lint:caller",
            "##[endgroup]",
            "caller grant missing",
            "##[error]Process completed with exit code 1.",
        )
    )
    text = findings_body.red([red_job(1, deny), red_job(2, other)])
    first = text[text.index("### job 1") : text.index("### job 2")]
    second = text[text.index("### job 2") :]
    assert "Its findings are above, under <code>audit:deny</code>." in first
    assert "```text\ndenied\n```" in first
    assert "No finding was recorded" not in first
    assert "_No finding was recorded._" in second
    assert "```text\ncaller grant missing\n```" in second


@pytest.mark.parametrize(
    "found", [SPELL, standing.Finding("lint:shell", "a.sh", "unformatted")]
)
def test_a_step_of_two_tasks_is_matched_by_either(
    findings_body: FindingsBody, found: object
) -> None:
    """A step that ran two tasks, seen by their `[task] ` prefixes."""
    log = logged(
        "##[group]Run mise run ci:lint",
        "mise run ci:lint",
        "##[endgroup]",
        "[lint:typos] $ typos",
        "[lint:shell] $ shfmt",
        "[lint:shell] ERROR task failed",
        "##[error]Process completed with exit code 1.",
    )
    cut = standing.tail(log)
    same(cut.tasks, frozenset({"ci:lint", "lint:typos", "lint:shell"}))
    findings_body.dir.joinpath("a.jsonl").unlink(missing_ok=True)
    records(findings_body.dir, "a.jsonl", found)
    text = findings_body.red([red_job(1, cut)])
    assert f"under <code>{found.task}</code>." in text
    assert "No finding was recorded" not in text


def test_a_malformed_line_is_counted(findings_body: FindingsBody) -> None:
    """Lines that are not a record are counted in the body, not fatal."""
    records(findings_body.dir, "a.jsonl", DENY)
    with (findings_body.dir / "a.jsonl").open("a", encoding="utf-8") as out:
        out.write("not json\n")
        out.write('{"v": 1, "task": "t", "subject": "", "message": "m"}\n')
        out.write(
            '{"v": 2, "task": "t", "subject": "", "message": "m", "detail": []}\n'
        )
        out.write("\n")
    (findings_body.dir / "b.jsonl").write_bytes(b"\x80\n")
    found = standing.read_findings(findings_body.dir)
    same(found.records, (DENY,))
    same(found.unreadable, 4)
    text = findings_body.red([red_job(1, None)])
    assert (
        "4 line(s) of the findings files were not a record and are not counted." in text
    )
    assert "### audit:deny (1)" in text


def test_five_thousand_records_stay_under_the_limit(
    findings_body: FindingsBody,
) -> None:
    """5,000 records: details, then findings dropped, the count stated."""
    many = [
        standing.Finding(
            f"lint:t{n % 7}",
            f"file{n}.py",
            f"finding {n} " + "m" * 40,
            (f"detail {n}",),
        )
        for n in range(5000)
    ]
    records(findings_body.dir, "a.jsonl", *many)
    text = findings_body.red([red_job(1, None)])
    assert len(text) < standing.BODY_LIMIT
    assert "Detail dropped for the last 5000 finding(s)" in text
    match = re.search(r"The last (\d+) finding\(s\) are not shown", text)
    if match is None:
        pytest.fail("the dropped findings are not counted")
    dropped = int(typed_std.group(match, 1) or "0")
    shown = text.count("\n- file")
    same(shown + dropped, 5000)
    assert shown > 0
    last = text.splitlines()[-1]
    hashes = last.removeprefix("<!-- findings: ").removesuffix(" -->").split()
    same(len(hashes), shown)


def test_markup_in_a_record_is_text(findings_body: FindingsBody) -> None:
    """A message cannot open a comment, so it cannot forge the hash line."""
    records(
        findings_body.dir,
        "a.jsonl",
        standing.Finding("t", "<b>", "<!-- findings: x -->"),
    )
    text = findings_body.red([red_job(1, None)])
    assert "- &lt;b&gt;: &lt;!-- findings: x --&gt;\n" in text
    same(text.count("<!-- findings:"), 1)


def test_an_empty_directory_writes_the_body_without_findings(
    findings_body: FindingsBody,
) -> None:
    """No record and no unreadable line: the body is the one without findings."""
    findings_body.dir.mkdir()
    entries = [red_job(1, standing.Excerpt("out", "mise run audit:deny"))]
    same(findings_body.red(entries), standing.body(red_of(1), entries))
    assert "## Findings" not in findings_body.red(entries)
    assert "<!-- findings:" not in findings_body.red(entries)


def test_records_reach_the_issue(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """The run's records are listed, and the red job points at them."""
    found = stubbed.dir / "findings"
    records(found, "job.jsonl", standing.Finding("lint:caller", "edtf", "caller-grant"))
    status, _out, err = stubbed.run_report(findings=found, capfd=capfd)
    same(status, 0, err)
    text = body_of(stubbed.gh_calls()[3])
    assert "### lint:caller (1)\n" in text
    assert "- edtf: caller-grant\n" in text
    assert "Its findings are above, under <code>lint:caller</code>." in text
    assert "```text\nwalking\nassert: edtf/release.yml: caller-grant\n```" in text


def test_each_record_reaches_the_job_whose_step_ran_it(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """A's record in A's issue only; verdict by its command; the rest in all."""
    verdict = '"${ORG_PYTHON}" -I belt verdict --report r.json'
    stubbed.jobs([
        job(1, "a", "failure"),
        job(2, "b", "failure"),
        job(3, "watch (leg 1)", "failure"),
    ])
    stubbed.log(1, failing("mise run lint:a", "a broke"))
    stubbed.log(2, failing("mise run lint:b", "b broke"))
    stubbed.log(3, failing(verdict, "a certificate did not reconcile"))
    found = stubbed.dir / "findings"
    records(
        found,
        "run.jsonl",
        standing.Finding("lint:a", "a.py", "finding of a"),
        standing.Finding("lint:b", "b.py", "finding of b"),
        standing.Finding("verdict", "leg 1", "finding of the watch"),
        standing.Finding("audit:loose", "", "finding of no failing step"),
    )
    status, _out, err = stubbed.run_report(findings=found, capfd=capfd)
    same(status, 0, err)
    made = {
        c[5]: body_of(c) for c in stubbed.gh_calls() if c[:2] == ["issue", "create"]
    }
    a, b = made["red: a (slsa)"], made["red: b (slsa)"]
    watch = made["red: watch (slsa)"]
    assert "- a.py: finding of a\n" in a
    assert "finding of b" not in a
    assert "- b.py: finding of b\n" in b
    assert "finding of a" not in b
    assert "### verdict (1)\n" in watch
    assert "Its findings are above, under <code>verdict</code>." in watch
    assert "finding of the watch" not in a + b
    for text in (a, b, watch):
        outside = text[text.index(standing.OUTSIDE) : text.index("## Red legs")]
        assert "- finding of no failing step\n" in outside
        same(outside.count("\n- "), 1)


def test_without_the_flag_no_findings_are_read(
    stubbed: Stubbed, capfd: pytest.CaptureFixture[str]
) -> None:
    """No `--findings`: the body has no findings section."""
    status, _out, err = stubbed.run_report(capfd=capfd)
    same(status, 0, err)
    assert "## Findings" not in body_of(stubbed.gh_calls()[3])
