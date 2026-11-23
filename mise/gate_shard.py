#!/usr/bin/env python3
"""Plan the gate's shards, and run one shard of a leg (#2049).

ci.yml's gate job is a matrix of legs, and a leg can run on several
runners side by side: in a Rust tree three or four lints that each
compile the workspace shared one runner's four cores (release-lab's lint
leg took 9m12s), and the canon's test suite is 611 CPU-seconds on four.

    gate_shard.py plan                     the matrix, for GITHUB_OUTPUT
    gate_shard.py run <leg> <shard> <of>   run this shard of the leg
    gate_shard.py deal <of>                print every shard's lints

THE PLAN runs before any tool is installed, on the runner's own Python,
and reads only the tree: the lint leg gets mise/gate-shards.toml's
`shards.lint`; the test leg gets `shards.test` where a root mise config
gives `test` a run line naming the belt's test runner
(unittest_guarded.py, which keeps one shard's files), and one runner
otherwise; properties one. The shard that runs re-reads `test` from mise
and reds when it does not name the runner: the plan can fail a gate,
never pass one.

A leg of one shard is `mise run ci:<leg>`, exactly as a laptop runs it.
A lint shard runs `mise run a ::: b ::: …` over its share of the tasks
`ci:lint`'s `lint:*` collects, from mise's own task list; the union of
the shards is that list, each task once. A test shard runs `test` with
ORG_TEST_SHARD naming it, and the first also `build` (where the tree has
one) and `test:pgrx`; coverage:check runs once, after every shard, in
the gate's coverage job, over the data the shards kept.

THE DEAL is longest first: the costliest lint goes to the shard with the
least dealt, then the next. A lint's cost is its row in
mise/gate-shards.toml where its `when` names a tracked file, and the
table's default otherwise, so a lint that skips in this tree weighs what
a skip weighs. Ties go by name, then by shard number: every shard
computes the whole deal from the same tree and gets the same answer.

stdlib only.
"""

import fnmatch
import json
import os
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
# The belt's test runner, which takes a share of a root's test files.
RUNNER = "unittest_guarded.py"
# The root mise configs a repository's `test` task is defined in.
CONFIGS = ("mise.toml", ".mise.toml", ".config/mise.toml", ".config/mise/config.toml")


@dataclass(frozen=True)
class Costs:
    """The table as decoded: shard counts, default, and each heavy lint."""

    lint_shards: int
    test_shards: int
    default: int
    rows: dict[str, tuple[int, tuple[str, ...] | None]]


def load(path: Path = TABLE) -> Costs:
    """Read the cost table.

    Returns:
        Its shard counts, default cost and rows.

    Raises:
        ValueError: a field is missing, or not the type it must be.

    """
    doc = decoded.load_toml(path.read_bytes())
    shards = decoded.as_object(doc.get("shards"), "shards")
    counts = [
        decoded.as_int(shards.get("lint"), "shards.lint"),
        decoded.as_int(shards.get("test"), "shards.test"),
    ]
    lint = decoded.as_object(doc.get("lint"), "lint")
    default = decoded.as_int(lint.get("default"), "lint.default")
    rows: dict[str, tuple[int, tuple[str, ...] | None]] = {}
    for name, value in decoded.as_object(lint.get("cost", {}), "lint.cost").items():
        row = decoded.as_object(value, f"lint.cost.{name}")
        seconds = decoded.as_int(row.get("seconds"), f"lint.cost.{name}.seconds")
        when: tuple[str, ...] | None = None
        if "when" in row:
            raw = row["when"]
            if not decoded.is_strings(raw) or not raw:
                msg = f"lint.cost.{name}.when: a non-empty list of pathspecs"
                raise ValueError(msg)
            when = tuple(raw)
        rows[name] = (seconds, when)
    if min(counts) < 1:
        msg = "shards: at least 1 for each leg"
        raise ValueError(msg)
    return Costs(counts[0], counts[1], default, rows)


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
    # Costliest first; a stable sort keeps equal costs in name order.
    for name in sorted(sorted(names), key=weigh.__getitem__, reverse=True):
        at = loads.index(min(loads))
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


def names_runner(run: object) -> bool:
    """Say whether a task's `run` names the belt's test runner.

    Returns:
        True when the run line, or any line of a run list, names it.

    """
    lines = [run] if isinstance(run, str) else run if decoded.is_strings(run) else []
    return any(RUNNER in line for line in lines)


