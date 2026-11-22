#!/usr/bin/env python3
"""certificate_issues.py against a stub `gh` on PATH (#1250, #1339).

The stub records every argv and answers `issue list` from the row's
canned search result (or fails). Each row calls the helper's `main` in
this process, the row's environment in place for the call (#1673,
#1699), and asserts what gh received and the message printed; one row,
the script row, starts it as the workflow does.

The reports start from stele v0.24.0's own output (testdata/SOURCE.md).
No live run produced a finding, so the finding rows add `findings` to a
copy of the real report in code, in the shape stele's signerVerdict
records them (internal/assert/reconcile.go at v0.24.0): subject
`CT index <n>`, assertion the condition, detail the reason, the CT index
and log, and the claims read.
"""

from __future__ import annotations

import json
import os
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

import certificate_issues
import decoded
import hermetic

HERE = Path(__file__).resolve().parent
HELPER = HERE / "certificate_issues.py"
DATA = HERE / "testdata"

REPO = "example-owner/example-repo"
RUN = "https://github.com/example-owner/example-repo/actions/runs/300"
LOG = "https://ctfe.sigstore.dev/2022"
PREFIX = "rekor-monitor: certificate at CT index"
OTHER = "example-owner/other/.github/workflows/x.yml"

STUB = """\
import json, os, sys
with open(os.environ["STUB_CALLS"], "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["issue", "list"]:
    if os.environ.get("STUB_LIST_FAIL"):
        print("HTTP 502: Bad Gateway", file=sys.stderr)
        sys.exit(1)
    print(os.environ["STUB_LIST"])
if sys.argv[1:3] == ["issue", "create"] and os.environ.get("STUB_CREATE_FAIL"):
    print("HTTP 403: Resource not accessible by integration", file=sys.stderr)
    sys.exit(1)
"""


def finding(index: int, condition: str, why: str) -> dict[str, object]:
    """Write one finding as stele records it.

    Returns:
        The finding.

    """
    claims = '{"buildSignerDigest":"0000000000000000000000000000000000000000"}'
    detail = f"{why} (CT index {index} of {LOG}; claims read: {claims})"
    return {"subject": f"CT index {index}", "assertion": condition, "detail": detail}


def search(index: int) -> list[str]:
    """Build the dedupe search gh is asked for one index.

    Returns:
        The argv.

    """
    return [
        "issue", "list", "--repo", REPO, "--state", "all", "--limit", "100",
        "--search", f'"CT index {index}" in:title', "--json", "number,title",
    ]  # fmt: skip


