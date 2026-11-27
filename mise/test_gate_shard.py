#!/usr/bin/env python3
"""The gate's plan: the repository's split, its counts, the lint deal.

mise/gate_shard.py lays out the gate's rows from the repository's own
tasks and shards.toml (#2160), and deals the `lint:*` tasks across the
lint shards from mise/gate-shards.toml (#2049). These rows hold the
plan to the split (one row per task, a count per task, today's matrix
where nothing is split), every refusal of the file to its message, the
table to the belt (every row a lint the belt defines), the deal to its
two properties (each lint on exactly one shard; the costliest on
different shards), and a cost to the tree.

stdlib `unittest`, run by `mise run test`.
"""

import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import gate_shard
import unittest_guarded
from tracked import tracked

HERE = Path(__file__).resolve().parent
CI = HERE.parent / ".github" / "workflows" / "ci.yml"


class Table(unittest.TestCase):
    """The cost table names the belt's lints and the matrix's count."""

    def test_every_row_is_a_belt_lint(self) -> None:
        """A row naming no belt lint is a stale figure, red by name."""
        config = decoded.load_toml((HERE / "config.toml").read_bytes())
        defined = set(decoded.as_object(config["tasks"], "tasks"))
        stale = sorted(set(gate_shard.load().rows) - defined)
        none: list[str] = []
        self.assertEqual(none, stale)

    def test_every_test_cost_is_a_tracked_test_file(self) -> None:
        """A test cost naming no tracked test file is stale, red by name (#2054)."""
        _, rows = unittest_guarded.file_costs()
        files = set(tracked("*test_*.py", root=HERE.parent))
        stale = sorted(set(rows) - files)
        none: list[str] = []
        self.assertEqual(none, stale)

    def test_the_canon_defaults(self) -> None:
        """The table's counts are defaults within the ceiling (#2160)."""
        costs = gate_shard.load()
        self.assertEqual(4, costs.lint_shards)
        mutants: dict[str, int] = {"rust": 4, "go": 4, "python": 48, "typescript": 1}
        deep: dict[str, int] = {"python": 8, "typescript": 1, "rust": 1}
        self.assertEqual(mutants, costs.languages["mutants"])
        self.assertEqual(deep, costs.languages["deep-tests"])
        self.assertLessEqual(costs.ceiling, gate_shard.MATRIX_LIMIT)

    def test_the_workflow_runs_the_plan(self) -> None:
        """ci.yml's gate matrix is the plan job's output, nothing else."""
        text = CI.read_text("utf-8")
        self.assertIn("      matrix: ${{ fromJSON(needs.plan.outputs.legs) }}\n", text)
        self.assertIn('gate_shard.py plan >> "${GITHUB_OUTPUT}"', text)


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


class Deal(unittest.TestCase):
    """Every lint once, the heavy ones apart, the same on every shard."""

    def test_each_lint_on_one_shard(self) -> None:
        """The union is the list, with no lint twice."""
        names = [f"lint:t{i}" for i in range(23)]
        hands = gate_shard.deal(names, 4, dict.fromkeys(names, 2))
        dealt: list[str] = [n for h in hands for n in h]
        want: list[str] = sorted(names)
        got: list[str] = sorted(dealt)
        self.assertEqual(want, got)
        self.assertEqual(len(dealt), len(set(dealt)))

    def test_the_heaviest_are_apart(self) -> None:
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
        self.assertEqual(want, hands)

    def test_more_shards_than_lints(self) -> None:
        """A shard with nothing dealt is empty, never an error."""
        want: list[list[str]] = [["lint:a"], []]
        self.assertEqual(want, gate_shard.deal(["lint:a"], 2, {"lint:a": 2}))


class Cost(unittest.TestCase):
    """A lint weighs its row only where it does not skip."""

    def test_when_names_a_tracked_file(self) -> None:
        """Cargo.toml tracked: the Rust lint weighs its seconds."""
        self.assertEqual(300, gate_shard.cost("lint:rust", COSTS, ["Cargo.toml"]))

    def test_when_names_nothing_tracked(self) -> None:
        """No Cargo.toml: the Rust lint skips, so it weighs the default."""
        self.assertEqual(2, gate_shard.cost("lint:rust", COSTS, ["go.mod"]))

    def test_no_when_is_every_tree(self) -> None:
        """A row without `when` weighs its seconds everywhere."""
        self.assertEqual(40, gate_shard.cost("lint:shell", COSTS, []))

    def test_an_unlisted_lint(self) -> None:
        """A repository's own lint weighs the default."""
        self.assertEqual(2, gate_shard.cost("lint:docs", COSTS, ["Cargo.toml"]))


def planted(tree: Path, config: str, run: str) -> list[str]:
    """Plant a root mise config whose `test` task runs `run`.

    Returns:
        The tracked files: the config alone.

    """
    (tree / config).parent.mkdir(parents=True, exist_ok=True)
    (tree / config).write_text(f"[tasks.test]\nrun = {run!r}\n", encoding="utf-8")
    return [config]


