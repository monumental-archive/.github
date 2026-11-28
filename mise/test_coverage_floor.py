#!/usr/bin/env python3
"""Table tests for the derived coverage floor, guard branches first.

#650's law, applied to #652: every guard branch gets a row, both
directions where there are two, and a planted failure is measured rather
than reasoned about. The guards here are the whole mechanism — a ratchet
that refuses when it should pass is a release nobody can cut, and a
ratchet that passes when it should refuse is the silent drift the floor
was made derived state to stop.

Every leg is held to a floor of its own (#2143), so every row's file is
written leg by leg, by hand: `record` and `planted` below, never `render`,
whose output is read back through `parse` in rows of its own.

Rewrites are asserted by re-reading the file through `parse`, never
through the writer's own bookkeeping: a render that only the renderer can
read is not a file the gate can enforce (the share-the-definition check
law, #650).

stdlib `unittest`, deliberately — #364 refused a test framework as a
fourth thing to port, and nothing is added to the belt to run this.

Run through the gate as `mise run test`, which `ci` collects.
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, ClassVar, Self, override

import coverage_floor as cf
import hermetic

if TYPE_CHECKING:
    from collections.abc import Callable


def leg_names(text: str) -> list[str]:
    """Name each leg a file states a line for, in order.

    Returns:
        Each line's first word, outside a comment.

    """
    return [
        line.split()[0]
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    ]


def floors(value: dict[str, cf.Held]) -> dict[str, cf.Held]:
    """Type a row's expected floors, so the literal is not read as Any.

    Returns:
        The floors as given.

    """
    return value


def planted(
    name: str,
    floor: str,
    measured: str | None = None,
    reset: str | None = None,
) -> str:
    """Build one leg's section: its record and its line.

    Returns:
        `# <leg> measured: <m>` (or `# <leg> reset: <why>`) and
        `<leg> <floor>`.

    """
    stated = f"reset: {reset}" if reset is not None else f"measured: {measured}"
    return f"# {name} {stated}\n{name} {floor}\n"


def record(
    *sections: str,
    legs: str | None = None,
    band: str | None = "2",
    derived: str | None = "v0.20.0",
    extra: tuple[str, ...] = (),
) -> str:
    """Build a floor file with the fields a row wants stated.

    `legs` defaults to every section's leg that the gate measures.

    Returns:
        The file text: the comment record, then each leg's section.

    """
    named = [
        found
        for section in sections
        for found in leg_names(section)
        if not cf.apart(found)
    ]
    lines = ["# Coverage floor -- DERIVED STATE, not a number to edit (#652).", "#"]
    if band is not None:
        lines.append(f"# band: {band}")
    if derived is not None:
        lines.append(f"# derived: {derived}")
    lines.append(f"# legs: {' '.join(named) if legs is None else legs}")
    lines.extend(extra)
    return "\n".join(lines) + "\n" + "".join(sections)


# The rows' usual leg: measured 85.4, so its floor is 83.4.
PY = planted("python", "83.4", "85.4")


class Tree:
    """A throwaway repository holding one `.coverage-floor`."""

    def __init__(self, text: str | None) -> None:
        """Create the temporary tree, writing the file when text is given."""
        self._dir = TemporaryDirectory()
        self.path = Path(self._dir.name) / ".coverage-floor"
        if text is not None:
            self.path.write_text(text, encoding="utf-8")

    def __enter__(self) -> Self:
        """Enter the context.

        Returns:
            This tree.

        """
        return self

    def __exit__(self, *_: object) -> None:
        """Remove the temporary tree."""
        self._dir.cleanup()

    def text(self) -> str | None:
        """Read the file back.

        Returns:
            The file's contents, or None when nothing was written.

        """
        if not self.path.exists():
            return None
        return self.path.read_text(encoding="utf-8")

    def written(self) -> str:
        """Read back a file the row expects to exist.

        Returns:
            The file's contents.

        Raises:
            AssertionError: nothing was written.

        """
        text = self.text()
        if text is None:
            msg = f"{self.path} was not written"
            raise AssertionError(msg)
        return text


# The task that runs the helper, whose name its findings carry (#1934).
TASK = "coverage:check"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


NO_PROBLEMS: list[str] = []
NO_LEGS: list[tuple[str, Decimal]] = []
NO_DROPPED: dict[str, str] = {}
# The refusal of a file in the shape before #2143, after its quoted line.
SHARED = (
    "the shape that held every leg to the weakest leg's floor. Every leg "
    "has a floor of its own (#2143): rewrite the file with "
    "`mise run coverage:adopt`"
)


def held(floor: str, measured: str | None = None, reset: str | None = None) -> cf.Held:
    """Spell a leg's floor as `parse` reads one.

    Returns:
        The floor, its measurement and its reset.

    """
    return cf.Held(
        Decimal(floor), None if measured is None else Decimal(measured), reset
    )


def clean(text: str) -> cf.Record:
    """Parse a floor file the row expects to read.

    Returns:
        The record.

    Raises:
        AssertionError: `parse` refused the file, naming its problems.

    """
    parsed, problems = cf.parse(text)
    if parsed is None or problems:
        msg = f"the floor did not parse: {problems}"
        raise AssertionError(msg)
    return parsed


def run[**P](
    call: Callable[P, int], *args: P.args, **kwargs: P.kwargs
) -> tuple[int, str, str]:
    """Call one of the script's entry points with its streams captured.

    Returns:
        The exit status, stdout and stderr.

    """
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        status = call(*args, **kwargs)
    return status, out.getvalue(), err.getvalue()


# The gate job, as coverage:check names itself to the hold (#1875).
GATE = frozenset({cf.GATE_JOB})


class TestEachLegItsOwnFloor(unittest.TestCase):
    """#2143's Done when: every leg is held to a floor of its own."""

    # python measured 95.0, so held at the cap; python-branches 82.0.
    TWO = record(
        planted("python", "90", "95.0"), planted("python-branches", "80.0", "82.0")
    )

    def test_a_leg_below_its_own_floor_above_the_others_is_red(self) -> None:
        """Python at 85.0: above python-branches' 80.0, below its own 90."""
        with Tree(self.TWO) as tree:
            got = run(
                cf.hold,
                tree.path,
                TASK,
                "python 85.0\npython-branches 83.0\n",
                GATE,
            )
        self.assertEqual(
            (
                1,
                f"{TASK}: python-branches leg holds its floor 80.0 (83.0%)\n",
                f"{TASK}: python: 85.0% is below its derived floor 90%\n",
            ),
            got,
        )

    def test_both_at_or_above_their_floors_hold(self) -> None:
        """Each leg at its own floor or above is held, and named."""
        with Tree(self.TWO) as tree:
            got = run(
                cf.hold, tree.path, TASK, "python 90\npython-branches 81.0\n", GATE
            )
        self.assertEqual(
            (
                0,
                (
                    f"{TASK}: python leg holds its floor 90 (90%)\n"
                    f"{TASK}: python-branches leg holds its floor 80.0 (81.0%)\n"
                ),
                "",
            ),
            got,
        )

    def test_a_leg_measured_at_100_is_written_at_90(self) -> None:
        """The cap is every leg's (#1786): 100 measured, 90 held."""
        with Tree(None) as tree:
            got = run(
                cf.derive,
                tree.path,
                "plpgsql 100\nplpgsql-branches 87.5\n",
                "coverage:adopt",
                adopt=True,
            )
            text = tree.written()
        self.assertEqual(
            (
                0,
                (
                    f"coverage-floor: adopted {tree.path}: plpgsql 90 (measured "
                    "100.0), plpgsql-branches 85.5 (measured 87.5)\n"
                ),
                "",
            ),
            got,
        )
        self.assertIn("\n# plpgsql measured: 100.0\nplpgsql 90\n", text)
        self.assertEqual(
            floors({
                "plpgsql": held("90", "100.0"),
                "plpgsql-branches": held("85.5", "87.5"),
            }),
            clean(text).floors,
        )

    def test_the_old_shape_is_refused_by_name(self) -> None:
        """A bare floor line, recorded or not, is refused, naming adopt."""
        legacy = (
            "# band: 2\n# measured: 94.4\n# derived: v1.78.6\n"
            "# legs: python python-branches\n90\n"
        )
        for name, text, line in (
            ("with its record", legacy, "90"),
            ("hand-typed", "83.4\n", "83.4"),
        ):
            with self.subTest(name), Tree(text) as tree:
                refused = (
                    f"{TASK}: {tree.path} states a bare floor line {line!r}: {SHARED}\n"
                )
                self.assertEqual((1, "", refused), run(cf.read, tree.path))
                self.assertEqual(
                    (1, "", refused), run(cf.hold, tree.path, TASK, "python 95\n", GATE)
                )

    def test_no_floor_file_exits_0(self) -> None:
        """No `.coverage-floor`: every entry point owes nothing."""
        with Tree(None) as tree:
            self.assertEqual((0, "", ""), run(cf.read, tree.path))
            self.assertEqual((0, "", ""), run(cf.read, tree.path, "rust-branches"))
            self.assertEqual((0, "", ""), run(cf.hold, tree.path, TASK, "go 1\n", GATE))
            self.assertEqual((0, "", ""), run(cf.recorded_legs, tree.path))
            self.assertEqual((0, "", ""), run(cf.planned, tree.path, '["15"]'))
            self.assertEqual(
                (0, f"coverage-floor: no {tree.path}, skipped\n", ""),
                run(cf.derive, tree.path, "go 85.4\n", "v1.0.0", adopt=False),
            )
            self.assertIsNone(tree.text())

    def test_a_measured_leg_with_no_floor_is_red_by_name(self) -> None:
        """A leg the file holds no line for has nothing to be held to."""
        with Tree(record(PY)) as tree:
            got = run(cf.hold, tree.path, TASK, "python 86.0\nplpgsql 87.5\n", GATE)
        self.assertEqual(
            (
                1,
                f"{TASK}: python leg holds its floor 83.4 (86.0%)\n",
                (
                    f"{TASK}: plpgsql: measured 87.5% and {tree.path} holds no floor"
                    " of its own for it: every leg is held to its own (#2143). Write"
                    " it from this measurement with `mise run coverage:adopt`\n"
                ),
            ),
            got,
        )

    def test_each_leg_is_derived_from_its_own_figure(self) -> None:
        """A weaker leg no longer sets a stronger leg's floor."""
        with Tree(None) as tree:
            run(cf.derive, tree.path, "go 85.4\nrust 61.5\n", "v0.21.0", adopt=True)
            parsed = clean(tree.written())
        self.assertEqual(
            floors({"go": held("83.4", "85.4"), "rust": held("59.5", "61.5")}),
            parsed.floors,
        )
        self.assertEqual(("go", "rust"), parsed.legs)


