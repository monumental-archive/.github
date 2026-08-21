#!/usr/bin/env python3
"""Read, and re-derive, the committed coverage floor.

Org canon — the mechanism half of #652. The floor stopped being a number
a human types the day the org measured what that costs: stele's ceiling
fell from 98.6 to 92.1 in two days with four features landed, nothing
red and nothing noticed, because the committed floor sat well below
where anyone would have set it and the headroom became a landing zone.

So the floor is DERIVED STATE. The release machinery measures the tree,
writes `floor = measured - band`, and the floor only ever rises; a
release measuring below `floor + band` fails loudly rather than lowering
anything. Between releases a pull request may spend the band — that is
what the band is for — but by release time the ceiling must be back at
or above the previous release's measurement, and the session that spent
it writes the tests while the reason is still in context.

Two entry points, one definition:

  read   `coverage:check` asks for the number to enforce. The file's own
         record is checked first: a floor that disagrees with the
         derivation it claims is refused as DRIFT, never repaired, on the
         reasoning that a floor found wrong is evidence the machinery was
         bypassed.
  write  the release path (`release/derive-coverage-floor.sh`) and its
         write-mode sibling `coverage:adopt` hand over a fresh
         measurement, one `<leg> <percent>` line per language on stdin —
         the belt convention, where the task enumerates and the helper
         consumes. The ratchet is applied here, so both callers get the
         same law from the same code.

Opt-in is unchanged: a repository with no `.coverage-floor` owes nothing
and skips clean in both directions. The belt offers the mechanism; the
committed file is the repository's adoption and its number is the
repository's choice.
"""

import argparse
import sys
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple

NAME = "coverage-floor"
DEFAULT_PATH = Path(".coverage-floor")

# The band, pinned here because it is org policy rather than a repository
# setting: two points of working room between releases. It is stated in
# every file this script writes so the number is readable where it
# applies, but the RATCHET reads this constant — a band a repository
# could widen by editing its own floor file is not a policy, it is a
# suggestion. A file recording a different band still parses (the
# agreement law below uses the file's own band, so changing this constant
# is not a flag day); the next release rewrites it.
BAND = Decimal(2)

# One decimal place, rounded DOWN. Go's `go tool cover` already reports
# one; cargo-llvm-cov reports a full float (61.53846153846154, measured
# on a fixture crate). Rounding down keeps the recorded measurement a
# truthful lower bound of what was measured, and quantising before both
# the ratchet and the record means the number that is compared is the
# number that is written — one value, not two that agree until they do
# not.
STEP = Decimal("0.1")

FLOOR_MIN = Decimal(0)
FLOOR_MAX = Decimal(100)

# The record fields, in the order a written file states them.
BAND_KEY = "band"
MEASURED_KEY = "measured"
DERIVED_KEY = "derived"
RESET_KEY = "reset"
KEYS = frozenset({BAND_KEY, MEASURED_KEY, DERIVED_KEY, RESET_KEY})

LEG_FIELDS = 2

TEMPLATE = """\
# Coverage floor -- DERIVED STATE, not a number to edit (#652).
#
# The release machinery re-derives this file at every release:
# floor = measured - band, and the floor only rises. A release whose
# measurement falls below floor + band FAILS, loudly; the resolution is
# tests, or a conscious reset recorded in place of the derivation below
# (replace the `measured` and `derived` lines with `reset: <reason>`).
# `coverage:check` refuses a floor that disagrees with this record as
# drift, and never repairs it.
#
# band: {band}
# measured: {measured}
# derived: {provenance}
{floor}
"""


class Record(NamedTuple):
    """A parsed `.coverage-floor`: the number, and how it was arrived at."""

    floor: Decimal
    band: Decimal
    measured: Decimal | None
    derived: str | None
    reset: str | None


def quantise(value: Decimal) -> Decimal:
    """Round a measurement down to the recorded precision.

    Returns:
        The value at one decimal place, never rounded up.

    """
    return value.quantize(STEP, rounding=ROUND_DOWN)


def number(text: str) -> Decimal | None:
    """Read a decimal the way this file writes them.

    Returns:
        The value, or None when the text is not a plain finite decimal.

    """
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return value


def fields(text: str) -> tuple[dict[str, str], list[str]]:
    """Split a floor file into its comment record and its bare lines.

    Returns:
        The `key: value` pairs stated in comments, and every non-comment,
        non-blank line in the order they appear.

    """
    record: dict[str, str] = {}
    bare: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not line.startswith("#"):
            bare.append(line)
            continue
        key, sep, value = line.lstrip("#").strip().partition(":")
        if sep and key.strip() in KEYS:
            record[key.strip()] = value.strip()
    return record, bare


