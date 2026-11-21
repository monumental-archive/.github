#!/usr/bin/env python3
"""Read, and re-derive, the committed coverage floor.

Org canon — the mechanism half of #652. The floor stopped being a number
a human types the day the org measured what that costs: stele's ceiling
fell from 98.6 to 92.1 in two days with four features landed, nothing
red and nothing noticed, because the committed floor sat well below
where anyone would have set it and the headroom became a landing zone.

So the floor is DERIVED STATE. The release machinery measures the tree,
writes `floor = min(cap, measured - band)`, and the floor never falls.
The release holds the same line the gate holds — the floor itself — so
a pull request that passes the gate can never red the release on main
(#1786; holding the release to `floor + band` did exactly that, three
pushes running, at 94.2 against 94.3). A release measuring within the
band keeps the floor and its record as they stand; one measuring higher
raises it. The cap, 90, is the org's line: above it coverage moves
freely, and the floor stops chasing the ceiling towards 100.

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

One leg is held apart (#1359): `rust-branches`, Rust branch coverage,
measured on a nightly and so never in the gate. It is a second line of
the same file, `rust-branches <floor>`, with its own `rust-branches
measured:` record under the same band and the same law — derived,
never typed, refused as drift, and only ever raised. It is not folded
into the minimum the main floor is cut from: `coverage:check` cannot
measure it, so the gate would be holding a number it never reads. The
Monday audit reads it (`read --leg rust-branches`); `coverage:adopt`
writes it; the release path, which measures on stable, carries it
through unchanged. `go-branches` (#1546), gobco's Go condition
coverage, is held apart the same way and for the same reason: the gate
never runs it, so it is the Monday audit's (`read --leg go-branches`).

Opt-in is unchanged: a repository with no `.coverage-floor` owes nothing
and skips clean in both directions. The belt offers the mechanism; the
committed file is the repository's adoption and its number is the
repository's choice.
"""

import argparse
import re
import sys
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple

import typed_std

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

# The cap (#1786, ruled 2026-11-20): no floor is derived above 90. Org
# policy like the band, so a constant here and never a repository
# setting. A floor written before the cap — above 90, agreeing with its
# record's uncapped derivation — is not drift: it is enforced at the cap
# and the next release rewrites it, so the canon bump reds no consumer.
FLOOR_CAP = Decimal(90)

# The record fields, in the order a written file states them.
BAND_KEY = "band"
MEASURED_KEY = "measured"
DERIVED_KEY = "derived"
RESET_KEY = "reset"
# The legs the floor holds (#1666): written by every derivation, read by
# `coverage:check`, and left only through a `dropped:` line.
LEGS_KEY = "legs"
DROPPED_KEY = "dropped"
LEG_NAME = re.compile(r"[a-z][a-z0-9-]*")
# The legs held as floors of their own rather than in the minimum.
SEPARATE = ("rust-branches", "go-branches")
KEYS = frozenset(
    {BAND_KEY, MEASURED_KEY, DERIVED_KEY, RESET_KEY, LEGS_KEY}
    | {f"{leg} {MEASURED_KEY}" for leg in SEPARATE},
)

LEG_FIELDS = 2

# Which job measures a leg, and so judges it (#1875). The floor's record
# is checked in every job that measures a leg, and each job judges only
# the recorded legs it owns: one owner per leg, derived from the leg's
# name, the same names the derivation writes. `pgrx-pgNN` is the gate's
# `pgrx` job at major NN (#1656), spelled `pgrx:NN`; every other leg is
# the gate job's. Strict: no other spelling of a pgrx leg routes to the
# pgrx job, so a misspelt one is the gate job's, which never measures
# it, and red by name.
GATE_JOB = "gate"
PGRX_LEG = re.compile(r"pgrx-pg([1-9][0-9]*)")
PGRX_JOB = re.compile(r"pgrx:([1-9][0-9]*)")

