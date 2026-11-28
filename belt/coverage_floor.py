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

Every leg is held to a floor of its own (#2143), cut by that law from
its own measurement: a `<leg> <floor>` line and a `<leg> measured:`
record each. The file once held one floor, cut from the weakest leg,
and so held every stronger leg below its own figure: a language
measuring well above the weakest could lose coverage down to it, and
the gate stayed green. A file still in that shape (a bare floor line)
is refused by name, with the command that rewrites it; it is never
read as "every leg at that floor".

Two entry points, one definition:

  read   `coverage:check` asks for the floors to enforce. The file's own
         record is checked first: a floor that disagrees with the
         derivation it claims is refused as DRIFT, never repaired, on the
         reasoning that a floor found wrong is evidence the machinery was
         bypassed.
  write  the release path (`release/derive-coverage-floor.sh`) and its
         write-mode sibling `coverage:adopt` hand over a fresh
         measurement, one `<leg> <percent>` line per leg on stdin — the
         belt convention, where the task enumerates and the helper
         consumes. The ratchet is applied here, leg by leg, so both
         callers get the same law from the same code.

Two legs are read off the gate (`SEPARATE`): `rust-branches` (#1359),
Rust branch coverage, measured on a nightly, and `go-branches` (#1546),
gobco's Go condition coverage. Their lines are the same shape as every
other leg's, under the same band and the same law. They are not in the
`legs:` record the gate demands, because `coverage:check` never
measures them: the Monday audit reads them (`read --leg <leg>`);
`coverage:adopt` writes them; the release path, which measures neither,
carries them through unchanged.

No fuzz target is a leg (#2090, reversing #1896). A target's figure
divides by every line it links, most of which it can never call, so it
fell whenever the crate grew and blocked merges with nothing wrong. The
nightly search (#1897) compares two measurements of one commit and
proposes what a corpus lacks; the floor holds no fuzz figure. A
`fuzz-*` line, record or `dropped:`/`legs:` entry still in a file is
refused by name, with the lines to delete.

A leg can report that this run cannot measure it exactly:
`<leg> not-held` in place of its percentage, where an owner's ruling
allows that leg to (NOT_HELD_LEGS; today only `plpgsql-branches` over
more than one test database, which plpgsql_check cannot merge: #2163,
#2169). Any other leg's `not-held` is refused as a malformed line.
`hold` holds nothing against an allowed one and counts it as reported,
so a recorded leg is not red for being unmeasurable; `write` keeps it in the
`legs:` record and cuts no floor from it, so its floor is left as it
stands, never lowered by a figure that was never taken.

Opt-in is unchanged: a repository with no `.coverage-floor` owes nothing
and skips clean in both directions. The belt offers the mechanism; the
committed file is the repository's adoption and its number is the
repository's choice.
"""

import argparse
import json
import re
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple

from belt import decoded, findings, typed_std

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

# The cap (#1786, ruled 2026-11-20): no floor is derived above 90, for
# every leg (#2143), so a leg measured at 100 is held at 90. Org policy
# like the band, so a constant here and never a repository setting.
FLOOR_CAP = Decimal(90)

# The record fields a file states once.
BAND_KEY = "band"
DERIVED_KEY = "derived"
# The legs the gate measures (#1666): written by every derivation, read
# by `coverage:check`, and left only through a `dropped:` line.
LEGS_KEY = "legs"
DROPPED_KEY = "dropped"
KEYS = frozenset({BAND_KEY, DERIVED_KEY, LEGS_KEY})
# The record fields each leg states, one of the two: `<leg> measured:`
# for a derived floor, `<leg> reset:` for one a human consciously re-set.
MEASURED_KEY = "measured"
RESET_KEY = "reset"
LEG_KEYS = frozenset({MEASURED_KEY, RESET_KEY})
LEG_NAME = re.compile(r"[a-z][a-z0-9-]*")
# The legs read off the gate, by the Monday audit, and so not in the
# `legs:` record the gate demands.
SEPARATE = ("rust-branches", "go-branches")
# A name the fuzz legs used before they left the floor (#2090): any
# word opening `fuzz-`, the bare `fuzz-` among them (#1899).
LEFT_FUZZ = re.compile(r"fuzz-\S*")
# The one refusal of a file that still holds one (#2090).
FUZZ_LEFT = (
    "fuzz legs left the floor (#2090): delete the line, its "
    '"measured" record and its comment'
)
# The one refusal of a file in the shape before #2143.
SHARED_LEFT = (
    "the shape that held every leg to the weakest leg's floor. Every leg "
    "has a floor of its own (#2143): rewrite the file with "
    "`mise run coverage:adopt`"
)

LEG_FIELDS = 2
# A leg this run reports and cannot measure exactly (#2163).
NOT_HELD = "not-held"
# The legs allowed to report it, each by an owner's ruling: today one,
# `plpgsql-branches` over more than one test database, which
# plpgsql_check cannot merge (#2163; held again by #2169). Any other
# leg's `not-held` is refused like a malformed measurement, so no
# measurement line can opt a leg out of its floor.
NOT_HELD_LEGS = frozenset({"plpgsql-branches"})


def apart(leg: str) -> bool:
    """Say whether a leg is read off the gate, by the Monday audit.

    Returns:
        True for a SEPARATE leg.

    """
    return leg in SEPARATE


# Which job measures a leg, and so judges it (#1875). The floor's record
# is checked in every job that measures a leg, and each job judges only
# the recorded legs it owns: one owner per leg, derived from the leg's
# name, the same names the derivation writes. `pgrx-pgNN` is the gate's
# `pgrx` job at major NN (#1656), spelled `pgrx:NN`; every other leg is
# the gate job's. Strict: no
# other spelling of a pgrx leg routes to the pgrx job, so a misspelt one
# is the gate job's, which never measures it, and red by name.
GATE_JOB = "gate"
PGRX_LEG = re.compile(r"pgrx-pg([1-9][0-9]*)")
PGRX_JOB = re.compile(r"pgrx:([1-9][0-9]*)")
# A Postgres major as the plan spells one: digits, no leading zero.
MAJOR = re.compile(r"[1-9][0-9]*")

TEMPLATE = """\
# Coverage floor -- DERIVED STATE, not a number to edit (#652).
#
# The release machinery re-derives this file at every release. Each leg
# has a floor of its own (#2143), floor = min({cap}, measured - band),
# and no floor ever falls. A release whose measurement of a leg falls
# below that leg's floor FAILS, loudly; the resolution is tests, or a
# conscious reset recorded in place of the leg's derivation (replace
# its `<leg> measured` line with `<leg> reset: <reason>`).
# `coverage:check` refuses a floor that disagrees with this record as
# drift, and never repairs it. `legs` names the legs the gate measures
# (#1666): a leg it names that a run measures nothing of is red, and a
# leg leaves it only through a line `dropped: <leg> <why>`.
#
# band: {band}
{derived}# legs: {legs}
"""

DERIVED_RECORD = "# derived: {provenance}\n"

LEG_TEMPLATE = """\
#
{note}# {leg} {key}: {value}
{leg} {floor}
"""

# What a leg read off the gate says of itself, above its record.
APART_NOTE = (
    "# {leg}: measured off the gate and held by the Monday audit\n"
    "# (#1359), under the same band and the same law.\n"
)

# A leg the record names is demanded at every `coverage:check` (#1666):
# its floor was derived from it, so a run that measures none of it is
# red rather than quietly passing over fewer legs. What each leg needs,
# for the line that follows that refusal.
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
        "belt plpgsql-coverage collect while ORG_PLPGSQL_COVERAGE is set"
    ),
}


class Held(NamedTuple):
    """One leg's floor, and how it was arrived at: a measurement or a reset."""

    floor: Decimal
    measured: Decimal | None
    reset: str | None


class Record(NamedTuple):
    """A parsed `.coverage-floor`: each leg's floor, and the file's record."""

    band: Decimal
    derived: str | None
    # The gate-measured legs the record names (#1666), in its order.
    legs: tuple[str, ...]
    # Leg name to its floor, for every leg the file holds: each in
    # `legs`, and each SEPARATE leg the file has a line for.
    floors: dict[str, Held]
    # Leg name to the reason it was consciously dropped.
    dropped: dict[str, str]


def derive_floor(measured: Decimal, band: Decimal = BAND) -> Decimal:
    """Cut a floor from a measurement: the one statement of the law.

    Returns:
        `measured - band`, never below 0 and never above the cap.

    """
    return min(FLOOR_CAP, max(FLOOR_MIN, measured - band))


def quantise(value: Decimal) -> Decimal:
    """Round a measurement down to the recorded precision.

    Returns:
        The value at one decimal place, never rounded up.

    """
    return value.quantize(STEP, rounding=ROUND_DOWN)


def fresh(measured: Decimal) -> Held:
    """Hold a leg at the floor its measurement derives.

    Returns:
        The quantised measurement and its floor.

    """
    quantised = quantise(measured)
    return Held(derive_floor(quantised), quantised, None)


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


def percent(text: str) -> Decimal | None:
    """Read a percentage the way this file writes them.

    Returns:
        The value, or None when it is not a number between 0 and 100.

    """
    value = number(text)
    if value is None or not FLOOR_MIN <= value <= FLOOR_MAX:
        return None
    return value


class Fields(NamedTuple):
    """A floor file split into what it states, before any law is applied."""

    # The `key: value` pairs stated once.
    record: dict[str, str]
    # Leg name to key (`measured` or `reset`) to every value stated.
    leg_records: dict[str, dict[str, list[str]]]
    # Leg name to every non-comment line opening with it.
    lines: dict[str, list[str]]


def fields(text: str) -> Fields:
    """Split a floor file into its comment record and its leg lines.

    Returns:
        The file's record, each leg's records and each leg's lines.

    """
    split = Fields({}, {}, {})
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not line.startswith("#"):
            split.lines.setdefault(line.split()[0], []).append(line)
            continue
        key, sep, value = line.lstrip("#").strip().partition(":")
        key = key.strip()
        if not sep:
            continue
        leg, _, rest = key.partition(" ")
        if key in KEYS:
            split.record[key] = value.strip()
        elif rest in LEG_KEYS and LEG_NAME.fullmatch(leg):
            split.leg_records.setdefault(leg, {}).setdefault(rest, []).append(
                value.strip()
            )
    return split


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


def legs_of(record: dict[str, str]) -> tuple[tuple[str, ...] | None, list[str]]:
    """Read the `legs:` record: the gate-measured legs the floor holds.

    Returns:
        The legs, or None with the problem that refused the line.

    """
    stated = record.get(LEGS_KEY)
    if stated is None or not stated.split():
        return None, [
            (
                "records no `legs:` line: the line names every leg the gate "
                "measures, and each has a floor of its own (#2143). Write "
                "the file from a real measurement with `mise run coverage:adopt`"
            ),
        ]
    named = tuple(stated.split())
    bad = [leg for leg in named if not LEG_NAME.fullmatch(leg) or apart(leg)]
    if bad or len(set(named)) != len(named):
        return None, [
            (
                f"legs record {stated!r} is not a list of distinct legs the "
                "gate measures (a leg read off the gate is not in it). The "
                "record is written by the derivation: restore it, or "
                "re-derive with `mise run coverage:adopt`"
            ),
        ]
    return named, []


def held_of(
    leg: str,
    lines: list[str],
    records: dict[str, list[str]],
    band: Decimal,
) -> tuple[Held | None, list[str]]:
    """Read one leg's line and record, and hold the floor to the record.

    Returns:
        The leg's floor, or None with the problem that refused it.

    """
    stated = [(key, value) for key, values in records.items() for value in values]
    if len(lines) != 1 or len(stated) != 1:
        return None, [
            (
                f"states {len(lines)} `{leg}` line(s) and {len(stated)} "
                f"`{leg} measured` or `{leg} reset` record(s): each leg is "
                "one line and one record, both written by "
                "`mise run coverage:adopt`"
            ),
        ]
    parts = lines[0].split()
    floor = percent(parts[1]) if len(parts) == LEG_FIELDS else None
    if floor is None:
        return None, [f"{leg} line {lines[0]!r} is not `{leg} <percent>`"]
    key, value = stated[0]
    if key == RESET_KEY:
        return reset_of(leg, floor, value)
    measured = percent(value)
    if measured is None:
        return None, [f"{leg} measured {value!r} is not a percentage"]
    if floor != derive_floor(measured, band):
        return None, [
            (
                f"{leg} floor {floor} disagrees with its own record: "
                f"measured {measured} under band {band} and the cap "
                f"{FLOOR_CAP} derives {derive_floor(measured, band)}. A "
                "floor found wrong is evidence the machinery was bypassed, "
                "so this is refused as drift and never repaired — restore "
                "the derivation, or record a conscious reset by replacing "
                f"the `{leg} measured` line with `{leg} reset: <why>`"
            ),
        ]
    return Held(floor, measured, None), []


def reset_of(leg: str, floor: Decimal, reason: str) -> tuple[Held | None, list[str]]:
    """Read a leg's floor a human consciously re-set.

    Returns:
        The leg's floor, or None with the problem that refused it.

    """
    if not reason:
        return None, [
            (
                f"records a {leg} reset with no reason. A conscious "
                "reset is conscious because it is recorded: "
                f"`{leg} reset: <why>`"
            ),
        ]
    return Held(min(floor, FLOOR_CAP), None, reason), []


def floors_of(
    split: Fields,
    named: tuple[str, ...],
    band: Decimal,
) -> tuple[dict[str, Held] | None, list[str]]:
    """Read every leg's floor: each leg `legs:` names, and each one apart.

    A line or record for a leg that is neither is refused by name: a
    floor the gate never reads would be a number nothing holds.

    Returns:
        Leg name to its floor, or None with every problem found.

    """
    floors: dict[str, Held] = {}
    stated = [
        *split.lines,
        *(leg for leg in split.leg_records if leg not in split.lines),
    ]
    problems: list[str] = [
        f"states a `{leg}` line or record and the `legs:` record does "
        "not name it: a floor nothing holds. Delete it, or re-derive "
        "with `mise run coverage:adopt`"
        for leg in stated
        if leg not in named and not apart(leg)
    ]
    for leg in (*named, *(leg for leg in SEPARATE if leg in stated)):
        held, refused = held_of(
            leg, split.lines.get(leg, []), split.leg_records.get(leg, {}), band
        )
        if held is None:
            problems.extend(refused)
            continue
        floors[leg] = held
    if problems:
        return None, problems
    return floors, []


def parse(text: str) -> tuple[Record | None, list[str]]:
    """Read a floor file and hold it to the derived-state law.

    A file that still states a fuzz leg (#2090), or a bare floor line
    (#2143), is refused for that alone, before any other reading.

    Returns:
        The record, or None with the problems that refused it. Every
        problem is a complete sentence naming its own remedy: this output
        is what a session sees when the gate goes red.

    """
    left = fuzz_left(text) or shared_left(text)
    return (None, left) if left else lawful(text)


def lawful(text: str) -> tuple[Record | None, list[str]]:
    """Hold a floor file in the per-leg shape to the derived-state law.

    Returns:
        The record, or None with the problems that refused it.

    """
    split = fields(text)
    band, problems = band_of(split.record)
    if band is None:
        return None, problems
    named, problems = legs_of(split.record)
    if named is None:
        return None, problems
    floors, problems = floors_of(split, named, band)
    dropped, unread = dropped_of(text)
    if floors is None or unread:
        return None, problems + unread
    return Record(band, split.record.get(DERIVED_KEY), named, floors, dropped), []


def shared_left(text: str) -> list[str]:
    """Name a bare floor line: the file's shape before #2143.

    Returns:
        One problem per bare line, empty when there is none.

    """
    bare = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
        and not line.strip().startswith("#")
        and number(line.split()[0]) is not None
    ]
    return [f"states a bare floor line {line!r}: {SHARED_LEFT}" for line in bare]


def fuzz_left(text: str) -> list[str]:
    """Name each fuzz leg a floor file still states (#2090).

    A bare line opening `fuzz-<name>`, a `fuzz-<name> measured:` record,
    and a `legs:` or `dropped:` line naming one: each is refused by name,
    once, so nothing is carried or ignored.

    Returns:
        One problem per fuzz leg named, in the order first stated.

    """
    named: list[str] = []
    for raw in text.splitlines():
        for word in leg_words(raw.strip()):
            if LEFT_FUZZ.fullmatch(word) and word not in named:
                named.append(word)
    return [f"states {leg}: {FUZZ_LEFT}" for leg in named]


def leg_words(line: str) -> list[str]:
    """Name the legs one line of a floor file states.

    Returns:
        A bare line's first word; a `legs:` record's every leg; a
        `dropped:` line's leg; a `<leg> measured:` or `<leg> reset:`
        record's leg; else nothing.

    """
    if not line.startswith("#"):
        return line.split()[:1]
    key, sep, value = line.lstrip("#").strip().partition(":")
    key = key.strip()
    named = {LEGS_KEY: value.split(), DROPPED_KEY: value.split()[:1]}
    if not sep:
        return []
    if key.rpartition(" ")[2] in LEG_KEYS and " " in key:
        return key.split()[:1]
    return named.get(key, [])


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
        if len(parts) == LEG_FIELDS and parts[1] == NOT_HELD:
            if parts[0] not in NOT_HELD_LEGS:
                problems.append(
                    f"measurement {line!r} is not `<leg> <percent>`: only"
                    f" {', '.join(sorted(NOT_HELD_LEGS))} may report"
                    f" `{NOT_HELD}` (#2163)"
                )
            continue
        figure = percent(parts[1]) if len(parts) == LEG_FIELDS else None
        if figure is None:
            problems.append(f"measurement {line!r} is not `<leg> <percent>`")
            continue
        measured.append((parts[0], figure))
    return measured, problems


def unheld(text: str) -> list[str]:
    """Name the legs a measurement reported as not held (#2163).

    Returns:
        Each leg printed `<leg> not-held`, in order.

    """
    return [
        parts[0]
        for parts in (raw.split() for raw in text.splitlines())
        if len(parts) == LEG_FIELDS
        and parts[1] == NOT_HELD
        and parts[0] in NOT_HELD_LEGS
    ]


def render(
    floors: dict[str, Held],
    provenance: str | None,
    band: Decimal = BAND,
) -> str:
    """Write the file the derivation describes.

    Returns:
        The whole file: the band, the provenance, the `legs:` record of
        every leg the gate measures, and a line and record for each leg,
        the gate's in the order given and then each SEPARATE one held.

    """
    named = [leg for leg in floors if not apart(leg)]
    text = TEMPLATE.format(
        cap=FLOOR_CAP,
        band=band,
        derived=DERIVED_RECORD.format(provenance=provenance) if provenance else "",
        legs=" ".join(named),
    )
    for leg in (*named, *(leg for leg in SEPARATE if leg in floors)):
        held = floors[leg]
        key, value = (
            (MEASURED_KEY, held.measured)
            if held.reset is None
            else (RESET_KEY, held.reset)
        )
        text += LEG_TEMPLATE.format(
            note=APART_NOTE.format(leg=leg) if apart(leg) else "",
            leg=leg,
            key=key,
            value=value,
            floor=held.floor,
        )
    return text


def complain(problems: list[str], path: Path) -> None:
    """Print every reason the file was refused, never only the first."""
    for problem in problems:
        findings.finding("", f"{path} {problem}")


def load(path: Path) -> tuple[Record | None, int]:
    """Read the committed floor, naming every reason it is refused.

    Returns:
        The record and 0; None and 0 when there is no floor; None and 1
        when the file does not hold to the derived-state law.

    """
    if not path.exists():
        return None, 0
    record, problems = parse(path.read_text(encoding="utf-8"))
    if record is None:
        complain(problems, path)
        return None, 1
    return record, 0


def read(path: Path, leg: str | None = None) -> int:
    """Print the floors `coverage:check` (or, for a leg, the audit) enforces.

    Returns:
        0 with a `<leg> <floor>` line per gate leg on stdout (or, for a
        leg asked for, its floor), 0 with an empty stdout when the
        repository has adopted no floor (or no line for the leg asked
        for), and 1 when the committed file does not hold to the
        derived-state law — every line of it, whichever was asked for.

    """
    record, status = load(path)
    if record is None:
        return status
    if leg is None:
        for name in record.legs:
            print(f"{name} {record.floors[name].floor}")
    elif leg in record.floors:
        print(record.floors[leg].floor)
    return 0


def ratchet(record: Record, measured: dict[str, Decimal], path: Path) -> bool:
    """Hold each freshly measured leg to the floor it already has.

    Every leg is judged before anything returns, so one run names every
    refusal rather than the first. The gate's line, not `floor + band`
    (#1786): the release holds what every merged pull request already
    held, so it cannot red main.

    Returns:
        True when every leg may be rewritten, False (each reason
        printed) when one would fall.

    """
    held = True
    for leg, figure in measured.items():
        if leg not in record.floors:
            continue
        floor = record.floors[leg].floor
        if quantise(figure) < floor:
            findings.finding(
                leg,
                f"measured {quantise(figure)}% is below its floor {floor}%. "
                "The floor never falls, so this release FAILS rather than "
                f"lowering it: write the tests that put {leg} back to "
                f"{floor}%, or consciously reset its floor by replacing the "
                f"`{leg} measured` line of {path} with `{leg} reset: <why>`",
            )
            held = False
    return held


def rederived(held: Held) -> Held:
    """Write a leg's record as it stands, its floor under the org's band.

    A record stated under another band is rewritten under this one, so
    the file written agrees with itself; a reset stands as it is.

    Returns:
        The leg's floor to write.

    """
    return held if held.measured is None else fresh(held.measured)


def carried(
    record: Record, measured: dict[str, Decimal], unheld: list[str]
) -> tuple[dict[str, Held], list[str]]:
    """Choose what each leg's line is written from.

    A fresh figure that raises (or keeps) the leg's floor is written; one
    within the band keeps the record the leg states, its measurement or
    its reset, so the line never falls; a SEPARATE leg this run did not
    measure, and a leg it reported as not held (#2163), is carried as it
    stands, and a dropped leg leaves the file.

    Returns:
        Leg name to its floor, the gate's legs in the order measured, and
        a line per leg saying what happened to it.

    """
    floors: dict[str, Held] = {}
    said: list[str] = []
    for leg, figure in measured.items():
        new = fresh(figure)
        old = record.floors.get(leg)
        if old is None:
            floors[leg] = new
            said.append(f"{leg} joins at {new.floor} (measured {new.measured})")
        elif new.floor >= old.floor:
            floors[leg] = new
            said.append(f"{leg} {old.floor} -> {new.floor} (measured {new.measured})")
        else:
            floors[leg] = rederived(old)
            said.append(
                f"{leg} {old.floor} held (measured {new.measured}, "
                f"within the {BAND}-point band)",
            )
    for leg in unheld:
        if leg in record.floors and leg not in measured:
            floors[leg] = rederived(record.floors[leg])
            said.append(f"{leg} {floors[leg].floor} carried (not held this run)")
    for leg in SEPARATE:
        if leg in record.floors and leg not in measured:
            floors[leg] = rederived(record.floors[leg])
            said.append(f"{leg} {floors[leg].floor} carried (not measured here)")
    return floors, said


def write(
    path: Path,
    measured: dict[str, Decimal],
    provenance: str,
    *,
    adopt: bool,
    unheld: list[str] | None = None,
) -> int:
    """Re-derive every leg's floor from a fresh measurement.

    `measured` is every leg this run measured, the gate's and any
    SEPARATE one. A leg the record names that this run did not measure
    refuses the derivation unless a `dropped:` line names it; a SEPARATE
    leg the file already holds and this run did not measure is carried
    through as it stands, so a release measuring on stable keeps the
    nightly's figure. `unheld` names the legs this run reported as not
    held (#2163): each counts as reported, and its line is carried as it
    stands, never re-derived or dropped.

    Returns:
        0 when the file was written or the repository owes nothing, and 1
        when the ratchet refuses or the committed file is not the
        machinery's to overwrite.

    """
    adopted = {leg: fresh(figure) for leg, figure in measured.items()}
    noted = ", ".join(
        f"{leg} {held.floor} (measured {held.measured})"
        for leg, held in adopted.items()
    )

    if not path.exists():
        if not adopt:
            print(f"{NAME}: no {path}, skipped")
            return 0
        path.write_text(render(adopted, provenance), encoding="utf-8")
        print(f"{NAME}: adopted {path}: {noted}")
        return 0

    record, problems = parse(path.read_text(encoding="utf-8"))
    if record is None:
        if not adopt:
            complain(problems, path)
            findings.finding(
                "",
                "a release does not repair a floor it did not "
                "write — fix the file, then release",
            )
            return 1
        # `coverage:adopt` is the write-mode channel and a human running
        # it IS the conscious act, so a recordless, drifted or old-shape
        # floor is exactly what it exists to migrate. It still cannot
        # lower a floor whose record parses: that path falls through to
        # the ratchet below.
        path.write_text(render(adopted, provenance), encoding="utf-8")
        print(f"{NAME}: rewrote {path}: {noted}")
        return 0

    rising = ratchet(record, measured, path)
    kept_legs = record_holds(record, [*measured, *(unheld or [])], path)
    if not rising or not kept_legs:
        return 1

    floors, said = carried(record, measured, unheld or [])
    # The provenance is the derivation that last wrote a leg's record
    # from its own measurement; one that held every leg within the band
    # keeps the file's.
    moved = any(
        leg not in record.floors or fresh(figure).floor >= record.floors[leg].floor
        for leg, figure in measured.items()
    )
    path.write_text(
        render(floors, provenance if moved else record.derived), encoding="utf-8"
    )
    for line in said:
        print(f"{NAME}: {line}")
    return 0


def recorded(record: Record) -> list[str]:
    """Name every leg the record demands a measurement of.

    Returns:
        The `legs:` record's legs.

    """
    return list(record.legs)


def record_holds(record: Record, legs: list[str], path: Path) -> bool:
    """Hold a derivation to the legs the record names (#1666).

    Returns:
        True when every recorded leg was measured or is consciously
        dropped, False (each missing leg named) otherwise.

    """
    missing = [
        leg for leg in recorded(record) if leg not in legs and leg not in record.dropped
    ]
    for leg in missing:
        findings.finding(
            "",
            f"the floor records {leg} and this derivation measured "
            "none. A leg leaves the record only on purpose: restore what it "
            f"measures, or add `# dropped: {leg} <reason>` to {path}",
        )
    return not missing


def derive(path: Path, source: str, provenance: str, *, adopt: bool) -> int:
    """Re-derive each leg's floor from its own measurement.

    Returns:
        A process exit status.

    """
    every, problems = legs(source)
    if problems:
        complain(problems, path)
        return 1
    if not any(not apart(leg) for leg, _ in every):
        findings.finding(
            "",
            "no leg was measured — a derivation that reads "
            "nothing must not write a floor",
        )
        return 1
    # Reported but not measured exactly: its line is carried as it
    # stands and the record keeps demanding it (#2163).
    return write(path, dict(every), provenance, adopt=adopt, unheld=unheld(source))


def recorded_legs(path: Path) -> int:
    """Print the gate-measured legs the record names, one per line.

    Returns:
        0, with nothing printed when there is no floor; 1 when the file
        does not hold to its own record.

    """
    record, status = load(path)
    for leg in record.legs if record else ():
        print(leg)
    return status


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


def unaccounted(record: Record, legs: list[str]) -> list[str]:
    """Name each recorded leg in `legs` that is not dropped, with its remedy.

    Returns:
        The legs named, empty when there is none.

    """
    missing = [leg for leg in legs if leg not in record.dropped]
    for leg in missing:
        findings.finding(
            "",
            f"the floor was derived with {leg} and this run measured none",
            [needs(leg).removeprefix("  ")],
        )
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
    """Hold this run's legs to their floors, and the floor's legs to this run.

    Every measured leg is held to its own floor (#2143), and a leg below
    it is red by name. A leg the record names, owned by one of `jobs`
    (the calling job), that this run did not measure is red by name
    (#1666); a recorded leg another job owns is that job's to judge
    (#1875). A measured leg with no floor of its own is red by name: it
    has no line to be held to, and `coverage:adopt` writes one.

    Returns:
        0 when every leg holds and every recorded leg reported (and when
        there is no floor, which holds nothing), else 1.

    """
    record, status = load(path)
    if record is None:
        return status
    measured, problems = legs(source)
    if problems:
        complain(problems, path)
        return 1
    names = [leg for leg, _ in measured]
    for leg, pct in measured:
        held = record.floors.get(leg)
        if held is None:
            findings.finding(
                leg,
                f"measured {pct}% and {path} holds no floor of its own for "
                "it: every leg is held to its own (#2143). Write it from "
                "this measurement with `mise run coverage:adopt`",
            )
            status = 1
            continue
        if pct < held.floor:
            findings.finding(leg, f"{pct}% is below its derived floor {held.floor}%")
            status = 1
            continue
        print(f"{label}: {leg} leg holds its floor {held.floor} ({pct}%)")
    for leg in unheld(source):
        # Reported, so not missing; not measured exactly, so not held,
        # and its floor is left as it stands (#2163).
        stands = record.floors.get(leg)
        print(
            f"{label}: {leg} leg not held: this run cannot measure it exactly"
            " (the measurement says why); "
            + (
                "it has no floor of its own yet"
                if stands is None
                else f"its floor {stands.floor} stands"
            ),
        )
        names.append(leg)
    owned = [leg for leg in recorded(record) if owner(leg) in jobs and leg not in names]
    if unaccounted(record, owned):
        status = 1
    return status


def planned_majors(text: str) -> list[str]:
    """Decode the plan's majors output: a JSON array of major strings (#1880).

    The plan writes `pgrx-plan.sh`'s stdout as it is, and this is the
    one reader of it: no shell takes the JSON apart first.

    Returns:
        The majors, in the plan's order.

    Raises:
        decoded.UndecodableError: not JSON, not an array, or an element
            that is not a string spelling a canonical major.

    """
    value = decoded.as_array(decoded.load_json(text), "the value")
    majors: list[str] = []
    for item in value:
        if not isinstance(item, str):
            msg = f"{json.dumps(item)} is not a string"
            raise decoded.UndecodableError(msg)
        if not MAJOR.fullmatch(item):
            msg = f"{json.dumps(item)} is not a canonical major"
            raise decoded.UndecodableError(msg)
        majors.append(item)
    return majors


def planned(path: Path, majors_json: str) -> int:
    """Hold the floor's record to this run's plan (#1875).

    Every recorded leg must have its owning job planned: the gate job
    always is, and `pgrx:NN` is when NN is among the majors the plan
    made, `majors_json` being `pgrx-plan.sh`'s output verbatim (#1880).
    Run by the `plan` job, before any leg starts, so a recorded leg no
    job will measure is red once, by name, rather than judged by no one.

    Returns:
        0 when every recorded leg has a planned owner (and when there is
        no floor or no record), else 1; 1 for majors that do not decode.

    """
    try:
        majors = planned_majors(majors_json)
    except decoded.UndecodableError as exc:
        findings.finding(
            "",
            f"the planned majors '{majors_json}' are not a JSON array "
            f"of canonical major strings: {exc}",
        )
        return 1
    record, status = load(path)
    if record is None:
        return status
    jobs = {GATE_JOB} | {f"pgrx:{major}" for major in majors}
    listed = ",".join(majors) or "none"
    orphaned = [
        leg
        for leg in record.legs
        if owner(leg) not in jobs and leg not in record.dropped
    ]
    for leg in orphaned:
        findings.finding(
            "",
            f"the floor records {leg}, measured by the {owner(leg)} "
            f"job, and this run plans none (planned majors: {listed})",
            [needs(leg).removeprefix("  ")],
        )
    return 1 if orphaned else 0


class Args(argparse.Namespace):
    """One subcommand's arguments, each typed as parsed."""

    path: Path
    command: str
    leg: str | None
    provenance: str
    label: str
    job: list[str]
    majors_json: str
    adopt: bool


def main(argv: list[str]) -> int:
    """Run one subcommand over the repository's floor file.

    Returns:
        A process exit status.

    """
    parser = argparse.ArgumentParser(description="the derived coverage floor")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    reader = sub.add_parser(
        "read", help="print each gate leg's floor to enforce, refusing drift"
    )
    reader.add_argument(
        "--leg",
        choices=SEPARATE,
        help="print the floor of a leg read off the gate (empty when it has none)",
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
    plan.add_argument(
        "--majors-json",
        required=True,
        help="the plan's majors output verbatim: a JSON array of major strings",
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
        return planned(args.path, args.majors_json)
    return derive(args.path, typed_std.stdin_text(), args.provenance, adopt=args.adopt)