def floor_of(bare: list[str]) -> tuple[Decimal | None, list[str]]:
    """Read the one bare number line a floor file carries.

    Returns:
        The floor, or None with the problem that refused it.

    """
    if len(bare) != 1:
        counted = "no floor" if not bare else f"{len(bare)} floors"
        return None, [
            (
                f"states {counted}: the file carries exactly one bare "
                "number line, and everything else is the comment record"
            ),
        ]
    floor = number(bare[0])
    if floor is None:
        return None, [f"floor {bare[0]!r} is not a number"]
    if not FLOOR_MIN <= floor <= FLOOR_MAX:
        return None, [f"floor {floor} is not a percentage between 0 and 100"]
    return floor, []


def band_of(record: dict[str, str]) -> tuple[Decimal | None, list[str]]:
    """Read the band the file was derived under.

    The file's own band, not this script's: changing the org's band is
    then a rewrite at the next release rather than a flag day across
    every repository that has adopted a floor.

    Returns:
        The band, or None with the problem that refused it.

    """
    stated = record.get(BAND_KEY)
    if stated is None:
        return None, [
            (
                "states no band. The floor is derived state (#652) and "
                "this file carries no derivation record — write one from "
                "a real measurement with `mise run coverage:adopt`"
            ),
        ]
    band = number(stated)
    if band is None or band < 0:
        return None, [f"band {stated!r} is not a number of points"]
    return band, []


def reset_form(
    record: dict[str, str],
    floor: Decimal,
    band: Decimal,
) -> tuple[Record | None, list[str]]:
    """Read a floor a human consciously re-set.

    Returns:
        The record, or None with the problem that refused it.

    """
    reset = record[RESET_KEY]
    if not reset:
        return None, [
            (
                "records a reset with no reason. A conscious reset is "
                "conscious because it is recorded: `reset: <why>`"
            ),
        ]
    return Record(floor, band, None, record.get(DERIVED_KEY), reset), []


def derived_form(
    record: dict[str, str],
    floor: Decimal,
    band: Decimal,
) -> tuple[Record | None, list[str]]:
    """Read a floor the machinery derived, and hold it to its own record.

    Returns:
        The record, or None with the drift that refused it.

    """
    measured = number(record[MEASURED_KEY])
    if measured is None or not FLOOR_MIN <= measured <= FLOOR_MAX:
        return None, [f"measured {record[MEASURED_KEY]!r} is not a percentage"]

    expected = max(FLOOR_MIN, measured - band)
    if floor != expected:
        return None, [
            (
                f"floor {floor} disagrees with its own record: measured "
                f"{measured} minus band {band} is {expected}. A floor "
                "found wrong is evidence the machinery was bypassed, so "
                "this is refused as drift and never repaired — restore "
                "the derivation, or record a conscious reset by replacing "
                "the `measured` and `derived` lines with `reset: <why>`"
            ),
        ]
    return Record(floor, band, measured, record.get(DERIVED_KEY), None), []


def parse(text: str) -> tuple[Record | None, list[str]]:
    """Read a floor file and hold it to the derived-state law.

    Returns:
        The record, or None with the problems that refused it. Every
        problem is a complete sentence naming its own remedy: this output
        is what a session sees when the gate goes red.

    """
    record, bare = fields(text)

    floor, problems = floor_of(bare)
    if floor is None:
        return None, problems
    band, problems = band_of(record)
    if band is None:
        return None, problems

    has_measured = MEASURED_KEY in record
    has_reset = RESET_KEY in record
    if has_measured and has_reset:
        return None, [
            (
                "records both a measurement and a reset. A reset states "
                "no measurement — it is a human overriding the derivation "
                "— so delete either the `measured` and `derived` lines or "
                "the `reset` one"
            ),
        ]
    if has_reset:
        return reset_form(record, floor, band)
    if has_measured:
        return derived_form(record, floor, band)
    return None, [
        (
            "carries no derivation record. The floor is derived state "
            "(#652): write the record from a real measurement with "
            "`mise run coverage:adopt`, or record a conscious reset with "
            "`reset: <why>`"
        ),
    ]


def legs(text: str) -> tuple[list[tuple[str, Decimal]], list[str]]:
    """Read the `<leg> <percent>` lines a measurement produced.

    Returns:
        The measured legs, and the problems with any line that was not
        one. A malformed line is never skipped: a derivation that quietly
        drops the leg it could not read writes a floor over a language
        nothing measured.

    """
    measured: list[tuple[str, Decimal]] = []
    problems: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        percent = number(parts[1]) if len(parts) == LEG_FIELDS else None
        if percent is None or not FLOOR_MIN <= percent <= FLOOR_MAX:
            problems.append(f"measurement {line!r} is not `<leg> <percent>`")
            continue
        measured.append((parts[0], percent))
    return measured, problems