# The drift refusal of a python floor of 85 over a measured 85.4.
DRIFT: list[str] = [
    (
        "python floor 85 disagrees with its own record: measured 85.4 under band"
        " 2 and the cap 90 derives 83.4. A floor found wrong is evidence the"
        " machinery was bypassed, so this is refused as drift and never repaired"
        " — restore the derivation, or record a conscious reset by replacing the"
        " `python measured` line with `python reset: <why>`"
    )
]


class TestParseRefusals(unittest.TestCase):
    """Every shape `parse` refuses, one row each."""

    def test_refusals(self) -> None:
        """A malformed record is refused by name, never read past."""
        rows = [
            ("an empty file", "", "states no band"),
            ("no band at all", record(PY, band=None), "states no band"),
            ("band not a number", record(PY, band="two"), "is not a number of points"),
            ("negative band", record(PY, band="-2"), "is not a number of points"),
            ("no legs line", "# band: 2\n" + PY, "records no `legs:` line"),
            ("an empty legs line", record(PY, legs=""), "records no `legs:` line"),
            (
                "a duplicate in legs",
                record(PY, legs="python python"),
                "is not a list of distinct legs",
            ),
            (
                "a leg read off the gate in legs",
                record(
                    PY,
                    planted("rust-branches", "70.0", "72.0"),
                    legs="python rust-branches",
                ),
                "is not a list of distinct legs",
            ),
            (
                "a recorded leg with no line",
                record(PY, legs="python go"),
                "states 0 `go` line(s) and 0 `go measured` or `go reset` record(s)",
            ),
            (
                "a line the record does not name",
                record(PY, planted("go", "80.0", "82.0"), legs="python"),
                "states a `go` line or record and the `legs:` record does not name it",
            ),
            (
                "a record the legs line does not name",
                record(PY, legs="python") + "# go measured: 82.0\n",
                "states a `go` line or record and the `legs:` record does not name it",
            ),
            (
                "a line with no record",
                record("python 83.4\n", legs="python"),
                "states 1 `python` line(s) and 0 `python measured`",
            ),
            (
                "two lines",
                record(PY + "python 83.4\n", legs="python"),
                "states 2 `python` line(s) and 1",
            ),
            (
                "measured and reset together",
                record(PY + "# python reset: a reason\n"),
                "states 1 `python` line(s) and 2",
            ),
            (
                "floor not a number",
                record(planted("python", "ninety", "85.4")),
                "python line 'python ninety' is not `python <percent>`",
            ),
            (
                "floor above 100",
                record(planted("python", "102", "104")),
                "python line 'python 102' is not `python <percent>`",
            ),
            (
                "measured not a number",
                record(planted("python", "83.4", "high")),
                "python measured 'high' is not a percentage",
            ),
            (
                "reset with no reason",
                record(planted("python", "88", reset="")),
                "records a python reset with no reason",
            ),
            (
                "floor disagrees with the record",
                record(planted("python", "85", "85.4")),
                "python floor 85 disagrees with its own record",
            ),
            (
                "a dropped line with no reason",
                record(PY, extra=("# dropped: plpgsql",)),
                "is not `dropped: <leg> <reason>`",
            ),
        ]
        for name, text, expected in rows:
            with self.subTest(name):
                parsed, problems = cf.parse(text)
                self.assertIsNone(parsed)
                self.assertEqual(1, len(problems), problems)
                self.assertIn(expected, problems[0])

    def test_drift_names_the_derivation_and_the_remedy(self) -> None:
        """A number typed over the record, refused whole."""
        _, problems = cf.parse(record(planted("python", "85", "85.4")))
        self.assertEqual(
            DRIFT,
            problems,
        )

    def test_every_leg_is_judged(self) -> None:
        """Two legs drifted: two problems, one per leg."""
        _, problems = cf.parse(
            record(planted("python", "85", "85.4"), planted("go", "1", "82.0"))
        )
        self.assertEqual(2, len(problems), problems)
        self.assertTrue(problems[0].startswith("python floor 85 disagrees"))
        self.assertTrue(problems[1].startswith("go floor 1 disagrees"))


class TestParseAccepts(unittest.TestCase):
    """The other direction: the records the law admits."""

    def test_derived_record(self) -> None:
        """Each floor equal to its measurement minus the band."""
        parsed = clean(record(PY, planted("go", "80.0", "82.0")))
        self.assertEqual(Decimal(2), parsed.band)
        self.assertEqual("v0.20.0", parsed.derived)
        self.assertEqual(("python", "go"), parsed.legs)
        self.assertEqual(
            floors({"python": held("83.4", "85.4"), "go": held("80.0", "82.0")}),
            parsed.floors,
        )
        self.assertEqual(NO_DROPPED, parsed.dropped)

    def test_blank_lines_are_not_lines(self) -> None:
        """A blank line anywhere in the file reads as nothing (#1659)."""
        parsed = clean("\n" + record(PY).replace("\n", "\n\n"))
        self.assertEqual(floors({"python": held("83.4", "85.4")}), parsed.floors)

    def test_trailing_zeros_are_the_same_number(self) -> None:
        """Agreement is numeric, so 83.40 and 83.4 are one floor."""
        parsed = clean(record(planted("python", "83.40", "85.4")))
        self.assertEqual(Decimal("83.4"), parsed.floors["python"].floor)

    def test_reset_is_a_record(self) -> None:
        """A conscious reset states a reason and no measurement."""
        parsed = clean(record(planted("python", "88", reset="pgrx members left")))
        self.assertEqual(
            floors({"python": held("88", None, "pgrx members left")}), parsed.floors
        )

    def test_the_file_states_its_own_band(self) -> None:
        """A file recording a different band is read under that band.

        Changing the org's band is not a flag day: the agreement law uses
        the band the file was derived under, and the next release rewrites
        it with the current one.
        """
        parsed = clean(record(planted("python", "82.4", "85.4"), band="3"))
        self.assertEqual(Decimal(3), parsed.band)

    def test_floor_clamps_at_zero(self) -> None:
        """A measurement below the band derives a floor of 0, not -0.5."""
        parsed = clean(record(planted("python", "0", "1.5")))
        self.assertEqual(Decimal(0), parsed.floors["python"].floor)

    def test_no_provenance_is_read_as_none(self) -> None:
        """`derived:` is the record's account, not a law."""
        self.assertIsNone(clean(record(PY, derived=None)).derived)


