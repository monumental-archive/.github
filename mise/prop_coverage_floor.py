#!/usr/bin/env python3
"""coverage-floor.py's derived floor, on measurements nobody wrote down.

The properties of `test_coverage_floor.py`'s fixed rows (#1548), over
generated measurements, provenances and floor files:

- Round trip (T): a rendered file parses back to the record that
  rendered it: the measurement quantised, the floor that measurement
  minus the band (never below 0), the provenance, and each separate
  leg likewise.
- Idempotence (I): quantising twice is quantising once, and never
  rounds up.
- Invariance (V): the floor only rises: a write over a derived file
  either refuses and leaves it byte for byte, or writes a floor no
  lower than the one it replaced.
- Refusal (R): `parse` and `legs` read any text and answer with a
  record or their own problems, never a traceback.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import contextlib
import importlib.util
import io
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

_SPEC = importlib.util.spec_from_file_location(
    "coverage_floor",
    Path(__file__).with_name("coverage-floor.py"),
)
cf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cf)

PERCENT = st.decimals(min_value=0, max_value=100, allow_nan=False, places=None)
# A provenance is one line of text: no line breaks of any kind.
PROVENANCE = (
    st
    .text(
        st.characters(blacklist_categories=("Cc", "Cs", "Zl", "Zp")),
        min_size=1,
        max_size=30,
    )
    .map(str.strip)
    .filter(bool)
)
SEPARATE = st.dictionaries(st.sampled_from(cf.SEPARATE), PERCENT, max_size=1)


def floor(measured: Decimal) -> Decimal:
    """Derive a floor from a measurement, as the law states it.

    Returns:
        The quantised measurement minus the band, never below zero.

    """
    return max(cf.FLOOR_MIN, cf.quantise(measured) - cf.BAND)


class RoundTrip(unittest.TestCase):
    """A rendered file reads back as what rendered it."""

    @given(PERCENT, PROVENANCE, SEPARATE)
    @example(Decimal("61.53846153846154"), "v1.0.0", {})
    @example(Decimal("1.99"), "x: y", {"rust-branches": Decimal("0.05")})
    def test_render_then_parse(
        self, measured: Decimal, provenance: str, separate: dict[str, Decimal]
    ) -> None:
        """Floor, band, measurement, provenance and legs come back."""
        record, problems = cf.parse(cf.render(measured, provenance, separate))
        self.assertEqual(problems, [])
        self.assertEqual(record.floor, floor(measured))
        self.assertEqual(record.band, cf.BAND)
        self.assertEqual(record.measured, cf.quantise(measured))
        self.assertEqual(record.derived, provenance)
        self.assertIsNone(record.reset)
        self.assertEqual(
            record.separate,
            {leg: (floor(m), cf.quantise(m)) for leg, m in separate.items()},
        )


class Quantise(unittest.TestCase):
    """One decimal place, rounded down, stable."""

    @given(PERCENT)
    def test_twice_is_once_and_never_up(self, value: Decimal) -> None:
        """Idempotent, a lower bound, within one step."""
        once = cf.quantise(value)
        self.assertEqual(cf.quantise(once), once)
        self.assertLessEqual(once, value)
        self.assertLess(value - once, cf.STEP)


class Ratchet(unittest.TestCase):
    """The floor only rises."""

    @given(PERCENT, PERCENT, PROVENANCE)
    @example(Decimal(50), Decimal("51.99"), "x")
    @example(Decimal(50), Decimal(52), "x")
    def test_refuses_unchanged_or_rises(
        self, first: Decimal, second: Decimal, provenance: str
    ) -> None:
        """Refused: the file is untouched. Written: the floor is no lower."""
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / ".coverage-floor"
            before = cf.render(first, provenance)
            path.write_text(before, encoding="utf-8")
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                status = cf.write(path, second, provenance, adopt=False)
            after = path.read_text(encoding="utf-8")
        if status:
            self.assertEqual(after, before)
            self.assertLess(cf.quantise(second), floor(first) + cf.BAND)
        else:
            self.assertGreaterEqual(cf.parse(after)[0].floor, floor(first))


class Refusal(unittest.TestCase):
    """Any file is read or refused by its own problems."""

    @given(
        st.one_of(
            st.text(max_size=200),
            st.lists(
                st.sampled_from([
                    "# band: 2",
                    "# measured: 50.0",
                    "# derived: x",
                    "# reset: why",
                    "# rust-branches measured: 9",
                    "rust-branches 7",
                    "48.0",
                    "nan",
                    "1e999999",
                    "-1",
                    "rust-branches",
                ]),
                max_size=8,
            ).map("\n".join),
        )
    )
    @example("rust-branches\n# band: 2\n48\n")
    def test_parse_answers_any_text(self, text: str) -> None:
        """A record, or None with at least one problem."""
        record, problems = cf.parse(text)
        if record is None:
            self.assertTrue(problems)
        else:
            self.assertEqual(problems, [])

    @given(st.text(max_size=200))
    def test_legs_answers_any_text(self, text: str) -> None:
        """Every non-blank line is a leg or a problem."""
        measured, problems = cf.legs(text)
        lines = [line for line in text.splitlines() if line.strip()]
        self.assertEqual(len(measured) + len(problems), len(lines))


if __name__ == "__main__":
    unittest.main()