def render(measured: Decimal, provenance: str) -> str:
    """Write the file the derivation describes.

    Returns:
        The whole file, band comment and all.

    """
    quantised = quantise(measured)
    return TEMPLATE.format(
        band=BAND,
        measured=quantised,
        provenance=provenance,
        floor=max(FLOOR_MIN, quantised - BAND),
    )


def complain(problems: list[str], path: Path) -> None:
    """Print every reason the file was refused, never only the first."""
    for problem in problems:
        print(f"{NAME}: {path} {problem}", file=sys.stderr)


def read(path: Path) -> int:
    """Print the floor `coverage:check` must enforce.

    Returns:
        0 with the floor on stdout, 0 with an empty stdout when the
        repository has adopted no floor, and 1 when the committed file
        does not hold to the derived-state law.

    """
    if not path.exists():
        return 0
    record, problems = parse(path.read_text(encoding="utf-8"))
    if record is None:
        complain(problems, path)
        return 1
    print(record.floor)
    return 0


def write(path: Path, measured: Decimal, provenance: str, *, adopt: bool) -> int:
    """Re-derive the floor from a fresh measurement.

    Returns:
        0 when the file was written or the repository owes nothing, and 1
        when the ratchet refuses or the committed file is not the
        machinery's to overwrite.

    """
    quantised = quantise(measured)
    derived = max(FLOOR_MIN, quantised - BAND)

    if not path.exists():
        if not adopt:
            print(f"{NAME}: no {path}, skipped")
            return 0
        path.write_text(render(measured, provenance), encoding="utf-8")
        print(f"{NAME}: adopted {path} at {derived} (measured {quantised})")
        return 0

    record, problems = parse(path.read_text(encoding="utf-8"))
    if record is None:
        if not adopt:
            complain(problems, path)
            print(
                f"{NAME}: a release does not repair a floor it did not "
                "write — fix the file, then release",
                file=sys.stderr,
            )
            return 1
        # `fix:*` is the write-mode channel and a human running it IS the
        # conscious act, so a recordless or drifted floor is exactly what
        # it exists to migrate. It still cannot lower a floor whose record
        # parses: that path falls through to the ratchet below.
        path.write_text(render(measured, provenance), encoding="utf-8")
        print(f"{NAME}: rewrote {path} at {derived} (measured {quantised})")
        return 0

    if quantised < record.floor + BAND:
        print(
            f"{NAME}: measured {quantised}% is below the floor "
            f"{record.floor}% plus the {BAND}-point band. The floor only "
            "rises, so this release FAILS rather than lowering it: write "
            f"the tests that put the ceiling back to {record.floor + BAND}%, "
            "or consciously reset the floor by replacing the `measured` "
            f"and `derived` lines of {path} with `reset: <why>`",
            file=sys.stderr,
        )
        return 1

    # The ratchet passed, so `measured - band >= floor` holds by
    # arithmetic: the new floor cannot be below the old one. Asserting
    # that again here would be a second definition of the same law, with
    # its own way of being wrong.
    path.write_text(render(measured, provenance), encoding="utf-8")
    print(f"{NAME}: floor {record.floor} -> {derived} (measured {quantised})")
    return 0


def derive(path: Path, source: str, provenance: str, *, adopt: bool) -> int:
    """Take the weakest measured leg and re-derive the floor from it.

    Returns:
        A process exit status.

    """
    measured, problems = legs(source)
    if problems:
        complain(problems, path)
        return 1
    if not measured:
        print(
            f"{NAME}: no leg was measured — a derivation that reads "
            "nothing must not write a floor",
            file=sys.stderr,
        )
        return 1
    # The weakest leg, because the floor is a minimum every leg must hold:
    # deriving from an average, or from the language that happens to be
    # measured last, writes a floor the gate will then refuse.
    weakest = min(percent for _, percent in measured)
    return write(path, weakest, provenance, adopt=adopt)


def main(argv: list[str] | None = None) -> int:
    """Run one subcommand over the repository's floor file.

    Returns:
        A process exit status.

    """
    parser = argparse.ArgumentParser(description="the derived coverage floor")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("read", help="print the floor to enforce, refusing drift")
    writer = sub.add_parser(
        "write",
        help="re-derive the floor from `<leg> <percent>` lines on stdin",
    )
    writer.add_argument("--provenance", required=True)
    writer.add_argument(
        "--adopt",
        action="store_true",
        help="write a floor this repository has none of, or migrate a recordless one",
    )
    args = parser.parse_args(argv)

    if args.command == "read":
        return read(args.path)
    return derive(args.path, sys.stdin.read(), args.provenance, adopt=args.adopt)


if __name__ == "__main__":
    sys.exit(main())
