#!/usr/bin/env python3
"""One shard's name, what it ran, and the union of every shard (#2160).

A gate row that runs one share of a task names it in one variable,
ORG_SHARD=k/n (1 <= k <= n), whatever runs the task: the belt's test
runner, the property runners, or a runner `measured` hands the share to
in that runner's own terms (vitest `--shard`, nextest `--partition`, a
share of `go test`'s packages). Unset, or 1/1, a runner runs everything,
exactly as a laptop's `mise run ci` does.

    k, n = shard.read() or (1, 1)

EACH SHARD SAYS WHAT IT RAN. A runner that takes a share calls
`report(runner, scope, population, ran)`: the whole population it chose
from (files, packages, tests) and the items it kept. Where
ORG_SHARD_REPORTS names a directory, the report is one JSON file there,
named for the row's task (ORG_SHARD_TASK), the shard, the runner and
its process, so two runners in one row, or one runner called twice,
never overwrite each other. Unset, nothing is written: a laptop has no
union to check.

THE UNION IS THE POPULATION. `shard.py union <dir> <task>=<n> …` reads
every report the rows kept and is red, by name, unless for every task
planned with n shards, every runner and scope it reported:

- every shard 1..n reported, each with the same population;
- every item of the population ran on exactly one shard: none dropped,
  none repeated, none that is not in the population.

A task planned with n > 1 that reported nothing at all is red too: its
command ran no runner that takes a share, so every shard ran all of it.
This is the rule the lint deal already keeps by construction.

stdlib only.
"""

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import decoded
import findings

VARIABLE = "ORG_SHARD"
REPORTS = "ORG_SHARD_REPORTS"
TASK = "ORG_SHARD_TASK"
FORM = re.compile(r"([1-9][0-9]*)/([1-9][0-9]*)")
USAGE = "usage: shard.py union <reports-dir> <task>=<shards> …"
USAGE_EXIT = 2


class ShardError(ValueError):
    """ORG_SHARD is set and is not k/n with 1 <= k <= n."""


def parse(raw: str) -> tuple[int, int] | None:
    """Read a shard's name.

    Returns:
        (k, n), or None for an empty name.

    Raises:
        ShardError: the name is not k/n with 1 <= k <= n.

    """
    text = raw.strip()
    if not text:
        return None
    found = FORM.fullmatch(text)
    if found is None or int(found[1]) > int(found[2]):
        msg = f"{VARIABLE}={raw!r} is not a shard (k/n, 1 <= k <= n)"
        raise ShardError(msg)
    return int(found[1]), int(found[2])


def read() -> tuple[int, int] | None:
    """Read this process's shard from the environment.

    Returns:
        (k, n), or None when the run is not sharded (unset, empty or 1/1).

    Raises:
        SystemExit: the variable is malformed, after the finding.

    """
    try:
        named = parse(os.environ.get(VARIABLE, ""))
    except ShardError as err:
        findings.finding("", str(err))
        raise SystemExit(1) from err
    return None if named is None or named == (1, 1) else named


def slug(text: str) -> str:
    """Make a name safe for a file name.

    Returns:
        The text with every character outside [A-Za-z0-9._-] as `_`.

    """
    return re.sub(r"[^A-Za-z0-9._-]", "_", text) or "_"


def report(runner: str, scope: str, population: list[str], ran: list[str]) -> None:
    """Write what this shard ran, where ORG_SHARD_REPORTS names a directory."""
    where = os.environ.get(REPORTS, "")
    named = read()
    if not where or named is None:
        return
    k, n = named
    task = os.environ.get(TASK, "")
    body = {
        "task": task,
        "runner": runner,
        "scope": scope,
        "shard": k,
        "of": n,
        "population": sorted(population),
        "ran": sorted(ran),
    }
    out = Path(where)
    out.mkdir(parents=True, exist_ok=True)
    name = f"{slug(task)}.{k}-of-{n}.{slug(runner)}.{os.getpid()}.json"
    (out / name).write_text(json.dumps(body, sort_keys=True), encoding="utf-8")


type Key = tuple[str, str, str]