TEMPLATE = """\
# Coverage floor -- DERIVED STATE, not a number to edit (#652).
#
# The release machinery re-derives this file at every release:
# floor = min({cap}, measured - band), and the floor never falls. A
# release whose measurement falls below the floor FAILS, loudly; the
# resolution is tests, or a conscious reset recorded in place of the
# derivation below (replace the `measured` and `derived` lines with
# `reset: <reason>`).
# `coverage:check` refuses a floor that disagrees with this record as
# drift, and never repairs it. `legs` names the legs the floor holds
# (#1666): a leg it names that a run measures nothing of is red, and a
# leg leaves it only through a line `dropped: <leg> <why>`.
#
# band: {band}
{record}# legs: {legs}
{floor}
"""

DERIVED_RECORD = "# measured: {measured}\n# derived: {provenance}\n"
RESET_RECORD = "# reset: {reason}\n"

# A leg the record names is demanded at every `coverage:check` (#1666):
# the floor was derived with it, so a run that measures none of it is
# red rather than quietly taking the minimum over fewer legs. What each
# leg needs, for the line that follows that refusal.
NEEDS = {
    "go": "the go leg needs go.mod",
    "rust": "the rust leg needs Cargo.toml and llvm-tools",
    "python": (
        "the python leg needs tracked *.py and a `test` task whose test "
        'command runs through "${ORG_BELT_DIR}/measured"'
    ),
    "typescript": (
        "the typescript leg needs a root package.json whose lockfile pins "
        "vitest and a coverage provider, and a `test` task that runs vitest"
    ),
    "plpgsql": (
        "the plpgsql leg needs a test command run through "
        '"${ORG_BELT_DIR}/measured" and its teardown to call '
        "plpgsql_coverage.py collect while ORG_PLPGSQL_COVERAGE is set"
    ),
}

SEPARATE_TEMPLATE = """\
#
# {leg}: a floor of its own (#1359), measured off the gate and held by
# the Monday audit, under the same band and the same law.
# {leg} {measured_key}: {measured}
{leg} {floor}
"""


class Record(NamedTuple):
    """A parsed `.coverage-floor`: the number, and how it was arrived at."""

    floor: Decimal
    band: Decimal
    measured: Decimal | None
    derived: str | None
    reset: str | None
    # Leg name to (floor, measured) for each SEPARATE leg the file holds;
    # `parse` always sets it, so None is only the unparsed default.
    separate: dict[str, tuple[Decimal, Decimal]] | None = None
    # The main-floor legs the record names (#1666); None for a file that
    # predates the record, which demands no leg until its next derivation.
    legs: tuple[str, ...] | None = None
    # Leg name to the reason it was consciously dropped.
    dropped: dict[str, str] | None = None


def derive_floor(measured: Decimal, band: Decimal = BAND) -> Decimal:
    """Cut a floor from a measurement: the one statement of the law.

    Returns:
        `measured - band`, never below 0 and never above the cap.

    """
    return min(FLOOR_CAP, max(FLOOR_MIN, measured - band))


def enforced(floor: Decimal, measured: Decimal, band: Decimal) -> Decimal | None:
    """Hold a stated floor to the measurement its record states.

    The capped derivation agrees; so does the uncapped one a floor
    written before the cap carries (#1786), which is enforced at the cap
    until the next release rewrites it.

    Returns:
        The floor to enforce, or None when the floor is drift.

    """
    uncapped = max(FLOOR_MIN, measured - band)
    if floor not in {derive_floor(measured, band), uncapped}:
        return None
    return min(floor, FLOOR_CAP)


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


def fields(text: str) -> tuple[dict[str, str], list[str], list[str]]:
    """Split a floor file into its comment record and its bare lines.

    Returns:
        The `key: value` pairs stated in comments, every non-comment,
        non-blank line in the order they appear, and of those apart the
        lines that open with a SEPARATE leg's name.

    """
    record: dict[str, str] = {}
    bare: list[str] = []
    apart: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not line.startswith("#"):
            (apart if line.split()[0] in SEPARATE else bare).append(line)
            continue
        key, sep, value = line.lstrip("#").strip().partition(":")
        if sep and key.strip() in KEYS:
            record[key.strip()] = value.strip()
    return record, bare, apart


