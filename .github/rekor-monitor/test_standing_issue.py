#!/usr/bin/env python3
"""standing_issue.py against a stub `gh` on PATH (#1353, #1339).

The stub records every argv it is called with and answers `issue list`
from the row's canned listing (or fails, for the read-failure row).
Each row calls the helper's `main` in this process with the alert job's
arguments, the row's environment in place for the call (#1673, #1699),
and asserts the exact arguments gh received, the colour and the message;
one row, the script row, starts it as the alert job does.

The checkpoints are stele v0.24.0's own files, captured from
ctfe.sigstore.dev/2022 on 2026-11-17 (testdata/SOURCE.md): one at the
head it verified, one 25,905 entries behind it. A row that needs another
position edits `next` or `treeHead.treeSize` of a copy in code.
"""

from __future__ import annotations

import json
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import] - the script row runs the helper as the workflow does
import sys
import tempfile
import unittest
from pathlib import Path

# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[2] / "mise"))

import hermetic
import standing_issue as standing

HERE = Path(__file__).resolve().parent
HELPER = HERE / "standing_issue.py"
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
LIST = [
    "issue", "list", "--repo", REPO, "--state", "open",
    "--limit", "1000", "--json", "number,title,body",
]  # fmt: skip
NEXT = "- Checkpoint next index (everything before it is judged): "
STARTED = "- Entries outstanding when this run started: "
NOW = "- Entries outstanding now: "

# checkpoint-budget.json: next 448,196,522 of a verified 448,222,427.
BEHIND_NEXT = 448196522
BEHIND_SIZE = 448222427


def checkpoint(name: str = "checkpoint-budget.json", **edits: int) -> dict:
    """Read a captured checkpoint, `next` or `size` edited.

    Returns:
        The checkpoint document.

    """
    doc = json.loads((DATA / name).read_text(encoding="utf-8"))
    if "next" in edits:
        doc["next"] = edits["next"]
    if "size" in edits:
        doc["treeHead"]["treeSize"] = edits["size"]
    return doc


def previous_body(since: str, runs: int) -> str:
    """Build an earlier body the helper wrote.

    Returns:
        A body with the two lines an edit carries forward.

    """
    return (
        "The signer-identity watch is not gaining ...\n\n"
        f"{NEXT}445,241,315\n"
        f"- Behind since: {since}\n"
        "- Latest run behind: https://example.invalid/old\n"
        f"- Runs behind since it opened: {runs}\n"
    )