class TestNumber(unittest.TestCase):
    """The one number reader, so no caller invents a second."""

    def test_reads_decimals(self) -> None:
        """A plain decimal reads as itself."""
        self.assertEqual(cf.number("85.4"), Decimal("85.4"))

    def test_refuses_words_and_infinities(self) -> None:
        """Words, NaN and Infinity are not measurements."""
        for text in ("", "ninety", "NaN", "Infinity", "-Infinity", "9 5"):
            with self.subTest(text):
                self.assertIsNone(cf.number(text))


class TestQuantise(unittest.TestCase):
    """Rounding, which is DOWN so a record never overstates."""

    def test_rounds_down(self) -> None:
        """cargo-llvm-cov's full float becomes a truthful lower bound."""
        self.assertEqual(cf.quantise(Decimal("61.53846153846154")), Decimal("61.5"))
        self.assertEqual(cf.quantise(Decimal("61.59")), Decimal("61.5"))
        self.assertEqual(cf.quantise(Decimal("85.4")), Decimal("85.4"))


class TestLegs(unittest.TestCase):
    """The measurement reader: `<leg> <percent>` and nothing else."""

    def test_reads_every_leg(self) -> None:
        """Each language the measurement printed comes back."""
        measured, problems = cf.legs("go 85.4\nrust 61.538\n")
        self.assertEqual(problems, NO_PROBLEMS)
        both: list[tuple[str, Decimal]] = [
            ("go", Decimal("85.4")),
            ("rust", Decimal("61.538")),
        ]
        self.assertEqual(measured, both)

    def test_blank_lines_are_not_legs(self) -> None:
        """Trailing newlines do not become a leg."""
        measured, problems = cf.legs("\ngo 85.4\n\n")
        self.assertEqual(problems, NO_PROBLEMS)
        go: list[tuple[str, Decimal]] = [("go", Decimal("85.4"))]
        self.assertEqual(measured, go)

    def test_malformed_lines_are_reported_not_skipped(self) -> None:
        """A line that is not a measurement refuses the whole derivation.

        Dropping it quietly would write a floor over a language nothing
        measured — the vacuous green the ratchet exists to prevent.
        """
        for line in ("go", "go 85.4 extra", "go high", "go 140"):
            with self.subTest(line):
                measured, problems = cf.legs(line)
                self.assertEqual(measured, NO_LEGS)
                self.assertEqual(len(problems), 1)
                self.assertIn("is not `<leg> <percent>`", problems[0])

    def test_a_leg_not_held_is_no_figure_and_no_problem(self) -> None:
        """`<leg> not-held` (#2163) is read apart from the figures."""
        text = "plpgsql 85.0\nplpgsql-branches not-held\n"
        measured, problems = cf.legs(text)
        self.assertEqual(problems, NO_PROBLEMS)
        one: list[tuple[str, Decimal]] = [("plpgsql", Decimal("85.0"))]
        self.assertEqual(measured, one)
        branches: list[str] = ["plpgsql-branches"]
        none: list[str] = []
        self.assertEqual(branches, cf.unheld(text))
        self.assertEqual(none, cf.unheld("plpgsql 85.0\nnot-held\nx not-held y\n"))

    def test_only_a_ruled_leg_may_report_not_held(self) -> None:
        """Any other leg's `not-held` is a malformed line (#2163)."""
        measured, problems = cf.legs("python 90.0\npython-branches not-held\n")
        python: list[tuple[str, Decimal]] = [("python", Decimal("90.0"))]
        self.assertEqual(measured, python)
        refused: list[str] = [OPT_OUT]
        self.assertEqual(refused, problems)
        none: list[str] = []
        self.assertEqual(none, cf.unheld("python-branches not-held\n"))


# The order a file states its legs in: the gate's, then those read off it.
ORDER: list[str] = ["rust", "rust-branches", "go-branches"]


class TestRender(unittest.TestCase):
    """What the machinery writes, read back through the parser."""

    FLOORS: ClassVar[dict[str, cf.Held]] = {
        "python": cf.fresh(Decimal("85.44")),
        "rust-branches": cf.fresh(Decimal("72.49")),
        "go": cf.Held(Decimal(88), None, "workspace split"),
    }

    def test_render_round_trips(self) -> None:
        """The written file parses back to every leg it was given."""
        parsed = clean(cf.render(self.FLOORS, "v1.52.0"))
        self.assertEqual(cf.BAND, parsed.band)
        self.assertEqual("v1.52.0", parsed.derived)
        self.assertEqual(("python", "go"), parsed.legs)
        self.assertEqual(
            floors({
                "python": held("83.4", "85.4"),
                "go": held("88", None, "workspace split"),
                "rust-branches": held("70.4", "72.4"),
            }),
            parsed.floors,
        )

    def test_render_states_the_band_and_the_decisions(self) -> None:
        """The band is stated, naming #652 and #2143, as decided."""
        text = cf.render(self.FLOORS, "v1.52.0")
        self.assertIn("#652", text)
        self.assertIn("#2143", text)
        self.assertIn("\n# band: 2\n# derived: v1.52.0\n# legs: python go\n", text)
        self.assertIn("\n# python measured: 85.4\npython 83.4\n", text)
        self.assertIn("\n# go reset: workspace split\ngo 88\n", text)

    def test_the_legs_read_off_the_gate_come_last(self) -> None:
        """rust-branches and go-branches follow every gate leg, in that order."""
        text = cf.render(
            {
                "go-branches": cf.fresh(Decimal(80)),
                "rust-branches": cf.fresh(Decimal(80)),
                "rust": cf.fresh(Decimal(80)),
            },
            "x",
        )
        self.assertEqual(ORDER, leg_names(text))
        self.assertIn("# legs: rust\n", text)


class TestRead(unittest.TestCase):
    """`coverage:check`'s half: each leg's number, or a named refusal."""

    def test_a_valid_floor_prints_each_gate_leg(self) -> None:
        """One `<leg> <floor>` line per leg the gate measures, in order."""
        text = record(
            PY, planted("go", "80.0", "82.0"), planted("rust-branches", "70.0", "72.0")
        )
        with Tree(text) as tree:
            self.assertEqual((0, "python 83.4\ngo 80.0\n", ""), run(cf.read, tree.path))

    def test_hand_edited_floor_is_refused_as_drift(self) -> None:
        """A number typed over the derivation reds the gate by name."""
        with Tree(record(planted("python", "85", "85.4"))) as tree:
            status, out, err = run(cf.read, tree.path)
        self.assertEqual((1, ""), (status, out))
        self.assertIn("python floor 85 disagrees with its own record", err)
        self.assertIn("never repaired", err)


