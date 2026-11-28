#!/usr/bin/env python3
"""Plan the gate's rows from the repository's own split, and run one (#2160).

ci.yml's gate job is a matrix of rows, one runner each. The canon
supplies the means to split and shard a leg; the repository decides how
its own work is split and sharded (#2160, decided with the owner):

    gate_shard.py plan                         the gate's matrix, for GITHUB_OUTPUT
    gate_shard.py plan mutants|deep-tests      one count per language, likewise
    gate_shard.py run <leg> <task> <k> <n>     run this row
    gate_shard.py union <dir>                  every shard's share is its whole task
    gate_shard.py deal <n>                     print every lint shard's lints

SPLITTING IS THE REPOSITORY'S TASKS. The plan reads the task graph from
the tracked root mise configs, and the belt's own config beside this
file, with the belt's decoder, before any tool is installed. Every task
in `test`'s dependency closure that has a run gets a row of its own, and
so does `test`'s own run if it has one. A repository that wants its slow
suite on a runner of its own makes that suite a task of its own. The
properties leg is the closure of `ci:properties`, split the same way,
and a belt task there gets a row only where the tree tracks what it
runs (APPLIES): the rule repo-mutants.yml's adoption keeps, decided
from tracked files before any tool is installed. A leg of one row, or
of none, is today's `ci:<leg>`.

THE MUTATION LEG (#1432) is `ci:mutants`: the belt's mutation legs on
what the change touches. Its rows are per language, one for each the
tree adopts (MUTATED, the file each leg's guard reads), one row each
(mutation_count, the one place that count is decided). A row runs
`ci:mutants` with MUTANTS_LEG (its language, which ci.yml sets from
the row), and a row of several shards MUTANTS_SHARD=k/n counted from
0, as the weekly run counts them. A tree adopting none gets one
`ci:mutants` row, which skips clean. Nothing is unioned: a mutation
shard's verdict is its own.

A row runs `mise run <task>`, its own depends included: a dependency
that is also a row of its own runs twice, and that is accepted (ruled
on #2160). `test`'s own run-line row runs the run line alone
(`--skip-deps`), because its depends are the other rows. A leg planned
as one row runs `ci:<leg>`, exactly as a laptop runs it; the first row
of a split test leg also runs `build` (where the tree has one) and
`test:pgrx`.

SHARD COUNTS ARE THE REPOSITORY'S CONTENT, in one tracked file it owns
at its root, `shards.toml` (FILE):

    lint = 4                          the lint leg's shards
    [test]
    "test:unit" = 4                   a test task's shards
    [properties]
    "test:python-properties" = 2      a properties task's shards
    [mutants]
    python = 48                       repo-mutants.yml, per language
    [deep-tests]
    python = 8                        repo-deep-tests.yml's properties

Anything the file does not name gets the canon's default from
mise/gate-shards.toml: one runner per task, and its lint and per-language
counts. A repository with no file plans as it does today. The file is
read by the plan only, never a workflow input. A count is an exact
integer from 1 to the canon's ceiling; anything else, an unknown key, a
task that does not exist, or a count above 1 on a task whose command has
no ADAPTER is red by name. Every row of a shard gets ORG_SHARD=k/n, the
one variable every adapter reads (mise/shard.py), and reports what it
ran; `union` holds the reports to each task's whole population.

The plan can fail a gate, never pass one: the first row of a split leg
re-reads the closure from mise itself and reds when it differs from the
one the plan read.

THE LINT DEAL is longest first: the costliest lint goes to the shard
with the least dealt, then the next. A lint's cost is its row in
mise/gate-shards.toml where its `when` names a tracked file, and the
table's default otherwise. Ties go by name, then by shard number: every
shard computes the whole deal from the same tree and gets the same
answer.

stdlib only.
"""

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import decoded
import findings
import shard
from gate_tools import matches
from tracked import tracked

if TYPE_CHECKING:
    from collections.abc import Callable

