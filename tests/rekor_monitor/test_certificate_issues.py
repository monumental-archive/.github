"""`belt certificate-issues` against a stub `gh` on PATH (#1250, #1339).

The stub records every argv and answers `issue list` from the row's
canned search result (or fails). Each row calls the helper's `main` in
this process, the row's environment in place for the call (#1673,
#1699), and asserts what gh received and the message printed.

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
import sys
from pathlib import Path

import pytest

from belt import decoded
from belt.rekor_monitor import certificate_issues
from belt.typed_pytest import fixture
from tests import support

COMMAND = "certificate-issues"

DATA = Path(__file__).resolve().parent / "testdata"

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


class Harness:
    """A stub gh, its call log and the real report, for each row."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Lay out a stub gh, its call log, and the real report."""
        self.make_env = env
        self.dir = Path(str(tmp_path))
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
        self.env = self.make_env(
            PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            GITHUB_REPOSITORY=REPO,
            STUB_CALLS=str(self.calls),
            STUB_LIST="[]",
        )

    def run_helper(
        self, *, capfd: pytest.CaptureFixture[str], **env: str
    ) -> tuple[int, str, str]:
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
        # The leg runs `belt certificate-issues` with no mise task around
        # it, so the finding writer names the command (#1934).
        with pytest.MonkeyPatch.context() as patched:
            patched.setattr(sys, "argv", [COMMAND, *argv])
            for name, value in {**self.env, **env}.items():
                patched.setenv(name, value)
            status = certificate_issues.main(argv)
        out, err = capfd.readouterr()
        return status, out, err

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


@fixture
def harness(tmp_path: Path, env: support.Env) -> Harness:
    """Give each row its own Harness."""
    return Harness(tmp_path, env=env)


