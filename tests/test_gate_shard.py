"""The gate's plan: the repository's split, its counts, the lint deal.

belt/gate_shard.py lays out the gate's rows from the repository's own
tasks and shards.toml (#2160), and deals the `lint:*` tasks across the
lint shards from mise/gate-shards.toml (#2049). These rows hold the
plan to the split (one row per task, a count per task, today's matrix
where nothing is split), every refusal of the file to its message, the
table to the belt (every row a lint the belt defines), the deal to its
two properties (each lint on exactly one shard; the costliest on
different shards), and a cost to the tree.
"""

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import decoded, gate_shard
from belt.tracked import tracked
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# The canon's lint shards (mise/gate-shards.toml), and the test rows of
# THREE with the own run added: its run, test:py, test:rs and test:ts.
CANON_LINT_SHARDS = 4
TEST_ROWS = 4

HERE = support.MISE
CI = HERE.parent / ".github" / "workflows" / "ci.yml"


# The cost table names the belt's lints and the matrix's count.
def test_every_row_is_a_belt_lint() -> None:
    """A row naming no belt lint is a stale figure, red by name."""
    config = decoded.load_toml((HERE / "config.toml").read_bytes())
    defined = set(decoded.as_object(config["tasks"], "tasks"))
    stale = sorted(set(gate_shard.load().rows) - defined)
    none: list[str] = []
    assert none == stale


def test_the_canon_defaults() -> None:
    """The table's counts are defaults within the ceiling (#2160)."""
    costs = gate_shard.load()
    assert costs.lint_shards == CANON_LINT_SHARDS
    mutants: dict[str, int] = {"rust": 4, "go": 4, "python": 4, "typescript": 8}
    deep: dict[str, int] = {"python": 8, "typescript": 1, "rust": 1}
    assert mutants == costs.languages["mutants"]
    assert deep == costs.languages["deep-tests"]
    assert costs.ceiling <= gate_shard.MATRIX_LIMIT


def test_the_workflow_runs_the_plan() -> None:
    """ci.yml's gate matrix is the plan job's output, nothing else."""
    text = CI.read_text("utf-8")
    assert "      matrix: ${{ fromJSON(needs.plan.outputs.legs) }}\n" in text
    assert 'belt gate-shard plan >> "${GITHUB_OUTPUT}"' in text


COSTS = gate_shard.Costs(
    lint_shards=4,
    ceiling=128,
    default=2,
    rows={
        "lint:rust": (300, ("Cargo.toml",)),
        "lint:msrv": (290, ("Cargo.toml",)),
        "lint:shell": (40, None),
    },
    languages={
        "mutants": {"rust": 4, "go": 4, "python": 48, "typescript": 1},
        "deep-tests": {"python": 8, "typescript": 1, "rust": 1},
    },
)


# Every lint once, the heavy ones apart, the same on every shard.
def test_each_lint_on_one_shard() -> None:
    """The union is the list, with no lint twice."""
    names = [f"lint:t{i}" for i in range(23)]
    hands = gate_shard.deal(names, 4, dict.fromkeys(names, 2))
    dealt: list[str] = [n for h in hands for n in h]
    want: list[str] = sorted(names)
    got: list[str] = sorted(dealt)
    assert want == got
    assert len(dealt) == len(set(dealt))


def test_the_heaviest_are_apart() -> None:
    """Two compile-heavy lints never share a runner when one is free."""
    names = ["lint:rust", "lint:msrv", "lint:shell", "lint:a", "lint:b"]
    weigh = {n: gate_shard.cost(n, COSTS, ["Cargo.toml"]) for n in names}
    hands = gate_shard.deal(names, 4, weigh)
    want: list[list[str]] = [
        ["lint:rust"],
        ["lint:msrv"],
        ["lint:shell"],
        ["lint:a", "lint:b"],
    ]
    assert want == hands


def test_more_shards_than_lints() -> None:
    """A shard with nothing dealt is empty, never an error."""
    want: list[list[str]] = [["lint:a"], []]
    assert want == gate_shard.deal(["lint:a"], 2, {"lint:a": 2})


# A lint weighs its row only where it does not skip.
def test_when_names_a_tracked_file() -> None:
    """Cargo.toml tracked: the Rust lint weighs its seconds."""
    assert (
        gate_shard.cost("lint:rust", COSTS, ["Cargo.toml"])
        == COSTS.rows["lint:rust"][0]
    )


def test_when_names_nothing_tracked() -> None:
    """No Cargo.toml: the Rust lint skips, so it weighs the default."""
    assert gate_shard.cost("lint:rust", COSTS, ["go.mod"]) == COSTS.default


def test_no_when_is_every_tree() -> None:
    """A row without `when` weighs its seconds everywhere."""
    assert gate_shard.cost("lint:shell", COSTS, []) == COSTS.rows["lint:shell"][0]


def test_an_unlisted_lint() -> None:
    """A repository's own lint weighs the default."""
    assert gate_shard.cost("lint:docs", COSTS, ["Cargo.toml"]) == COSTS.default


def planted(tree: Path, config: str, run: str) -> list[str]:
    """Plant a root mise config whose `test` task runs `run`.

    Returns:
        The tracked files: the config alone.

    """
    (tree / config).parent.mkdir(parents=True, exist_ok=True)
    (tree / config).write_text(f"[tasks.test]\nrun = {run!r}\n", encoding="utf-8")
    return [config]


