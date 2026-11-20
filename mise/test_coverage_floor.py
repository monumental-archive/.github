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

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Self

import coverage_floor as cf


def record(
    floor: str,
    band: str = "2",
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


def run(call: object, *args: object, **kwargs: object) -> tuple[int, str, str]:
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
        parsed, problems = cf.parse(record("83.4"))
        self.assertEqual(problems, [])
        self.assertEqual(parsed.floor, Decimal("83.4"))
        self.assertEqual(parsed.measured, Decimal("85.4"))
        self.assertEqual(parsed.band, Decimal(2))
        self.assertEqual(parsed.derived, "v0.20.0")
        self.assertIsNone(parsed.reset)

    def test_blank_lines_are_not_lines(self) -> None:
        """A blank line anywhere in the file reads as nothing (#1659)."""
        parsed, problems = cf.parse("\n" + record("83.4").replace("\n", "\n\n"))
        self.assertEqual(problems, [])
        self.assertEqual(parsed.floor, Decimal("83.4"))

    def test_trailing_zeros_are_the_same_number(self) -> None:
        """Agreement is numeric, so 83.40 and 83.4 are one floor."""
        parsed, problems = cf.parse(record("83.40"))
        self.assertEqual(problems, [])
        self.assertEqual(parsed.floor, Decimal("83.4"))

    def test_reset_is_a_record(self) -> None:
        """A conscious reset states a reason and no measurement."""
        parsed, problems = cf.parse(
            record(
                "88",
                measured=None,
                reset="pgrx members left the workspace",
                derived=None,
            ),
        )
        self.assertEqual(problems, [])
        self.assertEqual(parsed.floor, Decimal(88))
        self.assertIsNone(parsed.measured)
        self.assertEqual(parsed.reset, "pgrx members left the workspace")

    def test_the_file_states_its_own_band(self) -> None:
        """A file recording a different band is read under that band.

        Changing the org's band is not a flag day: the agreement law uses
        the band the file was derived under, and the next release rewrites
        it with the current one.
        """
        parsed, problems = cf.parse(record("82.4", band="3"))
        self.assertEqual(problems, [])
        self.assertEqual(parsed.band, Decimal(3))

    def test_floor_clamps_at_zero(self) -> None:
        """A measurement below the band derives a floor of 0, not -0.5."""
        parsed, problems = cf.parse(record("0", measured="1.5"))
        self.assertEqual(problems, [])
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
        self.assertEqual(problems, [])
        self.assertEqual(
            measured,
            [("go", Decimal("85.4")), ("rust", Decimal("61.538"))],
        )

    def test_blank_lines_are_not_legs(self) -> None:
        """Trailing newlines do not become a leg."""
        measured, problems = cf.legs("\ngo 85.4\n\n")
        self.assertEqual(problems, [])
        self.assertEqual(measured, [("go", Decimal("85.4"))])

    def test_malformed_lines_are_reported_not_skipped(self) -> None:
        """A line that is not a measurement refuses the whole derivation.

        Dropping it quietly would write a floor over a language nothing
        measured — the vacuous green the ratchet exists to prevent.
        """
        for line in ("go", "go 85.4 extra", "go high", "go 140"):
            with self.subTest(line):
                measured, problems = cf.legs(line)
                self.assertEqual(measured, [])
                self.assertEqual(len(problems), 1)
                self.assertIn("is not `<leg> <percent>`", problems[0])


class TestRender(unittest.TestCase):
    """What the machinery writes, read back through the parser."""

    def test_render_round_trips(self) -> None:
        """The written file parses, and states the band it was cut with."""
        text = cf.render(Decimal("85.44"), "v1.52.0")
        parsed, problems = cf.parse(text)
        self.assertEqual(problems, [])
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
            parsed, problems = cf.parse(tree.text())
        self.assertEqual(status, 0)
        self.assertEqual(err, "")
        self.assertIn("83.4 -> 84.8", out)
        self.assertEqual(problems, [])
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
                    after = tree.text()
                    parsed, problems = cf.parse(after)
                self.assertEqual((0, ""), (status, err))
                self.assertIn(f"floor 83.4 held (measured {figure}", out)
                self.assertEqual([], problems)
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
            parsed, _ = cf.parse(tree.text())
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
            parsed, _ = cf.parse(tree.text())
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
            parsed, problems = cf.parse(tree.text())
        self.assertEqual((0, "", []), (status, err, problems))
        self.assertIn("floor 88 held", out)
        self.assertEqual(Decimal(88), parsed.floor)
        self.assertEqual("workspace split", parsed.reset)
        self.assertEqual(("python",), parsed.legs)


class TestCap(unittest.TestCase):
    """The cap (#1786): no floor is derived above 90."""

    def test_a_derivation_stops_at_the_cap(self) -> None:
        """A measurement of 95.4 writes a floor of 90, not 93.4."""
        parsed, problems = cf.parse(cf.render(Decimal("95.4"), "v1.71.0"))
        self.assertEqual([], problems)
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
            parsed, problems = cf.parse(tree.text())
        self.assertEqual((0, "", []), (status, err, problems))
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
            status, out, err = run(cf.hold, tree.path, "lbl", "python 91.0\n")
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
            parsed, problems = cf.parse(tree.text())
        self.assertEqual(status, 0)
        self.assertIn("adopted", out)
        self.assertEqual(problems, [])
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
            parsed, problems = cf.parse(tree.text())
        self.assertEqual(status, 0)
        self.assertIn("rewrote", out)
        self.assertEqual(problems, [])
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
            parsed, _ = cf.parse(tree.text())
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


class TestSeparateLeg(unittest.TestCase):
    """The `rust-branches` line (#1359): its own record, law and ratchet."""

    def test_render_round_trips_the_line(self) -> None:
        """A rendered branch line parses back to the derivation it states."""
        text = cf.render(
            Decimal("85.44"), "coverage:adopt", {"rust-branches": Decimal("72.49")}
        )
        parsed, problems = cf.parse(text)
        self.assertEqual(problems, [])
        self.assertEqual(parsed.floor, Decimal("83.4"))
        self.assertEqual(
            parsed.separate, {"rust-branches": (Decimal("70.4"), Decimal("72.4"))}
        )

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
            parsed, problems = cf.parse(tree.text())
        self.assertEqual(status, 0)
        self.assertEqual(problems, [])
        self.assertIn("83.4 -> 84.0", out)
        self.assertEqual(
            parsed.separate, {"rust-branches": (Decimal("70.4"), Decimal("72.4"))}
        )

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
            parsed, _ = cf.parse(tree.text())
        self.assertEqual(status, 0)
        self.assertEqual(parsed.floor, Decimal("84.0"))
        self.assertEqual(parsed.separate["rust-branches"][0], Decimal("59.0"))

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
            parsed, problems = cf.parse(tree.text())
        self.assertEqual((0, "", []), (status, err, problems))
        self.assertEqual(Decimal("84.0"), parsed.floor)
        self.assertEqual(
            parsed.separate, {"rust-branches": (Decimal("70.4"), Decimal("72.4"))}
        )

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
            parsed, _ = cf.parse(tree.text())
        self.assertEqual(status, 0)
        self.assertIn("rust-branches 78.0 (measured 80.0)", out)
        self.assertEqual(parsed.separate["rust-branches"][0], Decimal("78.0"))


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
            parsed, _ = cf.parse(tree.text())
        self.assertEqual(status, 0)
        self.assertIn("83.4 -> 84.8", out)
        self.assertEqual(parsed.floor, Decimal("84.8"))


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
                        cf.hold, tree.path, "coverage:check", "python 86.0\n"
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
                cf.hold, tree.path, "coverage:check", "python 86.0\nplpgsql 87.5\n"
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
                cf.hold, tree.path, "coverage:check", "python 86.0\nplpgsql 80.1\n"
            )
        self.assertEqual(1, status)
        self.assertEqual(
            "coverage:check: plpgsql: 80.1% is below the derived floor 83.4%\n", err
        )

    def test_a_dropped_leg_is_not_demanded(self) -> None:
        """A recorded leg consciously dropped is not red when it is gone."""
        text = legs_record("83.4", "python plpgsql", "# dropped: plpgsql moved out")
        with Tree(text) as tree:
            status, _, err = run(cf.hold, tree.path, "lbl", "python 86.0\n")
        self.assertEqual((0, ""), (status, err))

    def test_no_legs_record_demands_nothing(self) -> None:
        """Every floor today: held, none demanded, nothing named."""
        with Tree(record("83.4")) as tree:
            status, out, err = run(cf.hold, tree.path, "lbl", "python 86.0\n")
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
            parsed, problems = cf.parse(tree.text())
        self.assertEqual((0, []), (status, problems))
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
            "coverage-floor: the floor records plpgsql and this derivation"
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
            parsed, _ = cf.parse(tree.text())
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
            self.assertEqual((0, "", ""), run(cf.hold, tree.path, "lbl", ""))

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


if __name__ == "__main__":
    unittest.main()
