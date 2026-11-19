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

import findings
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

INDEX = st.integers(0, 10**12)
CONDITIONS = st.lists(st.text(max_size=20), max_size=4)
JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=12),
    lambda inner: (
        st.lists(inner, max_size=4)
        | st.dictionaries(st.text(max_size=8), inner, max_size=4)
    ),
    max_leaves=12,
)
SUBJECT = st.one_of(
    INDEX.map(lambda n: f"CT index {n}"),
    st.text(max_size=16),
    JSON,
)
FINDING = st.one_of(
    st.fixed_dictionaries({"subject": SUBJECT}, optional={"assertion": JSON}),
    JSON,
)
# A finding of one certificate, as by_certificate grouped it.
FILED = st.fixed_dictionaries(
    {},
    optional={"assertion": st.text(max_size=12) | JSON, "detail": JSON},
)
# Past the JSON parser's depth: a RecursionError, not a ValueError.
DEEP = b"[" * 100_000
HEAD = "Failed, with stele's reason and the claims it read:"


def condition_lines(index: int, group: list, log: str) -> list[str]:
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


REPORT = st.one_of(
    st.fixed_dictionaries(
        {"target": st.just(findings.TARGET)},
        optional={"findings": st.one_of(st.lists(FINDING, max_size=5), JSON)},
    ),
    JSON,
)


class TitleRoundTrip(unittest.TestCase):
    """The dedup pattern finds its own title and no other."""

    @given(INDEX, INDEX, CONDITIONS)
    @example(12, 123, ["identity"])
    @example(123, 12, ["identity"])
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

    def setUp(self) -> None:
        """Hold one scratch report file for the test's examples."""
        self.scratch = tempfile.TemporaryDirectory()
        self.path = Path(self.scratch.name) / "report.json"

    def tearDown(self) -> None:
        """Remove the scratch file."""
        self.scratch.cleanup()

    @given(REPORT)
    @example({"target": findings.TARGET, "findings": [1]})
    @example({"target": findings.TARGET, "findings": [{"subject": 5}]})
    @example({"target": findings.TARGET, "findings": "ab"})
    def test_refuses_by_name_or_groups_every_finding(self, report: object) -> None:
        """Only FindingsError escapes; a grouping holds each finding once."""
        self.path.write_text(json.dumps(report), encoding="utf-8")
        try:
            grouped = findings.by_certificate(findings.read_report(self.path))
        except findings.FindingsError:
            return
        listed = report.get("findings") or []
        self.assertEqual(sum(len(v) for v in grouped.values()), len(listed))
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
    @example(7, [{"assertion": "chain", "detail": "a"}] * 2, "log")
    @example(7, [{"assertion": "chain", "detail": "a\n- **run**: b"}], "log")
    def test_each_condition_once(self, index: int, group: list, log: str) -> None:
        """One line per condition, in the title's order; more findings add none."""
        conditions = list(dict.fromkeys(str(f.get("assertion", "")) for f in group))
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