def separate_of(
    record: dict[str, str],
    apart: list[str],
    band: Decimal,
) -> tuple[dict[str, tuple[Decimal, Decimal]] | None, list[str]]:
    """Read the SEPARATE legs' lines and hold each to its own record.

    Returns:
        Leg name to (floor, measured), or None with every problem found.

    """
    lines: dict[str, list[str]] = {}
    for line in apart:
        lines.setdefault(line.split()[0], []).append(line)
    held: dict[str, tuple[Decimal, Decimal]] = {}
    problems: list[str] = []
    for leg in SEPARATE:
        key = f"{leg} {MEASURED_KEY}"
        stated = lines.get(leg, [])
        if not stated and key not in record:
            continue
        if len(stated) != 1 or key not in record:
            problems.append(
                f"states {len(stated)} `{leg}` line(s) and "
                f"{'a' if key in record else 'no'} `{key}` record: the "
                "leg is one line and one record, both written by "
                "`mise run coverage:adopt`, or neither",
            )
            continue
        parts = stated[0].split()
        floor = number(parts[1]) if len(parts) == LEG_FIELDS else None
        measured = number(record[key])
        if floor is None or not FLOOR_MIN <= floor <= FLOOR_MAX:
            problems.append(f"{leg} line {stated[0]!r} is not `{leg} <percent>`")
            continue
        if measured is None or not FLOOR_MIN <= measured <= FLOOR_MAX:
            problems.append(f"{key} {record[key]!r} is not a percentage")
            continue
        holds = enforced(floor, measured, band)
        if holds is None:
            problems.append(
                f"{leg} floor {floor} disagrees with its own record: "
                f"measured {measured} under band {band} and the cap "
                f"{FLOOR_CAP} derives {derive_floor(measured, band)}. "
                "Refused as drift and never repaired — restore the "
                "derivation, or delete both lines and re-derive with "
                "`mise run coverage:adopt`",
            )
            continue
        held[leg] = (holds, measured)
    if problems:
        return None, problems
    return held, []


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
    return Record(min(floor, FLOOR_CAP), band, None, record.get(DERIVED_KEY), reset), []


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

    holds = enforced(floor, measured, band)
    if holds is None:
        return None, [
            (
                f"floor {floor} disagrees with its own record: measured "
                f"{measured} under band {band} and the cap {FLOOR_CAP} "
                f"derives {derive_floor(measured, band)}. A floor "
                "found wrong is evidence the machinery was bypassed, so "
                "this is refused as drift and never repaired — restore "
                "the derivation, or record a conscious reset by replacing "
                "the `measured` and `derived` lines with `reset: <why>`"
            ),
        ]
    return Record(holds, band, measured, record.get(DERIVED_KEY), None), []


def main_form(
    record: dict[str, str],
    floor: Decimal,
    band: Decimal,
) -> tuple[Record | None, list[str]]:
    """Read the main floor's record in whichever form it is stated.

    Returns:
        The record, or None with the problem that refused it.

    """
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


def parse(text: str) -> tuple[Record | None, list[str]]:
    """Read a floor file and hold it to the derived-state law.

    Returns:
        The record, or None with the problems that refused it. Every
        problem is a complete sentence naming its own remedy: this output
        is what a session sees when the gate goes red.

    """
    record, bare, apart = fields(text)

    floor, problems = floor_of(bare)
    if floor is None:
        return None, problems
    band, problems = band_of(record)
    if band is None:
        return None, problems
    held, problems = separate_of(record, apart, band)
    if held is None:
        return None, problems
    main, problems = main_form(record, floor, band)
    if main is None:
        return None, problems
    recorded, problems = legs_of(record)
    dropped, unread = dropped_of(text)
    if problems or unread:
        return None, problems + unread
    return main._replace(separate=held, legs=recorded, dropped=dropped), []


