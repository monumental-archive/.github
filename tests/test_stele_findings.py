"""stele's report, read into finding records (#1935).

Rows for `belt stele-findings`, its `main` called in this process as
a task runs it, ORG_FINDINGS and MISE_TASK_NAME set, over a stub `stele`
on PATH that logs its arguments, says a line on stderr, prints the
row's document and exits with the row's status. One row runs the
belt's pinned stele itself on an SPDX document with one versionless
PURL: the row that meets the real tool.
"""

import json
import os
import sys
from pathlib import Path

import pytest

from belt import decoded, stele_findings
from belt.typed_pytest import fixture
from tests import support

HERE = support.MISE
TASK = "audit:inventory"

STUB = """\
import os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:
    log.write("\\t".join(sys.argv[1:]) + "\\n")
print("stub stele: walking", file=sys.stderr)
with open(os.environ["STUB_DOC"], encoding="utf-8") as doc:
    sys.stdout.write(doc.read())
sys.exit(int(os.environ["STUB_RC"]))
"""

DETAIL = (
    "a versionless PURL is invisible to every scanner, so an inventory"
    " carrying one gates on a set it cannot see"
)
QUOTED: dict[str, object] = {
    "schema": 9,
    "target": "assert inventory",
    "subject": "a.spdx.json",
    "verdict": "FAIL",
    "population": {
        "size": 1,
        "expected": 1,
        "source": "declared",
        "detail": "handed inventory documents",
    },
    "findings": [
        {
            "subject": "a.spdx.json",
            "assertion": "versioned-purl",
            "expected": "a versioned PURL",
            "actual": "pkg:cargo/a",
            "detail": DETAIL,
        }
    ],
}
SPDX: dict[str, object] = {
    "spdxVersion": "SPDX-2.3",
    "dataLicense": "CC0-1.0",
    "SPDXID": "SPDXRef-DOCUMENT",
    "name": "a",
    "documentNamespace": "https://example.invalid/a",
    "creationInfo": {"created": "2026-11-21T00:00:00Z", "creators": ["Tool: test"]},
    "packages": [
        {
            "name": "a",
            "SPDXID": "SPDXRef-a",
            "downloadLocation": "NOASSERTION",
            "externalRefs": [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": "pkg:cargo/a",
                }
            ],
        }
    ],
}
QUOTED_RECORD: dict[str, object] = {
    "v": 1,
    "task": TASK,
    "subject": "a.spdx.json",
    "message": f"versioned-purl: {DETAIL}",
    "detail": ["expected: a versioned PURL", "actual: pkg:cargo/a"],
}


def report(verdict: str, *entries: dict[str, object], schema: int = 9) -> str:
    """Write a report document as stele prints it.

    Returns:
        The document, one line.

    """
    doc: dict[str, object] = {
        "schema": schema,
        "target": "assert inventory",
        "subject": "the population",
        "verdict": verdict,
        "findings": list(entries),
    }
    return json.dumps(doc)