# A `test` that is three suites, each a task of its own (#2160): one runs
# the belt's test runner, one vitest through `measured`, one cargo test.
THREE = """
[tasks.test]
depends = ["test:py", "test:ts", { task = "test:rs", optional = true }]

[tasks."test:py"]
run = "mise/measured python3 mise/unittest_guarded.py discover"

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


class Planted(unittest.TestCase):
    """A scratch tree, its root mise config and its shards.toml."""

    @override
    def setUp(self) -> None:
        """Make a scratch tree for the planted config."""
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.tree = Path(scratch.name)

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
        with self.assertRaises(gate_shard.PlanError) as caught:
            gate_shard.plan(COSTS, files, self.tree)
        return caught.exception.lines


class Plan(Planted):
    """The matrix: the repository's tasks, sharded by its own file (#2160)."""

    def test_no_file_is_todays_matrix(self) -> None:
        """One `test` run line, no file: lint shards, ci:test, ci:properties."""
        files = planted(
            self.tree, "mise.toml", "python3 mise/unittest_guarded.py discover"
        )
        want = [*LINT_ROWS, ("test", "ci:test", 1, 1), PROPERTIES_ROW]
        self.assertEqual(want, legs(gate_shard.plan(COSTS, files, self.tree)))

    def test_no_test_task_is_todays_matrix(self) -> None:
        """No `test` at all: ci:test, whose `test` is optional, as today."""
        want = [*LINT_ROWS, ("test", "ci:test", 1, 1), PROPERTIES_ROW]
        self.assertEqual(want, legs(gate_shard.plan(COSTS, [], self.tree)))

    def test_three_tasks_plan_three_rows(self) -> None:
        """`test` depends on three tasks: one row each, by name."""
        files = self.plant(THREE)
        want = [
            *LINT_ROWS,
            ("test", "test:py", 1, 1),
            ("test", "test:rs", 1, 1),
            ("test", "test:ts", 1, 1),
            PROPERTIES_ROW,
        ]
        self.assertEqual(want, legs(gate_shard.plan(COSTS, files, self.tree)))

    def test_a_count_of_four_plans_six(self) -> None:
        """The file shards one of the three on four runners: six test rows."""
        files = self.plant(THREE, '[test]\n"test:py" = 4\n')
        rows = [
            r for r in legs(gate_shard.plan(COSTS, files, self.tree)) if r[0] == "test"
        ]
        want: list[tuple[object, ...]] = [
            *(("test", "test:py", k, 4) for k in range(1, 5)),
            ("test", "test:rs", 1, 1),
            ("test", "test:ts", 1, 1),
        ]
        self.assertEqual(want, rows)

    def test_tests_own_run_is_a_row_too(self) -> None:
        """A `test` with a run line and depends: its run is a row of its own."""
        config = THREE.replace(
            'depends = ["test:py"', 'run = "echo own"\ndepends = ["test:py"'
        )
        rows = legs(gate_shard.plan(COSTS, self.plant(config), self.tree))
        self.assertIn(("test", "test", 1, 1), rows)
        self.assertEqual(4, sum(r[0] == "test" for r in rows))

    def test_the_file_sets_the_lint_count(self) -> None:
        """`lint = 2`: two lint shards, not the canon's four."""
        files = self.plant(THREE, "lint = 2\n")
        rows = legs(gate_shard.plan(COSTS, files, self.tree))
        want: list[tuple[object, ...]] = [
            ("lint", "ci:lint", 1, 2),
            ("lint", "ci:lint", 2, 2),
        ]
        self.assertEqual(want, rows[:2])

    def test_a_named_properties_task_splits_the_leg(self) -> None:
        """Naming a properties task splits the leg into its tasks."""
        files = self.plant(THREE, '[properties]\n"test:python-properties" = 2\n')
        rows = [
            r
            for r in legs(gate_shard.plan(COSTS, files, self.tree))
            if r[0] == "properties"
        ]
        want: list[tuple[object, ...]] = [
            ("properties", "test:python-properties", 1, 2),
            ("properties", "test:python-properties", 2, 2),
            ("properties", "test:typescript-properties", 1, 1),
        ]
        self.assertEqual(want, rows)

    def test_an_untracked_config_is_not_read(self) -> None:
        """Only a tracked config plans: the gate reads tracked files only."""
        self.plant(THREE, '[test]\n"test:py" = 4\n')
        want = [*LINT_ROWS, ("test", "ci:test", 1, 1), PROPERTIES_ROW]
        self.assertEqual(want, legs(gate_shard.plan(COSTS, [], self.tree)))

    def test_the_canon_shards_its_tests(self) -> None:
        """The canon's own file shards its `test` on four runners."""
        rows = legs(
            gate_shard.plan(gate_shard.load(), tracked(root=HERE.parent), HERE.parent)
        )
        want: list[tuple[object, ...]] = [("test", "test", k, 4) for k in range(1, 5)]
        got: list[tuple[object, ...]] = [r for r in rows if r[0] == "test"]
        self.assertEqual(want, got)

    def test_per_language_counts(self) -> None:
        """The file's count wins; anything unnamed keeps the canon's."""
        files = self.plant(THREE, "[mutants]\npython = 12\n")
        want = {"rust": 4, "go": 4, "python": 12, "typescript": 1}
        self.assertEqual(
            want, gate_shard.plan_languages("mutants", COSTS, files, self.tree)
        )