class Harness(unittest.TestCase):
    """A stub gh, its call log and the real report, for each row."""

    @override
    def setUp(self) -> None:
        """Lay out a stub gh, its call log, and the real report."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "gh"
        stub.write_text(f"#!{sys.executable}\n{STUB}", encoding="utf-8")
        stub.chmod(0o755)
        self.calls = self.dir / "calls.jsonl"
        self.report = self.dir / "report.json"
        text = (DATA / "report-budget.json").read_text(encoding="utf-8")
        self.doc = decoded.as_object(decoded.load_json(text), "the report")
        # False: the row writes the report file itself (or none at all).
        self.write_report = True
        self.env = hermetic.env(
            PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            GITHUB_REPOSITORY=REPO,
            STUB_CALLS=str(self.calls),
            STUB_LIST="[]",
        )

    def run_helper(self, **env: str) -> hermetic.Called:
        """Write the report and call the helper's main in this process.

        The process's environment is the row's for the call (the stub gh
        first on PATH, the repository, the stub's own variables), so gh
        is started as the workflow starts it (#1673, #1699).

        Returns:
            Its exit status and output, captured.

        """
        if self.write_report:
            self.report.write_text(json.dumps(self.doc), encoding="utf-8")
        argv = ["--report", str(self.report), "--run-url", RUN]
        # The leg runs the script by path with no mise task around it, so
        # the finding writer names the script (#1934).
        with mock.patch.object(sys, "argv", [str(HELPER), *argv]):
            return hermetic.call_main(
                lambda: certificate_issues.main(argv), env={**self.env, **env}
            )

    def run_script(self, **env: str) -> subprocess.CompletedProcess[str]:
        """Write the report and start the helper as the workflow does.

        Returns:
            The finished process.

        """
        if self.write_report:
            self.report.write_text(json.dumps(self.doc), encoding="utf-8")
        argv = [
            sys.executable, str(HELPER), "--report", str(self.report), "--run-url", RUN,
        ]  # fmt: skip
        return subprocess.run(
            argv, env={**self.env, **env}, capture_output=True, text=True, check=False
        )

    def gh_calls(self) -> list[list[str]]:
        """Read what the stub gh was called with.

        Returns:
            Each call's argv, in order.

        """
        if not self.calls.exists():
            return []
        lines = self.calls.read_text(encoding="utf-8").splitlines()
        return [
            [
                decoded.as_str(arg, "an argument")
                for arg in decoded.as_array(decoded.load_json(line), "a call")
            ]
            for line in lines
        ]


class Findings(Harness):
    """One row per guard branch of certificate_issues.py."""

    def test_no_findings_files_nothing(self) -> None:
        """The real report, no findings: exit 0, gh never called."""
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.gh_calls(), list[list[str]]())
        self.assertEqual(
            proc.stdout.strip(), "findings: 0 certificate(s) did not reconcile"
        )

    def test_one_issue_per_certificate(self) -> None:
        """Two conditions of one certificate and one of another: two issues."""
        self.doc["findings"] = [
            finding(448200001, "foreign", f"called {OTHER}"),
            finding(448200001, "chain", "no candidate pins the signer at the digest"),
            finding(448199999, "run", "the forge answers 404 for run 1"),
        ]
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = self.gh_calls()
        pairs = [["issue", "list"], ["issue", "create"]] * 2
        heads = [c[:2] for c in calls]
        self.assertEqual(heads, pairs)
        self.assertEqual(calls[0], search(448199999))
        self.assertEqual(calls[2], search(448200001))
        create: list[str] = [
            "issue", "create", "--repo", REPO, "--title",
            f"{PREFIX} 448199999 did not reconcile (run)",
        ]  # fmt: skip
        self.assertEqual(calls[1][:6], create)
        self.assertEqual(
            calls[3][5],
            f"{PREFIX} 448200001 did not reconcile (foreign, chain)",
        )
        text = calls[3][7]
        self.assertIn(f"- CT index: 448200001 ({LOG})\n", text)
        self.assertIn(f"- Found by: {RUN}\n", text)
        self.assertIn(
            f"- **foreign**: called {OTHER} (CT index 448200001 of {LOG}; claims read:",
            text,
        )
        chain = "- **chain**: no candidate pins the signer at the digest (CT index"
        self.assertIn(chain, text)
        filed = "findings: CT index 448200001: filed (foreign, chain)"
        self.assertIn(filed, proc.stdout)
        self.assertIn("findings: 2 certificate(s) did not reconcile", proc.stdout)

    def test_one_condition_twice_is_one_line(self) -> None:
        """Two findings of one condition: one line, both reasons (#1557).

        prop_certificate_issues.py's shrunk example was two findings with no
        condition at all, written as two lines that each named "".
        """
        self.doc["findings"] = [
            finding(448200001, "chain", "no candidate pins the signer"),
            finding(448200001, "chain", "the digest is\nnot the release's"),
        ]
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = self.gh_calls()
        self.assertEqual(calls[1][5], f"{PREFIX} 448200001 did not reconcile (chain)")
        text = calls[1][7]
        self.assertEqual(text.count("- **chain**: "), 1)
        self.assertIn("- **chain**: no candidate pins the signer (CT index", text)
        self.assertIn("; the digest is not the release's (CT index", text)

    def test_already_filed_is_not_filed_again(self) -> None:
        """An issue for the index, open or closed: no second one."""
        self.doc["findings"] = [finding(448200001, "run", "gone")]
        listing = [
            {"number": 5, "title": f"{PREFIX} 4482000010 did not reconcile (run)"},
            {"number": 6, "title": f"{PREFIX} 448200001 did not reconcile (run)"},
        ]
        proc = self.run_helper(STUB_LIST=json.dumps(listing))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        searched: list[list[str]] = [search(448200001)]
        self.assertEqual(self.gh_calls(), searched)
        self.assertIn("findings: CT index 448200001: already filed as #6", proc.stdout)

    def test_a_longer_index_is_not_this_one(self) -> None:
        """A title for CT index 4482000010 does not dedupe CT index 448200001."""
        self.doc["findings"] = [finding(448200001, "run", "gone")]
        listing = [
            {"number": 5, "title": f"{PREFIX} 4482000010 did not reconcile (run)"},
        ]
        proc = self.run_helper(STUB_LIST=json.dumps(listing))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        create: list[str] = ["issue", "create"]
        self.assertEqual(self.gh_calls()[1][:2], create)

    def test_search_failure_saves_nothing(self) -> None:
        """The dedupe search failing: exit 2 by name, nothing created."""
        self.doc["findings"] = [finding(448200001, "run", "gone")]
        proc = self.run_helper(STUB_LIST_FAIL="1")
        self.assertEqual(proc.returncode, 2)
        searched: list[list[str]] = [search(448200001)]
        self.assertEqual(self.gh_calls(), searched)
        self.assertEqual(
            proc.stderr.strip(),
            "certificate_issues.py:"
            " gh issue list failed (exit 1): HTTP 502: Bad Gateway;"
            " the checkpoint is not saved",
        )

    def test_create_failure_saves_nothing(self) -> None:
        """An issue that cannot be created: exit 2 by name."""
        self.doc["findings"] = [finding(448200001, "run", "gone")]
        proc = self.run_helper(STUB_CREATE_FAIL="1")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(
            proc.stderr.strip(),
            "certificate_issues.py:"
            " gh issue create failed (exit 1): HTTP 403: Resource not"
            " accessible by integration; the checkpoint is not saved",
        )

    def test_no_report(self) -> None:
        """Stele refused before judging (empty stdout): exit 2 by name."""
        self.write_report = False
        self.report.write_text("", encoding="utf-8")
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.gh_calls(), list[list[str]]())
        self.assertEqual(
            proc.stderr.strip(),
            "certificate_issues.py:"
            " stele wrote no report: it refused before judging (its reason"
            " is in its own step); the checkpoint is not saved",
        )

    def test_report_not_json(self) -> None:
        """A report that is not JSON: exit 2 by name."""
        self.write_report = False
        text = "stele assert: the run could not see enough\n"
        self.report.write_text(text, encoding="utf-8")
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 2)
        prefix = "certificate_issues.py: stele's report is not JSON: "
        self.assertTrue(proc.stderr.startswith(prefix))

    def test_report_not_utf8(self) -> None:
        """A report that is not UTF-8: exit 2 by name, never a traceback.

        prop_certificate_issues.py's shrunk example (#1557): the one byte 0x80.
        """
        self.write_report = False
        self.report.write_bytes(b"\x80")
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertTrue(
            proc.stderr.startswith(
                "certificate_issues.py: stele's report is not JSON: "
            )
        )

    def test_report_nested_past_the_parser(self) -> None:
        """JSON nested past the parser's depth: exit 2 by name (#1557)."""
        self.write_report = False
        self.report.write_bytes(b"[" * 100_000)
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertTrue(
            proc.stderr.startswith(
                "certificate_issues.py: stele's report is not JSON: "
            )
        )

    def test_report_from_another_target(self) -> None:
        """Another verb's report: exit 2 by name, nothing filed."""
        self.doc["target"] = "assert tags"
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(
            proc.stderr.strip(),
            "certificate_issues.py:"
            " the report is not stele's assert signer-certificates report;"
            " the checkpoint is not saved",
        )

    def test_subject_not_a_ct_index(self) -> None:
        """A finding whose subject is not a CT index: exit 2, nothing filed."""
        self.doc["findings"] = [
            {"subject": "population", "assertion": "run", "detail": "x"}
        ]
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.gh_calls(), list[list[str]]())
        self.assertEqual(
            proc.stderr.strip(),
            "certificate_issues.py:"
            " a finding's subject is not a CT index: 'population';"
            " the checkpoint is not saved",
        )

    def test_no_repository(self) -> None:
        """GITHUB_REPOSITORY unset: exit 2 by name, gh never called.

        The script row (#1673): the helper started as the workflow starts
        it, reading its arguments and exiting with the documented status.
        """
        proc = self.run_script(GITHUB_REPOSITORY="")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.gh_calls(), list[list[str]]())
        self.assertEqual(
            proc.stderr.strip(), "certificate_issues.py: GITHUB_REPOSITORY is not set"
        )


class Decoded(Harness):
    """Each document certificate_issues.py reads is decoded where it is read (#1683)."""

    def test_a_search_answer_that_is_not_a_list_of_issues(self) -> None:
        """Each wrong shape of the search's answer: exit 2 by name."""
        self.doc["findings"] = [finding(448200001, "run", "gone")]
        title = json.dumps(f"{PREFIX} 448200001 did not reconcile (run)")
        rows = {
            '{"number": 7}': "the answer is not a list",
            "[7]": "issue 0 is not an object",
            '[{"number": 7}]': "issue 0: title is not a string",
            f'[{{"number": "7", "title": {title}}}]': (
                "issue 0: number is not an integer"
            ),
        }
        for answer, why in rows.items():
            with self.subTest(answer=answer):
                proc = self.run_helper(STUB_LIST=answer)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(
                    proc.stderr.strip(),
                    "certificate_issues.py:"
                    " gh issue list answered something that is not a list"
                    f" of issues: {why}; the checkpoint is not saved",
                )

    def test_a_search_answer_that_is_not_json(self) -> None:
        """A search answer that is not strict JSON: exit 2 by name."""
        self.doc["findings"] = [finding(448200001, "run", "gone")]
        proc = self.run_helper(STUB_LIST='{"a": 1, "a": 2}')
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(
            proc.stderr.strip(),
            "certificate_issues.py: gh issue list answered something that is not JSON:"
            ' duplicate key "a"; the checkpoint is not saved',
        )

    def test_findings_that_are_not_a_list(self) -> None:
        """Findings of another kind, falsy or not: exit 2 by name (#1683)."""
        values: tuple[object, ...] = ("", 0, dict[str, object](), "ab")
        for value in values:
            with self.subTest(findings=value):
                self.doc["findings"] = value
                proc = self.run_helper()
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(
                    proc.stderr.strip(),
                    "certificate_issues.py:"
                    f" the report's findings are not a list: {value!r};"
                    " the checkpoint is not saved",
                )

    def test_report_with_a_key_twice(self) -> None:
        """A report that is not strict JSON is not JSON here: exit 2 by name."""
        self.write_report = False
        self.report.write_text('{"target": "x", "target": "y"}', encoding="utf-8")
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(
            proc.stderr.strip(),
            "certificate_issues.py:"
            ' stele\'s report is not JSON: duplicate key "target";'
            " the checkpoint is not saved",
        )


if __name__ == "__main__":
    unittest.main()
