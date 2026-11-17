#!/usr/bin/env python3
"""standing-issue.py against a stub `gh` on PATH (#1353).

The stub records every argv it is called with and answers `issue list`
from the row's canned listing (or fails, for the read-failure row).
Each row runs the helper as the workflow does, as a process, and
asserts the exact arguments gh received.

The checkpoint line is the CT log's real signed tree head, captured
from ctfe.sigstore.dev/2022 `get-sth` on 2026-11-17: the monitor writes
the same JSON (ct-go's SignedTreeHead, one line) as its checkpoint.
"""

from __future__ import annotations

import json
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import] - the helper is run as the workflow runs it
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELPER = HERE / "standing-issue.py"

TITLE = "rekor-monitor: the signer-identity watch is behind"
REPO = "example-owner/example-repo"
RUN = "https://github.com/example-owner/example-repo/actions/runs/200"
FIRST_RUN = "https://github.com/example-owner/example-repo/actions/runs/100"
STH = (
    '{"tree_size":447930488,"timestamp":1794929933483,'
    '"sha256_root_hash":"IZRcFmBfOJW4uX1zk+EgOCXyf+HoyZo6qYTdV4JtU/4=",'
    '"tree_head_signature":"BAMARzBFAiBgaysRVYSF9q7VMI1vP4VwXoFbV6ZVwcFSM/'
    'PhKGGj4AIhANT4N8bb1o+Cwn8C4YHelGmYLMCkc4b5HGT1qC7BoZZh"}'
)

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
LIST = [
    "issue", "list", "--repo", REPO, "--state", "open",
    "--limit", "1000", "--json", "number,title,body",
]  # fmt: skip


def previous_body(since: str, failed: int) -> str:
    """Build an earlier body the helper wrote.

    Returns:
        A body with the two lines an edit carries forward.

    """
    return (
        "The signer-identity watch could not read ...\n\n"
        "- Checkpoint tree size: 445,241,315\n"
        f"- Behind since: {since}\n"
        "- Latest failed run: https://example.invalid/old\n"
        f"- Failed runs since it opened: {failed}\n"
    )