class Refused(Planted):
    """Every defect in the repository's file is red, by name (#2160)."""

    def test_a_count_on_a_task_with_no_adapter(self) -> None:
        """Cargo test cannot take a share: red, by name, never run n times."""
        got = self.red(THREE, '[test]\n"test:rs" = 2\n')
        self.assertEqual(1, len(got), got)
        self.assertIn(
            "shards.toml: [test] test:rs = 2: its command runs no runner", got[0]
        )
        self.assertIn("so every shard would run all of it", got[0])

    def test_a_count_above_the_ceiling(self) -> None:
        """129 against a ceiling of 128: red, by name."""
        got = self.red(THREE, '[test]\n"test:py" = 129\n')
        want = [
            (
                "shards.toml: [test] test:py = 129 is not from 1 to the canon's"
                " ceiling, 128"
            )
        ]
        self.assertEqual(want, got)

    def test_a_count_of_zero(self) -> None:
        """Zero shards: red, by name."""
        got = self.red(THREE, "lint = 0\n")
        want: list[str] = [
            "shards.toml: lint = 0 is not from 1 to the canon's ceiling, 128"
        ]
        self.assertEqual(want, got)

    def test_a_count_that_is_not_an_integer(self) -> None:
        """2.5, "4" and true are not whole numbers of shards: each red, by name."""
        for value, shown in (("2.5", "2.5"), ('"4"', "'4'"), ("true", "True")):
            with self.subTest(value=value):
                got = self.red(THREE, f'[test]\n"test:py" = {value}\n')
                want = [
                    (
                        f"shards.toml: [test] test:py = {shown} is not a whole"
                        " number of shards"
                    )
                ]
                self.assertEqual(want, got)

    def test_an_unknown_key(self) -> None:
        """A key the file does not hold, and a language that is none: red, by name."""
        got = self.red(THREE, "tests = 4\n[mutants]\ncobol = 2\n")
        want: list[str] = [
            (
                "shards.toml: tests: not a key the file holds"
                " (lint, test, properties, mutants, deep-tests)"
            ),
            (
                "shards.toml: [mutants] cobol: not a language"
                " (rust, go, python, typescript)"
            ),
        ]
        self.assertEqual(want, got)

    def test_a_task_that_does_not_exist(self) -> None:
        """A count on a task outside `test`'s closure: red, by name."""
        got = self.red(THREE, '[test]\n"test:docs" = 2\n')
        want = [
            (
                "shards.toml: [test] test:docs: not a task of the test leg"
                " (test:py, test:rs, test:ts)"
            )
        ]
        self.assertEqual(want, got)

    def test_a_language_with_no_shard_support(self) -> None:
        """Deep-test TypeScript properties run as one job: a count above 1 is red."""
        got = self.red(THREE, "[deep-tests]\ntypescript = 2\n")
        self.assertEqual(1, len(got), got)
        self.assertIn(
            "[deep-tests] typescript = 2: typescript has no way to take a share", got[0]
        )

    def test_a_dependency_nothing_defines(self) -> None:
        """A required dependency no config defines: red, by name."""
        got = self.red(THREE.replace('"test:ts", {', '"test:gone", {'), "")
        want: list[str] = [
            (
                "test depends on test:gone, which no tracked root mise config"
                " or the belt defines"
            )
        ]
        self.assertEqual(want, got)

    def test_the_matrix_stays_under_githubs_limit(self) -> None:
        """Three tasks at 128 shards is 389 rows with lint and properties: red."""
        config = THREE.replace("cargo test --workspace", "cargo nextest run")
        counts = '[test]\n"test:py" = 128\n"test:ts" = 128\n"test:rs" = 128\n'
        got = self.red(config, counts)
        want: list[str] = [
            "the plan is 389 rows, and GitHub runs at most 256 in one matrix"
        ]
        self.assertEqual(want, got)


class Adapter(unittest.TestCase):
    """A task's command names the runner it takes a share through."""

    def test_each_runner(self) -> None:
        """Each adapter, read off the line that runs it."""
        for line, want in (
            ("python3 mise/unittest_guarded.py roots mise", "unittest_guarded.py"),
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
        ):
            with self.subTest(line=line):
                self.assertEqual(want, gate_shard.adapter({"run": line}))


class Usage(unittest.TestCase):
    """A malformed command line is the usage exit, never a run."""

    def test_bad_arguments(self) -> None:
        """Each malformed form exits 2 before running anything."""
        for argv in (
            [],
            ["run", "lint", "ci:lint", "5", "4"],
            ["run", "lint", "ci:lint", "0", "4"],
            ["run", "docs", "ci:docs", "1", "1"],
            ["run", "test", "", "1", "1"],
            ["plan", "gate"],
            ["deal", "x"],
        ):
            with self.subTest(argv=argv):
                self.assertEqual(2, gate_shard.main(argv))


if __name__ == "__main__":
    unittest.main()
