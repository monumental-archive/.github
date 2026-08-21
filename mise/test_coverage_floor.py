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

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Self

_SPEC = importlib.util.spec_from_file_location(
    "coverage_floor",
    Path(__file__).with_name("coverage-floor.py"),
)
cf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cf)


def record(
    floor: str,
    band: str = "2",
    measured: str | None = "95.4",
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
            ("two bare lines", "# band: 2\n# measured: 95.4\n93.4\n90\n", "2 floors"),
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
            ("no band at all", record("93.4", band=None), "states no band"),
            (
                "band not a number",
                record("93.4", band="two"),
                "is not a number of points",
            ),
            (
                "negative band",
                record("97.4", band="-2"),
                "is not a number of points",
            ),
            (
                "measured and reset together",
                record("88", measured="95.4", reset="a reason"),
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
                record("93.4", measured="high"),
                "is not a percentage",
            ),
            (
                "floor disagrees with the record",
                record("85", measured="95.4"),
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
        parsed, problems = cf.parse(record("93.4"))
        self.assertEqual(problems, [])
        self.assertEqual(parsed.floor, Decimal("93.4"))
        self.assertEqual(parsed.measured, Decimal("95.4"))
        self.assertEqual(parsed.band, Decimal(2))
        self.assertEqual(parsed.derived, "v0.20.0")
        self.assertIsNone(parsed.reset)

    def test_trailing_zeros_are_the_same_number(self) -> None:
        """Agreement is numeric, so 93.40 and 93.4 are one floor."""
        parsed, problems = cf.parse(record("93.40"))
        self.assertEqual(problems, [])
        self.assertEqual(parsed.floor, Decimal("93.4"))

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
        parsed, problems = cf.parse(record("92.4", band="3"))
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
        self.assertEqual(cf.number("95.4"), Decimal("95.4"))

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
        self.assertEqual(cf.quantise(Decimal("95.4")), Decimal("95.4"))


class TestLegs(unittest.TestCase):
    """The measurement reader: `<leg> <percent>` and nothing else."""

    def test_reads_every_leg(self) -> None:
        """Each language the measurement printed comes back."""
        measured, problems = cf.legs("go 95.4\nrust 61.538\n")
        self.assertEqual(problems, [])
        self.assertEqual(
            measured,
            [("go", Decimal("95.4")), ("rust", Decimal("61.538"))],
        )

    def test_blank_lines_are_not_legs(self) -> None:
        """Trailing newlines do not become a leg."""
        measured, problems = cf.legs("\ngo 95.4\n\n")
        self.assertEqual(problems, [])
        self.assertEqual(measured, [("go", Decimal("95.4"))])

    def test_malformed_lines_are_reported_not_skipped(self) -> None:
        """A line that is not a measurement refuses the whole derivation.

        Dropping it quietly would write a floor over a language nothing
        measured — the vacuous green the ratchet exists to prevent.
        """
        for line in ("go", "go 95.4 extra", "go high", "go 140"):
            with self.subTest(line):
                measured, problems = cf.legs(line)
                self.assertEqual(measured, [])
                self.assertEqual(len(problems), 1)
                self.assertIn("is not `<leg> <percent>`", problems[0])


class TestRender(unittest.TestCase):
    """What the machinery writes, read back through the parser."""

    def test_render_round_trips(self) -> None:
        """The written file parses, and states the band it was cut with."""
        text = cf.render(Decimal("95.44"), "v1.52.0")
        parsed, problems = cf.parse(text)
        self.assertEqual(problems, [])
        self.assertEqual(parsed.measured, Decimal("95.4"))
        self.assertEqual(parsed.floor, Decimal("93.4"))
        self.assertEqual(parsed.band, cf.BAND)
        self.assertEqual(parsed.derived, "v1.52.0")

    def test_render_states_the_band_and_the_decision(self) -> None:
        """The band is stated as a comment naming #652, as decided."""
        text = cf.render(Decimal("95.4"), "v1.52.0")
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
        with Tree(record("93.4")) as tree:
            status, out, err = run(cf.read, tree.path)
        self.assertEqual(status, 0)
        self.assertEqual(out.strip(), "93.4")
        self.assertEqual(err, "")

    def test_hand_edited_floor_is_refused_as_drift(self) -> None:
        """A number typed over the derivation reds the gate by name."""
        with Tree(record("85", measured="95.4")) as tree:
            status, out, err = run(cf.read, tree.path)
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("disagrees with its own record", err)
        self.assertIn("never repaired", err)


class TestWriteRatchet(unittest.TestCase):
    """The release path's half, planted in both directions."""

    def test_healthy_release_raises_the_floor(self) -> None:
        """A ceiling that held rewrites the floor upwards."""
        with Tree(record("93.4", measured="95.4")) as tree:
            status, out, err = run(
                cf.write,
                tree.path,
                Decimal("96.8"),
                "v0.21.0",
                adopt=False,
            )
            parsed, problems = cf.parse(tree.text())
        self.assertEqual(status, 0)
        self.assertEqual(err, "")
        self.assertIn("93.4 -> 94.8", out)
        self.assertEqual(problems, [])
        self.assertEqual(parsed.floor, Decimal("94.8"))
        self.assertEqual(parsed.measured, Decimal("96.8"))
        self.assertEqual(parsed.derived, "v0.21.0")

    def test_below_the_band_fails_the_release(self) -> None:
        """A drop refuses loudly and changes nothing on disk."""
        before = record("93.4", measured="95.4")
        with Tree(before) as tree:
            status, out, err = run(
                cf.write,
                tree.path,
                Decimal("94.9"),
                "v0.21.0",
                adopt=False,
            )
            after = tree.text()
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("is below the floor", err)
        self.assertIn("The floor only", err)
        self.assertEqual(after, before)

    def test_exactly_at_the_band_holds_the_floor(self) -> None:
        """The boundary passes, and the floor stays where it was.

        "Only rises" means never falls: an unchanged ceiling is not a
        drop, and refusing it would make every quiet release red.
        """
        with Tree(record("93.4", measured="95.4")) as tree:
            status, _, err = run(
                cf.write,
                tree.path,
                Decimal("95.4"),
                "v0.21.0",
                adopt=False,
            )
            parsed, _ = cf.parse(tree.text())
        self.assertEqual(status, 0)
        self.assertEqual(err, "")
        self.assertEqual(parsed.floor, Decimal("93.4"))

    def test_one_tenth_below_the_band_fails(self) -> None:
        """The refusal is on the boundary, not near it."""
        with Tree(record("93.4", measured="95.4")) as tree:
            status, _, err = run(
                cf.write,
                tree.path,
                Decimal("95.3"),
                "v0.21.0",
                adopt=False,
            )
        self.assertEqual(status, 1)
        self.assertIn("is below the floor", err)

    def test_measurement_is_rounded_down_before_the_ratchet(self) -> None:
        """The compared number is the recorded one, not a wider float."""
        with Tree(record("93.4", measured="95.4")) as tree:
            status, _, err = run(
                cf.write,
                tree.path,
                Decimal("95.39999"),
                "v0.21.0",
                adopt=False,
            )
        self.assertEqual(status, 1)
        self.assertIn("measured 95.3%", err)

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


class TestWriteAdoption(unittest.TestCase):
    """Who may write a floor, and who may not."""

    def test_release_skips_a_repository_with_no_floor(self) -> None:
        """Opt-in: no file, nothing written, nothing owed."""
        with Tree(None) as tree:
            status, out, err = run(
                cf.write,
                tree.path,
                Decimal("95.4"),
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
                Decimal("95.44"),
                "coverage:adopt",
                adopt=True,
            )
            parsed, problems = cf.parse(tree.text())
        self.assertEqual(status, 0)
        self.assertIn("adopted", out)
        self.assertEqual(problems, [])
        self.assertEqual(parsed.floor, Decimal("93.4"))

    def test_release_refuses_to_repair_a_recordless_floor(self) -> None:
        """The machinery does not silently adopt a floor it did not write."""
        with Tree("90\n") as tree:
            status, _, err = run(
                cf.write,
                tree.path,
                Decimal("95.4"),
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
                Decimal("95.4"),
                "coverage:adopt",
                adopt=True,
            )
            parsed, problems = cf.parse(tree.text())
        self.assertEqual(status, 0)
        self.assertIn("rewrote", out)
        self.assertEqual(problems, [])
        self.assertEqual(parsed.floor, Decimal("93.4"))

    def test_adopt_cannot_lower_a_floor_that_parses(self) -> None:
        """`fix`-shaped is not an escape hatch from the ratchet."""
        before = record("93.4", measured="95.4")
        with Tree(before) as tree:
            status, _, err = run(
                cf.write,
                tree.path,
                Decimal("91.0"),
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
                "go 95.4\nrust 61.5\n",
                "v0.21.0",
                adopt=False,
            )
            parsed, _ = cf.parse(tree.text())
        self.assertEqual(status, 0)
        self.assertIn("measured 61.5", out)
        self.assertEqual(parsed.floor, Decimal("59.5"))

    def test_no_measurement_refuses(self) -> None:
        """A derivation that reads nothing must not write a floor."""
        with Tree(record("93.4")) as tree:
            status, _, err = run(cf.derive, tree.path, "\n", "v0.21.0", adopt=False)
            after = tree.text()
        self.assertEqual(status, 1)
        self.assertIn("no leg was measured", err)
        self.assertEqual(after, record("93.4"))

    def test_a_malformed_leg_refuses(self) -> None:
        """One unreadable line refuses rather than deriving from the rest."""
        with Tree(record("93.4")) as tree:
            status, _, err = run(
                cf.derive,
                tree.path,
                "go 95.4\nrust ???\n",
                "v0.21.0",
                adopt=False,
            )
        self.assertEqual(status, 1)
        self.assertIn("is not `<leg> <percent>`", err)


class TestMain(unittest.TestCase):
    """The command line both callers actually invoke."""

    def test_read_dispatches(self) -> None:
        """`read` prints the floor for coverage:check to enforce."""
        with Tree(record("93.4")) as tree:
            status, out, _ = run(cf.main, ["--path", str(tree.path), "read"])
        self.assertEqual(status, 0)
        self.assertEqual(out.strip(), "93.4")

    def test_write_reads_the_legs_from_stdin(self) -> None:
        """The belt convention: the task enumerates, the helper consumes."""
        with Tree(record("93.4")) as tree:
            stdin = sys.stdin
            sys.stdin = io.StringIO("go 96.8\n")
            try:
                status, out, _ = run(
                    cf.main,
                    ["--path", str(tree.path), "write", "--provenance", "v0.21.0"],
                )
            finally:
                sys.stdin = stdin
            parsed, _ = cf.parse(tree.text())
        self.assertEqual(status, 0)
        self.assertIn("93.4 -> 94.8", out)
        self.assertEqual(parsed.floor, Decimal("94.8"))


if __name__ == "__main__":
    unittest.main()