HERE = Path(__file__).resolve().parent
TABLE = HERE / "gate-shards.toml"
BELT = HERE / "config.toml"
# The repository's own counts, at its root (#2160).
FILE = "shards.toml"
LINTS = "lint:*"
LEGS = ("lint", "test", "properties", "mutants")
# The root mise configs a repository's tasks are defined in.
CONFIGS = ("mise.toml", ".mise.toml", ".config/mise.toml", ".config/mise/config.toml")
# Where each split leg's closure starts, and whether the start's own run
# is part of the leg (`test`'s is the suite; `ci:properties`'s is an echo).
STARTS = {"test": ("test", True), "properties": ("ci:properties", False)}
# What each belt task the plan may leave out runs over (#2160, the
# owner's ruling): a tracked file whose NAME matches one of the patterns,
# outside vendor/, exactly the population the task's own guard lists
# (python-properties.sh: `prop_*.py` at any depth; typescript-
# properties.sh: `*.prop.test.{ts,tsx,mts,cts}`). The plan gives the
# task no row where the tree tracks none: it would skip clean there.
# mise/test_gate_shard.py holds each row to its task's guard.
APPLIES = {
    "test:python-properties": ("prop_*.py",),
    "test:typescript-properties": (
        "*.prop.test.ts",
        "*.prop.test.tsx",
        "*.prop.test.mts",
        "*.prop.test.cts",
    ),
}
# GitHub refuses a matrix of more than 256 jobs.
MATRIX_LIMIT = 256
# The languages each per-language count is for; a count above 1 needs
# the leg's shard support, ADAPTED below.
LANGUAGES = {
    "mutants": ("rust", "go", "python", "typescript"),
    "deep-tests": ("python", "typescript", "rust"),
}
ADAPTED = {
    "mutants": ("rust", "go", "python", "typescript"),
    "deep-tests": ("python",),
}
# What adopts each mutation leg (#1432): a tracked file at the root
# whose name matches, exactly the files each leg's guard reads
# (mutants-<language>.sh). A StrykerJS config other than the JSON one is
# the TypeScript leg's red, so it gets the row that says so.
# mise/test_gate_shard.py holds each row to its leg's guard.
MUTATED = {
    "rust": (".cargo/mutants.toml",),
    "go": (".gomuignore",),
    "python": ("cosmic-ray.toml",),
    "typescript": (
        "stryker.config.json",
        "stryker.conf.*",
        "stryker.config.*",
        ".stryker.conf.*",
    ),
}


@dataclass(frozen=True)
class Costs:
    """mise/gate-shards.toml as decoded: the canon's defaults and costs."""

    lint_shards: int
    ceiling: int
    default: int
    rows: dict[str, tuple[int, tuple[str, ...] | None]]
    languages: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class Counts:
    """The repository's shards.toml as decoded; empty where it has none."""

    lint: int | None = None
    tasks: dict[str, dict[str, int]] = field(default_factory=dict)
    languages: dict[str, dict[str, int]] = field(default_factory=dict)


class PlanError(ValueError):
    """The plan cannot be laid out; each line names one defect."""

    def __init__(self, lines: list[str]) -> None:
        """Keep the defects."""
        super().__init__("\n".join(lines))
        self.lines = lines


def load(path: Path = TABLE) -> Costs:
    """Read the cost table.

    Returns:
        Its shard defaults, ceiling, default cost and rows.

    Raises:
        ValueError: a field is missing, or not the type it must be.

    """
    doc = decoded.load_toml(path.read_bytes())
    shards = decoded.as_object(doc.get("shards"), "shards")
    lint_shards = decoded.as_int(shards.get("lint"), "shards.lint")
    ceiling = decoded.as_int(shards.get("ceiling"), "shards.ceiling")
    languages: dict[str, dict[str, int]] = {}
    for leg, names in LANGUAGES.items():
        table = decoded.as_object(shards.get(leg), f"shards.{leg}")
        languages[leg] = {
            n: decoded.as_int(table.get(n), f"shards.{leg}.{n}") for n in names
        }
        if set(table) - set(names):
            msg = f"shards.{leg}: unknown {sorted(set(table) - set(names))}"
            raise ValueError(msg)
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
    counts = [lint_shards, *(n for t in languages.values() for n in t.values())]
    if min(counts) < 1 or max(counts) > ceiling or ceiling > MATRIX_LIMIT:
        msg = (
            "shards: every count from 1 to the ceiling, and the ceiling at"
            f" most {MATRIX_LIMIT}"
        )
        raise ValueError(msg)
    return Costs(lint_shards, ceiling, default, rows, languages)


