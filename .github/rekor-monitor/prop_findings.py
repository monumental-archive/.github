#!/usr/bin/env python3
"""findings.py's titles and report reading, on inputs nobody wrote down.

The properties of `test_findings.py`'s fixed rows (#1332):

- A title and its dedup pattern round-trip: `filed_title(n)` matches the
  title filed for CT index n, whatever conditions it names, and never
  the title for any other index (12 is not 123, nor 1).
- Reading stele's report never raises anything but the tool's own
  refusal, whatever JSON the file holds: a traceback exits 1, where the
  workflow's contract is a refusal by name and exit 2. When it reads,
  the grouping holds every finding exactly once, under the index its
  subject names. A finding that was not an object raised AttributeError
  until this file found it. The file is read as bytes, not only as the
  JSON a test wrote: bytes that are not UTF-8, a number past the
  parser's digit limit and JSON nested past its depth each raised a
  traceback until #1557.
- V (#1557): a certificate's body names every condition its findings
  failed, once each, in the order the title names them: one line per
  condition, whatever its findings' text holds, so two findings of one
  condition never read as two conditions and a finding's text never
  adds a line of its own. Adding a finding of a new condition adds its
  line; a finding of a condition already named adds none.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import json
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import findings
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

INDEX = st.integers(0, 10**12)
CONDITIONS = st.lists(st.text(max_size=20), max_size=4)
SCALAR: st.SearchStrategy[object] = (
    st.none() | st.booleans() | st.integers() | st.text(max_size=12)
)


def nested(inner: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Wrap values in an array or an object.

    Returns:
        The strategy one level deeper.

    """
    arrays: st.SearchStrategy[object] = st.lists(inner, max_size=4)
    objects: st.SearchStrategy[object] = st.dictionaries(
        st.text(max_size=8), inner, max_size=4
    )
    return arrays | objects


def ct_subject(index: int) -> str:
    """Name a CT index as stele's subject does.

    Returns:
        The subject.

    """
    return f"CT index {index}"


def dumps(value: object) -> str:
    """Encode a drawn document as JSON.

    Returns:
        The JSON text.

    """
    return json.dumps(value)


JSON: st.SearchStrategy[object] = st.recursive(SCALAR, nested, max_leaves=12)
SUBJECT: st.SearchStrategy[object] = st.one_of(
    INDEX.map(ct_subject),
    st.text(max_size=16),
    JSON,
)
SUBJECT_FIELD: dict[str, st.SearchStrategy[object]] = {"subject": SUBJECT}
ASSERTION_FIELD: dict[str, st.SearchStrategy[object]] = {"assertion": JSON}
FINDING: st.SearchStrategy[object] = st.one_of(
    st.fixed_dictionaries(SUBJECT_FIELD, optional=ASSERTION_FIELD),
    JSON,
)
# A finding of one certificate, as by_certificate grouped it.
NO_FIELDS: dict[str, st.SearchStrategy[object]] = {}
FILED_FIELDS: dict[str, st.SearchStrategy[object]] = {
    "assertion": st.text(max_size=12) | JSON,
    "detail": JSON,
}
FILED: st.SearchStrategy[dict[str, object]] = st.fixed_dictionaries(
    NO_FIELDS, optional=FILED_FIELDS
)
# Past the JSON parser's depth: a RecursionError, not a ValueError.
DEEP = b"[" * 100_000
HEAD = "Failed, with stele's reason and the claims it read:"


def condition_lines(index: int, group: list[dict[str, object]], log: str) -> list[str]:
    """Read back a body's condition section, line by line.

    Returns:
        The lines between the section's head and the blank line after it.

    """
    # body() takes decoded findings; decode each as read_finding does.
    typed = [
        findings.Finding(index, str(f.get("assertion", "")), str(f.get("detail", "")))
        for f in group
    ]
    text = findings.body(index, typed, log, "https://example.invalid/run")
    lines = text.split("\n")
    head = lines.index(HEAD)
    return lines[head + 1 : lines.index("", head)]