def legs_of(record: dict[str, str]) -> tuple[tuple[str, ...] | None, list[str]]:
    """Read the `legs:` record: the main-floor legs the floor holds.

    Returns:
        The legs (None when the file states no `legs:` line), and the
        problems that refused the line.

    """
    stated = record.get(LEGS_KEY)
    if stated is None:
        return None, []
    named = tuple(stated.split())
    if not named:
        return None, ["records `legs:` with no leg: the line names every leg it holds"]
    bad = [leg for leg in named if not LEG_NAME.fullmatch(leg) or leg in SEPARATE]
    if bad or len(set(named)) != len(named):
        return None, [
            (
                f"legs record {stated!r} is not a list of distinct main-floor "
                "legs (a leg held apart keeps its own line). The record is "
                "written by the derivation: restore it, or re-derive with "
                "`mise run coverage:adopt`"
            ),
        ]
    return named, []


def dropped_of(text: str) -> tuple[dict[str, str], list[str]]:
    """Read every `dropped: <leg> <reason>` line.

    Returns:
        Leg name to reason, and the problems with any line not of that
        shape.

    """
    dropped: dict[str, str] = {}
    problems: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("#"):
            continue
        key, sep, value = line.lstrip("#").strip().partition(":")
        if not sep or key.strip() != DROPPED_KEY:
            continue
        leg, _, reason = value.strip().partition(" ")
        if not LEG_NAME.fullmatch(leg) or not reason.strip():
            problems.append(
                f"dropped line {line!r} is not `dropped: <leg> <reason>`: a "
                "leg leaves the record on purpose, so the reason is stated",
            )
            continue
        dropped[leg] = reason.strip()
    return dropped, problems


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


def render(
    measured: Decimal,
    provenance: str,
    separate: dict[str, Decimal] | None = None,
    legs: list[str] | None = None,
    *,
    reset: tuple[Decimal, str] | None = None,
) -> str:
    """Write the file the derivation describes.

    `reset` carries a conscious reset through a release that does not
    raise it — its floor and reason — in place of the derived record;
    `measured` and `provenance` are then not written.

    Returns:
        The whole file, band comment and all, with a section for each
        SEPARATE leg given (leg name to its measurement).

    """
    quantised = quantise(measured)
    if reset is None:
        main = DERIVED_RECORD.format(measured=quantised, provenance=provenance)
        floor = derive_floor(quantised)
    else:
        main = RESET_RECORD.format(reason=reset[1])
        floor = reset[0]
    text = TEMPLATE.format(
        cap=FLOOR_CAP,
        band=BAND,
        record=main,
        legs=" ".join(legs or ()),
        floor=floor,
    )
    if not legs:
        text = text.replace("# legs: \n", "")
    for leg in SEPARATE:
        if separate is None or leg not in separate:
            continue
        leg_measured = quantise(separate[leg])
        text += SEPARATE_TEMPLATE.format(
            leg=leg,
            measured_key=MEASURED_KEY,
            measured=leg_measured,
            floor=derive_floor(leg_measured),
        )
    return text


def complain(problems: list[str], path: Path) -> None:
    """Print every reason the file was refused, never only the first."""
    for problem in problems:
        print(f"{NAME}: {path} {problem}", file=sys.stderr)


def read(path: Path, leg: str | None = None) -> int:
    """Print the floor `coverage:check` (or, for a leg, the audit) enforces.

    Returns:
        0 with the floor on stdout, 0 with an empty stdout when the
        repository has adopted no floor (or no line for the leg asked
        for), and 1 when the committed file does not hold to the
        derived-state law — every line of it, whichever was asked for.

    """
    if not path.exists():
        return 0
    record, problems = parse(path.read_text(encoding="utf-8"))
    if record is None:
        complain(problems, path)
        return 1
    held = record.separate or {}
    if leg is None:
        print(record.floor)
    elif leg in held:
        print(held[leg][0])
    return 0


def ratchet_separate(record: Record, separate: dict[str, Decimal]) -> bool:
    """Hold each freshly measured SEPARATE leg to the floor it already has.

    Returns:
        True when every leg may be rewritten, False (with the reason
        printed) when one would fall.

    """
    held = True
    holds = record.separate or {}
    for leg, measured in separate.items():
        if leg not in holds:
            continue
        floor = holds[leg][0]
        if quantise(measured) < floor:
            print(
                f"{NAME}: {leg} measured {quantise(measured)}% is below its "
                f"floor {floor}%. The floor never falls, so nothing is "
                f"written: write the tests that put {leg} back to {floor}%",
                file=sys.stderr,
            )
            held = False
    return held