def count(value: object, where: str, ceiling: int, red: list[str]) -> int | None:
    """Read one count of the repository's file.

    Returns:
        The count, or None after naming why it is not one.

    """
    if isinstance(value, bool) or not isinstance(value, int):
        red.append(f"{FILE}: {where} = {value!r} is not a whole number of shards")
        return None
    if not 1 <= value <= ceiling:
        red.append(
            f"{FILE}: {where} = {value} is not from 1 to the canon's ceiling, {ceiling}"
        )
        return None
    return value


def table_of(key: str, value: object, ceiling: int, red: list[str]) -> dict[str, int]:
    """Read one table of the repository's file: a count per task or language.

    Returns:
        Each name's count; a defect is named in `red` and left out.

    """
    if not decoded.is_object(value):
        red.append(f"{FILE}: [{key}] is not a table of counts")
        return {}
    out: dict[str, int] = {}
    for name, raw in value.items():
        if key in LANGUAGES and name not in LANGUAGES[key]:
            known = ", ".join(LANGUAGES[key])
            red.append(f"{FILE}: [{key}] {name}: not a language ({known})")
            continue
        got = count(raw, f"[{key}] {name}", ceiling, red)
        if got is not None:
            out[name] = got
    return out


def counts_of(doc: dict[str, object], ceiling: int) -> Counts:
    """Read the repository's file, as decoded.

    Returns:
        Its counts.

    Raises:
        PlanError: a key the file may not hold, or a count that is not one.

    """
    red: list[str] = []
    lint = None
    tasks: dict[str, dict[str, int]] = {}
    languages: dict[str, dict[str, int]] = {}
    for key, value in doc.items():
        if key == "lint":
            lint = count(value, "lint", ceiling, red)
        elif key in STARTS or key in LANGUAGES:
            into = (tasks if key in STARTS else languages).setdefault(key, {})
            into.update(table_of(key, value, ceiling, red))
        else:
            known = ", ".join(("lint", *STARTS, *LANGUAGES))
            red.append(f"{FILE}: {key}: not a key the file holds ({known})")
    for leg, table in languages.items():
        for name, got in sorted(table.items()):
            if got > 1 and name not in ADAPTED[leg]:
                red.append(
                    f"{FILE}: [{leg}] {name} = {got}: {name} has no way to take a"
                    " share there, so every shard would run all of it"
                )
    if red:
        raise PlanError(red)
    return Counts(lint, tasks, languages)


def read_counts(files: list[str], root: Path, ceiling: int) -> Counts:
    """Read the repository's file where it is tracked.

    Returns:
        Its counts, or none where the tree tracks no file.

    Raises:
        PlanError: the file does not decode, or holds a defect.

    """
    if FILE not in files:
        return Counts()
    try:
        doc = decoded.load_toml((root / FILE).read_bytes())
    except (OSError, ValueError) as err:
        raise PlanError([f"{FILE}: {err}"]) from err
    return counts_of(doc, ceiling)


type Task = dict[str, object]
type Row = dict[str, object]


def task_table(files: list[str], root: Path, belt: Path = BELT) -> dict[str, Task]:
    """Read every task the tree's tracked root configs and the belt define.

    Returns:
        Each task by name; a repository's own definition wins over the belt's.

    """
    tasks: dict[str, Task] = {}
    for path in (*(root / n for n in reversed(CONFIGS) if n in files), belt):
        if not path.is_file():
            continue
        doc = decoded.load_toml(path.read_bytes())
        table = doc.get("tasks")
        if not decoded.is_object(table):
            continue
        for name, value in table.items():
            if decoded.is_object(value):
                tasks.setdefault(name, value)
            elif isinstance(value, str) or decoded.is_strings(value):
                tasks.setdefault(name, {"run": value})
    return tasks