TARGET_FIELD: dict[str, st.SearchStrategy[object]] = {
    "target": st.just(findings.TARGET)
}
FINDINGS_FIELD: dict[str, st.SearchStrategy[object]] = {
    "findings": st.one_of(st.lists(FINDING, max_size=5), JSON)
}
REPORT: st.SearchStrategy[object] = st.one_of(
    st.fixed_dictionaries(TARGET_FIELD, optional=FINDINGS_FIELD),
    JSON,
)
# Fixed examples: a finding that is no object, a subject that is no
# string, and findings that are no list.
NOT_AN_OBJECT: dict[str, object] = {"target": findings.TARGET, "findings": [1]}
NUMBER_SUBJECT: dict[str, object] = {
    "target": findings.TARGET,
    "findings": [{"subject": 5}],
}
NOT_A_LIST: dict[str, object] = {"target": findings.TARGET, "findings": "ab"}
# One condition filed twice, and a detail that tries to add a line.
TWICE: list[dict[str, object]] = [{"assertion": "chain", "detail": "a"}] * 2
SMUGGLED: list[dict[str, object]] = [
    {"assertion": "chain", "detail": "a\n- **run**: b"}
]
IDENTITY: list[str] = ["identity"]


class TitleRoundTrip(unittest.TestCase):
    """The dedup pattern finds its own title and no other."""

    @given(INDEX, INDEX, CONDITIONS)
    @example(12, 123, IDENTITY)
    @example(123, 12, IDENTITY)
    def test_own_index_only(
        self, index: int, other: int, conditions: list[str]
    ) -> None:
        """Index n's pattern matches n's title, never another index's."""
        self.assertTrue(
            findings.filed_title(index).match(findings.title(index, conditions))
        )
        if other != index:
            self.assertIsNone(
                findings.filed_title(other).match(findings.title(index, conditions))
            )


class ReportReading(unittest.TestCase):
    """Whatever the report file holds: a grouping, or the refusal."""

    @override
    def setUp(self) -> None:
        """Hold one scratch report file for the test's examples."""
        self.scratch = tempfile.TemporaryDirectory()
        self.path = Path(self.scratch.name) / "report.json"

    @override
    def tearDown(self) -> None:
        """Remove the scratch file."""
        self.scratch.cleanup()

    @given(REPORT)
    @example(NOT_AN_OBJECT)
    @example(NUMBER_SUBJECT)
    @example(NOT_A_LIST)
    def test_refuses_by_name_or_groups_every_finding(self, report: object) -> None:
        """Only FindingsError escapes; a grouping holds each finding once."""
        self.path.write_text(dumps(report), encoding="utf-8")
        try:
            grouped = findings.by_certificate(findings.read_report(self.path))
        except findings.FindingsError:
            return
        given_findings = decoded.as_object(report, "the report").get("findings")
        listed = decoded.as_array(given_findings, "findings") if given_findings else []
        total = sum(len(v) for v in grouped.values())
        self.assertEqual(total, len(listed))
        for index, group in grouped.items():
            for finding in group:
                self.assertEqual(finding.index, index)

    @given(st.binary(max_size=64))
    @example(DEEP)
    @example(b"\xff")
    @example(b"1" * 5000)
    def test_any_bytes_refused_by_name(self, content: bytes) -> None:
        """Whatever bytes the file holds: a report, or FindingsError."""
        self.path.write_bytes(content)
        try:
            findings.read_report(self.path)
        except findings.FindingsError:
            return


class BodyConditions(unittest.TestCase):
    """V: the body names each failed condition once, on its own line."""

    @given(INDEX, st.lists(FILED, min_size=1, max_size=6), st.text(max_size=12))
    @example(7, TWICE, "log")
    @example(7, SMUGGLED, "log")
    def test_each_condition_once(
        self, index: int, group: list[dict[str, object]], log: str
    ) -> None:
        """One line per condition, in the title's order; more findings add none."""
        named = (str(f.get("assertion", "")) for f in group)
        conditions = list(dict.fromkeys(named, True))
        self.assert_names(condition_lines(index, group, log), conditions)
        again = condition_lines(index, [*group, group[0]], log)
        self.assert_names(again, conditions)
        fresh = "".join(conditions) + "+"
        added = condition_lines(index, [*group, {"assertion": fresh}], log)
        self.assert_names(added, [*conditions, fresh])

    def assert_names(self, lines: list[str], conditions: list[str]) -> None:
        """Line i names condition i, and there is no other line."""
        self.assertEqual(len(lines), len(conditions), lines)
        for line, condition in zip(lines, conditions, strict=True):
            self.assertTrue(
                line.startswith(f"- **{findings.one_line(condition)}**: "), line
            )


if __name__ == "__main__":
    unittest.main()