def carried(record: Record, fresh: dict[str, Decimal]) -> dict[str, Decimal]:
    """Choose the measurement each SEPARATE leg's line is written from.

    A fresh figure that raises (or keeps) the leg's floor is written; one
    within the band keeps the measurement the record states, so the line
    never falls; a leg this run did not measure is carried as it stands.

    Returns:
        Leg name to the measurement its line records.

    """
    holds = record.separate or {}
    rising = {
        leg: measured
        for leg, measured in fresh.items()
        if leg not in holds or derive_floor(quantise(measured)) >= holds[leg][0]
    }
    return {leg: m for leg, (_, m) in holds.items()} | rising


def write(  # ruff: ignore[too-many-arguments] -- one keyword per part of a measurement
    path: Path,
    measured: Decimal,
    provenance: str,
    *,
    adopt: bool,
    separate: dict[str, Decimal] | None = None,
    legs: list[str] | None = None,
) -> int:
    """Re-derive the floor from a fresh measurement.

    `legs` is the main-floor legs this measurement reported, written as
    the `legs:` record. A leg the record names that this run did not
    measure refuses the derivation unless a `dropped:` line names it.

    `separate` is the SEPARATE legs measured this time; a leg the file
    already holds and this run did not measure is carried through as it
    stands, so a release measuring on stable keeps the nightly's figure.

    Returns:
        0 when the file was written or the repository owes nothing, and 1
        when the ratchet refuses or the committed file is not the
        machinery's to overwrite.

    """
    quantised = quantise(measured)
    derived = derive_floor(quantised)
    fresh = dict(separate or {})
    noted = "".join(
        f", {leg} {derive_floor(quantise(m))} (measured {quantise(m)})"
        for leg, m in fresh.items()
    )

    if not path.exists():
        if not adopt:
            print(f"{NAME}: no {path}, skipped")
            return 0
        path.write_text(render(measured, provenance, fresh, legs), encoding="utf-8")
        print(f"{NAME}: adopted {path} at {derived} (measured {quantised}){noted}")
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
        path.write_text(render(measured, provenance, fresh, legs), encoding="utf-8")
        print(f"{NAME}: rewrote {path} at {derived} (measured {quantised}){noted}")
        return 0

    # The gate's line, not `floor + band` (#1786): the release holds what
    # every merged pull request already held, so it cannot red main.
    main_held = quantised >= record.floor
    if not main_held:
        print(
            f"{NAME}: measured {quantised}% is below the floor "
            f"{record.floor}%. The floor never falls, so this release "
            "FAILS rather than lowering it: write the tests that put it "
            f"back to {record.floor}%, or consciously reset the floor by "
            "replacing the `measured` and `derived` lines of "
            f"{path} with `reset: <why>`",
            file=sys.stderr,
        )
    # Every leg is judged before anything returns, so one run names every
    # refusal rather than the first.
    kept_legs = record_holds(record, legs or [], path)
    if not ratchet_separate(record, fresh) or not main_held or not kept_legs:
        return 1

    kept = carried(record, fresh)
    if derived >= record.floor:
        text = render(measured, provenance, kept, legs)
        print(
            f"{NAME}: floor {record.floor} -> {derived} (measured {quantised}){noted}",
        )
    else:
        # Within the band: the floor never falls, so the main record is
        # written as it stands — its measurement, or its reset — and only
        # the legs and the lines held apart move.
        if record.reset is not None or record.measured is None:
            reset = (record.floor, record.reset or "")
            text = render(quantised, "", kept, legs, reset=reset)
        else:
            text = render(record.measured, record.derived or "", kept, legs)
        print(
            f"{NAME}: floor {record.floor} held (measured {quantised}, "
            f"within the {BAND}-point band){noted}",
        )
    path.write_text(text, encoding="utf-8")
    return 0