def runs(task: Task) -> bool:
    """Say whether a task has a command of its own.

    Returns:
        True for a run line, a run list or a file task.

    """
    run = task.get("run")
    return bool(run) or bool(task.get("file")) or bool(task.get("run_windows"))


def lines(task: Task) -> list[str]:
    """Read a task's command lines.

    Returns:
        Its run line, or each line of its run list; a file task's path.

    """
    run = task.get("run")
    if isinstance(run, str):
        return [run]
    if decoded.is_strings(run):
        return list(run)
    if decoded.is_array(run):
        return [s for s in run if isinstance(s, str)]
    file = task.get("file")
    return [file] if isinstance(file, str) else []


def glob(pattern: str) -> re.Pattern[str]:
    """Compile a mise task pattern: `*` stays within one `:` group.

    Returns:
        The pattern as a regular expression.

    """
    return re.compile("".join("[^:]*" if c == "*" else re.escape(c) for c in pattern))


def depends(task: Task) -> list[tuple[str, bool]]:
    """Read what a task depends on, before and after it runs.

    Returns:
        (name or pattern, optional) for each entry.

    """
    out: list[tuple[str, bool]] = []
    for key in ("depends", "depends_post"):
        raw = task.get(key)
        entries = (
            [raw] if isinstance(raw, str) else raw if decoded.is_array(raw) else []
        )
        for entry in entries:
            if isinstance(entry, str) and entry.split():
                out.append((entry.split()[0], False))
            elif decoded.is_object(entry) and isinstance(entry.get("task"), str):
                name = decoded.as_str(entry["task"], "task")
                out.append((
                    name.split()[0] if name.split() else name,
                    entry.get("optional") is True,
                ))
    return out


def closure(
    start: str, lookup: Callable[[str], Task | None], names: list[str]
) -> tuple[list[str], list[str]]:
    """Walk a task's dependency closure.

    Returns:
        The tasks reached, `start` first then by name, and one line per
        dependency nothing defines.

    """
    seen: list[str] = []
    red: list[str] = []
    todo = [start]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        task = lookup(name)
        if task is None:
            continue
        seen.append(name)
        for want, optional in depends(task):
            found = (
                [n for n in names if glob(want).fullmatch(n)] if "*" in want else [want]
            )
            for dep in found:
                if lookup(dep) is None:
                    if not optional:
                        red.append(
                            f"{name} depends on {dep}, which no tracked root mise"
                            " config or the belt defines"
                        )
                    continue
                todo.append(dep)
    rest = sorted(n for n in seen if n != start)
    return ([start] if start in seen else []) + rest, red


# Each runner the belt takes a share of, and how its command names it:
# the belt's test runner, the belt's two property tasks, and the three
# runners `measured` hands the share to in the runner's own terms.
ADAPTERS: tuple[tuple[str, Callable[[str], bool]], ...] = (
    ("unittest_guarded.py", lambda line: "unittest_guarded.py" in line),
    ("python-properties", lambda line: "python-properties.sh" in line),
    ("typescript-properties", lambda line: "typescript-properties.sh" in line),
    (
        "vitest",
        lambda line: "measured" in line and re.search(r"\bvitest\b", line) is not None,
    ),
    (
        "nextest",
        lambda line: "measured" in line and re.search(r"\bnextest\b", line) is not None,
    ),
    (
        "go test",
        lambda line: (
            "measured" in line and re.search(r"\bgo\s+test\b", line) is not None
        ),
    ),
)


def adapter(task: Task) -> str | None:
    """Name the runner a task's command takes a share through.

    Returns:
        The adapter's name, or None when no line of its command names one.

    """
    for name, test in ADAPTERS:
        if any(test(line) for line in lines(task)):
            return name
    return None