def same[T](got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    assert got == want, msg or None


class Adapter:
    """The adapter over a stub stele."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Lay out a stub stele, its log, and the findings file."""
        self.make_env = env
        self.dir = Path(str(tmp_path))
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        stub = self.bin / "stele"
        stub.write_text(f"#!{sys.executable}\n{STUB}", encoding="utf-8")
        stub.chmod(0o755)
        self.log = self.dir / "stele.log"
        self.doc = self.dir / "doc.json"
        self.file = self.dir / "findings" / "job.jsonl"

    def enter(self, monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
        """Stand in the row's directory as a task runs the adapter, under `env`.

        The tool is looked up on `env`'s PATH, as a process started with
        it would find it.
        """
        found = support.optional_tool("stele", path=env["PATH"]) or "stele"
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(stele_findings, "STELE", found)
        monkeypatch.chdir(self.dir)

    def answer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        document: str,
        status: int,
        path: str | None = None,
    ) -> None:
        """Have the stub answer `document` and `status` when the adapter runs."""
        self.doc.write_text(document, encoding="utf-8")
        env = self.make_env(
            PATH=path or f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            ORG_FINDINGS=str(self.file),
            MISE_TASK_NAME=TASK,
            STUB_LOG=str(self.log),
            STUB_DOC=str(self.doc),
            STUB_RC=str(status),
            PYTHONUTF8="1",
        )
        self.enter(monkeypatch, env)

    def records(self) -> list[dict[str, object]]:
        """Read every record the adapter appended.

        Returns:
            The records, in order; none when the file was not written.

        """
        if not self.file.exists():
            return []
        return [
            decoded.as_object(decoded.load_json(line), "a record")
            for line in self.file.read_text(encoding="utf-8").splitlines()
        ]

    def refusal(self) -> tuple[object, str]:
        """Read the one record a refusal leaves: its own (#1934).

        Returns:
            Its subject and message; stele's findings are never recorded.

        """
        got = self.records()
        same(len(got), 1)
        return got[0]["subject"], str(got[0]["message"])


@fixture
def adapter(tmp_path: Path, env: support.Env) -> Adapter:
    """Give each row its own Adapter.

    Returns:
        The row's Adapter.

    """
    return Adapter(tmp_path, env=env)


def test_the_quoted_document_is_one_record(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The document the issue quotes: one record, the line, the verdict, 1."""
    adapter.answer(monkeypatch, json.dumps(QUOTED), 1)
    status = stele_findings.main(["assert", "inventory", "a.spdx.json"])
    out, err = capfd.readouterr()
    same(status, 1, err)
    want: list[dict[str, object]] = [QUOTED_RECORD]
    same(adapter.records(), want)
    same(
        err.splitlines(),
        [
            "stub stele: walking",
            f"{TASK}: a.spdx.json: versioned-purl: {DETAIL}",
            "  expected: a versioned PURL",
            "  actual: pkg:cargo/a",
        ],
    )
    same(out, f"{TASK}: stele: FAIL\n")


def test_a_pass_records_nothing(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """PASS with no findings: no record, exit 0."""
    adapter.answer(monkeypatch, report("PASS"), 0)
    status = stele_findings.main(["assert", "inventory", "a.spdx.json"])
    out, err = capfd.readouterr()
    same(status, 0, err)
    same(adapter.records(), [])
    same(out, f"{TASK}: stele: PASS\n")


def test_cannot_judge_without_findings_is_one_record(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A verdict other than PASS with no findings is one finding."""
    adapter.answer(monkeypatch, report("CANNOT_JUDGE"), 1)
    status = stele_findings.main(["assert", "inventory", "a.spdx.json"])
    out, err = capfd.readouterr()
    same(status, 1, err)
    want: list[dict[str, object]] = [
        {
            "v": 1,
            "task": TASK,
            "subject": "the population",
            "message": "CANNOT_JUDGE",
            "detail": [],
        }
    ]
    same(adapter.records(), want)
    same(out, f"{TASK}: stele: CANNOT_JUDGE\n")


def test_three_findings_are_three_records_in_order(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Each entry one record, in the document's order, fields as present."""
    entries: list[dict[str, object]] = [
        {"subject": "b", "assertion": "first", "detail": "one"},
        {"subject": "a", "assertion": "second", "actual": "x"},
        {"subject": "c", "assertion": "third", "expected": "y"},
    ]
    adapter.answer(monkeypatch, report("FAIL", *entries), 1)
    status = stele_findings.main(["assert", "inventory", "a.spdx.json"])
    _out, err = capfd.readouterr()
    same(status, 1, err)
    got = [(r["subject"], r["message"], r["detail"]) for r in adapter.records()]
    want: list[tuple[object, object, object]] = [
        ("b", "first: one", []),
        ("a", "second", ["actual: x"]),
        ("c", "third", ["expected: y"]),
    ]
    same(got, want)


def test_another_schema_is_refused_by_name(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Schema 10: refused, both numbers named, exit 1, only that recorded."""
    adapter.answer(monkeypatch, report("PASS", schema=10), 0)
    status = stele_findings.main(["assert", "inventory", "a.spdx.json"])
    out, err = capfd.readouterr()
    same(status, 1, err)
    assert "the report is schema 10; this adapter reads schema 9" in err
    subject, message = adapter.refusal()
    same(subject, "")
    assert "the report is schema 10" in message
    same(out, "")


def test_output_that_is_not_json_is_refused_by_name(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Not one JSON document: refused with stele's status, exit 1."""
    adapter.answer(monkeypatch, "assert: FAIL\n", 3)
    status = stele_findings.main(["assert", "inventory", "a.spdx.json"])
    _out, err = capfd.readouterr()
    same(status, 1, err)
    assert (
        f"{TASK}: stele assert inventory exited 3: the output is not stele's report"
    ) in err
    subject, message = adapter.refusal()
    same(subject, "")
    assert "exited 3: the output is not stele's report" in message


def test_a_malformed_finding_records_none(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """One entry that is not a finding: refused before any of stele's is."""
    entries: list[dict[str, object]] = [
        {"subject": "a", "assertion": "fine"},
        {"subject": "b"},
    ]
    adapter.answer(monkeypatch, report("FAIL", *entries), 1)
    status = stele_findings.main(["assert", "inventory", "a.spdx.json"])
    _out, err = capfd.readouterr()
    same(status, 1, err)
    assert "findings[1]: assertion" in err
    _, message = adapter.refusal()
    assert "findings[1]: assertion" in message


def test_stele_s_status_is_the_exit(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A status of 2 from stele with a PASS document exits 2."""
    adapter.answer(monkeypatch, report("PASS"), 2)
    status = stele_findings.main(["assert", "inventory", "a.spdx.json"])
    _out, err = capfd.readouterr()
    same(status, 2, err)
    same(adapter.records(), [])


def test_arguments_pass_unchanged_after_json(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The adapter calls `stele assert <target> --json` and the arguments."""
    args = ["--org", "an org", "--policy", "p.json", "--depth", "full", "--"]
    adapter.answer(monkeypatch, report("PASS"), 0)
    status = stele_findings.main(["assert", "evidence", "--", *args])
    _out, err = capfd.readouterr()
    same(status, 0, err)
    same(
        adapter.log.read_text(encoding="utf-8").splitlines(),
        ["\t".join(["assert", "evidence", "--json", *args])],
    )


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param(
            ["inventory"],
            (
                "belt stele-findings: error: argument mode: invalid choice: "
                "'inventory' (choose from 'assert')"
            ),
            id='["inventory"]',
        ),
    ],
)
def test_no_assertion_is_a_usage_refusal(
    argv: list[str], error: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """Arguments that name no assertion: argparse's exit 2 and its error."""
    with pytest.raises(SystemExit) as ended:
        stele_findings.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt stele-findings "), err
    assert err.splitlines()[-1] == error


def test_no_stele_is_refused_by_name(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """No stele on PATH: refused by name, exit 1, only that recorded."""
    adapter.answer(monkeypatch, report("PASS"), 0, path=str(adapter.dir / "empty"))
    status = stele_findings.main(["assert", "inventory", "a.spdx.json"])
    _out, err = capfd.readouterr()
    same(status, 1, err)
    assert "stele is not on PATH" in err
    same(adapter.refusal(), ("stele assert inventory", "stele is not on PATH"))


def test_the_pinned_stele_gives_the_one_record(
    adapter: Adapter,
    env: support.Env,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A versionless PURL, judged by stele itself: the quoted record."""
    stele = support.optional_tool("stele")
    if stele is None:
        pytest.skip("the belt's stele is not on PATH")
    (adapter.dir / "a.spdx.json").write_text(json.dumps(SPDX), encoding="utf-8")
    adapter.enter(
        monkeypatch,
        env(ORG_FINDINGS=str(adapter.file), MISE_TASK_NAME=TASK, PYTHONUTF8="1"),
    )
    status = stele_findings.main(["assert", "inventory", "a.spdx.json"])
    out, err = capfd.readouterr()
    same(status, 1, err)
    want: list[dict[str, object]] = [QUOTED_RECORD]
    same(adapter.records(), want)
    same(out, f"{TASK}: stele: FAIL\n")