class TestWriteRatchet(unittest.TestCase):
    """The release path's half, planted in both directions, leg by leg."""

    def test_healthy_release_raises_the_floor(self) -> None:
        """A ceiling that held rewrites the leg's floor upwards."""
        with Tree(record(PY)) as tree:
            got = run(cf.derive, tree.path, "python 86.8\n", "v0.21.0", adopt=False)
            parsed = clean(tree.written())
        self.assertEqual(
            (0, "coverage-floor: python 83.4 -> 84.8 (measured 86.8)\n", ""), got
        )
        self.assertEqual(floors({"python": held("84.8", "86.8")}), parsed.floors)
        self.assertEqual("v0.21.0", parsed.derived)

    def test_below_a_legs_floor_fails_the_release(self) -> None:
        """A drop under one leg's floor refuses by name and changes nothing."""
        before = record(PY, planted("go", "80.0", "82.0"))
        with Tree(before) as tree:
            got = run(
                cf.derive, tree.path, "python 82.0\ngo 90.0\n", "v0.21.0", adopt=False
            )
            after = tree.text()
        self.assertEqual(
            (
                1,
                "",
                (
                    f"{TASK}: python: measured 82.0% is below its floor 83.4%. The"
                    " floor never falls, so this release FAILS rather than lowering"
                    " it: write the tests that put python back to 83.4%, or"
                    " consciously reset its floor by replacing the `python measured`"
                    f" line of {tree.path} with `python reset: <why>`\n"
                ),
            ),
            got,
        )
        self.assertEqual(before, after)

    def test_every_leg_below_is_named(self) -> None:
        """Two legs below their floors: two refusals, one run."""
        with Tree(record(PY, planted("go", "80.0", "82.0"))) as tree:
            status, _, err = run(
                cf.derive, tree.path, "python 82.0\ngo 79.0\n", "v1", adopt=False
            )
        self.assertEqual(1, status)
        self.assertIn("python: measured 82.0% is below its floor 83.4%", err)
        self.assertIn("go: measured 79.0% is below its floor 80.0%", err)

    def test_within_the_band_passes_and_keeps_the_record(self) -> None:
        """The gate's line is the release's line (#1786).

        A measurement the gate passed — at or above the floor, below the
        last release's — passes the release and leaves the file exactly
        as it stood: the floor never falls, and nothing reds main.
        """
        for figure in ("85.3", "84.9", "83.4"):
            with self.subTest(figure):
                before = cf.render({"python": cf.fresh(Decimal("85.4"))}, "v0.20.0")
                with Tree(before) as tree:
                    got = run(
                        cf.derive,
                        tree.path,
                        f"python {figure}\n",
                        "v0.21.0",
                        adopt=False,
                    )
                    after = tree.written()
                self.assertEqual(
                    (
                        0,
                        (
                            "coverage-floor: python 83.4 held (measured"
                            f" {figure}, within the 2-point band)\n"
                        ),
                        "",
                    ),
                    got,
                )
                self.assertEqual(before, after)

    def test_one_leg_rises_while_another_holds(self) -> None:
        """Each leg is ratcheted alone; the provenance is the new one."""
        with Tree(record(PY, planted("go", "80.0", "82.0"))) as tree:
            got = run(cf.derive, tree.path, "python 85.0\ngo 90.0\n", "v1", adopt=False)
            parsed = clean(tree.written())
        self.assertEqual(
            (
                0,
                (
                    "coverage-floor: python 83.4 held (measured 85.0, within the"
                    " 2-point band)\ncoverage-floor: go 80.0 -> 88.0 (measured 90.0)\n"
                ),
                "",
            ),
            got,
        )
        self.assertEqual(
            floors({"python": held("83.4", "85.4"), "go": held("88.0", "90.0")}),
            parsed.floors,
        )
        self.assertEqual("v1", parsed.derived)

    def test_exactly_at_the_band_holds_the_floor(self) -> None:
        """The boundary rewrites the record, and the floor stays put."""
        with Tree(record(PY)) as tree:
            status, _, err = run(
                cf.derive, tree.path, "python 85.4\n", "v0.21.0", adopt=False
            )
            parsed = clean(tree.written())
        self.assertEqual((0, ""), (status, err))
        self.assertEqual(held("83.4", "85.4"), parsed.floors["python"])
        self.assertEqual("v0.21.0", parsed.derived)

    def test_one_tenth_below_the_floor_fails(self) -> None:
        """The refusal is on the boundary, not near it."""
        with Tree(record(PY)) as tree:
            status, _, err = run(
                cf.derive, tree.path, "python 83.3\n", "v0.21.0", adopt=False
            )
        self.assertEqual(1, status)
        self.assertIn("python: measured 83.3% is below its floor 83.4%", err)

    def test_measurement_is_rounded_down_before_the_ratchet(self) -> None:
        """The compared number is the recorded one, not a wider float."""
        with Tree(record(PY)) as tree:
            status, _, err = run(
                cf.derive, tree.path, "python 83.39999\n", "v0.21.0", adopt=False
            )
        self.assertEqual(1, status)
        self.assertIn("measured 83.3%", err)

    def test_a_reset_floor_ratchets_from_where_the_human_put_it(self) -> None:
        """A conscious reset is the floor the next release measures against."""
        with Tree(record(planted("python", "88", reset="workspace split"))) as tree:
            got = run(cf.derive, tree.path, "python 90.0\n", "v0.21.0", adopt=False)
            parsed = clean(tree.written())
        self.assertEqual(
            (0, "coverage-floor: python 88 -> 88.0 (measured 90.0)\n", ""), got
        )
        self.assertEqual(floors({"python": held("88.0", "90.0")}), parsed.floors)

    def test_a_reset_within_the_band_stays_a_reset(self) -> None:
        """A release that does not raise a reset floor keeps its reason."""
        with Tree(record(planted("python", "88", reset="workspace split"))) as tree:
            got = run(cf.derive, tree.path, "python 89.0\n", "v0.21.0", adopt=False)
            parsed = clean(tree.written())
        self.assertEqual(
            (
                0,
                (
                    "coverage-floor: python 88 held (measured 89.0, within the"
                    " 2-point band)\n"
                ),
                "",
            ),
            got,
        )
        self.assertEqual(
            floors({"python": held("88", None, "workspace split")}), parsed.floors
        )
        self.assertEqual("v0.20.0", parsed.derived)

    def test_a_record_under_another_band_is_rewritten_under_the_orgs(self) -> None:
        """A leg held within the band is written agreeing with the file's band."""
        with Tree(record(planted("python", "82.4", "85.4"), band="3")) as tree:
            status, _, err = run(
                cf.derive, tree.path, "python 83.0\n", "v1", adopt=False
            )
            parsed = clean(tree.written())
        self.assertEqual((0, ""), (status, err))
        self.assertEqual(
            (cf.BAND, held("83.4", "85.4")), (parsed.band, parsed.floors["python"])
        )


class TestCap(unittest.TestCase):
    """The cap (#1786): no floor is derived above 90, for any leg."""

    def test_a_derivation_stops_at_the_cap(self) -> None:
        """A measurement of 95.4 writes a floor of 90, not 93.4."""
        self.assertEqual(held("90", "95.4"), cf.fresh(Decimal("95.4")))

    def test_a_floor_above_the_cap_is_drift(self) -> None:
        """The cap is the derivation: 92.3 over 94.3 is refused, and 99."""
        for floor in ("92.3", "99"):
            with self.subTest(floor):
                parsed, problems = cf.parse(record(planted("python", floor, "94.3")))
                self.assertIsNone(parsed)
                self.assertIn("derives 90", problems[0])

    def test_a_capped_floor_still_fails_below_it(self) -> None:
        """Under 90 is under the floor."""
        with Tree(record(planted("python", "90", "94.3"))) as tree:
            status, _, err = run(
                cf.derive, tree.path, "python 89.9\n", "v1.71.0", adopt=False
            )
        self.assertEqual(1, status)
        self.assertIn("python: measured 89.9% is below its floor 90%", err)

    def test_the_incident_releases(self) -> None:
        """v1.70.5's floor, 94.2 measured: the release passes at 90.

        The defect #1786 records: main red three pushes running because
        the release demanded 94.3 of a tree the gate had passed.
        """
        with Tree(record(planted("python", "90", "94.3"))) as tree:
            got = run(cf.derive, tree.path, "python 94.2\n", "v1.71.0", adopt=False)
            parsed = clean(tree.written())
        self.assertEqual(
            (0, "coverage-floor: python 90 -> 90 (measured 94.2)\n", ""), got
        )
        self.assertEqual(held("90", "94.2"), parsed.floors["python"])

    def test_a_leg_read_off_the_gate_is_capped_too(self) -> None:
        """The legs the audit reads live under the same cap."""
        text = record(PY, planted("rust-branches", "90", "97.0"))
        with Tree(text) as tree:
            self.assertEqual((0, "90\n", ""), run(cf.read, tree.path, "rust-branches"))