def applies(task: str, files: list[str]) -> bool:
    """Say whether the tree tracks what a task runs over.

    Returns:
        True for a task APPLIES does not name, or one with a tracked
        file outside vendor/ whose name matches one of its patterns.

    """
    patterns = APPLIES.get(task)
    if patterns is None:
        return True
    return any(
        fnmatch.fnmatchcase(f.rsplit("/", 1)[-1], p)
        for f in files
        if not f.startswith("vendor/")
        for p in patterns
    )


def mutated(language: str, files: list[str]) -> bool:
    """Say whether the tree adopts a language's mutation leg.

    Returns:
        True when a tracked root file matches one of its MUTATED names.

    """
    return any(
        f.count("/") == p.count("/") and fnmatch.fnmatchcase(f, p)
        for f in files
        for p in MUTATED[language]
    )


def mutation_rows(costs: Costs, counts: Counts, files: list[str]) -> list[Row]:
    """Lay out the mutation leg (#1432): each adopted language's rows.

    Each language's count is mutation_count's. A tree that adopts no
    language gets one row, `ci:mutants`, which skips clean.

    Returns:
        The leg's rows, each adopted language in LANGUAGES order.

    """
    adopted = [n for n in LANGUAGES["mutants"] if mutated(n, files)]
    if not adopted:
        return [{"leg": "mutants", "task": "ci:mutants", "shard": 1, "of": 1}]
    return [
        {"leg": "mutants", "task": "ci:mutants", "language": n, "shard": k, "of": of}
        for n in adopted
        for of in (mutation_count(n, costs, counts),)
        for k in range(1, of + 1)
    ]


def mutation_count(language: str, costs: Costs, counts: Counts) -> int:
    """Count one language's rows of the gate's mutation leg.

    THE ONE PLACE the gate leg's count is decided (#1432): one row per
    adopted language, the owner's word on #1432. The weekly run's
    `[mutants]` count is repo-mutants.yml's, never the gate's; a change
    touches a share of the population, and each row plans for itself.

    Returns:
        The language's row count.

    """
    del language, costs, counts
    return 1


def leg_tasks(
    leg: str, tasks: dict[str, Task], files: list[str]
) -> tuple[list[str], list[str], list[str]]:
    """List the tasks that are a split leg's rows.

    Returns:
        The tasks with a command of their own that apply here, the ones
        that do not, and the closure's defects.

    """
    start, own = STARTS[leg]
    reached, red = closure(start, tasks.get, sorted(tasks))
    rows = [n for n in reached if runs(tasks[n]) and (n != start or own)]
    return (
        [n for n in rows if applies(n, files)],
        [n for n in rows if not applies(n, files)],
        red,
    )


def plan(
    costs: Costs, files: list[str], root: Path, belt: Path = BELT
) -> list[dict[str, object]]:
    """Lay out the gate's matrix: one row per runner.

    Returns:
        The rows, lint first, then test, then properties, then mutants.

    Raises:
        PlanError: a defect in the repository's file or its task graph.

    """
    counts = read_counts(files, root, costs.ceiling)
    tasks = task_table(files, root, belt)
    red: list[str] = []
    lint = counts.lint or costs.lint_shards
    rows: list[dict[str, object]] = [
        {"leg": "lint", "task": "ci:lint", "shard": k, "of": lint}
        for k in range(1, lint + 1)
    ]
    for leg in ("test", "properties"):
        named = counts.tasks.get(leg, {})
        names, idle, defects = leg_tasks(leg, tasks, files)
        red += defects
        for task in sorted(set(named) & set(idle)):
            red.append(
                f"{FILE}: [{leg}] {task}: the tree tracks nothing it runs"
                f" ({', '.join(APPLIES[task])}), so it has no row to shard"
            )
        for task in sorted(set(named) - set(names) - set(idle)):
            known = ", ".join(names) or "none"
            red.append(f"{FILE}: [{leg}] {task}: not a task of the {leg} leg ({known})")
        for task, got in sorted(named.items()):
            if got > 1 and task in names and adapter(tasks[task]) is None:
                red.append(
                    f"{FILE}: [{leg}] {task} = {got}: its command runs no runner the"
                    " belt can take a share of (unittest_guarded.py, the property"
                    " tasks, or vitest, nextest or go test through measured), so"
                    " every shard would run all of it"
                )
        legs = [(t, named.get(t, 1)) for t in names]
        if sum(n for _, n in legs) <= 1:
            legs = [(f"ci:{leg}", 1)]
        rows += [
            {"leg": leg, "task": task, "shard": k, "of": of}
            for task, of in legs
            for k in range(1, of + 1)
        ]
    rows += mutation_rows(costs, counts, files)
    for row in rows:
        row["label"] = label(row)
    if len(rows) > MATRIX_LIMIT:
        red.append(
            f"the plan is {len(rows)} rows, and GitHub runs at most"
            f" {MATRIX_LIMIT} in one matrix"
        )
    if red:
        raise PlanError(red)
    return rows