class StandingIssue(unittest.TestCase):
    """One row per guard branch of standing-issue.py."""

    def setUp(self) -> None:
        """Lay out a stub gh, its call log, and a checkpoint."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "gh"
        stub.write_text(f"#!{sys.executable}\n{STUB}", encoding="utf-8")
        stub.chmod(0o755)
        self.calls = self.dir / "calls.jsonl"
        self.checkpoint = self.dir / "checkpoint_log.txt"
        self.checkpoint.write_text(STH + "\n", encoding="utf-8")
        self.env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_REPOSITORY": REPO,
            "STUB_CALLS": str(self.calls),
            "STUB_LIST": "[]",
        }

    def run_helper(
        self,
        mode: str,
        listing: list[dict] | None = None,
        log_size: str = "447930999",
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
            sys.executable, str(HELPER), mode,
            "--checkpoint", str(self.checkpoint), "--run-url", RUN,
        ]  # fmt: skip
        if mode == "behind":
            argv += ["--log-size", log_size]
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

    def test_no_open_issue_creates_it(self) -> None:
        """No open issue: one create, under the exact title."""
        proc = self.run_helper("behind", [])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = self.gh_calls()
        self.assertEqual(calls[0], LIST)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][:6], CREATE)
        self.assertEqual(calls[1][6], "--body")
        text = calls[1][7]
        self.assertIn("- Checkpoint tree size: 447,930,488\n", text)
        self.assertIn("- Checkpoint signed tree head: 2026-11-17T15:38:53Z\n", text)
        self.assertIn("- Log size now: 447,930,999\n", text)
        self.assertIn("- Entries outstanding: 511\n", text)
        self.assertIn(f"- Behind since: {RUN}\n", text)
        self.assertIn(f"- Latest failed run: {RUN}\n", text)
        self.assertIn("- Failed runs since it opened: 1\n", text)

    def test_one_open_edits_it(self) -> None:
        """One open: edit that number, keep "behind since", count the run."""
        listing = [
            {"number": 7, "title": "unrelated", "body": ""},
            {"number": 42, "title": TITLE, "body": previous_body(FIRST_RUN, 3)},
        ]
        proc = self.run_helper("behind", listing)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = self.gh_calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][:5], ["issue", "edit", "42", "--repo", REPO])
        self.assertEqual(calls[1][5], "--body")
        text = calls[1][6]
        self.assertIn(f"- Behind since: {FIRST_RUN}\n", text)
        self.assertIn(f"- Latest failed run: {RUN}\n", text)
        self.assertIn("- Failed runs since it opened: 4\n", text)
        self.assertNotIn("create", [c[1] for c in calls])
        self.assertIn("updated the standing issue #42 (4 failed runs)", proc.stdout)

    def test_resembling_title_is_ignored(self) -> None:
        """A title that only resembles the fixed one is not the issue."""
        listing = [
            {"number": 9, "title": f"{TITLE} (run 1)", "body": ""},
            {"number": 10, "title": "rekor-monitor: failure (run 1)", "body": ""},
            {"number": 11, "title": TITLE.upper(), "body": ""},
        ]
        proc = self.run_helper("behind", listing)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = self.gh_calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][:6], CREATE)
        self.assertIn(f"- Behind since: {RUN}\n", calls[1][7])

    def test_list_failure_files_nothing(self) -> None:
        """Gh issue list failing: non-zero by name, nothing created."""
        proc = self.run_helper("behind", STUB_LIST_FAIL="1")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.gh_calls(), [LIST])
        self.assertEqual(
            proc.stderr.strip(),
            "standing-issue: gh issue list failed (exit 1): HTTP 502: Bad Gateway;"
            " nothing was filed",
        )

    def test_caught_up_closes_open_issue(self) -> None:
        """caught-up with one open: close it with one comment."""
        listing = [{"number": 42, "title": TITLE, "body": previous_body(FIRST_RUN, 3)}]
        proc = self.run_helper("caught-up", listing)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            self.gh_calls(),
            [
                LIST,
                [
                    "issue", "close", "42", "--repo", REPO, "--comment",
                    f"Caught up: {RUN} saved a checkpoint at tree size 447,930,488.",
                ],
            ],
        )  # fmt: skip

    def test_caught_up_with_none_open_does_nothing(self) -> None:
        """caught-up with none open: list only, exit 0."""
        proc = self.run_helper("caught-up", [{"number": 7, "title": "x", "body": ""}])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.gh_calls(), [LIST])
        self.assertIn("no standing issue open", proc.stdout)

    def test_no_checkpoint_is_named(self) -> None:
        """No checkpoint file: the body says so, never a blank."""
        self.checkpoint.unlink()
        proc = self.run_helper("behind", [])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = self.gh_calls()[1][7]
        missing = "unknown: no checkpoint file\n"
        self.assertIn(f"- Checkpoint tree size: {missing}", text)
        self.assertIn(f"- Checkpoint signed tree head: {missing}", text)
        self.assertIn(f"- Entries outstanding: {missing}", text)

    def test_unreadable_checkpoint_is_named(self) -> None:
        """A checkpoint that is not a signed tree head: named as unreadable."""
        self.checkpoint.write_text("not json\n", encoding="utf-8")
        proc = self.run_helper("behind", [])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = self.gh_calls()[1][7]
        unreadable = "unknown: the checkpoint file could not be read\n"
        self.assertIn(f"- Checkpoint tree size: {unreadable}", text)

    def test_log_unreachable_is_named(self) -> None:
        """The log unreachable (empty or null size): named, never a blank."""
        for given in ("", "null"):
            with self.subTest(log_size=given):
                self.calls.unlink(missing_ok=True)
                proc = self.run_helper("behind", [], log_size=given)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                text = self.gh_calls()[1][7]
                gone = "unknown: the log was unreachable\n"
                self.assertIn(f"- Log size now: {gone}", text)
                self.assertIn(f"- Entries outstanding: {gone}", text)

    def test_previous_body_unreadable(self) -> None:
        """An edited-by-hand body: "behind since" named as lost, count restarts."""
        listing = [{"number": 42, "title": TITLE, "body": "hand-written"}]
        proc = self.run_helper("behind", listing)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = self.gh_calls()[1][6]
        self.assertIn("- Behind since: unknown: not in the previous body\n", text)
        self.assertIn("- Failed runs since it opened: 1\n", text)

    def test_no_repository(self) -> None:
        """GITHUB_REPOSITORY unset: non-zero by name, gh never called."""
        proc = self.run_helper("behind", [], GITHUB_REPOSITORY="")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.gh_calls(), [])
        self.assertEqual(
            proc.stderr.strip(), "standing-issue: GITHUB_REPOSITORY is not set"
        )


if __name__ == "__main__":
    unittest.main()
