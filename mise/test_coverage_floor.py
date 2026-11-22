#!/usr/bin/env python3
"""Table tests for the derived coverage floor, guard branches first.

#650's law, applied to #652: every guard branch gets a row, both
directions where there are two, and a planted failure is measured rather
than reasoned about. The guards here are the whole mechanism — a ratchet
that refuses when it should pass is a release nobody can cut, and a
ratchet that passes when it should refuse is the silent drift the floor
was made derived state to stop.

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
from typing import TYPE_CHECKING, Self, override
from unittest import mock

import coverage_floor as cf
import hermetic

if TYPE_CHECKING:
    from collections.abc import Callable


def record(
    floor: str,
    band: str | None = "2",
    measured: str | None = "85.4",
    reset: str | None = None,
    derived: str | None = "v0.20.0",
) -> str:
    """Build a floor file with the fields a row wants stated.

    Returns:
        The file text, comment record and bare number line.

    """
    lines = ["# Coverage floor -- DERIVED STATE, not a number to edit (#652).", "#"]
    if band is not None:
        lines.append(f"# band: {band}")
    if measured is not None:
        lines.append(f"# measured: {measured}")
    if reset is not None:
        lines.append(f"# reset: {reset}")
    if derived is not None:
        lines.append(f"# derived: {derived}")
    lines.append(floor)
    return "\n".join(lines) + "\n"


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


# What a row expects of a clean parse or reading, named with its type.
# The task that runs the helper, whose name its findings carry (#1934).
TASK = "coverage:check"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    patch = mock.patch.dict(os.environ, {"MISE_TASK_NAME": TASK, "ORG_FINDINGS": ""})
    patch.start()
    unittest.addModuleCleanup(patch.stop)


NO_PROBLEMS: list[str] = []
NO_LEGS: list[tuple[str, Decimal]] = []


def clean(text: str) -> tuple[cf.Record, list[str]]:
    """Parse a floor file the row expects to read.

    Returns:
        The record and the problems `parse` found beside it.

    Raises:
        AssertionError: `parse` refused the file, naming its problems.

    """
    parsed, problems = cf.parse(text)
    if parsed is None:
        msg = f"the floor did not parse: {problems}"
        raise AssertionError(msg)
    return parsed, problems


def separate(record: cf.Record) -> dict[str, tuple[Decimal, Decimal]]:
    """Read a record's separate lines, which the row expects it to carry.

    Returns:
        Each separate leg's floor and measurement.

    Raises:
        AssertionError: the record carries no separate line.

    """
    if record.separate is None:
        msg = "the record carries no separate line"
        raise AssertionError(msg)
    return record.separate


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


class TestParseRefusals(unittest.TestCase):
    """Every shape `parse` refuses, one row each."""

    def test_refusals(self) -> None:
        """A malformed record is refused by name, never read past."""
        rows = [
            ("no bare line", "# band: 2\n", "no floor"),
            ("two bare lines", "# band: 2\n# measured: 85.4\n83.4\n90\n", "2 floors"),
            ("floor not a number", record("ninety"), "is not a number"),
            (
                "floor above 100",
                record("102", measured="104"),
                "not a percentage between",
            ),
            (
                "floor below zero",
                record("-1", measured="1"),
                "not a percentage between",
            ),
            ("no band at all", record("83.4", band=None), "states no band"),
            (
                "band not a number",
                record("83.4", band="two"),
                "is not a number of points",
            ),
            (
                "negative band",
                record("87.4", band="-2"),
                "is not a number of points",
            ),
            (
                "measured and reset together",
                record("88", measured="85.4", reset="a reason"),
                "records both a measurement and a reset",
            ),
            (
                "reset with no reason",
                record("88", measured=None, reset="", derived=None),
                "records a reset with no reason",
            ),
            (
                "recordless legacy floor",
                "90\n",
                "states no band",
            ),
            (
                "band but no derivation",
                record("90", measured=None, derived=None),
                "carries no derivation record",
            ),
            (
                "measured not a number",
                record("83.4", measured="high"),
                "is not a percentage",
            ),
            (
                "floor disagrees with the record",
                record("85", measured="85.4"),
                "disagrees with its own record",
            ),
        ]
        for name, text, expected in rows:
            with self.subTest(name):
                parsed, problems = cf.parse(text)
                self.assertIsNone(parsed)
                self.assertEqual(len(problems), 1)
                self.assertIn(expected, problems[0])


class TestParseAccepts(unittest.TestCase):
    """The other direction: the records the law admits."""

    def test_derived_record(self) -> None:
        """A floor equal to measured minus band is the derivation."""
        parsed, problems = clean(record("83.4"))
        self.assertEqual(problems, NO_PROBLEMS)
        self.assertEqual(parsed.floor, Decimal("83.4"))
        self.assertEqual(parsed.measured, Decimal("85.4"))
        self.assertEqual(parsed.band, Decimal(2))
        self.assertEqual(parsed.derived, "v0.20.0")
        self.assertIsNone(parsed.reset)

    def test_blank_lines_are_not_lines(self) -> None:
        """A blank line anywhere in the file reads as nothing (#1659)."""
        parsed, problems = clean("\n" + record("83.4").replace("\n", "\n\n"))
        self.assertEqual(problems, NO_PROBLEMS)
        self.assertEqual(parsed.floor, Decimal("83.4"))

    def test_trailing_zeros_are_the_same_number(self) -> None:
        """Agreement is numeric, so 83.40 and 83.4 are one floor."""
        parsed, problems = clean(record("83.40"))
        self.assertEqual(problems, NO_PROBLEMS)
        self.assertEqual(parsed.floor, Decimal("83.4"))

    def test_reset_is_a_record(self) -> None:
        """A conscious reset states a reason and no measurement."""
        parsed, problems = clean(
            record(
                "88",
                measured=None,
                reset="pgrx members left the workspace",
                derived=None,
            ),
        )
        self.assertEqual(problems, NO_PROBLEMS)
        self.assertEqual(parsed.floor, Decimal(88))
        self.assertIsNone(parsed.measured)
        self.assertEqual(parsed.reset, "pgrx members left the workspace")

    def test_the_file_states_its_own_band(self) -> None:
        """A file recording a different band is read under that band.

        Changing the org's band is not a flag day: the agreement law uses
        the band the file was derived under, and the next release rewrites
        it with the current one.
        """
        parsed, problems = clean(record("82.4", band="3"))
        self.assertEqual(problems, NO_PROBLEMS)
        self.assertEqual(parsed.band, Decimal(3))

    def test_floor_clamps_at_zero(self) -> None:
        """A measurement below the band derives a floor of 0, not -0.5."""
        parsed, problems = clean(record("0", measured="1.5"))
        self.assertEqual(problems, NO_PROBLEMS)
        self.assertEqual(parsed.floor, Decimal(0))


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


class TestRender(unittest.TestCase):
    """What the machinery writes, read back through the parser."""

    def test_render_round_trips(self) -> None:
        """The written file parses, and states the band it was cut with."""
        text = cf.render(Decimal("85.44"), "v1.52.0")
        parsed, problems = clean(text)
        self.assertEqual(problems, NO_PROBLEMS)
        self.assertEqual(parsed.measured, Decimal("85.4"))
        self.assertEqual(parsed.floor, Decimal("83.4"))
        self.assertEqual(parsed.band, cf.BAND)
        self.assertEqual(parsed.derived, "v1.52.0")

    def test_render_states_the_band_and_the_decision(self) -> None:
        """The band is stated as a comment naming #652, as decided."""
        text = cf.render(Decimal("85.4"), "v1.52.0")
        self.assertIn("#652", text)
        self.assertIn("# band: 2", text)


class TestRead(unittest.TestCase):
    """`coverage:check`'s half: the number, or a named refusal."""

    def test_no_floor_skips_clean(self) -> None:
        """A repository that adopted no floor owes nothing."""
        with Tree(None) as tree:
            status, out, err = run(cf.read, tree.path)
        self.assertEqual(status, 0)
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_valid_floor_prints_the_number(self) -> None:
        """The enforced number comes from the record, not the raw line."""
        with Tree(record("83.4")) as tree:
            status, out, err = run(cf.read, tree.path)
        self.assertEqual(status, 0)
        self.assertEqual(out.strip(), "83.4")
        self.assertEqual(err, "")

    def test_hand_edited_floor_is_refused_as_drift(self) -> None:
        """A number typed over the derivation reds the gate by name."""
        with Tree(record("85", measured="85.4")) as tree:
            status, out, err = run(cf.read, tree.path)
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("disagrees with its own record", err)
        self.assertIn("never repaired", err)


class TestWriteRatchet(unittest.TestCase):
    """The release path's half, planted in both directions."""

    def test_healthy_release_raises_the_floor(self) -> None:
        """A ceiling that held rewrites the floor upwards."""
        with Tree(record("83.4", measured="85.4")) as tree:
            status, out, err = run(
                cf.write,
                tree.path,
                Decimal("86.8"),
                "v0.21.0",
                adopt=False,
            )
            parsed, problems = clean(tree.written())
        self.assertEqual(status, 0)
        self.assertEqual(err, "")
        self.assertIn("83.4 -> 84.8", out)
        self.assertEqual(problems, NO_PROBLEMS)
        self.assertEqual(parsed.floor, Decimal("84.8"))
        self.assertEqual(parsed.measured, Decimal("86.8"))
        self.assertEqual(parsed.derived, "v0.21.0")

    def test_below_the_floor_fails_the_release(self) -> None:
        """A drop under the floor refuses loudly and changes nothing."""
        before = record("83.4", measured="85.4")
        with Tree(before) as tree:
            status, out, err = run(
                cf.write,
                tree.path,
                Decimal("82.0"),
                "v0.21.0",
                adopt=False,
            )
            after = tree.text()
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("measured 82.0% is below the floor 83.4%", err)
        self.assertIn("The floor never falls", err)
        self.assertEqual(after, before)

    def test_within_the_band_passes_and_keeps_the_record(self) -> None:
        """The gate's line is the release's line (#1786).

        A measurement the gate passed — at or above the floor, below the
        last release's — passes the release and leaves the file exactly
        as it stood: the floor never falls, and nothing reds main.
        """
        for figure in ("85.3", "84.9", "83.4"):
            with self.subTest(figure):
                before = record("83.4", measured="85.4")
                with Tree(before) as tree:
                    status, out, err = run(
                        cf.write, tree.path, Decimal(figure), "v0.21.0", adopt=False
                    )
                    after = tree.written()
                    parsed, problems = clean(after)
                self.assertEqual((0, ""), (status, err))
                self.assertIn(f"floor 83.4 held (measured {figure}", out)
                self.assertEqual(NO_PROBLEMS, problems)
                self.assertEqual(Decimal("83.4"), parsed.floor)
                self.assertEqual(Decimal("85.4"), parsed.measured)
                self.assertEqual("v0.20.0", parsed.derived)

    def test_exactly_at_the_band_holds_the_floor(self) -> None:
        """The boundary rewrites the record, and the floor stays put."""
        with Tree(record("83.4", measured="85.4")) as tree:
            status, _, err = run(
                cf.write,
                tree.path,
                Decimal("85.4"),
                "v0.21.0",
                adopt=False,
            )
            parsed, _ = clean(tree.written())
        self.assertEqual(status, 0)
        self.assertEqual(err, "")
        self.assertEqual(parsed.floor, Decimal("83.4"))
        self.assertEqual(parsed.derived, "v0.21.0")

    def test_one_tenth_below_the_floor_fails(self) -> None:
        """The refusal is on the boundary, not near it."""
        with Tree(record("83.4", measured="85.4")) as tree:
            status, _, err = run(
                cf.write,
                tree.path,
                Decimal("83.3"),
                "v0.21.0",
                adopt=False,
            )
        self.assertEqual(status, 1)
        self.assertIn("is below the floor", err)

    def test_measurement_is_rounded_down_before_the_ratchet(self) -> None:
        """The compared number is the recorded one, not a wider float."""
        with Tree(record("83.4", measured="85.4")) as tree:
            status, _, err = run(
                cf.write,
                tree.path,
                Decimal("83.39999"),
                "v0.21.0",
                adopt=False,
            )
        self.assertEqual(status, 1)
        self.assertIn("measured 83.3%", err)

    def test_a_reset_floor_ratchets_from_where_the_human_put_it(self) -> None:
        """A conscious reset is the floor the next release measures against."""
        reset = record("88", measured=None, reset="workspace split", derived=None)
        with Tree(reset) as tree:
            status, out, _ = run(
                cf.write,
                tree.path,
                Decimal("90.0"),
                "v0.21.0",
                adopt=False,
            )
            parsed, _ = clean(tree.written())
        self.assertEqual(status, 0)
        self.assertIn("88 -> 88.0", out)
        self.assertEqual(parsed.floor, Decimal("88.0"))
        self.assertIsNone(parsed.reset)

    def test_a_reset_within_the_band_stays_a_reset(self) -> None:
        """A release that does not raise a reset floor keeps its reason."""
        reset = record("88", measured=None, reset="workspace split", derived=None)
        with Tree(reset) as tree:
            status, out, err = run(
                cf.derive, tree.path, "python 89.0\n", "v0.21.0", adopt=False
            )
            parsed, problems = clean(tree.written())
        self.assertEqual((0, "", NO_PROBLEMS), (status, err, problems))
        self.assertIn("floor 88 held", out)
        self.assertEqual(Decimal(88), parsed.floor)
        self.assertEqual("workspace split", parsed.reset)
        self.assertEqual(("python",), parsed.legs)


class TestCap(unittest.TestCase):
    """The cap (#1786): no floor is derived above 90."""

    def test_a_derivation_stops_at_the_cap(self) -> None:
        """A measurement of 95.4 writes a floor of 90, not 93.4."""
        parsed, problems = clean(cf.render(Decimal("95.4"), "v1.71.0"))
        self.assertEqual(NO_PROBLEMS, problems)
        self.assertEqual(Decimal(90), parsed.floor)
        self.assertEqual(Decimal("95.4"), parsed.measured)

    def test_a_floor_written_before_the_cap_is_enforced_at_it(self) -> None:
        """The uncapped derivation is not drift; the gate holds 90."""
        with Tree(record("92.3", measured="94.3")) as tree:
            status, out, err = run(cf.read, tree.path)
        self.assertEqual((0, "90\n", ""), (status, out, err))

    def test_a_number_typed_above_the_cap_is_still_drift(self) -> None:
        """Neither derivation states 99, so it is refused."""
        parsed, problems = cf.parse(record("99", measured="94.3"))
        self.assertIsNone(parsed)
        self.assertIn("derives 90", problems[0])

    def test_the_incident_releases(self) -> None:
        """v1.70.5's floor, 94.2 measured: the release passes at 90.

        The defect #1786 records: main red three pushes running because
        the release demanded 94.3 of a tree the gate had passed.
        """
        with Tree(record("92.3", measured="94.3")) as tree:
            status, out, err = run(
                cf.derive, tree.path, "python 94.2\n", "v1.71.0", adopt=False
            )
            parsed, problems = clean(tree.written())
        self.assertEqual((0, "", NO_PROBLEMS), (status, err, problems))
        self.assertIn("floor 90 -> 90", out)
        self.assertEqual(Decimal(90), parsed.floor)
        self.assertEqual(Decimal("94.2"), parsed.measured)

    def test_a_capped_floor_still_fails_below_it(self) -> None:
        """Under 90 is under the floor."""
        with Tree(record("90", measured="94.3")) as tree:
            status, _, err = run(
                cf.derive, tree.path, "python 89.9\n", "v1.71.0", adopt=False
            )
        self.assertEqual(1, status)
        self.assertIn("measured 89.9% is below the floor 90%", err)

    def test_a_separate_leg_is_capped_too(self) -> None:
        """The legs held apart live under the same cap."""
        text = record("83.4") + "# rust-branches measured: 97.0\nrust-branches 95.0\n"
        with Tree(text) as tree:
            status, out, err = run(cf.read, tree.path, "rust-branches")
        self.assertEqual((0, "90\n", ""), (status, out, err))
        rendered = cf.render(
            Decimal("85.4"), "v1.71.0", {"rust-branches": Decimal("97.0")}
        )
        self.assertIn("\nrust-branches 90\n", rendered)

    def test_the_gate_holds_the_cap(self) -> None:
        """A legacy floor of 92.3: a leg at 91 holds."""
        with Tree(record("92.3", measured="94.3")) as tree:
            status, out, err = run(cf.hold, tree.path, "lbl", "python 91.0\n", GATE)
        self.assertEqual(
            (0, "lbl: python leg holds the floor 90 (91.0%)\n", ""),
            (status, out, err),
        )


class TestWriteAdoption(unittest.TestCase):
    """Who may write a floor, and who may not."""

    def test_release_skips_a_repository_with_no_floor(self) -> None:
        """Opt-in: no file, nothing written, nothing owed."""
        with Tree(None) as tree:
            status, out, err = run(
                cf.write,
                tree.path,
                Decimal("85.4"),
                "v0.21.0",
                adopt=False,
            )
            after = tree.text()
        self.assertEqual(status, 0)
        self.assertIn("skipped", out)
        self.assertEqual(err, "")
        self.assertIsNone(after)

    def test_adopt_writes_the_first_floor(self) -> None:
        """coverage:adopt turns a measurement into the repository's floor."""
        with Tree(None) as tree:
            status, out, _ = run(
                cf.write,
                tree.path,
                Decimal("85.44"),
                "coverage:adopt",
                adopt=True,
            )
            parsed, problems = clean(tree.written())
        self.assertEqual(status, 0)
        self.assertIn("adopted", out)
        self.assertEqual(problems, NO_PROBLEMS)
        self.assertEqual(parsed.floor, Decimal("83.4"))

    def test_release_refuses_to_repair_a_recordless_floor(self) -> None:
        """The machinery does not silently adopt a floor it did not write."""
        with Tree("90\n") as tree:
            status, _, err = run(
                cf.write,
                tree.path,
                Decimal("85.4"),
                "v0.21.0",
                adopt=False,
            )
            after = tree.text()
        self.assertEqual(status, 1)
        self.assertIn("states no band", err)
        self.assertIn("does not repair a floor it did not write", err)
        self.assertEqual(after, "90\n")

    def test_adopt_migrates_a_recordless_floor(self) -> None:
        """The other direction: the migration off a hand-typed number."""
        with Tree("90\n") as tree:
            status, out, _ = run(
                cf.write,
                tree.path,
                Decimal("85.4"),
                "coverage:adopt",
                adopt=True,
            )
            parsed, problems = clean(tree.written())
        self.assertEqual(status, 0)
        self.assertIn("rewrote", out)
        self.assertEqual(problems, NO_PROBLEMS)
        self.assertEqual(parsed.floor, Decimal("83.4"))

    def test_adopt_cannot_lower_a_floor_that_parses(self) -> None:
        """`fix`-shaped is not an escape hatch from the ratchet."""
        before = record("83.4", measured="85.4")
        with Tree(before) as tree:
            status, _, err = run(
                cf.write,
                tree.path,
                Decimal("81.0"),
                "coverage:adopt",
                adopt=True,
            )
            after = tree.text()
        self.assertEqual(status, 1)
        self.assertIn("is below the floor", err)
        self.assertEqual(after, before)


class TestDerive(unittest.TestCase):
    """The stdin half: which leg the floor is cut from."""

    def test_the_weakest_leg_sets_the_floor(self) -> None:
        """A mixed repository derives from its worst-covered language."""
        with Tree(record("50", measured="52")) as tree:
            status, out, _ = run(
                cf.derive,
                tree.path,
                "go 85.4\nrust 61.5\n",
                "v0.21.0",
                adopt=False,
            )
            parsed, _ = clean(tree.written())
        self.assertEqual(status, 0)
        self.assertIn("measured 61.5", out)
        self.assertEqual(parsed.floor, Decimal("59.5"))

    def test_no_measurement_refuses(self) -> None:
        """A derivation that reads nothing must not write a floor."""
        with Tree(record("83.4")) as tree:
            status, _, err = run(cf.derive, tree.path, "\n", "v0.21.0", adopt=False)
            after = tree.text()
        self.assertEqual(status, 1)
        self.assertIn("no leg was measured", err)
        self.assertEqual(after, record("83.4"))

    def test_a_malformed_leg_refuses(self) -> None:
        """One unreadable line refuses rather than deriving from the rest."""
        with Tree(record("83.4")) as tree:
            status, _, err = run(
                cf.derive,
                tree.path,
                "go 85.4\nrust ???\n",
                "v0.21.0",
                adopt=False,
            )
        self.assertEqual(status, 1)
        self.assertIn("is not `<leg> <percent>`", err)


BRANCH = "# rust-branches measured: 72.4\nrust-branches 70.4\n"
# BRANCH as the parser reads it: the floor, then the measurement.
BRANCH_HELD: dict[str, tuple[Decimal, Decimal]] = {
    "rust-branches": (Decimal("70.4"), Decimal("72.4"))
}


class TestSeparateLeg(unittest.TestCase):
    """The `rust-branches` line (#1359): its own record, law and ratchet."""

    def test_render_round_trips_the_line(self) -> None:
        """A rendered branch line parses back to the derivation it states."""
        text = cf.render(
            Decimal("85.44"), "coverage:adopt", {"rust-branches": Decimal("72.49")}
        )
        parsed, problems = clean(text)
        self.assertEqual(problems, NO_PROBLEMS)
        self.assertEqual(parsed.floor, Decimal("83.4"))
        self.assertEqual(parsed.separate, BRANCH_HELD)

    def test_the_main_floor_ignores_the_line(self) -> None:
        """coverage:check reads the main number, never the branch line."""
        with Tree(record("83.4") + BRANCH) as tree:
            status, out, err = run(cf.read, tree.path)
        self.assertEqual((status, out, err), (0, "83.4\n", ""))

    def test_read_leg_prints_the_line(self) -> None:
        """The audit's read: the branch floor, from the record."""
        with Tree(record("83.4") + BRANCH) as tree:
            status, out, err = run(cf.read, tree.path, "rust-branches")
        self.assertEqual((status, out, err), (0, "70.4\n", ""))

    def test_read_leg_without_a_line_is_empty(self) -> None:
        """No line: an empty answer, which the audit reads as a skip."""
        with Tree(record("83.4")) as tree:
            status, out, err = run(cf.read, tree.path, "rust-branches")
        self.assertEqual((status, out, err), (0, "", ""))

    def test_refusals(self) -> None:
        """A line and its record come together, and agree, or are refused."""
        rows = [
            (
                "line without record",
                "rust-branches 70.4\n",
                "states 1 `rust-branches` line(s) and no `rust-branches measured`",
            ),
            (
                "record without line",
                "# rust-branches measured: 72.4\n",
                "states 0 `rust-branches` line(s) and a `rust-branches measured`",
            ),
            (
                "two lines",
                BRANCH + "rust-branches 70.4\n",
                "states 2 `rust-branches` line(s)",
            ),
            (
                "typed over the record",
                "# rust-branches measured: 72.4\nrust-branches 60\n",
                "rust-branches floor 60 disagrees with its own record",
            ),
            (
                "not a number",
                "# rust-branches measured: 72.4\nrust-branches high\n",
                "is not `rust-branches <percent>`",
            ),
            (
                "a record that is not a percentage",
                "# rust-branches measured: lots\nrust-branches 70.4\n",
                "rust-branches measured 'lots' is not a percentage",
            ),
        ]
        for name, extra, expected in rows:
            with self.subTest(name):
                parsed, problems = cf.parse(record("83.4") + extra)
                self.assertIsNone(parsed)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(expected, problems[0])

    def test_a_release_carries_the_line_through(self) -> None:
        """Stable measures no branches, so the release keeps what it found."""
        with Tree(record("83.4", measured="85.4") + BRANCH) as tree:
            status, out, _ = run(
                cf.derive, tree.path, "rust 86.0\n", "v1.2.0", adopt=False
            )
            parsed, problems = clean(tree.written())
        self.assertEqual(status, 0)
        self.assertEqual(problems, NO_PROBLEMS)
        self.assertIn("83.4 -> 84.0", out)
        self.assertEqual(parsed.separate, BRANCH_HELD)

    def test_the_line_is_not_in_the_minimum(self) -> None:
        """A lower branch figure does not drag the main floor down."""
        with Tree(record("83.4", measured="85.4")) as tree:
            status, _, _ = run(
                cf.derive,
                tree.path,
                "rust 86.0\nrust-branches 61.0\n",
                "coverage:adopt",
                adopt=True,
            )
            parsed, _ = clean(tree.written())
        self.assertEqual(status, 0)
        self.assertEqual(parsed.floor, Decimal("84.0"))
        self.assertEqual(separate(parsed)["rust-branches"][0], Decimal("59.0"))

    def test_the_line_never_falls(self) -> None:
        """Below its floor, neither line is rewritten."""
        before = record("83.4", measured="85.4") + BRANCH
        with Tree(before) as tree:
            status, _, err = run(
                cf.derive,
                tree.path,
                "rust 86.0\nrust-branches 70.3\n",
                "coverage:adopt",
                adopt=True,
            )
            after = tree.text()
        self.assertEqual(status, 1)
        self.assertIn("rust-branches measured 70.3% is below its floor 70.4%", err)
        self.assertEqual(after, before)

    def test_the_line_within_the_band_keeps_its_record(self) -> None:
        """A branch figure the audit passed keeps the line as it stood."""
        with Tree(record("83.4", measured="85.4") + BRANCH) as tree:
            status, _, err = run(
                cf.derive,
                tree.path,
                "rust 86.0\nrust-branches 72.3\n",
                "v1.2.0",
                adopt=False,
            )
            parsed, problems = clean(tree.written())
        self.assertEqual((0, "", NO_PROBLEMS), (status, err, problems))
        self.assertEqual(Decimal("84.0"), parsed.floor)
        self.assertEqual(parsed.separate, BRANCH_HELD)

    def test_the_line_rises(self) -> None:
        """A higher branch figure raises the line."""
        with Tree(record("83.4", measured="85.4") + BRANCH) as tree:
            status, out, _ = run(
                cf.derive,
                tree.path,
                "rust 86.0\nrust-branches 80.0\n",
                "coverage:adopt",
                adopt=True,
            )
            parsed, _ = clean(tree.written())
        self.assertEqual(status, 0)
        self.assertIn("rust-branches 78.0 (measured 80.0)", out)
        self.assertEqual(separate(parsed)["rust-branches"][0], Decimal("78.0"))


class TestMain(unittest.TestCase):
    """The command line both callers actually invoke."""

    def test_read_dispatches(self) -> None:
        """`read` prints the floor for coverage:check to enforce."""
        with Tree(record("83.4")) as tree:
            status, out, _ = run(cf.main, ["--path", str(tree.path), "read"])
        self.assertEqual(status, 0)
        self.assertEqual(out.strip(), "83.4")

    def test_write_reads_the_legs_from_stdin(self) -> None:
        """The belt convention: the task enumerates, the helper consumes."""
        with Tree(record("83.4")) as tree:
            stdin = sys.stdin
            sys.stdin = io.StringIO("go 86.8\n")
            try:
                status, out, _ = run(
                    cf.main,
                    ["--path", str(tree.path), "write", "--provenance", "v0.21.0"],
                )
            finally:
                sys.stdin = stdin
            parsed, _ = clean(tree.written())
        self.assertEqual(status, 0)
        self.assertIn("83.4 -> 84.8", out)
        self.assertEqual(parsed.floor, Decimal("84.8"))


# The gate job, as coverage:check names itself to the hold (#1875).
GATE = frozenset({cf.GATE_JOB})


def legs_record(floor: str, legs: str, *extra: str) -> str:
    """Build a derived floor file that names its legs (#1666).

    Returns:
        The file text, with `# legs:` and any extra comment lines.

    """
    head, number = record(floor).rsplit("\n", 2)[0], floor
    lines = [head, f"# legs: {legs}", *extra, number]
    return "\n".join(lines) + "\n"


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
        for leg, needs in (("plpgsql", PG_NEEDS), ("typescript", TS_NEEDS)):
            with self.subTest(leg=leg):
                text = legs_record("83.4", f"python {leg}")
                with Tree(text) as tree:
                    status, out, err = run(
                        cf.hold, tree.path, "coverage:check", "python 86.0\n", GATE
                    )
                self.assertEqual(1, status)
                self.assertEqual(
                    "coverage:check: python leg holds the floor 83.4 (86.0%)\n", out
                )
                self.assertEqual(
                    f"coverage:check: the floor was derived with {leg} and this"
                    " run measured none\n" + REMEDY.format(needs=needs, leg=leg),
                    err,
                )

    def test_an_unrecorded_measured_leg_is_held_and_named(self) -> None:
        """A new leg is held now and joins the record at the derivation."""
        with Tree(legs_record("83.4", "python")) as tree:
            status, out, err = run(
                cf.hold,
                tree.path,
                "coverage:check",
                "python 86.0\nplpgsql 87.5\n",
                GATE,
            )
        self.assertEqual((0, ""), (status, err))
        self.assertEqual(
            "coverage:check: python leg holds the floor 83.4 (86.0%)\n"
            "coverage:check: plpgsql leg holds the floor 83.4 (87.5%)\n"
            "coverage:check: plpgsql: not yet in the floor's record: it joins"
            " at the next derivation\n",
            out,
        )

    def test_a_leg_below_the_floor_is_red(self) -> None:
        """Every leg is judged; the one below is named."""
        with Tree(legs_record("83.4", "python plpgsql")) as tree:
            status, _, err = run(
                cf.hold,
                tree.path,
                "coverage:check",
                "python 86.0\nplpgsql 80.1\n",
                GATE,
            )
        self.assertEqual(1, status)
        self.assertEqual(
            "coverage:check: plpgsql: 80.1% is below the derived floor 83.4%\n", err
        )

    def test_a_dropped_leg_is_not_demanded(self) -> None:
        """A recorded leg consciously dropped is not red when it is gone."""
        text = legs_record("83.4", "python plpgsql", "# dropped: plpgsql moved out")
        with Tree(text) as tree:
            status, _, err = run(cf.hold, tree.path, "lbl", "python 86.0\n", GATE)
        self.assertEqual((0, ""), (status, err))

    def test_no_legs_record_demands_nothing(self) -> None:
        """Every floor today: held, none demanded, nothing named."""
        with Tree(record("83.4")) as tree:
            status, out, err = run(cf.hold, tree.path, "lbl", "python 86.0\n", GATE)
        self.assertEqual(
            (0, "lbl: python leg holds the floor 83.4 (86.0%)\n", ""),
            (status, out, err),
        )

    def test_a_derivation_writes_the_legs_line(self) -> None:
        """A floor with no record gains it at its next derivation."""
        with Tree(record("83.4")) as tree:
            status, _, _ = run(
                cf.derive,
                tree.path,
                "python 86.0\npython-branches 85.5\n",
                "v1.2.0",
                adopt=False,
            )
            parsed, problems = clean(tree.written())
        self.assertEqual((0, NO_PROBLEMS), (status, problems))
        self.assertEqual(("python", "python-branches"), parsed.legs)

    def test_a_derivation_that_drops_a_leg_fails_without_dropped(self) -> None:
        """A recorded leg the run did not measure refuses the derivation."""
        text = legs_record("83.4", "python plpgsql")
        with Tree(text) as tree:
            status, _, err = run(
                cf.derive, tree.path, "python 86.0\n", "v1.2.0", adopt=False
            )
            after = tree.text()
        self.assertEqual(1, status)
        self.assertEqual(text, after)
        self.assertEqual(
            f"{TASK}: the floor records plpgsql and this derivation"
            " measured none. A leg leaves the record only on purpose: restore"
            " what it measures, or add `# dropped: plpgsql <reason>` to"
            f" {tree.path}\n",
            err,
        )

    def test_a_derivation_that_drops_a_leg_passes_with_dropped(self) -> None:
        """With its reason recorded, the leg leaves the record."""
        text = legs_record("83.4", "python plpgsql", "# dropped: plpgsql moved out")
        with Tree(text) as tree:
            status, _, err = run(
                cf.derive, tree.path, "python 86.0\n", "v1.2.0", adopt=False
            )
            parsed, _ = clean(tree.written())
        self.assertEqual((0, ""), (status, err))
        self.assertEqual(("python",), parsed.legs)

    def test_the_record_lines_are_held_to_their_shape(self) -> None:
        """A legs or dropped line the derivation could not have written."""
        for name, extra, expected in (
            ("empty legs", "# legs:", "records `legs:` with no leg"),
            ("a duplicate", "# legs: python python", "is not a list of distinct"),
            ("a separate leg", "# legs: rust-branches", "is not a list of distinct"),
            ("no reason", "# dropped: plpgsql", "is not `dropped: <leg> <reason>`"),
        ):
            with self.subTest(name):
                text = record("83.4").replace("83.4\n", f"{extra}\n83.4\n")
                parsed, problems = cf.parse(text)
                self.assertIsNone(parsed)
                self.assertEqual(1, len(problems), problems)
                self.assertIn(expected, problems[0])

    def test_no_floor_holds_nothing(self) -> None:
        """Without a floor file there is nothing to hold or demand."""
        with Tree(None) as tree:
            self.assertEqual((0, "", ""), run(cf.hold, tree.path, "lbl", "", GATE))

    def test_legs_prints_the_record(self) -> None:
        """One leg a line; nothing for no file or no record."""
        with Tree(legs_record("83.4", "python plpgsql")) as tree:
            self.assertEqual(
                (0, "python\nplpgsql\n", ""), run(cf.recorded_legs, tree.path)
            )
        with Tree(record("83.4")) as tree:
            self.assertEqual((0, "", ""), run(cf.recorded_legs, tree.path))
        with Tree(None) as tree:
            self.assertEqual((0, "", ""), run(cf.recorded_legs, tree.path))


def owned_record(legs: str) -> str:
    """Build a floor of 90 that records `legs` (#1875).

    Returns:
        The file text.

    """
    return record("90", measured="92.0").replace("\n90\n", f"\n# legs: {legs}\n90\n")


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
                (0, "lbl: rust leg holds the floor 90 (99%)\n", ""),
            ),
            (
                "gate scope: a recorded gate leg not measured is red",
                "rust python",
                "rust 99\n",
                GATE,
                (
                    1,
                    "lbl: rust leg holds the floor 90 (99%)\n",
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
                (0, "lbl: pgrx-pg15 leg holds the floor 90 (95%)\n", ""),
            ),
            (
                "pgrx scope: its major below the floor is red",
                "rust pgrx-pg15",
                "pgrx-pg15 80\n",
                PGRX15,
                (1, "", f"{TASK}: pgrx-pg15: 80% is below the derived floor 90%\n"),
            ),
            (
                "pgrx scope: another major, and the gate's legs, are not its own",
                "rust pgrx-pg15 pgrx-pg16",
                "pgrx-pg15 95\n",
                PGRX15,
                (0, "lbl: pgrx-pg15 leg holds the floor 90 (95%)\n", ""),
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
        """A dropped leg needs no owner; no floor, or no record, needs none."""
        dropped = owned_record("rust pgrx-pg14").replace(
            "\n90\n", "\n# dropped: pgrx-pg14 postgres 14 dropped\n90\n"
        )
        for name, text in (
            ("dropped", dropped),
            ("no record", record("90", measured="92.0")),
            ("no floor", None),
        ):
            with self.subTest(name), Tree(text) as tree:
                self.assertEqual((0, "", ""), run(cf.planned, tree.path, '["15"]'))

    def test_the_plan_refuses_a_floor_off_its_record(self) -> None:
        """Drift is the same refusal in the plan as everywhere else."""
        with Tree(record("88.0")) as tree:
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
        """`pgrx-pgNN` strictly is the pgrx job; every other leg the gate's.

        Every `fuzz-*` leg among them (#1896): coverage:check in the gate
        job measures it.
        """
        for leg, job in (
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
            ("fuzz-parse-marker", "gate"),
            ("fuzz-internal-policy-load", "gate"),
        ):
            with self.subTest(leg):
                self.assertEqual(job, cf.owner(leg))

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
                held = run(
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
                    "--label",
                    "plan",
                    "--majors-json",
                    '["16"]',
                ],
            )
        self.assertEqual((0, "lbl: pgrx-pg15 leg holds the floor 90 (95%)\n", ""), held)
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
                    "--label",
                    "plan",
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
        self.assertIn('planned \\\n  --label plan --majors-json "${majors}"\n', body)


def fuzz_line(leg: str, floor: str, measured: str) -> str:
    """Render a fuzz target's section by hand (#1896).

    Returns:
        The record line and the floor line.

    """
    return f"# {leg} measured: {measured}\n{leg} {floor}\n"


MARKER = "fuzz-parse-marker"
NO_LINES: dict[str, tuple[Decimal, Decimal]] = {}
LOAD = "fuzz-internal-policy-load"
FUZZ_NEEDS = REMEDY.format(needs=cf.NEEDS["fuzz"], leg=MARKER)


class TestFuzzLegs(unittest.TestCase):
    """Each fuzz target is a floor line of its own, held in the gate (#1896)."""

    def test_render_round_trips_every_target(self) -> None:
        """Each target's line parses back to the derivation it states."""
        text = cf.render(
            Decimal("93.0"),
            "coverage:adopt",
            {MARKER: Decimal(100), LOAD: Decimal("43.69")},
            ["go"],
        )
        parsed, problems = clean(text)
        self.assertEqual(problems, NO_PROBLEMS)
        lines: dict[str, tuple[Decimal, Decimal]] = {
            LOAD: (Decimal("41.6"), Decimal("43.6")),
            MARKER: (Decimal(90), Decimal("100.0")),
        }
        self.assertEqual(lines, separate(parsed))
        self.assertIn(fuzz_line(LOAD, "41.6", "43.6"), text)

    def test_a_target_is_not_in_the_minimum(self) -> None:
        """A shallow corpus does not drag the main floor down."""
        with Tree(None) as tree:
            status, out, err = run(
                cf.derive,
                tree.path,
                f"go 93.0\n{LOAD} 43.6\n",
                "coverage:adopt",
                adopt=True,
            )
            parsed, _ = clean(tree.written())
        self.assertEqual((0, ""), (status, err))
        self.assertEqual(
            f"coverage-floor: adopted {tree.path} at 90 (measured 93.0),"
            f" {LOAD} 41.6 (measured 43.6)\n",
            out,
        )
        self.assertEqual((Decimal(90), ("go",)), (parsed.floor, parsed.legs))

    def test_a_hand_edited_line_is_drift(self) -> None:
        """A line typed over its record is refused, never repaired."""
        parsed, problems = cf.parse(record("83.4") + fuzz_line(MARKER, "50", "43.6"))
        self.assertIsNone(parsed)
        refused: list[str] = [
            (
                f"{MARKER} floor 50 disagrees with its own record: measured"
                " 43.6 under band 2 and the cap 90 derives 41.6. Refused as"
                " drift and never repaired — restore the derivation, or"
                " delete both lines and re-derive with `mise run"
                " coverage:adopt`"
            )
        ]
        self.assertEqual(refused, problems)

    def test_a_target_with_no_line_is_red_naming_it(self) -> None:
        """Measured and unrecorded: red, the remedy coverage:adopt."""
        with Tree(record("83.4")) as tree:
            status, out, err = run(
                cf.hold, tree.path, "lbl", f"rust 86.0\n{MARKER} 100\n", GATE
            )
        self.assertEqual(1, status)
        self.assertEqual("lbl: rust leg holds the floor 83.4 (86.0%)\n", out)
        self.assertEqual(
            f"{TASK}: {MARKER}: the fuzz target has no line in {tree.path} —"
            " every fuzz target is a leg of the floor (#1896): run `mise run"
            f" coverage:adopt` and commit {tree.path}\n",
            err,
        )

    def test_the_bare_fuzz_leg_is_held_apart(self) -> None:
        """`fuzz-`, a root target named exactly `Fuzz`, is a fuzz leg (#1899)."""
        bare = "fuzz-"
        with Tree(None) as tree:
            status, _, err = run(
                cf.derive,
                tree.path,
                f"go 93.0\n{bare} 40.0\n",
                "coverage:adopt",
                adopt=True,
            )
            parsed, _ = clean(tree.written())
        self.assertEqual((0, ""), (status, err))
        # Not in the minimum: the main floor is go's alone.
        self.assertEqual((Decimal(90), ("go",)), (parsed.floor, parsed.legs))
        self.assertIn(bare, separate(parsed))
        with Tree(record("83.4")) as tree:
            status, out, err = run(
                cf.hold, tree.path, "lbl", f"rust 86.0\n{bare} 40.0\n", GATE
            )
        self.assertEqual(1, status)
        self.assertEqual("lbl: rust leg holds the floor 83.4 (86.0%)\n", out)
        self.assertEqual(
            f"{TASK}: {bare}: the fuzz target has no line in {tree.path} —"
            " every fuzz target is a leg of the floor (#1896): run `mise run"
            f" coverage:adopt` and commit {tree.path}\n",
            err,
        )

    def test_a_target_is_held_to_its_own_line(self) -> None:
        """Above the main floor is not enough: below its own line is red."""
        text = record("83.4") + fuzz_line(MARKER, "88", "90.0")
        for name, figure, want in (
            (
                "holds",
                "90.0",
                (0, f"lbl: {MARKER} leg holds its floor 88 (90.0%)\n", ""),
            ),
            (
                "below",
                "87.9",
                (1, "", f"{TASK}: {MARKER}: 87.9% is below its derived floor 88%\n"),
            ),
        ):
            with self.subTest(name), Tree(text) as tree:
                self.assertEqual(
                    want, run(cf.hold, tree.path, "lbl", f"{MARKER} {figure}\n", GATE)
                )

    def test_a_recorded_target_the_gate_measures_none_of_is_red(self) -> None:
        """The gate job owns the line; a pgrx job does not judge it."""
        text = record("83.4") + fuzz_line(MARKER, "88", "90.0")
        with Tree(text) as tree:
            gate = run(cf.hold, tree.path, "lbl", "", GATE)
            pgrx = run(cf.hold, tree.path, "lbl", "", PGRX15)
        self.assertEqual(
            (
                1,
                "",
                f"{TASK}: the floor was derived with {MARKER} and this run measured"
                " none\n" + FUZZ_NEEDS,
            ),
            gate,
        )
        self.assertEqual((0, "", ""), pgrx)

    def test_a_vanished_target_leaves_only_through_dropped(self) -> None:
        """A derivation without the target refuses; with `dropped:` the line goes."""
        text = record("83.4") + fuzz_line(MARKER, "88", "90.0")
        with Tree(text) as tree:
            status, _, err = run(cf.derive, tree.path, "rust 86.0\n", "v1", adopt=False)
        self.assertEqual(1, status)
        self.assertEqual(
            f"{TASK}: the floor records {MARKER} and this derivation"
            " measured none. A leg leaves the record only on purpose: restore"
            f" what it measures, or add `# dropped: {MARKER} <reason>` to"
            f" {tree.path}\n",
            err,
        )
        why = f"# dropped: {MARKER} target removed\n"
        dropped = text.replace("83.4\n", f"{why}83.4\n", 1)
        with Tree(dropped) as tree:
            status, _, err = run(cf.derive, tree.path, "rust 86.0\n", "v1", adopt=False)
            parsed, _ = clean(tree.written())
        self.assertEqual((0, ""), (status, err))
        self.assertEqual(NO_LINES, separate(parsed))

    def test_the_legs_record_never_names_a_target(self) -> None:
        """A target keeps its own line; `legs:` naming one is refused."""
        parsed, problems = cf.parse(legs_record("83.4", f"go {MARKER}"))
        self.assertIsNone(parsed)
        self.assertEqual(1, len(problems), problems)
        self.assertIn("is not a list of distinct main-floor legs", problems[0])


if __name__ == "__main__":
    unittest.main()