def label(row: dict[str, object]) -> str:
    """Name a row for its job: the leg, its task where split, its shard.

    Returns:
        `lint 2/4`, `test`, `test test:unit`, `test test:unit 3/4`,
        `mutants python 3/48`.

    """
    leg, task, at, of = row["leg"], row["task"], row["shard"], row["of"]
    name = str(leg) if task == f"ci:{leg}" else f"{leg} {task}"
    if "language" in row:
        name = f"{leg} {row['language']}"
    return f"{name} {at}/{of}" if of != 1 else name


def plan_languages(
    leg: str, costs: Costs, files: list[str], root: Path
) -> dict[str, int]:
    """Lay out one per-language workflow's counts.

    Returns:
        Each language's count: the repository's where it names one.

    """
    counts = read_counts(files, root, costs.ceiling)
    return costs.languages[leg] | counts.languages.get(leg, {})


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


def mise_tasks(mise: str) -> list[str]:
    """List every task mise defines in this tree.

    Returns:
        The task names, sorted.

    """
    out = subprocess.run(
        [mise, "tasks", "ls", "--hidden", "--json"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    entries = decoded.as_array(decoded.load_json(out), "mise tasks ls")
    return sorted(
        decoded.as_str(decoded.as_object(e, "task").get("name"), "name")
        for e in entries
    )


def lint_tasks(mise: str) -> list[str]:
    """List the tasks `ci:lint`'s `lint:*` collects, as mise lists them.

    Returns:
        The task names, sorted.

    """
    return [n for n in mise_tasks(mise) if fnmatch.fnmatchcase(n, LINTS)]


def hands(of: int, mise: str, costs: Costs) -> list[list[str]]:
    """Deal this tree's lints over `of` shards.

    Returns:
        Each shard's lints.

    """
    names = lint_tasks(mise)
    files = tracked()
    return deal(names, of, {n: cost(n, costs, files) for n in names})


def task_info(mise: str, name: str) -> Task | None:
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


def held(leg: str, mise: str) -> list[str]:
    """Hold the plan's closure of a split leg to mise's own.

    Returns:
        One line per difference; empty when mise agrees with the plan.

    """
    names = mise_tasks(mise)
    cache: dict[str, Task | None] = {}

    def lookup(name: str) -> Task | None:
        if name not in cache:
            cache[name] = task_info(mise, name) if name in names else None
        return cache[name]

    start, own = STARTS[leg]
    reached, _ = closure(start, lookup, names)
    files = tracked()
    live = [
        n
        for n in reached
        if (t := lookup(n)) is not None
        and runs(t)
        and (n != start or own)
        and applies(n, files)
    ]
    planned, _, _ = leg_tasks(leg, task_table(files, Path.cwd()), files)
    if live == planned:
        return []
    line = (
        f"the plan read the {leg} leg's tasks as {', '.join(planned) or 'none'}, and"
        f" mise reads them as {', '.join(live) or 'none'}: a task defined where"
        " the plan cannot read it (a file task, an untracked config) cannot be split"
    )
    return [line]


def run_args(mise: str, tasks: list[list[str]]) -> list[str]:
    """Spell tasks as one `mise run`, side by side.

    Returns:
        The argument vector.

    """
    args = [mise, "run"]
    for at, words in enumerate(tasks):
        args += [*([":::"] if at else []), *words]
    return args


def run_tasks(mise: str, tasks: list[list[str]], env: dict[str, str]) -> int:
    """Run tasks side by side, as one `mise run`.

    Returns:
        mise's exit status.

    """
    sys.stdout.flush()
    return subprocess.run(
        run_args(mise, tasks), check=False, env=os.environ | env
    ).returncode


def run_row(leg: str, task: str, at: int, of: int, mise: str) -> int:
    """Run one row of a split test or properties leg.

    Returns:
        mise's exit status, or 1 when the plan and mise disagree.

    """
    first = at == 1 and task == first_task(leg)
    if first:
        red = held(leg, mise)
        for line in red:
            findings.finding("", line)
        if red:
            return 1
    start, _ = STARTS[leg]
    env = {shard.VARIABLE: f"{at}/{of}", shard.TASK: task}
    # `--skip-deps` applies to a whole `mise run`, so `test`'s own run
    # line goes in a run of its own, beside the one that runs the rest.
    others: list[list[str]] = []
    if leg == "test" and first:
        others += [["build"]] if task_info(mise, "build") is not None else []
        others.append(["test:pgrx"])
    if task != start:
        status = run_tasks(mise, [[task], *others], env)
    elif others:
        status = run_side_by_side(mise, [[["--skip-deps", task]], others], env)
    else:
        status = run_tasks(mise, [["--skip-deps", task]], env)
    if leg == "test" and status == 0:
        status = rust_export(mise, task)
    return status


def run_side_by_side(
    mise: str, groups: list[list[list[str]]], env: dict[str, str]
) -> int:
    """Run several `mise run`s at once.

    Returns:
        0 when every run passed, else the first non-zero status.

    """
    sys.stdout.flush()
    procs = [subprocess.Popen(run_args(mise, g), env=os.environ | env) for g in groups]
    codes = [p.wait() for p in procs]
    return next((c for c in codes if c), 0)


def rust_export(mise: str, task: str) -> int:
    """Keep this row's Rust measurement as a report the coverage job can read.

    A row's profiles need its own instrumented binaries, so the row
    renders them here (coverage-measure.sh rust-export) into the run
    directory it keeps; elsewhere it does nothing.

    Returns:
        0, or the export's status.

    """
    run_dir = os.environ.get("ORG_GATE_COVERAGE_RUN", "")
    if (
        not run_dir
        or "Cargo.toml" not in tracked("Cargo.toml")
        or not Path(".coverage-floor").is_file()
    ):
        return 0
    argv = [
        mise,
        "exec",
        "--",
        "bash",
        str(HERE / "coverage-measure.sh"),
        task,
        "rust-export",
    ]
    sys.stdout.flush()
    return subprocess.run(argv, check=False).returncode


def first_task(leg: str) -> str:
    """Name the task of a split leg's first row, from the plan's own order.

    Returns:
        The first row's task, as the plan laid it out in this tree.

    """
    rows = plan(load(), tracked(), Path.cwd())
    return next(str(r["task"]) for r in rows if r["leg"] == leg)


USAGE = (
    "usage: gate_shard.py plan [mutants|deep-tests]"
    " | gate_shard.py run <leg> <task> <shard> <of>"
    " | gate_shard.py union <reports-dir> | gate_shard.py deal <of>"
)
USAGE_EXIT = 2


def whole(text: str) -> int | None:
    """Read a positive whole number.

    Returns:
        The number, or None when the text is not one.

    """
    return int(text) if text.isdigit() and int(text) > 0 else None


def run_mutants(at: int, of: int, mise: str) -> int:
    """Run one mutation row: its language's leg, its share of the change.

    ci.yml names the row's language in MUTANTS_LEG; the legs count their
    shards from 0 (MUTANTS_SHARD=k/n, k < n), as the weekly run does.

    Returns:
        mise's exit status, or 1 when a sharded row names no language.

    """
    if of > 1 and not os.environ.get("MUTANTS_LEG"):
        findings.finding("", f"mutants {at}/{of}: MUTANTS_LEG names no language")
        return 1
    env = {"MUTANTS_SHARD": f"{at - 1}/{of}"} if of > 1 else {}
    return run_tasks(mise, [["ci:mutants"]], env)


def run(leg: str, task: str, at: int, of: int, mise: str) -> int:
    """Run one row: `ci:<leg>` alone, a lint shard's share, or one task's.

    Returns:
        mise's exit status.

    """
    if leg == "mutants":
        return run_mutants(at, of, mise)
    if task == f"ci:{leg}" and of == 1:
        return run_tasks(mise, [[task]], {})
    if leg == "lint":
        hand = hands(of, mise, load())[at - 1]
        print(f"gate_shard: lint {at}/{of}: {len(hand)} lints: {' '.join(hand)}")
        return run_tasks(mise, [[n] for n in hand], {}) if hand else 0
    return run_row(leg, task, at, of, mise)


def parse(argv: list[str]) -> tuple[str, str, str, int, int] | None:
    """Read the command line.

    Returns:
        The command, the leg, the task, the shard and the count, or None
        when it is malformed.

    """
    match argv:
        case ["plan"]:
            return "plan", "", "", 0, 0
        case ["plan", leg] if leg in LANGUAGES:
            return "plan", leg, "", 0, 0
        case ["union", directory]:
            return "union", "", directory, 0, 0
        case ["deal", count] if of := whole(count):
            return "deal", "lint", "", 1, of
        case ["run", leg, task, at, count] if leg in LEGS and task:
            k, of = whole(at), whole(count)
            if k is not None and of is not None and k <= of:
                return "run", leg, task, k, of
    return None


def print_plan(leg: str) -> int:
    """Print a plan for GITHUB_OUTPUT.

    Returns:
        0, or 1 after naming every defect.

    """
    try:
        counts = plan_languages(leg, load(), tracked(), Path.cwd()) if leg else {}
        rows = [] if leg else plan(load(), tracked(), Path.cwd())
    except PlanError as err:
        for line in err.lines:
            findings.finding("", line)
        return 1
    if leg:
        # The mutation legs name a shard from 0 (MUTANTS_SHARD=k/n,
        # k < n, as they always have); everything else from 1.
        base = 0 if leg == "mutants" else 1
        for name, n in counts.items():
            shards: list[int] = list(range(base, n + base))
            print(f"{name}-shards={json.dumps(shards)}")
            print(f"{name}-of={n}")
        return 0
    split = {
        str(r["leg"])
        for r in rows
        if r["leg"] in STARTS and r["task"] != f"ci:{r['leg']}"
    }
    matrix: dict[str, object] = {"include": rows}
    print(f"legs={json.dumps(matrix, separators=(',', ':'))}")
    print(f"split={'true' if split else 'false'}")
    print(f"test-split={'true' if 'test' in split else 'false'}")
    return 0


def union(directory: str) -> int:
    """Hold every row's shard reports to the plan's tasks.

    Returns:
        0 when every sharded task's union is its whole population.

    """
    try:
        rows = plan(load(), tracked(), Path.cwd())
    except PlanError as err:
        for line in err.lines:
            findings.finding("", line)
        return 1
    planned = {str(r["task"]): int(str(r["of"])) for r in rows if r["leg"] in STARTS}
    return shard.main([
        "union",
        directory,
        *(f"{t}={n}" for t, n in sorted(planned.items())),
    ])


def main(argv: list[str]) -> int:
    """Plan, run a row, check the union, or print the deal.

    Returns:
        The exit status: mise's for `run`, 2 for a malformed command line.

    """
    parsed = parse(argv)
    if parsed is None:
        print(USAGE, file=sys.stderr)
        return USAGE_EXIT
    command, leg, task, at, of = parsed
    if command == "plan":
        return print_plan(leg)
    if command == "union":
        return union(task)
    mise = shutil.which("mise")
    if mise is None:
        print("gate_shard: mise is not on PATH", file=sys.stderr)
        return 1
    if command == "run":
        return run(leg, task, at, of, mise)
    for k, hand in enumerate(hands(of, mise, load()), 1):
        print(f"lint {k}/{of}: {' '.join(hand)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