# A `test` that is three suites, each a task of its own (#2160): one runs
# one pytest and one vitest through `measured`, one cargo test.
THREE = """
[tasks.test]
depends = ["test:py", "test:ts", { task = "test:rs", optional = true }]

[tasks."test:py"]
run = "mise/measured pytest -c mise/pytest.toml tests"

[tasks."test:ts"]
run = '"${ORG_BELT_DIR}/measured" pnpm exec vitest run'

[tasks."test:rs"]
run = '"${ORG_BELT_DIR}/measured" cargo test --workspace'
"""


def legs(rows: list[dict[str, object]]) -> list[tuple[object, ...]]:
    """Read the plan's rows as (leg, task, shard, of).

    Returns:
        One tuple per runner.

    """
    return [(r["leg"], r["task"], r["shard"], r["of"]) for r in rows]


LINT_ROWS: list[tuple[object, ...]] = [("lint", "ci:lint", i, 4) for i in range(1, 5)]
PROPERTIES_ROW: tuple[object, ...] = ("properties", "ci:properties", 1, 1)


class Planted:
    """A scratch tree, its root mise config and its shards.toml."""

    def __init__(self, tmp_path: Path) -> None:
        """Make a scratch tree for the planted config."""
        self.tree = Path(str(tmp_path))

    def plant(self, config: str, counts: str | None = None) -> list[str]:
        """Plant `mise.toml` and, given, the repository's shards.toml.

        Returns:
            The tracked files.

        """
        (self.tree / "mise.toml").write_text(config, encoding="utf-8")
        if counts is None:
            return ["mise.toml"]
        (self.tree / gate_shard.FILE).write_text(counts, encoding="utf-8")
        return ["mise.toml", gate_shard.FILE]

    def red(self, config: str, counts: str) -> list[str]:
        """Plan a planted tree that must be refused.

        Returns:
            The plan's defects, one line each.

        """
        files = self.plant(config, counts)
        with pytest.raises(gate_shard.PlanError) as caught:
            gate_shard.plan(COSTS, files, self.tree)
        return caught.value.lines


@fixture
def planted_state(tmp_path: Path) -> Planted:
    """Give each row its own Planted.

    Returns:
        The row's Planted.

    """
    return Planted(tmp_path)


class Plan(Planted):
    """The matrix: the repository's tasks, sharded by its own file (#2160)."""

    def properties(self, files: list[str]) -> list[tuple[object, ...]]:
        """Plan the planted tree and keep its properties rows.

        Returns:
            The properties leg's (leg, task, shard, of).

        """
        rows = legs(gate_shard.plan(COSTS, files, self.tree))
        return [r for r in rows if r[0] == "properties"]


@fixture
def plan_state(tmp_path: Path) -> Plan:
    """Give each row its own Plan.

    Returns:
        The row's Plan.

    """
    return Plan(tmp_path)


def test_no_file_is_todays_matrix(plan_state: Plan) -> None:
    """One `test` run line, no file: lint shards, ci:test, ci:properties."""
    files = planted(plan_state.tree, "mise.toml", "mise/measured pytest tests")
    want = [*LINT_ROWS, ("test", "ci:test", 1, 1), PROPERTIES_ROW]
    assert want == legs(gate_shard.plan(COSTS, files, plan_state.tree))


def test_no_test_task_is_todays_matrix(plan_state: Plan) -> None:
    """No `test` at all: ci:test, whose `test` is optional, as today."""
    want = [*LINT_ROWS, ("test", "ci:test", 1, 1), PROPERTIES_ROW]
    assert want == legs(gate_shard.plan(COSTS, [], plan_state.tree))


def test_three_tasks_plan_three_rows(plan_state: Plan) -> None:
    """`test` depends on three tasks: one row each, by name."""
    files = plan_state.plant(THREE)
    want = [
        *LINT_ROWS,
        ("test", "test:py", 1, 1),
        ("test", "test:rs", 1, 1),
        ("test", "test:ts", 1, 1),
        PROPERTIES_ROW,
    ]
    assert want == legs(gate_shard.plan(COSTS, files, plan_state.tree))


def test_a_count_of_four_plans_six(plan_state: Plan) -> None:
    """The file shards one of the three on four runners: six test rows."""
    files = plan_state.plant(THREE, '[test]\n"test:py" = 4\n')
    rows = [
        r
        for r in legs(gate_shard.plan(COSTS, files, plan_state.tree))
        if r[0] == "test"
    ]
    want: list[tuple[object, ...]] = [
        *(("test", "test:py", k, 4) for k in range(1, 5)),
        ("test", "test:rs", 1, 1),
        ("test", "test:ts", 1, 1),
    ]
    assert want == rows


def test_tests_own_run_is_a_row_too(plan_state: Plan) -> None:
    """A `test` with a run line and depends: its run is a row of its own."""
    config = THREE.replace(
        'depends = ["test:py"', 'run = "echo own"\ndepends = ["test:py"'
    )
    rows = legs(gate_shard.plan(COSTS, plan_state.plant(config), plan_state.tree))
    assert ("test", "test", 1, 1) in rows
    assert sum(r[0] == "test" for r in rows) == TEST_ROWS