def record_holds(record: Record, legs: list[str], path: Path) -> bool:
    """Hold a derivation to the legs the record names (#1666).

    Returns:
        True when every recorded leg was measured or is consciously
        dropped, False (each missing leg named) otherwise.

    """
    dropped = record.dropped or {}
    missing = [
        leg for leg in record.legs or () if leg not in legs and leg not in dropped
    ]
    for leg in missing:
        print(
            f"{NAME}: the floor records {leg} and this derivation measured "
            "none. A leg leaves the record only on purpose: restore what it "
            f"measures, or add `# dropped: {leg} <reason>` to {path}",
            file=sys.stderr,
        )
    return not missing


def derive(path: Path, source: str, provenance: str, *, adopt: bool) -> int:
    """Take the weakest measured leg and re-derive the floor from it.

    Returns:
        A process exit status.

    """
    every, problems = legs(source)
    if problems:
        complain(problems, path)
        return 1
    measured = [(leg, pct) for leg, pct in every if leg not in SEPARATE]
    separate = {leg: pct for leg, pct in every if leg in SEPARATE}
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
    return write(
        path,
        weakest,
        provenance,
        adopt=adopt,
        separate=separate,
        legs=[leg for leg, _ in measured],
    )


def recorded_legs(path: Path) -> int:
    """Print the main-floor legs the record names, one per line.

    Returns:
        0, with nothing printed when there is no floor or no `legs:`
        record; 1 when the file does not hold to its own record.

    """
    if not path.exists():
        return 0
    record, problems = parse(path.read_text(encoding="utf-8"))
    if record is None:
        complain(problems, path)
        return 1
    for leg in record.legs or ():
        print(leg)
    return 0


def owner(leg: str) -> str:
    """Name the one job that measures a leg (#1875).

    Returns:
        `pgrx:NN` for `pgrx-pgNN`, else the gate job.

    """
    pgrx = PGRX_LEG.fullmatch(leg)
    return f"pgrx:{pgrx.group(1)}" if pgrx else GATE_JOB


def job_of(text: str) -> str:
    """Read a job as `hold --job` names it: `gate` or `pgrx:NN`.

    Returns:
        The job.

    Raises:
        argparse.ArgumentTypeError: any other spelling.

    """
    if text == GATE_JOB or PGRX_JOB.fullmatch(text):
        return text
    msg = f"{text!r} is not a job: `{GATE_JOB}` or `pgrx:<major>`"
    raise argparse.ArgumentTypeError(msg)


def unaccounted(record: Record, label: str, legs: list[str]) -> list[str]:
    """Name each recorded leg in `legs` that is not dropped, with its remedy.

    Returns:
        The legs named, empty when there is none.

    """
    dropped = record.dropped or {}
    missing = [leg for leg in legs if leg not in dropped]
    for leg in missing:
        print(
            f"{label}: the floor was derived with {leg} and this run measured none",
            file=sys.stderr,
        )
        print(needs(leg), file=sys.stderr)
    return missing


def needs(leg: str) -> str:
    """Name what a leg needs to report, for the line after its refusal.

    Returns:
        The remedy line.

    """
    family = leg.removesuffix("-branches")
    what = NEEDS.get(family, "restore what the leg measures")
    return (
        f"  {what}; or record the leg's removal on purpose: "
        f"`# dropped: {leg} <reason>` in .coverage-floor"
    )


def hold(path: Path, label: str, source: str, jobs: frozenset[str]) -> int:
    """Hold this run's legs to the floor, and the floor's legs to this run.

    Every measured leg is held to the floor. A leg the record names, owned
    by one of `jobs` (the calling job), that this run did not measure is
    red by name (#1666); a recorded leg another job owns is that job's to
    judge (#1875). A measured leg the record does not name is held like
    any other and named, since it joins the record at the next
    derivation. A file with no `legs:` record demands no leg.

    Returns:
        0 when every leg holds and every recorded leg reported (and when
        there is no floor, which holds nothing), else 1.

    """
    if not path.exists():
        return 0
    record, problems = parse(path.read_text(encoding="utf-8"))
    if record is None:
        complain(problems, path)
        return 1
    measured, problems = legs(source)
    if problems:
        complain(problems, path)
        return 1
    status = 0
    names = [leg for leg, _ in measured]
    for leg, pct in measured:
        if pct < record.floor:
            print(
                f"{label}: {leg}: {pct}% is below the derived floor {record.floor}%",
                file=sys.stderr,
            )
            status = 1
            continue
        print(f"{label}: {leg} leg holds the floor {record.floor} ({pct}%)")
        if record.legs is not None and leg not in record.legs:
            print(
                f"{label}: {leg}: not yet in the floor's record: it joins at "
                "the next derivation",
            )
    owned = [
        leg for leg in record.legs or () if owner(leg) in jobs and leg not in names
    ]
    if unaccounted(record, label, owned):
        status = 1
    return status