def load(directory: Path) -> list[dict[str, object]]:
    """Read every report in a directory, at any depth.

    Returns:
        The reports, in file-name order.

    """
    reports = []
    for path in sorted(directory.rglob("*.json")):
        doc = decoded.as_object(decoded.load_json(path.read_bytes()), str(path))
        reports.append(doc)
    return reports


def union(reports: list[dict[str, object]], planned: dict[str, int]) -> list[str]:
    """Hold every planned task's shards to their population.

    Returns:
        One line per defect, empty when every union is whole.

    """
    groups: dict[Key, dict[int, list[dict[str, object]]]] = {}
    for doc in reports:
        task = decoded.as_str(doc.get("task"), "task")
        runner = decoded.as_str(doc.get("runner"), "runner")
        scope = decoded.as_str(doc.get("scope"), "scope")
        k = decoded.as_int(doc.get("shard"), "shard")
        groups.setdefault((task, runner, scope), {}).setdefault(k, []).append(doc)
    red: list[str] = []
    for task, n in sorted(planned.items()):
        if n > 1 and not any(key[0] == task for key in groups):
            red.append(
                f"{task}: planned on {n} shards, and no shard reported what it"
                " ran: its command runs no runner that takes a share"
            )
    for (task, runner, scope), shards in sorted(groups.items()):
        name = f"{task}: {runner} ({scope})" if scope else f"{task}: {runner}"
        planned_n = planned.get(task)
        if planned_n is None:
            red.append(f"{name}: reported by a task the plan did not shard")
            continue
        red += whole(name, planned_n, shards)
    return red


def whole(name: str, n: int, shards: dict[int, list[dict[str, object]]]) -> list[str]:
    """Hold one runner's shards of one task to its population.

    Returns:
        One line per defect.

    """
    red: list[str] = []
    missing = [k for k in range(1, n + 1) if k not in shards]
    if missing:
        red.append(
            f"{name}: shard(s) {', '.join(map(str, missing))} of {n} reported nothing"
        )
    populations: set[tuple[str, ...]] = set()
    ran: Counter[str] = Counter()
    for docs in shards.values():
        for doc in docs:
            if decoded.as_int(doc.get("of"), "of") != n:
                red.append(f"{name}: a shard named itself of {doc.get('of')}, not {n}")
            chosen = decoded.as_array(doc.get("population"), "population")
            populations.add(tuple(decoded.as_str(p, "population") for p in chosen))
            ran.update(
                decoded.as_str(r, "ran")
                for r in decoded.as_array(doc.get("ran"), "ran")
            )
    if len(populations) > 1:
        red.append(
            f"{name}: the shards chose from {len(populations)} different populations"
        )
        return red
    population = set(next(iter(populations), ()))
    dropped = sorted(population - set(ran))
    repeated = sorted(item for item, count in ran.items() if count > 1)
    foreign = sorted(set(ran) - population)
    if dropped:
        red.append(f"{name}: ran on no shard: {', '.join(dropped)}")
    if repeated:
        red.append(f"{name}: ran on more than one shard: {', '.join(repeated)}")
    if foreign:
        red.append(f"{name}: ran but is not in the population: {', '.join(foreign)}")
    return red


def planned_counts(args: list[str]) -> dict[str, int] | None:
    """Read `<task>=<shards>` arguments.

    Returns:
        Each task's count, or None when one is malformed.

    """
    counts: dict[str, int] = {}
    for arg in args:
        task, sep, count = arg.rpartition("=")
        if not sep or not task or not count.isdecimal() or int(count) < 1:
            return None
        counts[task] = int(count)
    return counts


def main(argv: list[str]) -> int:
    """Check the union of every shard's report.

    Returns:
        0 when every planned task's shards are its whole population, 1
        when not, 2 for a malformed command line.

    """
    match argv:
        case ["union", directory, *rest] if (
            counts := planned_counts(rest)
        ) is not None:
            pass
        case _:
            print(USAGE, file=sys.stderr)
            return USAGE_EXIT
    red = union(load(Path(directory)), counts)
    for line in red:
        findings.finding("", line)
    if red:
        return 1
    sharded = sorted(t for t, n in counts.items() if n > 1)
    named = ", ".join(sharded) or "none sharded"
    print(f"shard: every shard's share is its whole population: {named}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