def test_the_file_sets_the_lint_count(plan_state: Plan) -> None:
    """`lint = 2`: two lint shards, not the canon's four."""
    files = plan_state.plant(THREE, "lint = 2\n")
    rows = legs(gate_shard.plan(COSTS, files, plan_state.tree))
    want: list[tuple[object, ...]] = [
        ("lint", "ci:lint", 1, 2),
        ("lint", "ci:lint", 2, 2),
    ]
    assert want == rows[:2]


def test_properties_no_population_is_one_row(plan_state: Plan) -> None:
    """No property file tracked: no task applies, today's ci:properties."""
    want: list[tuple[object, ...]] = [PROPERTIES_ROW]
    assert want == plan_state.properties(plan_state.plant(THREE))


def test_one_languages_properties_is_todays_matrix(plan_state: Plan) -> None:
    """Only Python properties: one applies, one row, today's ci:properties.

    The TypeScript task's population is not tracked, so it plans no row.
    """
    files = [*plan_state.plant(THREE), "pkg/prop_codec.py", "vendor/x/a.prop.test.ts"]
    want: list[tuple[object, ...]] = [PROPERTIES_ROW]
    assert want == plan_state.properties(files)
    whole = legs(gate_shard.plan(COSTS, files, plan_state.tree))
    rest: list[tuple[object, ...]] = [r for r in whole if r[0] != "test"]
    want_rest: list[tuple[object, ...]] = [*LINT_ROWS, PROPERTIES_ROW]
    assert want_rest == rest


def test_both_languages_properties_plan_two_rows(plan_state: Plan) -> None:
    """Python and TypeScript properties tracked: a row each."""
    files = [*plan_state.plant(THREE), "prop_codec.py", "web/codec.prop.test.mts"]
    want: list[tuple[object, ...]] = [
        ("properties", "test:python-properties", 1, 1),
        ("properties", "test:typescript-properties", 1, 1),
    ]
    assert want == plan_state.properties(files)


def test_a_named_properties_task_is_sharded(plan_state: Plan) -> None:
    """A count on an applying properties task shards it; the other has no row."""
    files = [
        *plan_state.plant(THREE, '[properties]\n"test:python-properties" = 2\n'),
        "tests/prop_codec.py",
    ]
    want: list[tuple[object, ...]] = [
        ("properties", "test:python-properties", 1, 2),
        ("properties", "test:python-properties", 2, 2),
    ]
    assert want == plan_state.properties(files)


@pytest.mark.usefixtures("plan_state")
def test_the_applies_table_is_each_tasks_guard() -> None:
    """Each pattern is in the task's own script, which lists that population."""
    scripts = {
        "test:python-properties": "python-properties.sh",
        "test:typescript-properties": "typescript-properties.sh",
    }
    names: list[str] = sorted(scripts)
    applied: list[str] = sorted(gate_shard.APPLIES)
    assert names == applied
    for task, script in scripts.items():
        text = (HERE / script).read_text("utf-8")
        for pattern in gate_shard.APPLIES[task]:
            assert f"'*{pattern.lstrip('*')}'" in text


def test_an_untracked_config_is_not_read(plan_state: Plan) -> None:
    """Only a tracked config plans: the gate reads tracked files only."""
    plan_state.plant(THREE, '[test]\n"test:py" = 4\n')
    want = [*LINT_ROWS, ("test", "ci:test", 1, 1), PROPERTIES_ROW]
    assert want == legs(gate_shard.plan(COSTS, [], plan_state.tree))


@pytest.mark.usefixtures("plan_state")
def test_the_canon_shards_its_tests() -> None:
    """The canon's own file shards its `test` on four runners."""
    rows = legs(
        gate_shard.plan(gate_shard.load(), tracked(root=HERE.parent), HERE.parent)
    )
    want: list[tuple[object, ...]] = [("test", "test", k, 4) for k in range(1, 5)]
    got: list[tuple[object, ...]] = [r for r in rows if r[0] == "test"]
    assert want == got


def test_per_language_counts(plan_state: Plan) -> None:
    """The file's count wins; anything unnamed keeps the canon's."""
    files = plan_state.plant(THREE, "[mutants]\npython = 12\n")
    want = {"rust": 4, "go": 4, "python": 12, "typescript": 1}
    assert want == gate_shard.plan_languages("mutants", COSTS, files, plan_state.tree)


def test_a_count_on_a_task_the_tree_gives_nothing_to_run(
    planted_state: Planted,
) -> None:
    """TypeScript properties counted, none tracked: red, by name."""
    got = planted_state.red(THREE, '[properties]\n"test:typescript-properties" = 2\n')
    want: list[str] = [
        (
            "shards.toml: [properties] test:typescript-properties: the tree"
            " tracks nothing it runs (*.prop.test.ts, *.prop.test.tsx,"
            " *.prop.test.mts, *.prop.test.cts), so it has no row to shard"
        )
    ]
    assert want == got


def test_a_count_on_a_task_with_no_adapter(planted_state: Planted) -> None:
    """Cargo test cannot take a share: red, by name, never run n times."""
    got = planted_state.red(THREE, '[test]\n"test:rs" = 2\n')
    assert len(got) == 1, got
    assert "shards.toml: [test] test:rs = 2: its command runs no runner" in got[0]
    assert "so every shard would run all of it" in got[0]