def planned(path: Path, label: str, majors: str) -> int:
    """Hold the floor's record to this run's plan (#1875).

    Every recorded leg must have its owning job planned: the gate job
    always is, and `pgrx:NN` is when NN is among `majors`, the
    comma-separated list the plan made. Run by the `plan` job, before
    any leg starts, so a recorded leg no job will measure is red once,
    by name, rather than judged by no one.

    Returns:
        0 when every recorded leg has a planned owner (and when there is
        no floor or no record), else 1.

    """
    if not path.exists():
        return 0
    record, problems = parse(path.read_text(encoding="utf-8"))
    if record is None:
        complain(problems, path)
        return 1
    jobs = {GATE_JOB} | {f"pgrx:{major}" for major in majors.split(",") if major}
    dropped = record.dropped or {}
    orphaned = [
        leg
        for leg in record.legs or ()
        if owner(leg) not in jobs and leg not in dropped
    ]
    for leg in orphaned:
        print(
            f"{label}: the floor records {leg}, measured by the {owner(leg)} "
            f"job, and this run plans none (planned majors: {majors or 'none'})",
            file=sys.stderr,
        )
        print(needs(leg), file=sys.stderr)
    return 1 if orphaned else 0


class Args(argparse.Namespace):
    """One subcommand's arguments, each typed as parsed."""

    path: Path
    command: str
    leg: str | None
    provenance: str
    label: str
    job: list[str]
    majors: str
    adopt: bool


def main(argv: list[str] | None = None) -> int:
    """Run one subcommand over the repository's floor file.

    Returns:
        A process exit status.

    """
    parser = argparse.ArgumentParser(description="the derived coverage floor")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    reader = sub.add_parser("read", help="print the floor to enforce, refusing drift")
    reader.add_argument(
        "--leg",
        choices=SEPARATE,
        help="print the floor of a leg held apart (empty when the file has none)",
    )
    writer = sub.add_parser(
        "write",
        help="re-derive the floor from `<leg> <percent>` lines on stdin",
    )
    writer.add_argument("--provenance", required=True)
    sub.add_parser("legs", help="print the legs the floor's record names")
    holder = sub.add_parser(
        "hold",
        help="hold `<leg> <percent>` lines on stdin to the floor and its record",
    )
    holder.add_argument("--label", required=True)
    holder.add_argument(
        "--job",
        type=job_of,
        action="append",
        required=True,
        help="a job the caller is (`gate` or `pgrx:<major>`): the recorded "
        "legs it owns are the ones it must report",
    )
    plan = sub.add_parser(
        "planned",
        help="hold the floor's recorded legs to the jobs this run plans",
    )
    plan.add_argument("--label", required=True)
    plan.add_argument(
        "--majors",
        required=True,
        help="the planned Postgres majors, comma-separated (empty for none)",
    )
    writer.add_argument(
        "--adopt",
        action="store_true",
        help="write a floor this repository has none of, or migrate a recordless one",
    )
    args = parser.parse_args(argv, namespace=Args())
    if args.command == "read":
        return read(args.path, args.leg)
    if args.command == "legs":
        return recorded_legs(args.path)
    if args.command == "hold":
        return hold(args.path, args.label, typed_std.stdin_text(), frozenset(args.job))
    if args.command == "planned":
        return planned(args.path, args.label, args.majors)
    return derive(args.path, typed_std.stdin_text(), args.provenance, adopt=args.adopt)


if __name__ == "__main__":
    sys.exit(main())
