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
  code, 0 and 4 are green whether or not stele judged anything
  (#1967), and anything else is red.
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
from typing import TypedDict, override

import verdict
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

# Past the JSON parser's depth: a RecursionError, not a ValueError.
DEEP = b"[" * 100_000

SCALAR: st.SearchStrategy[object] = (
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=12)
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


def encoded(value: object) -> bytes:
    """Write a drawn document as a report file's bytes.

    Returns:
        The JSON text, encoded.

    """
    return json.dumps(value).encode()


def numeral(number: int) -> str:
    """Write an exit status as the step hands it on.

    Returns:
        Its decimal text.

    """
    return str(number)


class Leg(TypedDict):
    """One leg of the monitor, as verdict.py judges it.

    Exit status, saved flag, finding count, the facts verdict.py reads,
    and why the report could not be read (None: it could).
    """

    code: str
    saved: bool
    found: int
    judged: str | None
    stopped: str
    missing: str | None


class Reported(TypedDict):
    """A leg whose report holds findings, with whatever facts."""

    code: str
    saved: bool
    findings: list[object]
    facts: list[object]


def leg(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] - one per drawn field
    code: str,
    saved: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - one field of the drawn leg
    found: int,
    judged: str | None,
    stopped: str,
    missing: str | None,
) -> Leg:
    """Assemble one drawn leg.

    Returns:
        The leg.

    """
    return {
        "code": code,
        "saved": saved,
        "found": found,
        "judged": judged,
        "stopped": stopped,
        "missing": missing,
    }


def reported(
    code: str,
    saved: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - one field of the drawn leg
    findings: list[object],
    facts: list[object],
) -> Reported:
    """Assemble one drawn leg with findings.

    Returns:
        The leg.

    """
    return {"code": code, "saved": saved, "findings": findings, "facts": facts}


JSON: st.SearchStrategy[object] = st.recursive(SCALAR, nested, max_leaves=12)
NAME: st.SearchStrategy[object] = (
    st.sampled_from(["judged", "outstanding", "stopped"]) | JSON
)
NAME_FIELD: dict[str, st.SearchStrategy[object]] = {"name": NAME}
VALUE_FIELD: dict[str, st.SearchStrategy[object]] = {"value": JSON}
FACT: st.SearchStrategy[object] = st.one_of(
    st.fixed_dictionaries(NAME_FIELD, optional=VALUE_FIELD),
    JSON,
)
TARGET_FIELD: dict[str, st.SearchStrategy[object]] = {"target": st.just(verdict.TARGET)}
REPORT_FIELDS: dict[str, st.SearchStrategy[object]] = {
    "facts": st.lists(FACT, max_size=4) | JSON,
    "findings": st.lists(JSON, max_size=4) | JSON,
}
REPORT: st.SearchStrategy[object] = st.one_of(
    st.fixed_dictionaries(TARGET_FIELD, optional=REPORT_FIELDS),
    JSON,
)
CONTENT = st.one_of(
    st.binary(max_size=64),
    REPORT.map(encoded),
)
NUMERAL = st.integers(0, 300).map(numeral)
CODE: st.SearchStrategy[str] = st.one_of(
    st.sampled_from(["", "0", "1", "2", "3", "4", "00", "04", " 4"]),
    NUMERAL,
    st.text(max_size=4),
)
JUDGED: st.SearchStrategy[str | None] = st.one_of(
    st.none(), st.just("none"), st.text(max_size=12)
)
SAVED: st.SearchStrategy[bool] = st.booleans()
FOUND: st.SearchStrategy[int] = st.integers(0, 5)
STOPPED: st.SearchStrategy[str] = st.text(max_size=8)
MISSING: st.SearchStrategy[str | None] = st.none() | st.text(min_size=1, max_size=8)
LEG: st.SearchStrategy[Leg] = st.builds(
    leg, CODE, SAVED, FOUND, JUDGED, STOPPED, MISSING
)
FINDINGS: st.SearchStrategy[list[object]] = st.lists(JSON, min_size=1, max_size=4)
FACTS: st.SearchStrategy[list[object]] = st.lists(FACT, max_size=4)
REPORTED: st.SearchStrategy[Reported] = st.builds(
    reported, CODE, SAVED, FINDINGS, FACTS
)
# A leg every row starts from: saved, no finding, read, one range judged.
ROW: Leg = {
    "code": "4",
    "saved": True,
    "found": 0,
    "judged": "1..2",
    "stopped": "x",
    "missing": None,
}
# The table's fixed rows, each one field off ROW.
NOT_JUDGED: Leg = {**ROW, "code": "4", "judged": None}
JUDGED_NONE: Leg = {**ROW, "code": "4", "judged": "none"}
ONE_FOUND: Leg = {**ROW, "code": "4", "found": 1}
UNREAD: Leg = {**ROW, "code": "0", "missing": "the report could not be read"}
# Exit 0 and saved, with a finding and a fact that is no object.
CLEAN_EXIT: Reported = {
    "code": "0",
    "saved": True,
    "findings": [{"subject": "CT index 1"}],
    "facts": [1],
}


class Scratch(unittest.TestCase):
    """One scratch report file for a test's examples."""

    @override
    def setUp(self) -> None:
        """Hold the scratch file."""
        self.scratch = tempfile.TemporaryDirectory()
        self.path = Path(self.scratch.name) / "report.json"

    @override
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
    @example(NOT_JUDGED)
    @example(JUDGED_NONE)
    @example(ONE_FOUND)
    @example(UNREAD)
    def test_table_holds(self, leg: Leg) -> None:
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
        elif code in {"0", "4"}:
            expected = False
        else:
            expected = True
        self.assertEqual(red, expected, why)


class AnyFindingIsRed(Scratch):
    """V: a finding in the report is red, whatever else it says."""

    @given(REPORTED)
    @example(CLEAN_EXIT)
    def test_finding_is_red(self, leg: Reported) -> None:
        """Whatever the exit code, saved or not, whatever the facts."""
        doc: dict[str, object] = {
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