def test_a_count_above_the_ceiling(planted_state: Planted) -> None:
    """129 against a ceiling of 128: red, by name."""
    got = planted_state.red(THREE, '[test]\n"test:py" = 129\n')
    want = [
        ("shards.toml: [test] test:py = 129 is not from 1 to the canon's ceiling, 128")
    ]
    assert want == got


def test_a_count_of_zero(planted_state: Planted) -> None:
    """Zero shards: red, by name."""
    got = planted_state.red(THREE, "lint = 0\n")
    want: list[str] = [
        "shards.toml: lint = 0 is not from 1 to the canon's ceiling, 128"
    ]
    assert want == got


@pytest.mark.parametrize(
    ("value", "shown"), [("2.5", "2.5"), ('"4"', "'4'"), ("true", "True")]
)
def test_a_count_that_is_not_an_integer(
    planted_state: Planted, value: str, shown: str
) -> None:
    """2.5, "4" and true are not whole numbers of shards: each red, by name."""
    got = planted_state.red(THREE, f'[test]\n"test:py" = {value}\n')
    want = [(f"shards.toml: [test] test:py = {shown} is not a whole number of shards")]
    assert want == got


def test_an_unknown_key(planted_state: Planted) -> None:
    """A key the file does not hold, and a language that is none: red, by name."""
    got = planted_state.red(THREE, "tests = 4\n[mutants]\ncobol = 2\n")
    want: list[str] = [
        (
            "shards.toml: tests: not a key the file holds"
            " (lint, test, properties, mutants, deep-tests)"
        ),
        ("shards.toml: [mutants] cobol: not a language (rust, go, python, typescript)"),
    ]
    assert want == got


def test_a_task_that_does_not_exist(planted_state: Planted) -> None:
    """A count on a task outside `test`'s closure: red, by name."""
    got = planted_state.red(THREE, '[test]\n"test:docs" = 2\n')
    want = [
        (
            "shards.toml: [test] test:docs: not a task of the test leg"
            " (test:py, test:rs, test:ts)"
        )
    ]
    assert want == got


def test_a_language_with_no_shard_support(planted_state: Planted) -> None:
    """Deep-test TypeScript properties run as one job: a count above 1 is red."""
    got = planted_state.red(THREE, "[deep-tests]\ntypescript = 2\n")
    assert len(got) == 1, got
    assert (
        "[deep-tests] typescript = 2: typescript has no way to take a share" in got[0]
    )


def test_a_dependency_nothing_defines(planted_state: Planted) -> None:
    """A required dependency no config defines: red, by name."""
    got = planted_state.red(THREE.replace('"test:ts", {', '"test:gone", {'), "")
    want: list[str] = [
        (
            "test depends on test:gone, which no tracked root mise config"
            " or the belt defines"
        )
    ]
    assert want == got


def test_the_matrix_stays_under_githubs_limit(planted_state: Planted) -> None:
    """Three tasks at 128 shards is 389 rows with lint and properties: red."""
    config = THREE.replace("cargo test --workspace", "cargo nextest run")
    counts = '[test]\n"test:py" = 128\n"test:ts" = 128\n"test:rs" = 128\n'
    got = planted_state.red(config, counts)
    want: list[str] = [
        "the plan is 389 rows, and GitHub runs at most 256 in one matrix"
    ]
    assert want == got


# A task's command names the runner it takes a share through.
@pytest.mark.parametrize(
    ("line", "want"),
    [
        ('"${ORG_BELT_DIR}/measured" pytest -c mise/pytest.toml tests', "pytest"),
        ("pytest tests", None),
        ('"${ORG_BELT_DIR}/python-properties.sh" gate', "python-properties"),
        (
            '"${ORG_BELT_DIR}/typescript-properties.sh" gate',
            "typescript-properties",
        ),
        ('"${ORG_BELT_DIR}/measured" pnpm exec vitest run', "vitest"),
        ('"${ORG_BELT_DIR}/measured" cargo nextest run --workspace', "nextest"),
        ('"${ORG_BELT_DIR}/measured" go test ./...', "go test"),
        ("pnpm exec vitest run", None),
        ('"${ORG_BELT_DIR}/measured" cargo test', None),
    ],
)
def test_each_runner(line: str, want: str | None) -> None:
    """Each adapter, read off the line that runs it."""
    assert want == gate_shard.adapter({"run": line})