class Rows(unittest.TestCase):
    """A stub gh, the run's two checkpoints, and the helper run."""

    def setUp(self) -> None:
        """Lay out a stub gh and a run that did not advance."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "gh"
        stub.write_text(f"#!{sys.executable}\n{STUB}", encoding="utf-8")
        stub.chmod(0o755)
        self.calls = self.dir / "calls.jsonl"
        self.start = self.dir / "run-start.json"
        self.end = self.dir / "checkpoint.json"
        self.write(self.start, checkpoint())
        self.write(self.end, checkpoint())
        self.env = hermetic.env(
            PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            GITHUB_REPOSITORY=REPO,
            STUB_CALLS=str(self.calls),
            STUB_LIST="[]",
        )

    @staticmethod
    def write(path: Path, doc: dict) -> None:
        """Write a checkpoint where the helper reads it."""
        path.write_text(json.dumps(doc), encoding="utf-8")

    def arguments(self, kept: str) -> list[str]:
        """Build the alert job's command line.

        Returns:
            The arguments after the program name.

        """
        return [
            "--start", str(self.start), "--start-kept", kept,
            "--checkpoint", str(self.end), "--legs", "success", "--run-url", RUN,
        ]  # fmt: skip

    def run_helper(
        self,
        listing: list[dict] | None = None,
        kept: str = "true",
        **env: str,
    ) -> hermetic.Called:
        """Call the helper's main in this process, as the alert job runs it.

        The process's environment is the row's for the call (the stub gh
        first on PATH, the repository, the stub's own variables).

        Returns:
            Its exit status and output, captured.

        """
        full = {**self.env, **env}
        if listing is not None:
            full["STUB_LIST"] = json.dumps(listing)
        argv = self.arguments(kept)
        return hermetic.call_main(lambda: standing.main(argv), env=full)

    def run_script(self, **env: str) -> subprocess.CompletedProcess[str]:
        """Start the helper as the alert job does.

        Returns:
            The finished process.

        """
        argv = [sys.executable, str(HELPER), *self.arguments("true")]
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
            argv,
            env={**self.env, "STUB_LIST": "[]", **env},
            capture_output=True,
            text=True,
            check=False,
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


class StandingIssue(Rows):
    """The standing issue (#1353), row for row."""

    def test_no_open_issue_creates_it(self) -> None:
        """Not advanced, no open issue: red, one create, under the exact title."""
        proc = self.run_helper([])
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
        self.assertIn(f"{STARTED}25,905\n", text)
        self.assertIn(f"{NOW}25,905\n", text)
        why = "the checkpoint did not advance in this run (next 448196522)"
        self.assertIn(f"- This run: {why}\n", text)
        self.assertIn("- Legs: success\n", text)
        self.assertIn(f"- Behind since: {RUN}\n", text)
        self.assertIn(f"- Latest run behind: {RUN}\n", text)
        self.assertIn("- Runs behind since it opened: 1\n", text)
        self.assertIn("standing-issue: opened the standing issue", proc.stdout)
        self.assertIn(f"::error::{why}", proc.stdout)

    def test_one_open_edits_it(self) -> None:
        """One open: edit that number, keep "behind since", count the run."""
        listing = [
            {"number": 7, "title": "unrelated", "body": ""},
            {"number": 42, "title": TITLE, "body": previous_body(FIRST_RUN, 3)},
        ]
        proc = self.run_helper(listing)
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
        proc = self.run_helper(listing)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        calls = self.gh_calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][:6], CREATE)
        self.assertIn(f"- Behind since: {RUN}\n", calls[1][7])

    def test_list_failure_files_nothing(self) -> None:
        """Gh issue list failing: exit 2 by name, nothing created."""
        proc = self.run_helper(STUB_LIST_FAIL="1")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.gh_calls(), [LIST])
        self.assertEqual(
            proc.stderr.strip(),
            "standing-issue: gh issue list failed (exit 1): HTTP 502: Bad Gateway;"
            " nothing was filed",
        )

    def test_caught_up_closes_open_issue(self) -> None:
        """Newest checkpoint at its head, one open: green, close it."""
        self.write(self.end, checkpoint("checkpoint-pass.json"))
        listing = [{"number": 42, "title": TITLE, "body": previous_body(FIRST_RUN, 3)}]
        proc = self.run_helper(listing)
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
        newest = "standing-issue: the newest checkpoint: next index 448221522"
        self.assertIn(newest, proc.stdout)
        green = "standing-issue: green: judged to the head of the log"
        self.assertIn(green, proc.stdout)

    def test_caught_up_with_none_open_does_nothing(self) -> None:
        """At the head with none open: list only, green."""
        self.write(self.end, checkpoint("checkpoint-pass.json"))
        proc = self.run_helper([{"number": 7, "title": "x", "body": ""}])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.gh_calls(), [LIST])
        self.assertIn("no standing issue open", proc.stdout)

    def test_no_checkpoint_is_named(self) -> None:
        """No checkpoint at all (before the founding run): red, named."""
        self.start.unlink()
        self.end.unlink()
        proc = self.run_helper([], kept="false")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        text = self.gh_calls()[1][7]
        missing = "unknown: no checkpoint file\n"
        self.assertIn(f"{NEXT}{missing}", text)
        self.assertIn(f"- Checkpoint signed tree head: {missing}", text)
        founded = "none counted: this run founded the checkpoint"
        self.assertIn(f"{STARTED}{founded}\n", text)
        self.assertIn(f"{NOW}{missing}", text)
        self.assertIn("::error::the newest checkpoint: no checkpoint file", proc.stdout)

    def test_unreadable_checkpoint_is_named(self) -> None:
        """A newest checkpoint that is not stele's: red, named as unreadable."""
        self.end.write_text("not json\n", encoding="utf-8")
        proc = self.run_helper([])
        self.assertEqual(proc.returncode, 1, proc.stderr)
        text = self.gh_calls()[1][7]
        unreadable = "unknown: the checkpoint file could not be read\n"
        self.assertIn(f"{NEXT}{unreadable}", text)
        self.assertIn(f"- Checkpoint signed tree head: {unreadable}", text)

    def test_next_infinity_is_unreadable(self) -> None:
        """A `next` of Infinity: named unreadable, never a traceback (#1557)."""
        doc = checkpoint()
        self.end.write_text(
            json.dumps(doc).replace(f'"next": {BEHIND_NEXT}', '"next": Infinity'),
            encoding="utf-8",
        )
        proc = self.run_helper([])
        self.assertEqual(proc.returncode, 1, proc.stderr)
        text = self.gh_calls()[1][7]
        self.assertIn(f"{NEXT}unknown: the checkpoint file could not be read\n", text)

    def test_timestamp_out_of_range_is_unreadable(self) -> None:
        """A head time past the platform's range: named, never a traceback.

        prop_standing_issue.py's shrunk example (#1557): the start's
        timestamp 9.223372036854776e+21.
        """
        doc = checkpoint()
        doc["treeHead"]["timestamp"] = 9.223372036854776e21
        self.write(self.start, doc)
        proc = self.run_helper([])
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn(
            "::error::the checkpoint the run started from was not kept"
            " (the checkpoint file could not be read)",
            proc.stdout,
        )

    def test_nested_past_the_parser_is_unreadable(self) -> None:
        """JSON nested past the parser's depth: named, never a traceback."""
        self.end.write_bytes(b"[" * 100_000)
        proc = self.run_helper([])
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn(
            "::error::the newest checkpoint: the checkpoint file could not be read",
            proc.stdout,
        )

    def test_start_not_kept_is_named(self) -> None:
        """The start job did not finish: red, behind, the count named unknown."""
        proc = self.run_helper([], kept="")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        text = self.gh_calls()[1][7]
        self.assertIn(f"{STARTED}unknown: the start job did not finish\n", text)
        self.assertIn(
            "::error::the checkpoint the run started from was not kept"
            " (the start job did not finish)",
            proc.stdout,
        )

    def test_previous_body_unreadable(self) -> None:
        """An edited-by-hand body: "behind since" named as lost, count restarts."""
        listing = [{"number": 42, "title": TITLE, "body": "hand-written"}]
        proc = self.run_helper(listing)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        text = self.gh_calls()[1][6]
        self.assertIn("- Behind since: unknown: not in the previous body\n", text)
        self.assertIn("- Runs behind since it opened: 1\n", text)

    def test_no_repository(self) -> None:
        """GITHUB_REPOSITORY unset: exit 2 by name, gh never called.

        The script row (#1673): the helper started as the alert job starts
        it, reading its arguments and exiting with the documented status.
        """
        proc = self.run_script(GITHUB_REPOSITORY="")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.gh_calls(), [])
        self.assertEqual(
            proc.stderr.strip(), "standing-issue: GITHUB_REPOSITORY is not set"
        )


