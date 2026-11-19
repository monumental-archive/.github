#!/usr/bin/env python3
"""verdict.py's reading and judging, on inputs nobody wrote down.

The properties of `test_verdict.py`'s fixed rows (#1557):

- R: reading the report never raises, whatever bytes the file holds.
  The tool has no refusal of its own: a report it cannot read is a
  `Report` naming why, and the leg is judged red from it. Bytes that
  decode to JSON nested past the parser's depth raised RecursionError,
  a traceback, until this file found it.
- V: the documented table holds for every combination of exit status,
  saved flag, finding count, the report's `judged` fact and whether the
  report could be read: no status is red, not saved is red, a report
  that could not be read is red, any finding is red whatever the exit
  code, 0 is green, 4 is green exactly when stele judged something,
  and anything else is red.
- V: a report that holds a finding is red whatever the exit code and
  however the rest of the report reads. A saved report whose facts were
  not stele's shape read as unreadable with no findings, and judged
  green on exit 0, until this file found it.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import json
import tempfile
import unittest
from pathlib import Path

import verdict
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

# Past the JSON parser's depth: a RecursionError, not a ValueError.
DEEP = b"[" * 100_000

JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=12),
    lambda inner: (
        st.lists(inner, max_size=4)
        | st.dictionaries(st.text(max_size=8), inner, max_size=4)
    ),
    max_leaves=12,
)
FACT = st.one_of(
    st.fixed_dictionaries(
        {"name": st.sampled_from(["judged", "outstanding", "stopped"]) | JSON},
        optional={"value": JSON},
    ),
    JSON,
)
REPORT = st.one_of(
    st.fixed_dictionaries(
        {"target": st.just(verdict.TARGET)},
        optional={
            "facts": st.lists(FACT, max_size=4) | JSON,
            "findings": st.lists(JSON, max_size=4) | JSON,
        },
    ),
    JSON,
)
CONTENT = st.one_of(
    st.binary(max_size=64),
    REPORT.map(lambda doc: json.dumps(doc).encode()),
)
CODE = st.one_of(
    st.sampled_from(["", "0", "1", "2", "3", "4", "00", "04", " 4"]),
    st.integers(0, 300).map(str),
    st.text(max_size=4),
)
JUDGED = st.one_of(st.none(), st.just("none"), st.text(max_size=12))
# One leg: exit status, saved flag, finding count, the facts verdict.py
# reads, and why the report could not be read (None: it could).
LEG = st.fixed_dictionaries({
    "code": CODE,
    "saved": st.booleans(),
    "found": st.integers(0, 5),
    "judged": JUDGED,
    "stopped": st.text(max_size=8),
    "missing": st.none() | st.text(min_size=1, max_size=8),
})
# A leg every row starts from: saved, no finding, read, one range judged.
ROW = {
    "code": "4",
    "saved": True,
    "found": 0,
    "judged": "1..2",
    "stopped": "x",
    "missing": None,
}


class Scratch(unittest.TestCase):
    """One scratch report file for a test's examples."""

    def setUp(self) -> None:
        """Hold the scratch file."""
        self.scratch = tempfile.TemporaryDirectory()
        self.path = Path(self.scratch.name) / "report.json"

    def tearDown(self) -> None:
        """Remove the scratch file."""
        self.scratch.cleanup()


class Reading(Scratch):
    """R: whatever the report file holds, a Report."""

    @given(CONTENT)
    @example(DEEP)
    @example(b"\xff")
    @example(b'{"target": "assert signer-certificates", "facts": [1]}')
    def test_never_raises(self, content: bytes) -> None:
        """Only a Report comes back; a report it could not read names why."""
        self.path.write_bytes(content)
        report = verdict.read_report(self.path)
        self.assertIsInstance(report, verdict.Report)
        if report.missing is not None:
            self.assertEqual((report.facts, report.findings), ({}, 0))


class Table(unittest.TestCase):
    """V: the docstring's table, for every combination."""

    @given(LEG)
    @example({**ROW, "code": "4", "judged": None})
    @example({**ROW, "code": "4", "judged": "none"})
    @example({**ROW, "code": "4", "found": 1})
    @example({**ROW, "code": "0", "missing": "the report could not be read"})
    def test_table_holds(self, leg: dict) -> None:
        """Each row of the table, and only that row, decides the colour."""
        code, judged, missing = leg["code"], leg["judged"], leg["missing"]
        facts = {"stopped": leg["stopped"]}
        if judged is not None:
            facts["judged"] = judged
        report = verdict.Report(facts, leg["found"], missing)
        red, why = verdict.judge(code, report, saved=leg["saved"])
        if not code.isdigit():
            expected = True
            self.assertEqual(why, verdict.KILLED)
        elif not leg["saved"] or missing or leg["found"]:
            expected = True
        elif code == "0":
            expected = False
        elif code == "4":
            expected = judged in {None, "none"}
        else:
            expected = True
        self.assertEqual(red, expected, why)


class AnyFindingIsRed(Scratch):
    """V: a finding in the report is red, whatever else it says."""

    @given(
        st.fixed_dictionaries({
            "code": CODE,
            "saved": st.booleans(),
            "findings": st.lists(JSON, min_size=1, max_size=4),
            "facts": st.lists(FACT, max_size=4),
        })
    )
    @example({
        "code": "0",
        "saved": True,
        "findings": [{"subject": "CT index 1"}],
        "facts": [1],
    })
    def test_finding_is_red(self, leg: dict) -> None:
        """Whatever the exit code, saved or not, whatever the facts."""
        doc = {
            "target": verdict.TARGET,
            "findings": leg["findings"],
            "facts": leg["facts"],
        }
        self.path.write_text(json.dumps(doc), encoding="utf-8")
        report = verdict.read_report(self.path)
        red, _ = verdict.judge(leg["code"], report, saved=leg["saved"])
        self.assertTrue(red)


if __name__ == "__main__":
    unittest.main()