class TestWriteAdoption(unittest.TestCase):
    """Who may write a floor, and who may not."""

    def test_adopt_writes_the_first_floor(self) -> None:
        """coverage:adopt turns a measurement into the repository's floor."""
        with Tree(None) as tree:
            got = run(
                cf.derive, tree.path, "python 85.44\n", "coverage:adopt", adopt=True
            )
            parsed = clean(tree.written())
        self.assertEqual(
            (
                0,
                f"coverage-floor: adopted {tree.path}: python 83.4 (measured 85.4)\n",
                "",
            ),
            got,
        )
        self.assertEqual(floors({"python": held("83.4", "85.4")}), parsed.floors)

    def test_release_refuses_to_repair_the_old_shape(self) -> None:
        """The machinery does not silently migrate a floor it did not write."""
        with Tree("90\n") as tree:
            got = run(cf.derive, tree.path, "python 85.4\n", "v0.21.0", adopt=False)
            after = tree.text()
        self.assertEqual(
            (
                1,
                "",
                (
                    f"{TASK}: {tree.path} states a bare floor line '90': {SHARED}\n"
                    f"{TASK}: a release does not repair a floor it did not write —"
                    " fix the file, then release\n"
                ),
            ),
            got,
        )
        self.assertEqual("90\n", after)

    def test_adopt_migrates_the_old_shape(self) -> None:
        """The other direction: `coverage:adopt` is the named rewrite."""
        legacy = "# band: 2\n# measured: 82.0\n# derived: v1\n# legs: go rust\n80.0\n"
        with Tree(legacy) as tree:
            got = run(
                cf.derive,
                tree.path,
                "go 95.0\nrust 82.0\n",
                "coverage:adopt",
                adopt=True,
            )
            parsed = clean(tree.written())
        self.assertEqual(
            (
                0,
                (
                    f"coverage-floor: rewrote {tree.path}: go 90 (measured 95.0), rust"
                    " 80.0 (measured 82.0)\n"
                ),
                "",
            ),
            got,
        )
        self.assertEqual(
            floors({"go": held("90", "95.0"), "rust": held("80.0", "82.0")}),
            parsed.floors,
        )

    def test_adopt_cannot_lower_a_floor_that_parses(self) -> None:
        """`fix`-shaped is not an escape hatch from the ratchet."""
        before = record(PY)
        with Tree(before) as tree:
            status, _, err = run(
                cf.derive, tree.path, "python 81.0\n", "coverage:adopt", adopt=True
            )
            after = tree.text()
        self.assertEqual(1, status)
        self.assertIn("is below its floor", err)
        self.assertEqual(before, after)

    def test_adopt_adds_a_new_leg(self) -> None:
        """A leg the file has no floor for joins at its own figure."""
        with Tree(record(PY)) as tree:
            got = run(
                cf.derive,
                tree.path,
                "python 85.4\nplpgsql 92.5\n",
                "coverage:adopt",
                adopt=True,
            )
            parsed = clean(tree.written())
        self.assertEqual(
            (
                0,
                (
                    "coverage-floor: python 83.4 -> 83.4 (measured 85.4)\n"
                    "coverage-floor: plpgsql joins at 90 (measured 92.5)\n"
                ),
                "",
            ),
            got,
        )
        self.assertEqual(("python", "plpgsql"), parsed.legs)


class TestDerive(unittest.TestCase):
    """The stdin half: what a derivation will read."""

    def test_no_measurement_refuses(self) -> None:
        """A derivation that reads nothing must not write a floor."""
        for source in ("\n", "rust-branches 80.0\n"):
            with self.subTest(source), Tree(record(PY)) as tree:
                got = run(cf.derive, tree.path, source, "v0.21.0", adopt=False)
                after = tree.text()
            self.assertEqual(
                (
                    1,
                    "",
                    (
                        f"{TASK}: no leg was measured — a derivation that reads"
                        " nothing must not write a floor\n"
                    ),
                ),
                got,
            )
            self.assertEqual(record(PY), after)

    def test_a_malformed_leg_refuses(self) -> None:
        """One unreadable line refuses rather than deriving from the rest."""
        with Tree(record(PY)) as tree:
            status, _, err = run(
                cf.derive, tree.path, "python 85.4\nrust ???\n", "v0.21.0", adopt=False
            )
        self.assertEqual(1, status)
        self.assertIn("is not `<leg> <percent>`", err)


BRANCH = planted("rust-branches", "70.4", "72.4")


class TestLegsReadOffTheGate(unittest.TestCase):
    """`rust-branches` and `go-branches` (#1359, #1546): the audit's legs."""

    def test_read_leg_prints_the_line(self) -> None:
        """The audit's read: the branch floor, from the record."""
        with Tree(record(PY, BRANCH)) as tree:
            self.assertEqual(
                (0, "70.4\n", ""), run(cf.read, tree.path, "rust-branches")
            )
            self.assertEqual((0, "python 83.4\n", ""), run(cf.read, tree.path))

    def test_read_leg_without_a_line_is_empty(self) -> None:
        """No line: an empty answer, which the audit reads as a skip."""
        with Tree(record(PY)) as tree:
            self.assertEqual((0, "", ""), run(cf.read, tree.path, "go-branches"))

    def test_the_line_is_not_in_the_legs_record(self) -> None:
        """The gate never measures it, so the gate never demands it."""
        with Tree(None) as tree:
            run(
                cf.derive,
                tree.path,
                "rust 86.0\nrust-branches 61.0\n",
                "coverage:adopt",
                adopt=True,
            )
            parsed = clean(tree.written())
        self.assertEqual(("rust",), parsed.legs)
        self.assertEqual(held("59.0", "61.0"), parsed.floors["rust-branches"])

    def test_a_release_carries_the_line_through(self) -> None:
        """Stable measures no branches, so the release keeps what it found."""
        with Tree(record(planted("rust", "83.4", "85.4"), BRANCH)) as tree:
            got = run(cf.derive, tree.path, "rust 86.0\n", "v1.2.0", adopt=False)
            parsed = clean(tree.written())
        self.assertEqual(
            (
                0,
                (
                    "coverage-floor: rust 83.4 -> 84.0 (measured 86.0)\n"
                    "coverage-floor: rust-branches 70.4 carried (not measured here)\n"
                ),
                "",
            ),
            got,
        )
        self.assertEqual(held("70.4", "72.4"), parsed.floors["rust-branches"])

    def test_the_line_never_falls(self) -> None:
        """Below its floor, neither line is rewritten."""
        before = record(planted("rust", "83.4", "85.4"), BRANCH)
        with Tree(before) as tree:
            status, _, err = run(
                cf.derive,
                tree.path,
                "rust 86.0\nrust-branches 70.3\n",
                "coverage:adopt",
                adopt=True,
            )
            after = tree.text()
        self.assertEqual(1, status)
        self.assertIn("rust-branches: measured 70.3% is below its floor 70.4%", err)
        self.assertEqual(before, after)

    def test_the_line_rises(self) -> None:
        """A higher branch figure raises the line."""
        with Tree(record(planted("rust", "83.4", "85.4"), BRANCH)) as tree:
            status, out, _ = run(
                cf.derive,
                tree.path,
                "rust 86.0\nrust-branches 80.0\n",
                "coverage:adopt",
                adopt=True,
            )
            parsed = clean(tree.written())
        self.assertEqual(0, status)
        self.assertIn("rust-branches 70.4 -> 78.0 (measured 80.0)", out)
        self.assertEqual(held("78.0", "80.0"), parsed.floors["rust-branches"])


class TestMain(unittest.TestCase):
    """The command line both callers actually invoke."""

    def test_read_dispatches(self) -> None:
        """`read` prints the floors for coverage:check to enforce."""
        with Tree(record(PY)) as tree:
            got = run(cf.main, ["--path", str(tree.path), "read"])
        self.assertEqual((0, "python 83.4\n", ""), got)

    def test_write_reads_the_legs_from_stdin(self) -> None:
        """The belt convention: the task enumerates, the helper consumes."""
        with Tree(record(planted("go", "83.4", "85.4"))) as tree:
            stdin = sys.stdin
            sys.stdin = io.StringIO("go 86.8\n")
            try:
                got = run(
                    cf.main,
                    ["--path", str(tree.path), "write", "--provenance", "v0.21.0"],
                )
            finally:
                sys.stdin = stdin
            parsed = clean(tree.written())
        self.assertEqual(
            (0, "coverage-floor: go 83.4 -> 84.8 (measured 86.8)\n", ""), got
        )
        self.assertEqual(held("84.8", "86.8"), parsed.floors["go"])


REMEDY = (
    "  {needs}; or record the leg's removal on purpose:"
    " `# dropped: {leg} <reason>` in .coverage-floor\n"
)
TS_NEEDS = (
    "the typescript leg needs a root package.json whose lockfile pins "
    "vitest and a coverage provider, and a `test` task that runs vitest"
)
PG_NEEDS = (
    "the plpgsql leg needs a test command run through "
    '"${ORG_BELT_DIR}/measured" and its teardown to call '
    "plpgsql_coverage.py collect while ORG_PLPGSQL_COVERAGE is set"
)