def slices(files: list[str], root: Path) -> bool:
    """Say whether this tree's `test` task runs the belt's test runner.

    Returns:
        True when a tracked root mise config defines `test` with a run
        line naming unittest_guarded.py.

    """
    for name in CONFIGS:
        if name not in files:
            continue
        doc = decoded.load_toml((root / name).read_bytes())
        tasks = doc.get("tasks")
        test = tasks.get("test") if decoded.is_object(tasks) else None
        if decoded.is_object(test) and names_runner(test.get("run")):
            return True
    return False


def plan(costs: Costs, files: list[str], root: Path) -> list[dict[str, object]]:
    """Lay out the gate's matrix: one row per runner.

    Returns:
        The rows, lint shards first, then test, then properties.

    """
    counts = {
        "lint": costs.lint_shards,
        "test": costs.test_shards if slices(files, root) else 1,
        "properties": 1,
    }
    return [
        {"leg": leg, "shard": at, "of": of}
        for leg, of in counts.items()
        for at in range(1, of + 1)
    ]


def task_info(mise: str, name: str) -> dict[str, object] | None:
    """Ask mise for one task.

    Returns:
        The task's JSON, or None when the tree defines no such task.

    """
    done = subprocess.run(
        [mise, "tasks", "info", name, "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        return None
    return decoded.as_object(decoded.load_json(done.stdout), name)


def test_shard(shard: int, of: int, mise: str) -> int:
    """Run this shard of the test leg.

    Returns:
        mise's exit status, or 1 when `test` does not run the belt's
        test runner, so the shard could not take a share.

    """
    test = task_info(mise, "test")
    if test is None or not names_runner(test.get("run")):
        print(
            f"gate_shard: the plan gave the test leg {of} shards, but `test`"
            f" does not run {RUNNER}, so a shard would run the whole suite",
            file=sys.stderr,
        )
        return 1
    tasks = ["test"]
    if shard == 1:
        tasks += [t for t in ("build",) if task_info(mise, t) is not None]
        tasks.append("test:pgrx")
    return run_tasks(mise, tasks, {"ORG_TEST_SHARD": f"{shard}/{of}"})


def run_tasks(mise: str, tasks: list[str], env: dict[str, str]) -> int:
    """Run tasks side by side, as one `mise run`.

    Returns:
        mise's exit status.

    """
    args = [mise, "run"]
    for at, name in enumerate(tasks):
        args += [*([":::"] if at else []), name]
    sys.stdout.flush()
    return subprocess.run(args, check=False, env=os.environ | env).returncode


USAGE = (
    "usage: gate_shard.py plan | gate_shard.py run <leg> <shard> <of>"
    " | gate_shard.py deal <of>"
)
USAGE_EXIT = 2


def whole(text: str) -> int | None:
    """Read a positive whole number.

    Returns:
        The number, or None when the text is not one.

    """
    return int(text) if text.isdigit() and int(text) > 0 else None


def run(leg: str, shard: int, of: int, mise: str) -> int:
    """Run one shard of a leg: `ci:<leg>` alone, or this shard's share.

    Returns:
        mise's exit status.

    """
    if of == 1:
        return run_tasks(mise, [f"ci:{leg}"], {})
    if leg == "test":
        return test_shard(shard, of, mise)
    if leg != "lint":
        print(
            f"gate_shard: the {leg} leg runs on one runner, not {of}", file=sys.stderr
        )
        return 1
    hand = hands(of, mise, load())[shard - 1]
    print(f"gate_shard: lint {shard}/{of}: {len(hand)} lints: {' '.join(hand)}")
    return run_tasks(mise, hand, {}) if hand else 0


def parse(argv: list[str]) -> tuple[str, str, int, int] | None:
    """Read the command line.

    Returns:
        The command, the leg, the shard and the count (`deal` has leg
        "lint" and shard 1), or None when it is malformed.

    """
    match argv:
        case ["plan"]:
            return "plan", "", 0, 0
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
    if command == "plan":
        rows = plan(load(), tracked(), Path.cwd())
        tests = sum(r["leg"] == "test" for r in rows)
        matrix: dict[str, object] = {"include": rows}
        print(f"legs={json.dumps(matrix, separators=(',', ':'))}")
        print(f"test-shards={tests}")
        return 0
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
