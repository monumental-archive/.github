#!/usr/bin/env python3
"""stele's report, read into finding records (#1935).

Rows for `mise/stele_findings.py`, run as a task runs it: a child
process with ORG_FINDINGS and MISE_TASK_NAME set, over a stub `stele`
on PATH that logs its arguments, says a line on stderr, prints the
row's document and exits with the row's status. One row runs the
belt's pinned stele itself on an SPDX document with one versionless
PURL: the row that meets the real tool.

stdlib `unittest`, matching the rest of mise/.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic

HERE = Path(__file__).resolve().parent
ADAPTER = HERE / "stele_findings.py"
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


def same[T](case: unittest.TestCase, got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    case.assertEqual(got, want, msg or None)


class Adapter(unittest.TestCase):
    """The adapter over a stub stele."""

    @override
    def setUp(self) -> None:
        """Lay out a stub stele, its log, and the findings file."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        stub = self.bin / "stele"
        stub.write_text(f"#!{sys.executable}\n{STUB}", encoding="utf-8")
        stub.chmod(0o755)
        self.log = self.dir / "stele.log"
        self.doc = self.dir / "doc.json"
        self.file = self.dir / "findings" / "job.jsonl"

    def run_adapter(
        self, document: str, status: int, *args: str, path: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the adapter as a task runs it, the stub answering.

        Returns:
            The finished process.

        """
        self.doc.write_text(document, encoding="utf-8")
        env = hermetic.env(
            PATH=path or f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            ORG_FINDINGS=str(self.file),
            MISE_TASK_NAME=TASK,
            STUB_LOG=str(self.log),
            STUB_DOC=str(self.doc),
            STUB_RC=str(status),
            PYTHONUTF8="1",
        )
        argv = list(args) or ["assert", "inventory", "a.spdx.json"]
        return subprocess.run(
            [sys.executable, str(ADAPTER), *argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=self.dir,
            check=False,
        )

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

    def test_the_quoted_document_is_one_record(self) -> None:
        """The document the issue quotes: one record, the line, the verdict, 1."""
        done = self.run_adapter(json.dumps(QUOTED), 1)
        same(self, done.returncode, 1, done.stderr)
        want: list[dict[str, object]] = [QUOTED_RECORD]
        same(self, self.records(), want)
        same(
            self,
            done.stderr.splitlines(),
            [
                "stub stele: walking",
                f"{TASK}: a.spdx.json: versioned-purl: {DETAIL}",
                "  expected: a versioned PURL",
                "  actual: pkg:cargo/a",
            ],
        )
        same(self, done.stdout, f"{TASK}: stele: FAIL\n")

    def test_a_pass_records_nothing(self) -> None:
        """PASS with no findings: no record, exit 0."""
        done = self.run_adapter(report("PASS"), 0)
        same(self, done.returncode, 0, done.stderr)
        same(self, self.records(), [])
        same(self, done.stdout, f"{TASK}: stele: PASS\n")

    def test_cannot_judge_without_findings_is_one_record(self) -> None:
        """A verdict other than PASS with no findings is one finding."""
        done = self.run_adapter(report("CANNOT_JUDGE"), 1)
        same(self, done.returncode, 1, done.stderr)
        want: list[dict[str, object]] = [
            {
                "v": 1,
                "task": TASK,
                "subject": "the population",
                "message": "CANNOT_JUDGE",
                "detail": [],
            }
        ]
        same(self, self.records(), want)
        same(self, done.stdout, f"{TASK}: stele: CANNOT_JUDGE\n")

    def test_three_findings_are_three_records_in_order(self) -> None:
        """Each entry one record, in the document's order, fields as present."""
        entries: list[dict[str, object]] = [
            {"subject": "b", "assertion": "first", "detail": "one"},
            {"subject": "a", "assertion": "second", "actual": "x"},
            {"subject": "c", "assertion": "third", "expected": "y"},
        ]
        done = self.run_adapter(report("FAIL", *entries), 1)
        same(self, done.returncode, 1, done.stderr)
        got = [(r["subject"], r["message"], r["detail"]) for r in self.records()]
        want: list[tuple[object, object, object]] = [
            ("b", "first: one", []),
            ("a", "second", ["actual: x"]),
            ("c", "third", ["expected: y"]),
        ]
        same(self, got, want)

    def test_another_schema_is_refused_by_name(self) -> None:
        """Schema 10: refused, both numbers named, exit 1, no record."""
        done = self.run_adapter(report("PASS", schema=10), 0)
        same(self, done.returncode, 1, done.stderr)
        self.assertIn(
            "the report is schema 10; this adapter reads schema 9", done.stderr
        )
        same(self, self.records(), [])
        same(self, done.stdout, "")

    def test_output_that_is_not_json_is_refused_by_name(self) -> None:
        """Not one JSON document: refused with stele's status, exit 1."""
        done = self.run_adapter("assert: FAIL\n", 3)
        same(self, done.returncode, 1, done.stderr)
        self.assertIn(
            f"{TASK}: stele assert inventory exited 3:"
            " the output is not stele's report",
            done.stderr,
        )
        same(self, self.records(), [])

    def test_a_malformed_finding_records_none(self) -> None:
        """One entry that is not a finding: refused before any is recorded."""
        entries: list[dict[str, object]] = [
            {"subject": "a", "assertion": "fine"},
            {"subject": "b"},
        ]
        done = self.run_adapter(report("FAIL", *entries), 1)
        same(self, done.returncode, 1, done.stderr)
        self.assertIn("findings[1]: assertion", done.stderr)
        same(self, self.records(), [])

    def test_stele_s_status_is_the_exit(self) -> None:
        """A status of 2 from stele with a PASS document exits 2."""
        done = self.run_adapter(report("PASS"), 2)
        same(self, done.returncode, 2, done.stderr)
        same(self, self.records(), [])

    def test_arguments_pass_unchanged_after_json(self) -> None:
        """The adapter calls `stele assert <target> --json` and the arguments."""
        args = ["--org", "an org", "--policy", "p.json", "--depth", "full", "--"]
        done = self.run_adapter(report("PASS"), 0, "assert", "evidence", *args)
        same(self, done.returncode, 0, done.stderr)
        same(
            self,
            self.log.read_text(encoding="utf-8").splitlines(),
            ["\t".join(["assert", "evidence", "--json", *args])],
        )

    def test_no_assertion_is_a_usage_refusal(self) -> None:
        """Arguments that name no assertion: usage, exit 1, stele not run."""
        done = self.run_adapter(report("PASS"), 0, "inventory")
        same(self, done.returncode, 1, done.stderr)
        self.assertIn("usage: stele_findings.py assert <target>", done.stderr)
        self.assertFalse(self.log.exists())

    def test_no_stele_is_refused_by_name(self) -> None:
        """No stele on PATH: refused by name, exit 1, no record."""
        done = self.run_adapter(report("PASS"), 0, path=str(self.dir / "empty"))
        same(self, done.returncode, 1, done.stderr)
        self.assertIn("stele is not on PATH", done.stderr)
        same(self, self.records(), [])

    def test_the_pinned_stele_gives_the_one_record(self) -> None:
        """A versionless PURL, judged by stele itself: the quoted record."""
        stele = hermetic.optional_tool("stele")
        if stele is None:
            self.skipTest("the belt's stele is not on PATH")
        (self.dir / "a.spdx.json").write_text(json.dumps(SPDX), encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(ADAPTER), "assert", "inventory", "a.spdx.json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=hermetic.env(
                ORG_FINDINGS=str(self.file), MISE_TASK_NAME=TASK, PYTHONUTF8="1"
            ),
            cwd=self.dir,
            check=False,
        )
        same(self, done.returncode, 1, done.stderr)
        want: list[dict[str, object]] = [QUOTED_RECORD]
        same(self, self.records(), want)
        same(self, done.stdout, f"{TASK}: stele: FAIL\n")


if __name__ == "__main__":
    unittest.main()