class Gaining(Rows):
    """Did the run gain on the log (#1339)? One row per branch of judge()."""

    def test_gaining_files_nothing(self) -> None:
        """Advanced, outstanding smaller than at the start: green, no issue."""
        self.write(self.end, checkpoint(next=BEHIND_NEXT + 10000))
        proc = self.run_helper([])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.gh_calls(), [])
        self.assertIn(
            "standing-issue: green: gaining: outstanding 25,905 at the start,"
            " 15,905 now",
            proc.stdout,
        )

    def test_advanced_but_outstanding_grew(self) -> None:
        """Advanced, but the log grew faster: behind, both counts, still green."""
        grown = checkpoint(next=BEHIND_NEXT + 1000, size=BEHIND_SIZE + 5000)
        self.write(self.end, grown)
        proc = self.run_helper([])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = self.gh_calls()
        self.assertEqual(calls[1][:6], CREATE)
        text = calls[1][7]
        self.assertIn(f"{STARTED}25,905\n", text)
        self.assertIn(f"{NOW}29,905\n", text)
        self.assertIn(
            "- This run: advanced but not gaining: outstanding 25,905 at the start,"
            " 29,905 now\n",
            text,
        )

    def test_advanced_but_outstanding_equal(self) -> None:
        """Advanced, outstanding exactly as at the start: not smaller, behind."""
        level = checkpoint(next=BEHIND_NEXT + 1000, size=BEHIND_SIZE + 1000)
        self.write(self.end, level)
        proc = self.run_helper([])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.gh_calls()[1][:6], CREATE)

    def test_first_run_after_founding(self) -> None:
        """No checkpoint at the start: no earlier count, by name, not an alert."""
        self.start.unlink()
        proc = self.run_helper([], kept="false")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.gh_calls(), [])
        self.assertIn(
            "standing-issue: green: advanced to 448196522; no earlier count,"
            " this run founded it",
            proc.stdout,
        )

    def test_kept_but_not_restored(self) -> None:
        """The start was kept but its copy is gone: red, behind, named."""
        self.start.unlink()
        proc = self.run_helper([])
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(self.gh_calls()[1][:6], CREATE)
        self.assertIn(
            "::error::the checkpoint the run started from was not kept"
            " (no checkpoint file)",
            proc.stdout,
        )


class Decoded(Rows):
    """Each document standing_issue.py reads, decoded where it is read (#1683)."""

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
                doc = checkpoint()
                if field == "next":
                    doc["next"] = value
                else:
                    doc["treeHead"][field] = value
                self.write(self.end, doc)
                proc = self.run_helper([])
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertIn(
                    "::error::the newest checkpoint:"
                    " the checkpoint file could not be read",
                    proc.stdout,
                )

    def test_a_listing_that_is_not_a_list_of_issues(self) -> None:
        """Each wrong shape of gh's listing: exit 2 by name, nothing filed."""
        title = json.dumps(TITLE)
        rows = {
            "{}": "the answer is not a list",
            "[1]": "issue 0 is not an object",
            f'[{{"title": {title}, "number": "7"}}]': (
                "issue 0: number is not an integer"
            ),
            f'[{{"title": {title}, "number": 7, "body": 3}}]': (
                "issue 0: body is not a string"
            ),
        }
        for listing, why in rows.items():
            with self.subTest(listing=listing):
                proc = self.run_helper(STUB_LIST=listing)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(
                    proc.stderr.strip(),
                    "standing-issue: gh issue list answered something that is not"
                    f" a list of issues: {why}; nothing was filed",
                )

    def test_a_listing_that_is_not_strict_json(self) -> None:
        """A key twice in gh's listing: not JSON here, exit 2 by name."""
        proc = self.run_helper(STUB_LIST='{"a": 1, "a": 2}')
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(
            proc.stderr.strip(),
            "standing-issue: gh issue list answered something that is not JSON:"
            ' duplicate key "a"; nothing was filed',
        )


if __name__ == "__main__":
    unittest.main()