class TestLegsRecord(unittest.TestCase):
    """The floor names the legs it holds, and holds them (#1666)."""

    def test_a_recorded_leg_that_reports_none_is_red_by_name(self) -> None:
        """Each of plpgsql and typescript is named, with what it needs."""
        for name, needs in (("plpgsql", PG_NEEDS), ("typescript", TS_NEEDS)):
            with (
                self.subTest(leg=name),
                Tree(record(PY, planted(name, "80.0", "82.0"))) as tree,
            ):
                got = run(cf.hold, tree.path, TASK, "python 86.0\n", GATE)
            self.assertEqual(
                (
                    1,
                    f"{TASK}: python leg holds its floor 83.4 (86.0%)\n",
                    f"{TASK}: the floor was derived with {name} and this"
                    " run measured none\n" + REMEDY.format(needs=needs, leg=name),
                ),
                got,
            )

    def test_a_dropped_leg_is_not_demanded(self) -> None:
        """A recorded leg consciously dropped is not red when it is gone."""
        text = record(
            PY,
            planted("plpgsql", "80.0", "82.0"),
            extra=("# dropped: plpgsql moved out",),
        )
        with Tree(text) as tree:
            got = run(cf.hold, tree.path, "lbl", "python 86.0\n", GATE)
        self.assertEqual((0, "lbl: python leg holds its floor 83.4 (86.0%)\n", ""), got)

    def test_a_derivation_that_drops_a_leg_fails_without_dropped(self) -> None:
        """A recorded leg the run did not measure refuses the derivation."""
        text = record(PY, planted("plpgsql", "80.0", "82.0"))
        with Tree(text) as tree:
            got = run(cf.derive, tree.path, "python 86.0\n", "v1.2.0", adopt=False)
            after = tree.text()
        self.assertEqual(
            (
                1,
                "",
                (
                    f"{TASK}: the floor records plpgsql and this derivation"
                    " measured none. A leg leaves the record only on purpose: restore"
                    " what it measures, or add `# dropped: plpgsql <reason>` to"
                    f" {tree.path}\n"
                ),
            ),
            got,
        )
        self.assertEqual(text, after)

    def test_a_derivation_that_drops_a_leg_passes_with_dropped(self) -> None:
        """With its reason recorded, the leg and its line leave the file."""
        text = record(
            PY,
            planted("plpgsql", "80.0", "82.0"),
            extra=("# dropped: plpgsql moved out",),
        )
        with Tree(text) as tree:
            status, _, err = run(
                cf.derive, tree.path, "python 86.0\n", "v1.2.0", adopt=False
            )
            parsed = clean(tree.written())
        self.assertEqual((0, ""), (status, err))
        self.assertEqual(("python",), parsed.legs)
        kept: list[str] = [*parsed.floors]
        only: list[str] = ["python"]
        self.assertEqual(only, kept)

    def test_legs_prints_the_record(self) -> None:
        """One leg a line; nothing for no file."""
        with Tree(record(PY, planted("plpgsql", "80.0", "82.0"))) as tree:
            self.assertEqual(
                (0, "python\nplpgsql\n", ""), run(cf.recorded_legs, tree.path)
            )

    def test_legs_refuses_a_file_off_its_record(self) -> None:
        """The record's legs are read through the same law."""
        with Tree("90\n") as tree:
            status, out, _ = run(cf.recorded_legs, tree.path)
        self.assertEqual((1, ""), (status, out))

    def test_a_recorded_leg_not_held_is_named_and_not_red(self) -> None:
        """Reported but unmeasurable (#2163): not missing, nothing held."""
        with Tree(PG_HELD) as tree:
            status, out, err = run(
                cf.hold,
                tree.path,
                "coverage:check",
                "plpgsql 86.0\nplpgsql-branches not-held\n",
                GATE,
            )
            after = tree.text()
        self.assertEqual((0, ""), (status, err))
        self.assertEqual(
            "coverage:check: plpgsql leg holds its floor 83.4 (86.0%)\n"
            "coverage:check: plpgsql-branches leg not held: this run cannot"
            " measure it exactly (the measurement says why); its floor 70.0"
            " stands\n",
            out,
        )
        self.assertEqual(PG_HELD, after)

    def test_a_leg_not_held_with_no_floor_yet_is_named(self) -> None:
        """Nothing to stand and nothing derived from it: named, not red."""
        text = record(planted("plpgsql", "83.4", "85.4"))
        with Tree(text) as tree:
            status, out, err = run(
                cf.hold,
                tree.path,
                "coverage:check",
                "plpgsql 86.0\nplpgsql-branches not-held\n",
                GATE,
            )
        self.assertEqual((0, ""), (status, err))
        self.assertIn(
            "coverage:check: plpgsql-branches leg not held: this run cannot"
            " measure it exactly (the measurement says why); it has no floor"
            " of its own yet\n",
            out,
        )

    def test_a_derivation_carries_a_leg_not_held_as_it_stands(self) -> None:
        """Its line is cut from nothing, and the record keeps demanding it."""
        with Tree(PG_HELD) as tree:
            status, out, err = run(
                cf.derive,
                tree.path,
                "plpgsql 88.0\nplpgsql-branches not-held\n",
                "v1.2.0",
                adopt=False,
            )
            parsed = clean(tree.written())
        self.assertEqual((0, ""), (status, err))
        self.assertEqual(("plpgsql", "plpgsql-branches"), parsed.legs)
        self.assertEqual(Decimal("86.0"), parsed.floors["plpgsql"].floor)
        self.assertEqual(Decimal("70.0"), parsed.floors["plpgsql-branches"].floor)
        self.assertIn(
            "coverage-floor: plpgsql-branches 70.0 carried (not held this run)\n",
            out,
        )

    def test_a_leg_not_ruled_not_held_is_refused_by_hold(self) -> None:
        """coverage:check refuses the line by name and holds nothing."""
        with Tree(PY_HELD) as tree:
            status, out, err = run(
                cf.hold,
                tree.path,
                "coverage:check",
                "python 86.0\npython-branches not-held\n",
                GATE,
            )
            path = tree.path
        self.assertEqual((1, ""), (status, out))
        self.assertEqual(f"{TASK}: {path} {OPT_OUT}\n", err)

    def test_a_leg_not_ruled_not_held_is_refused_by_derive(self) -> None:
        """The derivation refuses the line by name and writes nothing."""
        with Tree(PY_HELD) as tree:
            status, _, err = run(
                cf.derive,
                tree.path,
                "python 86.0\npython-branches not-held\n",
                "v1.2.0",
                adopt=False,
            )
            after, path = tree.text(), tree.path
        self.assertEqual(1, status)
        self.assertEqual(PY_HELD, after)
        self.assertEqual(f"{TASK}: {path} {OPT_OUT}\n", err)


# The PL/pgSQL legs on lines of their own (#2143, #2163), and the
# Python ones, whose branch leg no ruling lets report `not-held`.
PG_HELD = record(
    planted("plpgsql", "83.4", "85.4"), planted("plpgsql-branches", "70.0", "72.0")
)
PY_HELD = record(
    planted("python", "83.4", "85.4"), planted("python-branches", "80.0", "82.0")
)

# A `not-held` from a leg no ruling allows (#2163), as legs() names it.
OPT_OUT = (
    "measurement 'python-branches not-held' is not `<leg> <percent>`: only"
    " plpgsql-branches may report `not-held` (#2163)"
)


def owned_record(legs: str) -> str:
    """Build a floor that records `legs`, each measured 92.0 and held at 90.

    Returns:
        The file text.

    """
    return record(*(planted(name, "90", "92.0") for name in legs.split()))


PGRX15 = frozenset({"pgrx:15"})
NO_OWNER = REMEDY.replace("{needs}", "restore what the leg measures")


