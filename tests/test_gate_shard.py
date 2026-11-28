"""The gate's plan: the repository's split, its counts, the lint deal.

mise/gate_shard.py lays out the gate's rows from the repository's own
tasks and shards.toml (#2160), and deals the `lint:*` tasks across the
lint shards from mise/gate-shards.toml (#2049). These rows hold the
plan to the split (one row per task, a count per task, today's matrix
where nothing is split), every refusal of the file to its message, the
table to the belt (every row a lint the belt defines), the deal to its
two properties (each lint on exactly one shard; the costliest on
different shards), and a cost to the tree.
"""

from pathlib import Path

import pytest

from belt import decoded, gate_shard
from belt.tracked import tracked
from belt.typed_pytest import fixture
from tests import support

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
    assert costs.lint_shards == 4
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
    assert gate_shard.cost("lint:rust", COSTS, ["Cargo.toml"]) == 300


def test_when_names_nothing_tracked() -> None:
    """No Cargo.toml: the Rust lint skips, so it weighs the default."""
    assert gate_shard.cost("lint:rust", COSTS, ["go.mod"]) == 2


def test_no_when_is_every_tree() -> None:
    """A row without `when` weighs its seconds everywhere."""
    assert gate_shard.cost("lint:shell", COSTS, []) == 40


def test_an_unlisted_lint() -> None:
    """A repository's own lint weighs the default."""
    assert gate_shard.cost("lint:docs", COSTS, ["Cargo.toml"]) == 2


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
    """Give each row its own Planted."""
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
    """Give each row its own Plan."""
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
    assert sum(r[0] == "test" for r in rows) == 4


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
    planted_state: Planted, value: object, shown: object
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
def test_each_runner(line: object, want: object) -> None:
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
            "belt gate-shard run: error: argument SHARD: '0' is not a positive"
            " whole number",
        ),
        (
            ["run", "docs", "ci:docs", "1", "1"],
            "belt gate-shard run: error: argument leg: invalid choice: 'docs'"
            " (choose from 'lint', 'test', 'properties')",
        ),
        (["run", "test", "", "1", "1"], "belt gate-shard: error: the task is empty"),
        (
            ["plan", "gate"],
            "belt gate-shard plan: error: argument LEG: invalid choice: 'gate'"
            " (choose from '', 'mutants', 'deep-tests')",
        ),
        (
            ["deal", "x"],
            "belt gate-shard deal: error: argument OF: 'x' is not a positive"
            " whole number",
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
