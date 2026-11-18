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
  until this file found it.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

_SPEC = importlib.util.spec_from_file_location(
    "findings",
    Path(__file__).with_name("findings.py"),
)
findings = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(findings)

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
                subject = finding["subject"]
                self.assertTrue(subject.startswith("CT index "))
                self.assertEqual(int(subject.removeprefix("CT index ")), index)


if __name__ == "__main__":
    unittest.main()