class TestOwners(unittest.TestCase):
    """One owner per leg, and each job judges only its own (#1875)."""

    def test_hold_judges_only_the_calling_jobs_legs(self) -> None:
        """One row per branch of the scoped hold, each red row's message."""
        for name, legs, source, jobs, want in (
            (
                "gate scope: a recorded pgrx leg is the pgrx job's",
                "rust pgrx-pg15",
                "rust 99\n",
                GATE,
                (0, "lbl: rust leg holds its floor 90 (99%)\n", ""),
            ),
            (
                "gate scope: a recorded gate leg not measured is red",
                "rust python",
                "rust 99\n",
                GATE,
                (
                    1,
                    "lbl: rust leg holds its floor 90 (99%)\n",
                    f"{TASK}: the floor was derived with python and this run"
                    " measured none\n"
                    + REMEDY.format(needs=cf.NEEDS["python"], leg="python"),
                ),
            ),
            (
                "pgrx scope: its recorded major not measured is red",
                "rust pgrx-pg15",
                "",
                PGRX15,
                (
                    1,
                    "",
                    f"{TASK}: the floor was derived with pgrx-pg15 and this run"
                    " measured none\n" + NO_OWNER.format(leg="pgrx-pg15"),
                ),
            ),
            (
                "pgrx scope: its major above the floor holds",
                "rust pgrx-pg15",
                "pgrx-pg15 95\n",
                PGRX15,
                (0, "lbl: pgrx-pg15 leg holds its floor 90 (95%)\n", ""),
            ),
            (
                "pgrx scope: its major below the floor is red",
                "rust pgrx-pg15",
                "pgrx-pg15 80\n",
                PGRX15,
                (1, "", f"{TASK}: pgrx-pg15: 80% is below its derived floor 90%\n"),
            ),
            (
                "pgrx scope: another major, and the gate's legs, are not its own",
                "rust pgrx-pg15 pgrx-pg16",
                "pgrx-pg15 95\n",
                PGRX15,
                (0, "lbl: pgrx-pg15 leg holds its floor 90 (95%)\n", ""),
            ),
        ):
            with self.subTest(name), Tree(owned_record(legs)) as tree:
                self.assertEqual(want, run(cf.hold, tree.path, "lbl", source, jobs))

    def test_the_plan_holds_the_record_to_its_planned_jobs(self) -> None:
        """Every recorded leg needs its owner planned; red by name if not."""
        for name, legs, majors, want in (
            (
                "a recorded major the plan dropped",
                "rust pgrx-pg14 pgrx-pg15",
                '["15","16","17","18"]',
                (
                    1,
                    "",
                    f"{TASK}: the floor records pgrx-pg14, measured by the pgrx:14"
                    " job, and this run plans none (planned majors:"
                    " 15,16,17,18)\n" + NO_OWNER.format(leg="pgrx-pg14"),
                ),
            ),
            (
                "no major planned at all",
                "rust pgrx-pg15",
                "[]",
                (
                    1,
                    "",
                    f"{TASK}: the floor records pgrx-pg15, measured by the pgrx:15"
                    " job, and this run plans none (planned majors: none)\n"
                    + NO_OWNER.format(leg="pgrx-pg15"),
                ),
            ),
            (
                "every recorded leg owned",
                "rust python pgrx-pg15 pgrx-pg18",
                '["15","16","17","18"]',
                (0, "", ""),
            ),
        ):
            with self.subTest(name), Tree(owned_record(legs)) as tree:
                self.assertEqual(want, run(cf.planned, tree.path, majors))

    def test_the_plan_accepts_a_dropped_leg_and_no_floor(self) -> None:
        """A dropped leg needs no owner; no floor needs none."""
        dropped = record(
            planted("rust", "90", "92.0"),
            planted("pgrx-pg14", "90", "92.0"),
            extra=("# dropped: pgrx-pg14 postgres 14 dropped",),
        )
        for name, text in (("dropped", dropped), ("no floor", None)):
            with self.subTest(name), Tree(text) as tree:
                self.assertEqual((0, "", ""), run(cf.planned, tree.path, '["15"]'))

    def test_the_plan_refuses_a_floor_off_its_record(self) -> None:
        """Drift is the same refusal in the plan as everywhere else."""
        with Tree(record(planted("rust", "88.0", "85.4"))) as tree:
            status, _, err = run(cf.planned, tree.path, '["15"]')
        self.assertEqual(1, status)
        self.assertIn(f"{TASK}:", err)

    def test_the_plan_decodes_its_majors_as_json(self) -> None:
        """`--majors-json` is the plan's array of major strings, or red (#1880).

        Every refusal names the value as given, and none reaches the
        floor: a value the helper cannot read plans nothing.
        """
        refused = (
            TASK + ": the planned majors '{value}' are not a JSON array of"
            " canonical major strings: {why}\n"
        )
        for name, legs, majors, want in (
            ("no major", "rust", "[]", (0, "", "")),
            ("one major", "rust pgrx-pg15", '["15"]', (0, "", "")),
            (
                "two majors",
                "rust pgrx-pg15 pgrx-pg16",
                '["15","16"]',
                (0, "", ""),
            ),
            (
                "a number, not a string",
                "rust",
                "[15]",
                (1, "", refused.format(value="[15]", why="15 is not a string")),
            ),
            (
                "a leading zero",
                "rust",
                '["015"]',
                (
                    1,
                    "",
                    refused.format(
                        value='["015"]', why='"015" is not a canonical major'
                    ),
                ),
            ),
            (
                "a string, not an array",
                "rust",
                '"15"',
                (1, "", refused.format(value='"15"', why="the value is not a list")),
            ),
            (
                "not JSON at all",
                "rust",
                "15,16",
                (
                    1,
                    "",
                    refused.format(
                        value="15,16", why="Extra data: line 1 column 3 (char 2)"
                    ),
                ),
            ),
        ):
            with self.subTest(name), Tree(owned_record(legs)) as tree:
                self.assertEqual(want, run(cf.planned, tree.path, majors))

    def test_one_owner_per_leg_family(self) -> None:
        """`pgrx-pgNN` strictly is the pgrx job; every other leg the gate's."""
        for name, job in (
            ("rust", "gate"),
            ("go", "gate"),
            ("python", "gate"),
            ("python-branches", "gate"),
            ("typescript", "gate"),
            ("plpgsql", "gate"),
            ("pgrx-pg15", "pgrx:15"),
            ("pgrx-pg100", "pgrx:100"),
            ("pgrx-pg015", "gate"),
            ("pgrx-pg", "gate"),
            ("pgrx-15", "gate"),
            ("pgrx-pg15-branches", "gate"),
            ("xpgrx-pg15", "gate"),
        ):
            with self.subTest(name):
                self.assertEqual(job, cf.owner(name))

    def test_a_job_is_named_strictly(self) -> None:
        """`hold --job` takes `gate` or `pgrx:<major>` and nothing else."""
        self.assertEqual("gate", cf.job_of("gate"))
        self.assertEqual("pgrx:15", cf.job_of("pgrx:15"))
        for text in ("pgrx", "pgrx:", "pgrx:015", "pgrx-pg15", "Gate"):
            with self.subTest(text), self.assertRaises(argparse.ArgumentTypeError):
                cf.job_of(text)

    def test_the_command_line_reaches_both_checks(self) -> None:
        """`hold --job` and `planned --majors-json`, as the callers invoke them."""
        with Tree(owned_record("rust pgrx-pg15")) as tree:
            stdin = sys.stdin
            sys.stdin = io.StringIO("pgrx-pg15 95\n")
            try:
                got = run(
                    cf.main,
                    [
                        "--path",
                        str(tree.path),
                        "hold",
                        "--label",
                        "lbl",
                        "--job",
                        "pgrx:15",
                    ],
                )
            finally:
                sys.stdin = stdin
            plan = run(
                cf.main,
                [
                    "--path",
                    str(tree.path),
                    "planned",
                    "--majors-json",
                    '["16"]',
                ],
            )
        self.assertEqual((0, "lbl: pgrx-pg15 leg holds its floor 90 (95%)\n", ""), got)
        self.assertEqual(1, plan[0])


# The canon tree this suite belongs to: its `mise/` and its workflows.
CANON = Path(__file__).resolve().parent.parent
BASH = hermetic.tool("bash")
# The `plan` job's step, exactly as ci.yml runs it.
PLAN_STEP = re.compile(r"^ {8}id: plan\n {8}run: \|\n((?: {10}.*\n)*)", re.MULTILINE)
# A cargo that answers `metadata` with the planted workspace, and nothing else.
CARGO_STUB = """#!/usr/bin/env bash
[[ "$1 $2" == "metadata --no-deps" ]] || exit 99
cat "${PLANT_METADATA}"
"""
PLANNED = (15, 16, 17, 18)


