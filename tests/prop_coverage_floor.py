"""coverage_floor.py's derived floor, on measurements nobody wrote down.

The properties of `test_coverage_floor.py`'s fixed rows (#1548), over
generated measurements, provenances and floor files:

- Round trip (T): a rendered file parses back to the record that
  rendered it: each leg's measurement quantised, its floor that
  measurement minus the band (never below 0, never above the cap,
  #1786), the provenance, and the gate's legs in order (#2143).
- Idempotence (I): quantising twice is quantising once, and never
  rounds up.
- Invariance (V): no floor falls: a write over a derived file refuses,
  leaving it byte for byte, exactly when a leg's measurement is below
  that leg's floor, and otherwise writes every floor no lower than the
  one it replaced.
- Refusal (R): `parse` and `legs` read any text and answer with a
  record or their own problems, never a traceback.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import contextlib
import io
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import example
from hypothesis import strategies as st

from belt import coverage_floor as cf
from belt.typed_hypothesis import given

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
# A measurement: at least one leg the gate measures, and any read off it.
LEGS = st.dictionaries(
    st.sampled_from(["go", "rust", "python", "python-branches", "plpgsql"]),
    PERCENT,
    min_size=1,
    max_size=3,
)
APART = st.dictionaries(st.sampled_from(cf.SEPARATE), PERCENT, max_size=1)
NO_LEG: dict[str, Decimal] = {}
SMALL_LEG: dict[str, Decimal] = {"rust-branches": Decimal("0.05")}
FLOAT_LEG: dict[str, Decimal] = {"python": Decimal("61.53846153846154")}
TINY_LEG: dict[str, Decimal] = {"go": Decimal("1.99")}


def floor(measured: Decimal) -> Decimal:
    """Derive a floor from a measurement, as the law states it.

    Returns:
        The quantised measurement minus the band, never below zero and
        never above the cap.

    """
    return min(cf.FLOOR_CAP, max(cf.FLOOR_MIN, cf.quantise(measured) - cf.BAND))


class TestRoundTrip:
    """A rendered file reads back as what rendered it."""

    @given(LEGS, PROVENANCE, APART)
    @example(FLOAT_LEG, "v1.0.0", NO_LEG)
    @example(TINY_LEG, "x: y", SMALL_LEG)
    def test_render_then_parse(
        self, gate: dict[str, Decimal], provenance: str, apart: dict[str, Decimal]
    ) -> None:
        """Each leg's floor and measurement, the band and provenance come back."""
        measured = gate | apart
        floors = {leg: cf.fresh(m) for leg, m in measured.items()}
        record, problems = cf.parse(cf.render(floors, provenance))
        assert problems == list[str]()
        if record is None:
            pytest.fail("a rendered file did not read back")
        assert record.band == cf.BAND
        assert record.derived == provenance
        named: tuple[str, ...] = tuple(gate)
        assert record.legs == named
        want = {
            leg: cf.Held(floor(m), cf.quantise(m), None) for leg, m in measured.items()
        }
        assert record.floors == want


class TestQuantise:
    """One decimal place, rounded down, stable."""

    @given(PERCENT)
    @example(
        Decimal(
            "12.4999999999999999999999999999950907152865081376266506282146775419773"
        )
    )
    def test_twice_is_once_and_never_up(self, value: Decimal) -> None:
        """Idempotent, a lower bound, within one step.

        The step is held by comparison, which Decimal does exactly, never
        by `value - once`: that subtraction rounds to the context's 28
        digits, and for the example above returns exactly 0.1 where the
        true gap is just under it (#1555, audit seed 2849470511876116666).
        """
        once = cf.quantise(value)
        assert cf.quantise(once) == once
        assert once <= value
        assert value < once + cf.STEP


class TestRatchet:
    """No floor falls, and the release holds the gate's line."""

    @given(PERCENT, PERCENT, PERCENT, PERCENT, PROVENANCE)
    @example(Decimal(50), Decimal(80), Decimal("51.99"), Decimal(80), "x")
    @example(Decimal(50), Decimal(80), Decimal(52), Decimal("77.9"), "x")
    @example(Decimal(50), Decimal(80), Decimal("47.99"), Decimal(90), "x")
    @example(Decimal("94.3"), Decimal(80), Decimal("94.2"), Decimal(80), "x")
    def test_refuses_unchanged_or_rises(
        self,
        first: Decimal,
        other: Decimal,
        second: Decimal,
        other_second: Decimal,
        provenance: str,
    ) -> None:
        """Refused: the file is untouched. Written: no floor is lower."""
        before_floors = {"go": cf.fresh(first), "rust": cf.fresh(other)}
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / ".coverage-floor"
            before = cf.render(before_floors, provenance)
            path.write_text(before, encoding="utf-8")
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                status = cf.write(
                    path, {"go": second, "rust": other_second}, provenance, adopt=False
                )
            after = path.read_text(encoding="utf-8")
        falls = cf.quantise(second) < floor(first) or cf.quantise(other_second) < floor(
            other
        )
        assert bool(status) == falls
        if status:
            assert after == before
        else:
            written, _ = cf.parse(after)
            if written is None:
                pytest.fail("the written file did not read back")
            for leg, held in before_floors.items():
                assert written.floors[leg].floor >= held.floor


class TestRefusal:
    """Any file is read or refused by its own problems."""

    @given(
        st.one_of(
            st.text(max_size=200),
            st.lists(
                st.sampled_from([
                    "# band: 2",
                    "# derived: x",
                    "# legs: go",
                    "# legs: go go",
                    "# go measured: 50.0",
                    "go 48.0",
                    "# go reset: why",
                    "# rust-branches measured: 9",
                    "rust-branches 7",
                    "48.0",
                    "go nan",
                    "go 1e999999",
                    "-1",
                    "rust-branches",
                    "# dropped: go",
                ]),
                max_size=8,
            ).map("\n".join),
        )
    )
    @example("rust-branches\n# band: 2\n48\n")
    @example("# band: 2\n# legs: go\n# go measured: 50.0\ngo 48.0\n")
    def test_parse_answers_any_text(self, text: str) -> None:
        """A record, or None with at least one problem."""
        record, problems = cf.parse(text)
        if record is None:
            assert problems
        else:
            assert problems == list[str]()

    @given(st.text(max_size=200))
    def test_legs_answers_any_text(self, text: str) -> None:
        """Every non-blank line is a leg or a problem."""
        measured, problems = cf.legs(text)
        lines = [line for line in text.splitlines() if line.strip()]
        assert len(measured) + len(problems) == len(lines)
