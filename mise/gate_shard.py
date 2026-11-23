#!/usr/bin/env python3
"""Run one shard of a gate leg (#2049).

ci.yml's gate job is a matrix of legs, and the lint leg runs on several
runners side by side: in a Rust tree three or four lints that each
compile the workspace shared one runner's four cores, and the leg was
the slowest of the gate (release-lab 9m12s, edtf 6m00s). Every shard
deals the same `lint:*` tasks the same way and runs its own share.

    gate_shard.py run <leg> <shard> <of>   run this shard of the leg
    gate_shard.py deal <of>                print every shard's lints

A leg of one shard is `mise run ci:<leg>`, exactly as a laptop runs it.
The lint leg of more is `mise run a ::: b ::: …` over this shard's
share of the tasks `ci:lint`'s `lint:*` collects, from mise's own task
list; the union of the shards is that list, each task once.

THE DEAL is longest first: the costliest lint goes to the shard with the
least dealt, then the next. A lint's cost is its row in
mise/gate-shards.toml where its `when` names a tracked file, and the
table's default otherwise, so a lint that skips in this tree weighs what
a skip weighs. Ties go by name, then by shard number: every shard
computes the whole deal from the same tree and gets the same answer.

stdlib only.
"""

import fnmatch
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import decoded
from gate_tools import matches
from tracked import tracked

TABLE = Path(__file__).resolve().parent / "gate-shards.toml"
LINTS = "lint:*"
LEGS = ("lint", "test", "properties")


@dataclass(frozen=True)
class Costs:
    """The table as decoded: shard count, default, and each heavy lint."""

    shards: int
    default: int
    rows: dict[str, tuple[int, tuple[str, ...] | None]]


def load(path: Path = TABLE) -> Costs:
    """Read the cost table.

    Returns:
        Its shard count, default cost and rows.

    Raises:
        ValueError: a field is missing, or not the type it must be.

    """
    doc = decoded.load_toml(path.read_bytes())
    shards = decoded.as_int(doc.get("shards"), "shards")
    default = decoded.as_int(doc.get("default"), "default")
    rows: dict[str, tuple[int, tuple[str, ...] | None]] = {}
    for name, value in decoded.as_object(doc.get("lint", {}), "lint").items():
        row = decoded.as_object(value, f"lint.{name}")
        seconds = decoded.as_int(row.get("seconds"), f"lint.{name}.seconds")
        when: tuple[str, ...] | None = None
        if "when" in row:
            raw = row["when"]
            if not decoded.is_strings(raw) or not raw:
                msg = f"lint.{name}.when: a non-empty list of pathspecs"
                raise ValueError(msg)
            when = tuple(raw)
        rows[name] = (seconds, when)
    if shards < 1:
        msg = "shards: at least 1"
        raise ValueError(msg)
    return Costs(shards, default, rows)


def cost(name: str, costs: Costs, files: list[str]) -> int:
    """Weigh one lint in this tree.

    Returns:
        Its row's seconds where its `when` names a tracked file (or it
        has none), else the default.

    """
    row = costs.rows.get(name)
    if row is None:
        return costs.default
    seconds, when = row
    if when is None or any(matches(p, f) for p in when for f in files):
        return seconds
    return costs.default


def deal(names: list[str], of: int, weigh: dict[str, int]) -> list[list[str]]:
    """Deal the lints across `of` shards, costliest first.

    Returns:
        Each shard's lints, shard 1 first, each list sorted by name.

    """
    loads = [0] * of
    hands: list[list[str]] = [[] for _ in range(of)]
    for name in sorted(names, key=lambda n: (-weigh[n], n)):
        at = min(range(of), key=lambda i: (loads[i], i))
        loads[at] += weigh[name]
        hands[at].append(name)
    return [sorted(h) for h in hands]


def lint_tasks(mise: str) -> list[str]:
    """List the tasks `ci:lint`'s `lint:*` collects, as mise lists them.

    Returns:
        The task names, sorted.

    """
    out = subprocess.run(
        [mise, "tasks", "ls", "--hidden", "--json"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    names = []
    for entry in decoded.as_array(decoded.load_json(out), "mise tasks ls"):
        name = decoded.as_str(decoded.as_object(entry, "task").get("name"), "name")
        if fnmatch.fnmatchcase(name, LINTS):
            names.append(name)
    return sorted(names)


def hands(of: int, mise: str, costs: Costs) -> list[list[str]]:
    """Deal this tree's lints over `of` shards.

    Returns:
        Each shard's lints.

    """
    names = lint_tasks(mise)
    files = tracked()
    return deal(names, of, {n: cost(n, costs, files) for n in names})


USAGE = "usage: gate_shard.py run <leg> <shard> <of> | gate_shard.py deal <of>"
USAGE_EXIT = 2


def whole(text: str) -> int | None:
    """Read a positive whole number.

    Returns:
        The number, or None when the text is not one.

    """
    return int(text) if text.isdigit() and int(text) > 0 else None


def run(leg: str, shard: int, of: int, mise: str) -> int:
    """Run one shard of a leg: `ci:<leg>` alone, or this shard's lints.

    Returns:
        mise's exit status.

    """
    if of == 1:
        return subprocess.run([mise, "run", f"ci:{leg}"], check=False).returncode
    if leg != "lint":
        print(
            f"gate_shard: the {leg} leg runs on one runner, not {of}", file=sys.stderr
        )
        return 1
    hand = hands(of, mise, load())[shard - 1]
    print(f"gate_shard: lint {shard}/{of}: {len(hand)} lints: {' '.join(hand)}")
    if not hand:
        return 0
    args = [mise, "run"]
    for at, name in enumerate(hand):
        args += [*([":::"] if at else []), name]
    sys.stdout.flush()
    return subprocess.run(args, check=False).returncode


def parse(argv: list[str]) -> tuple[str, str, int, int] | None:
    """Read the command line.

    Returns:
        The command, the leg, the shard and the count (`deal` has leg
        "lint" and shard 1), or None when it is malformed.

    """
    match argv:
        case ["deal", count] if of := whole(count):
            return "deal", "lint", 1, of
        case ["run", leg, at, count] if leg in LEGS:
            shard, of = whole(at), whole(count)
            if shard is not None and of is not None and shard <= of:
                return "run", leg, shard, of
    return None


def main(argv: list[str]) -> int:
    """Run or print the deal.

    Returns:
        The exit status: mise's for `run`, 2 for a malformed command line.

    """
    parsed = parse(argv)
    if parsed is None:
        print(USAGE, file=sys.stderr)
        return USAGE_EXIT
    command, leg, shard, of = parsed
    mise = shutil.which("mise")
    if mise is None:
        print("gate_shard: mise is not on PATH", file=sys.stderr)
        return 1
    if command == "run":
        return run(leg, shard, of, mise)
    for at, hand in enumerate(hands(of, mise, load()), 1):
        print(f"lint {at}/{of}: {' '.join(hand)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