# Each record the plan rows plant, and what `planned` says of it:
# (name, legs, status, stderr).
PLAN_ROWS: tuple[tuple[str, str, int, str], ...] = (
    (
        "every planned major recorded",
        "rust pgrx-pg15 pgrx-pg16 pgrx-pg17 pgrx-pg18",
        0,
        "",
    ),
    (
        "a recorded major the plan does not make",
        "rust pgrx-pg14 pgrx-pg15",
        1,
        "coverage_floor.py: the floor records pgrx-pg14, measured by the pgrx:14 job,"
        " and this run plans none (planned majors: 15,16,17,18)\n"
        + NO_OWNER.format(leg="pgrx-pg14"),
    ),
)


def plan_step() -> str:
    """Read the `plan` step's `run:` block out of the canon's ci.yml.

    Returns:
        The block, dedented, as bash runs it.

    Raises:
        LookupError: the step is gone.

    """
    text = (CANON / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    found = PLAN_STEP.search(text)
    if found is None:
        msg = "no `id: plan` step with a `run: |` block in ci.yml"
        raise LookupError(msg)
    return textwrap.dedent(found.group(1))


class TestThePlanFeedsTheHelper(unittest.TestCase):
    """pgrx-plan.sh's real output, read by `planned` as the plan passes it (#1880).

    The rows the helper's own table holds are hand-written; these run
    the producer against a workspace declaring pg15 to pg18 and hand
    its stdout on, so a format the two disagree on is red here. The
    canon's own gate cannot reach it: the canon has no pgrx crate.
    """

    @override
    def setUp(self) -> None:
        """Plant a workspace with one extension declaring pg15 to pg18."""
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.tree = self.tmp / "tree"
        self.tree.mkdir()
        (self.tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        self.canon = hermetic.canon(self.tree / ".org-canon")
        bins = self.tmp / "bin"
        bins.mkdir()
        (bins / "cargo").write_text(CARGO_STUB, encoding="utf-8")
        (bins / "cargo").chmod(0o755)
        root = self.tree / "crates" / "ext"
        ext: dict[str, object] = {
            "id": f"path+file://{root}#ext@1.0.0",
            "name": "ext",
            "version": "1.0.0",
            "publish": None,
            "dependencies": [{"name": "pgrx"}],
            "features": {
                "default": ["pg18"],
                "pg_test": [],
                **{f"pg{m}": [f"pgrx/pg{m}"] for m in PLANNED},
            },
            "targets": [{"kind": ["lib"], "crate_types": ["lib"]}],
            "manifest_path": f"{root}/Cargo.toml",
        }
        metadata = self.tmp / "metadata.json"
        document: dict[str, object] = {
            "packages": [ext],
            "workspace_members": [ext["id"]],
            "workspace_root": str(self.tree),
            "target_directory": str(self.tree / "target"),
        }
        metadata.write_text(json.dumps(document), encoding="utf-8")
        self.output = self.tmp / "github-output"
        self.output.touch()
        self.env = hermetic.env() | {
            "PATH": f"{bins}{os.pathsep}{os.environ['PATH']}",
            "ORG_PYTHON": sys.executable,
            "PLANT_METADATA": str(metadata),
            "GITHUB_OUTPUT": str(self.output),
        }
        # pgrx-plan.sh plans a tracked workspace only (#2013).
        git = hermetic.tool("git")
        for step in (["init", "-q"], ["add", "Cargo.toml"]):
            subprocess.run([git, *step], cwd=self.tree, env=self.env, check=True)

    def run_in_tree(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        """Run a command in the planted tree.

        Returns:
            The finished process.

        """
        return subprocess.run(
            argv,
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def floor(self, legs: str) -> None:
        """Commit a floor recording `legs` to the planted tree."""
        (self.tree / ".coverage-floor").write_text(owned_record(legs), encoding="utf-8")

    def test_the_producers_stdout_is_the_helpers_input(self) -> None:
        """pgrx-plan.sh's literal stdout, given to `planned --majors-json`."""
        plan = self.run_in_tree([
            BASH,
            str(self.canon / "mise" / "pgrx-plan.sh"),
            str(self.canon),
        ])
        self.assertEqual(0, plan.returncode, plan.stderr)
        self.assertEqual('["15","16","17","18"]\n', plan.stdout)
        for name, legs, status, err in PLAN_ROWS:
            with self.subTest(name):
                self.floor(legs)
                done = self.run_in_tree([
                    sys.executable,
                    str(self.canon / "mise" / "coverage_floor.py"),
                    "planned",
                    "--majors-json",
                    plan.stdout,
                ])
                self.assertEqual(
                    (status, "", err), (done.returncode, done.stdout, done.stderr)
                )

    def test_the_plan_step_hands_the_producer_to_the_helper(self) -> None:
        """ci.yml's own `plan` step, producer to helper, in the planted tree."""
        for name, legs, status, err in PLAN_ROWS:
            with self.subTest(name):
                self.floor(legs)
                done = self.run_in_tree([BASH, "-c", plan_step()])
                self.assertEqual(status, done.returncode, done.stderr)
                self.assertEqual(err, done.stderr)

    def test_the_plan_step_passes_the_majors_verbatim(self) -> None:
        """No parameter expansion takes the plan's JSON apart in the step."""
        body = plan_step()
        expansions = [m.group(0) for m in re.finditer(r"\$\{[^}]*majors[^}]*\}", body)]
        verbatim: list[str] = ["${majors}"] * len(expansions)
        self.assertEqual(verbatim, expansions)
        self.assertIn('planned \\\n  --majors-json "${majors}"\n', body)


MARKER = "fuzz-parse-marker"
LEFT = (
    "fuzz legs left the floor (#2090): delete the line, its"
    ' "measured" record and its comment'
)


class TestFuzzLegsLeft(unittest.TestCase):
    """No fuzz target is a leg; a fuzz line left in a floor is refused (#2090)."""

    def test_a_fuzz_line_and_its_record_are_red_by_name(self) -> None:
        """`fuzz-x 50` and its record: one refusal, item 3's message."""
        with Tree(record(PY) + planted("fuzz-x", "50", "52.0")) as tree:
            got = run(cf.read, tree.path)
        self.assertEqual((1, "", f"{TASK}: {tree.path} states fuzz-x: {LEFT}\n"), got)

    def test_every_form_that_names_a_fuzz_leg_is_refused(self) -> None:
        """The line, the record, `legs:` and `dropped:`: none carried or ignored."""
        for name, text in (
            ("line", record(PY) + f"{MARKER} 88\n"),
            ("record", record(PY) + f"# {MARKER} measured: 90.0\n"),
            ("reset", record(PY) + f"# {MARKER} reset: why\n"),
            ("legs", record(PY, legs=f"python {MARKER}")),
            ("dropped", record(PY, extra=(f"# dropped: {MARKER} gone",))),
            ("bare", record(PY) + planted("fuzz-", "88", "90.0")),
        ):
            fuzz = "fuzz-" if name == "bare" else MARKER
            with self.subTest(name):
                parsed, problems = cf.parse(text)
                want: list[str] = [f"states {fuzz}: {LEFT}"]
                self.assertIsNone(parsed)
                self.assertEqual(want, problems)

    def test_two_fuzz_legs_are_each_named_once(self) -> None:
        """Every leg left is named, each once, in the order stated."""
        text = (
            record(PY)
            + planted(MARKER, "88", "90.0")
            + planted("fuzz-load", "40", "42.0")
        )
        _, problems = cf.parse(text)
        want: list[str] = [
            f"states {MARKER}: {LEFT}",
            f"states fuzz-load: {LEFT}",
        ]
        self.assertEqual(want, problems)

    def test_the_gate_and_the_release_refuse_it_too(self) -> None:
        """`hold` and a release's `write` read the same file the same way."""
        text = record(PY) + planted(MARKER, "88", "90.0")
        refused = f"{TASK}: {{path}} states {MARKER}: {LEFT}\n"
        with Tree(text) as tree:
            gate = run(cf.hold, tree.path, "lbl", "python 86.0\n", GATE)
            release = run(cf.derive, tree.path, "python 86.0\n", "v1", adopt=False)
            self.assertEqual((1, "", refused.format(path=tree.path)), gate)
            self.assertEqual(1, release[0])
            self.assertTrue(
                release[2].startswith(refused.format(path=tree.path)), release[2]
            )

    def test_a_fuzz_figure_in_a_measurement_is_no_leg_apart(self) -> None:
        """Nothing holds a fuzz figure apart: `fuzz` is no family of NEEDS."""
        self.assertNotIn("fuzz", cf.NEEDS)
        self.assertFalse(cf.apart(MARKER))


if __name__ == "__main__":
    unittest.main()