# A malformed command line is the usage exit, never a run.
@pytest.mark.parametrize(
    ("argv", "error"),
    [
        ([], "belt gate-shard: error: the following arguments are required: mode"),
        (
            ["run", "lint", "ci:lint", "5", "4"],
            "belt gate-shard: error: shard 5 is past 4",
        ),
        (
            ["run", "lint", "ci:lint", "0", "4"],
            (
                "belt gate-shard run: error: argument SHARD: '0' is not a positive"
                " whole number"
            ),
        ),
        (
            ["run", "docs", "ci:docs", "1", "1"],
            (
                "belt gate-shard run: error: argument leg: invalid choice: 'docs'"
                " (choose from 'lint', 'test', 'properties')"
            ),
        ),
        (["run", "test", "", "1", "1"], "belt gate-shard: error: the task is empty"),
        (
            ["plan", "gate"],
            (
                "belt gate-shard plan: error: argument LEG: invalid choice: 'gate'"
                " (choose from '', 'mutants', 'deep-tests')"
            ),
        ),
        (
            ["deal", "x"],
            (
                "belt gate-shard deal: error: argument OF: 'x' is not a positive"
                " whole number"
            ),
        ),
    ],
)
def test_bad_arguments(
    argv: list[str], error: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """Each malformed form is argparse's exit 2, before running anything."""
    with pytest.raises(SystemExit) as ended:
        gate_shard.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt gate-shard"), err
    assert err.splitlines()[-1] == error


# The cost table's own refusals, on planted tables.
TABLE = """
[shards]
lint = 2
ceiling = 8

[shards.mutants]
rust = 1
go = 1
python = 1
typescript = 1

[shards.deep-tests]
python = 1
typescript = 1
rust = 1

[lint]
default = 5
"""


@pytest.mark.parametrize(
    ("old", "new", "error"),
    [
        (
            "typescript = 1\n",
            "typescript = 1\nzig = 1\n",
            "shards.mutants: unknown ['zig']",
        ),
        (
            "default = 5\n",
            'default = 5\n\n[lint.cost."lint:a"]\nseconds = 9\nwhen = []\n',
            "lint.cost.lint:a.when: a non-empty list of pathspecs",
        ),
        (
            "default = 5\n",
            'default = 5\n\n[lint.cost."lint:a"]\nseconds = 9\nwhen = "*.rs"\n',
            "lint.cost.lint:a.when: a non-empty list of pathspecs",
        ),
    ],
    ids=["an unknown language", "an empty when", "a when that is no list"],
)
def test_a_table_off_its_shape_is_refused(
    old: str, new: str, error: str, tmp_path: Path
) -> None:
    """Each malformed table is a ValueError naming the field."""
    path = tmp_path / "gate-shards.toml"
    path.write_text(TABLE.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(ValueError, match=r".") as caught:
        gate_shard.load(path)
    assert str(caught.value) == error


@pytest.mark.parametrize(
    "shards",
    ["lint = 9\nceiling = 8", "lint = 0\nceiling = 8", "lint = 2\nceiling = 300"],
    ids=["above the ceiling", "zero", "a ceiling past GitHub's"],
)
def test_a_count_outside_the_ceiling_is_refused(shards: str, tmp_path: Path) -> None:
    """Every count from 1 to the ceiling, the ceiling within the matrix."""
    path = tmp_path / "gate-shards.toml"
    path.write_text(TABLE.replace("lint = 2\nceiling = 8", shards), encoding="utf-8")
    with pytest.raises(ValueError, match=r".") as caught:
        gate_shard.load(path)
    assert str(caught.value) == (
        "shards: every count from 1 to the ceiling, and the ceiling at most 256"
    )


def test_a_file_that_does_not_decode_is_refused(planted_state: Planted) -> None:
    """A shards.toml TOML cannot read is red, its reason named."""
    got = planted_state.red(THREE, "lint = [")
    assert len(got) == 1
    assert got[0].startswith(f"{gate_shard.FILE}: ")


def test_a_leg_that_is_no_table_is_refused(planted_state: Planted) -> None:
    """`test = 4` where a table of counts belongs: red, by name."""
    want = [f"{gate_shard.FILE}: [test] is not a table of counts"]
    assert planted_state.red(THREE, "test = 4\n") == want


# The task table, as the plan reads it.
def test_the_task_table_reads_every_shape_a_config_holds(tmp_path: Path) -> None:
    """A table, a run line, a run list; a config with no task table is skipped."""
    (tmp_path / "mise.toml").write_text(
        '[tasks]\nline = "echo a"\nlist = ["echo b", "echo c"]\nnone = 1\n'
        '[tasks.table]\nrun = "echo d"\n',
        encoding="utf-8",
    )
    (tmp_path / ".mise.toml").write_text("tasks = 1\n", encoding="utf-8")
    files = ["mise.toml", ".mise.toml", ".config/mise.toml"]
    belt = tmp_path / "no-belt.toml"
    got = gate_shard.task_table(files, tmp_path, belt)
    want: dict[str, gate_shard.Task] = {
        "line": {"run": "echo a"},
        "list": {"run": ["echo b", "echo c"]},
        "table": {"run": "echo d"},
    }
    assert got == want


@pytest.mark.parametrize(
    ("task", "want"),
    [
        ({"run": "a"}, ["a"]),
        ({"run": ["a", "b"]}, ["a", "b"]),
        ({"run": ["a", {"task": "b"}]}, ["a"]),
        ({"file": "scripts/x.sh"}, ["scripts/x.sh"]),
        ({"depends": ["a"]}, []),
    ],
    ids=["a line", "a list", "a list with a task", "a file task", "no command"],
)
def test_a_tasks_lines(task: gate_shard.Task, want: list[str]) -> None:
    """The lines an adapter is looked for in, from each shape of command."""
    assert gate_shard.lines(task) == want


def test_dependencies_by_pattern_and_shape(tmp_path: Path) -> None:
    """A pattern stays within one `:` group; a shared, a blank, a bad entry."""
    config = """
[tasks.test]
depends = "test:*"
depends_post = ["", { task = 1 }, { task = "" }, { task = "test:late" }]

[tasks."test:a"]
run = "echo a"
depends = ["test:b"]

[tasks."test:b"]
run = "echo b"

[tasks."test:b:deep"]
run = "echo deep"

[tasks."test:late"]
run = "echo late"
depends = [{ task = "test:gone", optional = true }]
"""
    (tmp_path / "mise.toml").write_text(config, encoding="utf-8")
    tasks = gate_shard.task_table(["mise.toml"], tmp_path, tmp_path / "no-belt.toml")
    reached, red = gate_shard.closure("test", tasks.get, sorted(tasks))
    assert reached == ["test", "test:a", "test:b", "test:late"]
    # A blank task name is a dependency nothing defines; the bad and
    # empty entries are no dependency at all.
    assert red == [
        "test depends on , which no tracked root mise config or the belt defines"
    ]


def test_a_start_nothing_defines_reaches_nothing() -> None:
    """A closure from a task nothing defines is empty, with no defect."""
    none: dict[str, gate_shard.Task] = {}
    assert gate_shard.closure("test", none.get, []) == ([], [])


# The command over a tracked tree, mise a stub: each `mise run` is
# recorded with the shard it was handed, and mise's task listing and
# `tasks info` are written per row.
MISE_STUB = """#!/bin/bash
here=$(dirname "$0")
case "$1 $2" in
  "tasks ls") cat "${here}/tasks.json" ;;
  "tasks info") cat "${here}/info/$3.json" 2>/dev/null || exit 1 ;;
  *) printf '%s|%s\\n' "${ORG_SHARD:-}" "$*" >> "${here}/calls"
     exit "$(cat "${here}/status" 2>/dev/null || echo 0)" ;;
esac
"""


class Gate:
    """A tracked tree the command runs in, with a stub mise on PATH."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Make the tree and the stub, and run from the tree."""
        self.tree = tmp_path / "repo"
        self.tree.mkdir()
        self.bin = tmp_path / "bin"
        (self.bin / "info").mkdir(parents=True)
        stub = self.bin / "mise"
        stub.write_text(MISE_STUB, encoding="utf-8")
        stub.chmod(0o755)
        self.monkeypatch = monkeypatch
        monkeypatch.chdir(self.tree)
        monkeypatch.setenv("PATH", f"{self.bin}{os.pathsep}{os.environ['PATH']}")

    def plant(self, files: dict[str, str], track: support.Track) -> None:
        """Write and track the files, and tell the stub the tree's tasks."""
        for name, text in files.items():
            path = self.tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        track(self.tree)
        listed = tracked(root=self.tree)
        tasks = gate_shard.task_table(listed, self.tree, self.tree / "no-belt.toml")
        self.tell(tasks)

    def tell(self, tasks: dict[str, gate_shard.Task]) -> None:
        """Answer `mise tasks ls` and `mise tasks info` with these tasks."""
        names = [{"name": name} for name in sorted(tasks)]
        (self.bin / "tasks.json").write_text(json.dumps(names), encoding="utf-8")
        for old in (self.bin / "info").iterdir():
            old.unlink()
        for name, task in tasks.items():
            info = self.bin / "info" / f"{name}.json"
            info.write_text(json.dumps(task), encoding="utf-8")

    def answer(self, status: int) -> None:
        """Make every `mise run` exit with this status."""
        (self.bin / "status").write_text(f"{status}\n", encoding="utf-8")

    def calls(self) -> list[str]:
        """Read the `mise run`s, as `ORG_SHARD|argv`, in sorted order.

        Returns:
            One line per call; side-by-side runs land in either order.

        """
        path = self.bin / "calls"
        if not path.exists():
            return []
        return sorted(path.read_text(encoding="utf-8").splitlines())


@fixture
def gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Gate:
    """Give a row its own tracked tree and stub mise.

    Returns:
        The row's Gate.

    """
    return Gate(tmp_path, monkeypatch)


OWN_RUN = """
[tasks.test]
run = '"${ORG_BELT_DIR}/measured" pytest tests'
depends = ["test:a"]

[tasks."test:a"]
run = '"${ORG_BELT_DIR}/measured" pytest slow'

[tasks.build]
run = "echo build"
"""


def test_the_plan_prints_the_matrix_for_github_output(
    gate: Gate, track: support.Track, capfd: pytest.CaptureFixture[str]
) -> None:
    """Split test leg: the rows, `split` and `test-split` true."""
    gate.plant({"mise.toml": THREE}, track)
    assert gate_shard.main(["plan"]) == 0
    out = capfd.readouterr().out.splitlines()
    legs_line, split, test_split = out
    matrix = decoded.as_object(
        decoded.load_json(legs_line.removeprefix("legs=")), "legs"
    )
    rows = decoded.as_array(matrix["include"], "include")
    tasks = [decoded.as_object(r, "row")["task"] for r in rows]
    assert tasks[-4:] == ["test:py", "test:rs", "test:ts", "ci:properties"]
    assert (split, test_split) == ("split=true", "test-split=true")


def test_an_unsplit_plan_says_so(
    gate: Gate, track: support.Track, capfd: pytest.CaptureFixture[str]
) -> None:
    """One run line: no leg split."""
    gate.plant({"mise.toml": "[tasks.test]\nrun = 'pytest'\n"}, track)
    assert gate_shard.main(["plan"]) == 0
    assert capfd.readouterr().out.splitlines()[1:] == [
        "split=false",
        "test-split=false",
    ]


@pytest.mark.parametrize(
    ("leg", "want"),
    [
        (
            "mutants",
            [
                "rust-shards=[0, 1, 2, 3]",
                "rust-of=4",
                "go-shards=[0, 1, 2, 3]",
                "go-of=4",
                "python-shards=[0, 1]",
                "python-of=2",
                "typescript-shards=[0, 1, 2, 3, 4, 5, 6, 7]",
                "typescript-of=8",
            ],
        ),
        (
            "deep-tests",
            [
                "python-shards=[1, 2, 3, 4, 5, 6, 7, 8]",
                "python-of=8",
                "typescript-shards=[1]",
                "typescript-of=1",
                "rust-shards=[1]",
                "rust-of=1",
            ],
        ),
    ],
)
def test_a_per_language_plan_counts_from_its_base(
    leg: str,
    want: list[str],
    gate: Gate,
    track: support.Track,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The mutation legs count shards from 0, every other from 1."""
    gate.plant({"mise.toml": THREE, gate_shard.FILE: "[mutants]\npython = 2\n"}, track)
    assert gate_shard.main(["plan", leg]) == 0
    assert capfd.readouterr().out.splitlines() == want


@pytest.mark.parametrize("command", [["plan"], ["plan", "mutants"], ["union", "r"]])
def test_a_refused_file_reds_the_plan_by_name(
    command: list[str],
    gate: Gate,
    track: support.Track,
    as_task: Callable[[str], None],
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Every defect of the file is a finding, and exit 1."""
    as_task("plan")
    gate.plant({"mise.toml": THREE, gate_shard.FILE: "lint = 0\n"}, track)
    assert gate_shard.main(command) == 1
    assert capfd.readouterr().err == (
        f"plan: {gate_shard.FILE}: lint = 0 is not from 1 to the canon's ceiling, 128\n"
    )


def test_an_unsharded_union_with_no_report_is_whole(
    gate: Gate, track: support.Track, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Nothing sharded, nothing reported: every share is whole."""
    gate.plant({"mise.toml": THREE}, track)
    assert gate_shard.main(["union", str(tmp_path / "reports")]) == 0
    assert capfd.readouterr().out == (
        "shard: every shard's share is its whole population: none sharded\n"
    )


def test_a_sharded_task_that_reported_nothing_is_red(
    gate: Gate,
    track: support.Track,
    tmp_path: Path,
    as_task: Callable[[str], None],
    capfd: pytest.CaptureFixture[str],
) -> None:
    """test:py on two shards, no report: named, exit 1."""
    as_task("union")
    gate.plant({"mise.toml": THREE, gate_shard.FILE: '[test]\n"test:py" = 2\n'}, track)
    assert gate_shard.main(["union", str(tmp_path / "reports")]) == 1
    assert capfd.readouterr().err == (
        "union: test:py: planned on 2 shards, and no shard reported what it"
        " ran: its command runs no runner that takes a share\n"
    )


def test_no_mise_on_path_is_named(
    gate: Gate, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """A row cannot run without mise: said, exit 1."""
    gate.monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert gate_shard.main(["run", "test", "ci:test", "1", "1"]) == 1
    assert capfd.readouterr().err == "gate_shard: mise is not on PATH\n"


def test_an_unsplit_leg_runs_its_ci_task(gate: Gate, track: support.Track) -> None:
    """`ci:test` 1/1 is one `mise run ci:test`, no shard handed over."""
    gate.plant({"mise.toml": THREE}, track)
    failed = 3
    gate.answer(failed)
    assert gate_shard.main(["run", "test", "ci:test", "1", "1"]) == failed
    assert gate.calls() == ["|run ci:test"]


LINTS = """
[tasks."lint:a"]
run = "echo a"

[tasks."lint:b"]
run = "echo b"

[tasks."lint:c"]
run = "echo c"
"""


def test_a_lint_shard_runs_its_hand_side_by_side(
    gate: Gate, track: support.Track, capfd: pytest.CaptureFixture[str]
) -> None:
    """Shard 1 of 2 runs its lints as one `mise run`, joined by `:::`."""
    gate.plant({"mise.toml": LINTS}, track)
    assert gate_shard.main(["run", "lint", "ci:lint", "1", "2"]) == 0
    assert capfd.readouterr().out == "gate_shard: lint 1/2: 2 lints: lint:a lint:c\n"
    assert gate.calls() == ["|run lint:a ::: lint:c"]


def test_an_empty_lint_hand_runs_nothing(
    gate: Gate, track: support.Track, capfd: pytest.CaptureFixture[str]
) -> None:
    """Shard 4 of 4 over three lints holds none: said, exit 0, no run."""
    gate.plant({"mise.toml": LINTS}, track)
    assert gate_shard.main(["run", "lint", "ci:lint", "4", "4"]) == 0
    assert capfd.readouterr().out == "gate_shard: lint 4/4: 0 lints: \n"
    assert gate.calls() == []


def test_the_deal_prints_every_hand(
    gate: Gate, track: support.Track, capfd: pytest.CaptureFixture[str]
) -> None:
    """`deal 2` prints each shard's lints, shard 1 first."""
    gate.plant({"mise.toml": LINTS}, track)
    assert gate_shard.main(["deal", "2"]) == 0
    assert capfd.readouterr().out == "lint 1/2: lint:a lint:c\nlint 2/2: lint:b\n"


def test_the_first_test_row_runs_test_pgrx_beside_its_task(
    gate: Gate, track: support.Track
) -> None:
    """test:py 1/1 is the first row: held, then run with test:pgrx, no build."""
    gate.plant({"mise.toml": THREE}, track)
    assert gate_shard.main(["run", "test", "test:py", "1", "1"]) == 0
    assert gate.calls() == ["1/1|run test:py ::: test:pgrx"]


def test_a_later_row_runs_its_task_alone(gate: Gate, track: support.Track) -> None:
    """test:ts is not the first row: its task alone, its shard handed over."""
    gate.plant({"mise.toml": THREE}, track)
    assert gate_shard.main(["run", "test", "test:ts", "1", "1"]) == 0
    assert gate.calls() == ["1/1|run test:ts"]


def test_a_first_row_mise_disagrees_with_is_red(
    gate: Gate,
    track: support.Track,
    as_task: Callable[[str], None],
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Mise lists no test:ts: the plan's closure and mise's differ, nothing run."""
    as_task("gate")
    gate.plant({"mise.toml": THREE}, track)
    tasks = gate_shard.task_table(["mise.toml"], gate.tree, gate.tree / "none.toml")
    del tasks["test:ts"]
    gate.tell(tasks)
    assert gate_shard.main(["run", "test", "test:py", "1", "1"]) == 1
    assert capfd.readouterr().err == (
        "gate: the plan read the test leg's tasks as test:py, test:rs, test:ts,"
        " and mise reads them as test:py, test:rs: a task defined where the"
        " plan cannot read it (a file task, an untracked config) cannot be split\n"
    )
    assert gate.calls() == []


def test_tests_own_first_row_runs_beside_build_and_test_pgrx(
    gate: Gate, track: support.Track
) -> None:
    """`test`'s run line skips its depends, beside a run of build and test:pgrx."""
    gate.plant({"mise.toml": OWN_RUN}, track)
    assert gate_shard.main(["run", "test", "test", "1", "1"]) == 0
    assert gate.calls() == ["1/1|run --skip-deps test", "1/1|run build ::: test:pgrx"]


def test_tests_own_later_shard_runs_its_line_alone(
    gate: Gate, track: support.Track
) -> None:
    """Shard 2 of 2 of `test`'s own run line: the line alone, no depends."""
    gate.plant({"mise.toml": OWN_RUN, gate_shard.FILE: "[test]\ntest = 2\n"}, track)
    assert gate_shard.main(["run", "test", "test", "2", "2"]) == 0
    assert gate.calls() == ["2/2|run --skip-deps test"]


def test_side_by_side_runs_report_the_first_failure(
    gate: Gate, track: support.Track
) -> None:
    """Both runs fail: the row's status is the first non-zero one."""
    gate.plant({"mise.toml": OWN_RUN}, track)
    failed = 4
    gate.answer(failed)
    assert gate_shard.main(["run", "test", "test", "1", "1"]) == failed


def test_a_properties_row_runs_its_task_alone(gate: Gate, track: support.Track) -> None:
    """The properties leg's first row runs no build and no test:pgrx."""
    config = """
[tasks."ci:properties"]
depends = ["prop:a", "prop:b"]

[tasks."prop:a"]
run = "echo a"

[tasks."prop:b"]
run = "echo b"
"""
    gate.plant({"mise.toml": config}, track)
    assert gate_shard.main(["run", "properties", "prop:a", "1", "1"]) == 0
    assert gate.calls() == ["1/1|run prop:a"]


def test_a_measured_rust_row_exports_its_profiles(
    gate: Gate, track: support.Track
) -> None:
    """A test row in a Rust tree with a floor renders its report after."""
    gate.plant(
        {"mise.toml": THREE, "Cargo.toml": "[package]\n", ".coverage-floor": "90\n"},
        track,
    )
    gate.monkeypatch.setenv("ORG_GATE_COVERAGE_RUN", str(gate.tree / "run"))
    assert gate_shard.main(["run", "test", "test:ts", "1", "1"]) == 0
    export = f"|exec -- bash {support.MISE / 'coverage-measure.sh'} test:ts rust-export"
    assert gate.calls() == ["1/1|run test:ts", export]


@pytest.mark.parametrize(
    "files",
    [{"Cargo.toml": "[package]\n"}, {".coverage-floor": "90\n"}],
    ids=["no floor", "no crate"],
)
def test_a_row_with_no_rust_measurement_exports_nothing(
    files: dict[str, str], gate: Gate, track: support.Track
) -> None:
    """A run directory but no tracked crate, or no floor: no export."""
    gate.plant({"mise.toml": THREE, **files}, track)
    gate.monkeypatch.setenv("ORG_GATE_COVERAGE_RUN", str(gate.tree / "run"))
    assert gate_shard.main(["run", "test", "test:ts", "1", "1"]) == 0
    assert gate.calls() == ["1/1|run test:ts"]
