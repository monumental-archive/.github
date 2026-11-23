#!/usr/bin/env python3
"""The lint leg's shards deal every lint once, heaviest apart (#2049).

mise/gate_shard.py deals the `lint:*` tasks across the gate's lint
shards from mise/gate-shards.toml. These rows hold the table to the
belt (every row a lint the belt defines, the shard count the matrix
runs), the deal to its two properties (each lint on exactly one shard;
the costliest on different shards), and a cost to the tree (a lint
whose `when` names no tracked file weighs the default).

stdlib `unittest`, run by `mise run test`.
"""

import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import gate_shard

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

    def test_the_workflow_runs_the_plan(self) -> None:
        """ci.yml's gate matrix is the plan job's output, nothing else."""
        text = CI.read_text("utf-8")
        self.assertIn("      matrix: ${{ fromJSON(needs.plan.outputs.legs) }}\n", text)
        self.assertIn('gate_shard.py plan >> "${GITHUB_OUTPUT}"', text)


COSTS = gate_shard.Costs(
    lint_shards=4,
    test_shards=3,
    default=2,
    rows={
        "lint:rust": (300, ("Cargo.toml",)),
        "lint:msrv": (290, ("Cargo.toml",)),
        "lint:shell": (40, None),
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


def legs(rows: list[dict[str, object]]) -> list[tuple[object, object, object]]:
    """Read the plan's rows as (leg, shard, of).

    Returns:
        One triple per runner.

    """
    return [(r["leg"], r["shard"], r["of"]) for r in rows]


LINT_ROWS: list[tuple[object, object, object]] = [("lint", i, 4) for i in range(1, 5)]


class Plan(unittest.TestCase):
    """The matrix: lint shards, test shards where `test` slices, properties."""

    @override
    def setUp(self) -> None:
        """Make a scratch tree for the planted config."""
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.tree = Path(scratch.name)

    def test_a_test_task_on_the_runner_shards(self) -> None:
        """`test` runs unittest_guarded.py: the table's test shards."""
        files = planted(
            self.tree, "mise.toml", "python3 mise/unittest_guarded.py discover"
        )
        want = [
            *LINT_ROWS,
            *[("test", i, 3) for i in range(1, 4)],
            ("properties", 1, 1),
        ]
        self.assertEqual(want, legs(gate_shard.plan(COSTS, files, self.tree)))

    def test_a_run_list_naming_the_runner_shards(self) -> None:
        """A run list with the runner on any line counts."""
        config = self.tree / ".config/mise.toml"
        config.parent.mkdir()
        config.write_text(
            '[tasks.test]\nrun = ["echo hi", "python3 unittest_guarded.py"]\n',
            encoding="utf-8",
        )
        rows = legs(gate_shard.plan(COSTS, [".config/mise.toml"], self.tree))
        self.assertEqual(("test", 3, 3), rows[6])

    def test_cargo_test_is_one_runner(self) -> None:
        """A `test` that cannot take a share runs on one runner."""
        files = planted(self.tree, "mise.toml", "cargo test --workspace")
        want = [*LINT_ROWS, ("test", 1, 1), ("properties", 1, 1)]
        self.assertEqual(want, legs(gate_shard.plan(COSTS, files, self.tree)))

    def test_an_untracked_config_is_not_read(self) -> None:
        """Only a tracked config plans: the gate lints tracked files only."""
        planted(self.tree, "mise.toml", "python3 unittest_guarded.py")
        want = [*LINT_ROWS, ("test", 1, 1), ("properties", 1, 1)]
        self.assertEqual(want, legs(gate_shard.plan(COSTS, [], self.tree)))

    def test_the_canon_shards_its_tests(self) -> None:
        """The canon's own `test` runs the runner: its plan shards it."""
        real = gate_shard.load()
        rows = legs(gate_shard.plan(real, ["mise.toml"], HERE.parent))
        self.assertIn(("test", real.test_shards, real.test_shards), rows)


class Usage(unittest.TestCase):
    """A malformed command line is the usage exit, never a run."""

    def test_bad_arguments(self) -> None:
        """Each malformed form exits 2 before running anything."""
        for argv in (
            [],
            ["run", "lint", "5", "4"],
            ["run", "lint", "0", "4"],
            ["run", "docs", "1", "1"],
            ["deal", "x"],
        ):
            with self.subTest(argv=argv):
                self.assertEqual(2, gate_shard.main(argv))


if __name__ == "__main__":
    unittest.main()
