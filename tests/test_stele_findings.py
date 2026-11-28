"""stele's report, read into finding records (#1935).

Rows for `mise/stele_findings.py`, run as a task runs it: a child
process with ORG_FINDINGS and MISE_TASK_NAME set, over a stub `stele`
on PATH that logs its arguments, says a line on stderr, prints the
row's document and exits with the row's status. One row runs the
belt's pinned stele itself on an SPDX document with one versionless
PURL: the row that meets the real tool.
"""

import json
import os
import subprocess
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

    def __init__(
        self, tmp_path: Path, capfd: pytest.CaptureFixture[str], *, env: support.Env
    ) -> None:
        """Lay out a stub stele, its log, and the findings file."""
        self.make_env = env
        self.capfd = capfd
        self.dir = Path(str(tmp_path))
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        stub = self.bin / "stele"
        stub.write_text(f"#!{sys.executable}\n{STUB}", encoding="utf-8")
        stub.chmod(0o755)
        self.log = self.dir / "stele.log"
        self.doc = self.dir / "doc.json"
        self.file = self.dir / "findings" / "job.jsonl"

    def adapt(
        self, argv: list[str], env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        """Call the adapter as a task runs it, in process, under `env`.

        The tool is looked up on `env`'s PATH, as a process started with
        it would find it.

        Returns:
            Its status and what it wrote, as a finished process's.

        """
        found = support.optional_tool("stele", path=env["PATH"]) or "stele"
        with pytest.MonkeyPatch.context() as patched:
            for name, value in env.items():
                patched.setenv(name, value)
            patched.setattr(stele_findings, "STELE", found)
            patched.chdir(self.dir)
            status = stele_findings.main(argv)
        out, err = self.capfd.readouterr()
        return subprocess.CompletedProcess(argv, status, out, err)

    def run_adapter(
        self, document: str, status: int, *args: str, path: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the adapter as a task runs it, the stub answering.

        Returns:
            The finished process.

        """
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
        argv = list(args) or ["assert", "inventory", "a.spdx.json"]
        return self.adapt([*argv], env)

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
def adapter(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], env: support.Env
) -> Adapter:
    """Give each row its own Adapter."""
    return Adapter(tmp_path, capfd, env=env)


def test_the_quoted_document_is_one_record(adapter: Adapter) -> None:
    """The document the issue quotes: one record, the line, the verdict, 1."""
    done = adapter.run_adapter(json.dumps(QUOTED), 1)
    same(done.returncode, 1, done.stderr)
    want: list[dict[str, object]] = [QUOTED_RECORD]
    same(adapter.records(), want)
    same(
        done.stderr.splitlines(),
        [
            "stub stele: walking",
            f"{TASK}: a.spdx.json: versioned-purl: {DETAIL}",
            "  expected: a versioned PURL",
            "  actual: pkg:cargo/a",
        ],
    )
    same(done.stdout, f"{TASK}: stele: FAIL\n")


def test_a_pass_records_nothing(adapter: Adapter) -> None:
    """PASS with no findings: no record, exit 0."""
    done = adapter.run_adapter(report("PASS"), 0)
    same(done.returncode, 0, done.stderr)
    same(adapter.records(), [])
    same(done.stdout, f"{TASK}: stele: PASS\n")


def test_cannot_judge_without_findings_is_one_record(adapter: Adapter) -> None:
    """A verdict other than PASS with no findings is one finding."""
    done = adapter.run_adapter(report("CANNOT_JUDGE"), 1)
    same(done.returncode, 1, done.stderr)
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
    same(done.stdout, f"{TASK}: stele: CANNOT_JUDGE\n")


def test_three_findings_are_three_records_in_order(adapter: Adapter) -> None:
    """Each entry one record, in the document's order, fields as present."""
    entries: list[dict[str, object]] = [
        {"subject": "b", "assertion": "first", "detail": "one"},
        {"subject": "a", "assertion": "second", "actual": "x"},
        {"subject": "c", "assertion": "third", "expected": "y"},
    ]
    done = adapter.run_adapter(report("FAIL", *entries), 1)
    same(done.returncode, 1, done.stderr)
    got = [(r["subject"], r["message"], r["detail"]) for r in adapter.records()]
    want: list[tuple[object, object, object]] = [
        ("b", "first: one", []),
        ("a", "second", ["actual: x"]),
        ("c", "third", ["expected: y"]),
    ]
    same(got, want)


def test_another_schema_is_refused_by_name(adapter: Adapter) -> None:
    """Schema 10: refused, both numbers named, exit 1, only that recorded."""
    done = adapter.run_adapter(report("PASS", schema=10), 0)
    same(done.returncode, 1, done.stderr)
    assert "the report is schema 10; this adapter reads schema 9" in done.stderr
    subject, message = adapter.refusal()
    same(subject, "")
    assert "the report is schema 10" in message
    same(done.stdout, "")


def test_output_that_is_not_json_is_refused_by_name(adapter: Adapter) -> None:
    """Not one JSON document: refused with stele's status, exit 1."""
    done = adapter.run_adapter("assert: FAIL\n", 3)
    same(done.returncode, 1, done.stderr)
    assert (
        f"{TASK}: stele assert inventory exited 3: the output is not stele's report"
    ) in done.stderr
    subject, message = adapter.refusal()
    same(subject, "")
    assert "exited 3: the output is not stele's report" in message


def test_a_malformed_finding_records_none(adapter: Adapter) -> None:
    """One entry that is not a finding: refused before any of stele's is."""
    entries: list[dict[str, object]] = [
        {"subject": "a", "assertion": "fine"},
        {"subject": "b"},
    ]
    done = adapter.run_adapter(report("FAIL", *entries), 1)
    same(done.returncode, 1, done.stderr)
    assert "findings[1]: assertion" in done.stderr
    _, message = adapter.refusal()
    assert "findings[1]: assertion" in message


def test_stele_s_status_is_the_exit(adapter: Adapter) -> None:
    """A status of 2 from stele with a PASS document exits 2."""
    done = adapter.run_adapter(report("PASS"), 2)
    same(done.returncode, 2, done.stderr)
    same(adapter.records(), [])


def test_arguments_pass_unchanged_after_json(adapter: Adapter) -> None:
    """The adapter calls `stele assert <target> --json` and the arguments."""
    args = ["--org", "an org", "--policy", "p.json", "--depth", "full", "--"]
    done = adapter.run_adapter(report("PASS"), 0, "assert", "evidence", *args)
    same(done.returncode, 0, done.stderr)
    same(
        adapter.log.read_text(encoding="utf-8").splitlines(),
        ["\t".join(["assert", "evidence", "--json", *args])],
    )


def test_no_assertion_is_a_usage_refusal(adapter: Adapter) -> None:
    """Arguments that name no assertion: usage, exit 1, stele not run."""
    done = adapter.run_adapter(report("PASS"), 0, "inventory")
    same(done.returncode, 1, done.stderr)
    assert "usage: belt stele-findings assert <target>" in done.stderr
    assert not adapter.log.exists()


def test_no_stele_is_refused_by_name(adapter: Adapter) -> None:
    """No stele on PATH: refused by name, exit 1, only that recorded."""
    done = adapter.run_adapter(report("PASS"), 0, path=str(adapter.dir / "empty"))
    same(done.returncode, 1, done.stderr)
    assert "stele is not on PATH" in done.stderr
    same(adapter.refusal(), ("stele assert inventory", "stele is not on PATH"))


def test_the_pinned_stele_gives_the_one_record(
    adapter: Adapter, env: support.Env
) -> None:
    """A versionless PURL, judged by stele itself: the quoted record."""
    stele = support.optional_tool("stele")
    if stele is None:
        pytest.skip("the belt's stele is not on PATH")
    (adapter.dir / "a.spdx.json").write_text(json.dumps(SPDX), encoding="utf-8")
    done = adapter.adapt(
        ["assert", "inventory", "a.spdx.json"],
        env(ORG_FINDINGS=str(adapter.file), MISE_TASK_NAME=TASK, PYTHONUTF8="1"),
    )
    same(done.returncode, 1, done.stderr)
    want: list[dict[str, object]] = [QUOTED_RECORD]
    same(adapter.records(), want)
    same(done.stdout, f"{TASK}: stele: FAIL\n")