def test_no_findings_files_nothing(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """The real report, no findings: exit 0, gh never called."""
    status, out, err = harness.run_helper(capfd=capfd)
    assert status == 0, err
    assert harness.gh_calls() == list[list[str]]()
    assert out.strip() == "findings: 0 certificate(s) did not reconcile"


def test_one_issue_per_certificate(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """Two conditions of one certificate and one of another: two issues."""
    harness.doc["findings"] = [
        finding(448200001, "foreign", f"called {OTHER}"),
        finding(448200001, "chain", "no candidate pins the signer at the digest"),
        finding(448199999, "run", "the forge answers 404 for run 1"),
    ]
    status, out, err = harness.run_helper(capfd=capfd)
    assert status == 0, err
    calls = harness.gh_calls()
    pairs = [["issue", "list"], ["issue", "create"]] * 2
    heads = [c[:2] for c in calls]
    assert heads == pairs
    assert calls[0] == search(448199999)
    assert calls[2] == search(448200001)
    create: list[str] = [
        "issue", "create", "--repo", REPO, "--title",
        f"{PREFIX} 448199999 did not reconcile (run)",
    ]  # fmt: skip
    assert calls[1][:6] == create
    assert calls[3][5] == f"{PREFIX} 448200001 did not reconcile (foreign, chain)"
    text = calls[3][7]
    assert f"- CT index: 448200001 ({LOG})\n" in text
    assert f"- Found by: {RUN}\n" in text
    assert (
        f"- **foreign**: called {OTHER} (CT index 448200001 of {LOG}; claims read:"
        in text
    )
    chain = "- **chain**: no candidate pins the signer at the digest (CT index"
    assert chain in text
    filed = "findings: CT index 448200001: filed (foreign, chain)"
    assert filed in out
    assert "findings: 2 certificate(s) did not reconcile" in out


def test_one_condition_twice_is_one_line(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """Two findings of one condition: one line, both reasons (#1557).

    prop_certificate_issues.py's shrunk example was two findings with no
    condition at all, written as two lines that each named "".
    """
    harness.doc["findings"] = [
        finding(448200001, "chain", "no candidate pins the signer"),
        finding(448200001, "chain", "the digest is\nnot the release's"),
    ]
    status, _out, err = harness.run_helper(capfd=capfd)
    assert status == 0, err
    calls = harness.gh_calls()
    assert calls[1][5] == f"{PREFIX} 448200001 did not reconcile (chain)"
    text = calls[1][7]
    assert text.count("- **chain**: ") == 1
    assert "- **chain**: no candidate pins the signer (CT index" in text
    assert "; the digest is not the release's (CT index" in text


def test_already_filed_is_not_filed_again(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """An issue for the index, open or closed: no second one."""
    harness.doc["findings"] = [finding(448200001, "run", "gone")]
    listing = [
        {"number": 5, "title": f"{PREFIX} 4482000010 did not reconcile (run)"},
        {"number": 6, "title": f"{PREFIX} 448200001 did not reconcile (run)"},
    ]
    status, out, err = harness.run_helper(STUB_LIST=json.dumps(listing), capfd=capfd)
    assert status == 0, err
    searched: list[list[str]] = [search(448200001)]
    assert harness.gh_calls() == searched
    assert "findings: CT index 448200001: already filed as #6" in out


def test_a_longer_index_is_not_this_one(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """A title for CT index 4482000010 does not dedupe CT index 448200001."""
    harness.doc["findings"] = [finding(448200001, "run", "gone")]
    listing = [
        {"number": 5, "title": f"{PREFIX} 4482000010 did not reconcile (run)"},
    ]
    status, _out, err = harness.run_helper(STUB_LIST=json.dumps(listing), capfd=capfd)
    assert status == 0, err
    create: list[str] = ["issue", "create"]
    assert harness.gh_calls()[1][:2] == create


def test_search_failure_saves_nothing(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """The dedupe search failing: exit 2 by name, nothing created."""
    harness.doc["findings"] = [finding(448200001, "run", "gone")]
    status, _out, err = harness.run_helper(STUB_LIST_FAIL="1", capfd=capfd)
    assert status == 2
    searched: list[list[str]] = [search(448200001)]
    assert harness.gh_calls() == searched
    assert err.strip() == (
        "certificate-issues:"
        " gh issue list failed (exit 1): HTTP 502: Bad Gateway;"
        " the checkpoint is not saved"
    )


def test_create_failure_saves_nothing(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """An issue that cannot be created: exit 2 by name."""
    harness.doc["findings"] = [finding(448200001, "run", "gone")]
    status, _out, err = harness.run_helper(STUB_CREATE_FAIL="1", capfd=capfd)
    assert status == 2
    assert err.strip() == (
        "certificate-issues:"
        " gh issue create failed (exit 1): HTTP 403: Resource not"
        " accessible by integration; the checkpoint is not saved"
    )


def test_no_report(harness: Harness, capfd: pytest.CaptureFixture[str]) -> None:
    """Stele refused before judging (empty stdout): exit 2 by name."""
    harness.write_report = False
    harness.report.write_text("", encoding="utf-8")
    status, _out, err = harness.run_helper(capfd=capfd)
    assert status == 2
    assert harness.gh_calls() == list[list[str]]()
    assert err.strip() == (
        "certificate-issues:"
        " stele wrote no report: it refused before judging (its reason"
        " is in its own step); the checkpoint is not saved"
    )


def test_report_not_json(harness: Harness, capfd: pytest.CaptureFixture[str]) -> None:
    """A report that is not JSON: exit 2 by name."""
    harness.write_report = False
    text = "stele assert: the run could not see enough\n"
    harness.report.write_text(text, encoding="utf-8")
    status, _out, err = harness.run_helper(capfd=capfd)
    assert status == 2
    prefix = "certificate-issues: stele's report is not JSON: "
    assert err.startswith(prefix)


def test_report_not_utf8(harness: Harness, capfd: pytest.CaptureFixture[str]) -> None:
    """A report that is not UTF-8: exit 2 by name, never a traceback.

    prop_certificate_issues.py's shrunk example (#1557): the one byte 0x80.
    """
    harness.write_report = False
    harness.report.write_bytes(b"\x80")
    status, _out, err = harness.run_helper(capfd=capfd)
    assert status == 2, err
    assert err.startswith("certificate-issues: stele's report is not JSON: ")


def test_report_nested_past_the_parser(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """JSON nested past the parser's depth: exit 2 by name (#1557)."""
    harness.write_report = False
    harness.report.write_bytes(b"[" * 100_000)
    status, _out, err = harness.run_helper(capfd=capfd)
    assert status == 2, err
    assert err.startswith("certificate-issues: stele's report is not JSON: ")


def test_report_from_another_target(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """Another verb's report: exit 2 by name, nothing filed."""
    harness.doc["target"] = "assert tags"
    status, _out, err = harness.run_helper(capfd=capfd)
    assert status == 2
    assert err.strip() == (
        "certificate-issues:"
        " the report is not stele's assert signer-certificates report;"
        " the checkpoint is not saved"
    )


def test_subject_not_a_ct_index(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """A finding whose subject is not a CT index: exit 2, nothing filed."""
    harness.doc["findings"] = [
        {"subject": "population", "assertion": "run", "detail": "x"}
    ]
    status, _out, err = harness.run_helper(capfd=capfd)
    assert status == 2
    assert harness.gh_calls() == list[list[str]]()
    assert err.strip() == (
        "certificate-issues:"
        " a finding's subject is not a CT index: 'population';"
        " the checkpoint is not saved"
    )


def test_no_repository(harness: Harness, capfd: pytest.CaptureFixture[str]) -> None:
    """GITHUB_REPOSITORY unset: exit 2 by name, gh never called."""
    status, _out, err = harness.run_helper(GITHUB_REPOSITORY="", capfd=capfd)
    assert status == 2
    assert harness.gh_calls() == list[list[str]]()
    assert err.strip() == f"{COMMAND}: GITHUB_REPOSITORY is not set"


def test_a_search_answer_that_is_not_a_list_of_issues(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """Each wrong shape of the search's answer: exit 2 by name."""
    harness.doc["findings"] = [finding(448200001, "run", "gone")]
    title = json.dumps(f"{PREFIX} 448200001 did not reconcile (run)")
    rows = {
        '{"number": 7}': "the answer is not a list",
        "[7]": "issue 0 is not an object",
        '[{"number": 7}]': "issue 0: title is not a string",
        f'[{{"number": "7", "title": {title}}}]': ("issue 0: number is not an integer"),
    }
    for answer, why in rows.items():
        status, _out, err = harness.run_helper(STUB_LIST=answer, capfd=capfd)
        assert status == 2
        assert err.strip() == (
            "certificate-issues:"
            " gh issue list answered something that is not a list"
            f" of issues: {why}; the checkpoint is not saved"
        )


def test_a_search_answer_that_is_not_json(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """A search answer that is not strict JSON: exit 2 by name."""
    harness.doc["findings"] = [finding(448200001, "run", "gone")]
    status, _out, err = harness.run_helper(STUB_LIST='{"a": 1, "a": 2}', capfd=capfd)
    assert status == 2
    assert err.strip() == (
        "certificate-issues: gh issue list answered something that is not JSON:"
        ' duplicate key "a"; the checkpoint is not saved'
    )


def test_findings_that_are_not_a_list(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """Findings of another kind, falsy or not: exit 2 by name (#1683)."""
    values: tuple[object, ...] = ("", 0, dict[str, object](), "ab")
    for value in values:
        harness.doc["findings"] = value
        status, _out, err = harness.run_helper(capfd=capfd)
        assert status == 2
        assert err.strip() == (
            "certificate-issues:"
            f" the report's findings are not a list: {value!r};"
            " the checkpoint is not saved"
        )


def test_report_with_a_key_twice(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """A report that is not strict JSON is not JSON here: exit 2 by name."""
    harness.write_report = False
    harness.report.write_text('{"target": "x", "target": "y"}', encoding="utf-8")
    status, _out, err = harness.run_helper(capfd=capfd)
    assert status == 2
    assert err.strip() == (
        "certificate-issues:"
        ' stele\'s report is not JSON: duplicate key "target";'
        " the checkpoint is not saved"
    )
