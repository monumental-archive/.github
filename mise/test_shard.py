#!/usr/bin/env python3
"""Every shard's share is its task's whole population, each item once (#2160).

mise/shard.py names a shard (ORG_SHARD=k/n), writes what each shard
ran, and holds the union of every shard's report to the population it
chose from. These rows plant the union's two failures (a shard that
drops an item, one that repeats one) and each of its other refusals, by
message, then run the belt's test runner for real on a fixture tree at
1/2 and 2/2 and hold what the two shards reported to the whole suite.

stdlib `unittest`, run by `mise run test`.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic
import shard

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "unittest_guarded.py"


def doc(
    k: int, n: int, ran: list[str], population: list[str] | None = None
) -> dict[str, object]:
    """Make one shard's report of task `t`, runner `r`.

    Returns:
        The report, as shard.report writes it.

    """
    return {
        "task": "t",
        "runner": "r",
        "scope": "",
        "shard": k,
        "of": n,
        "population": population or ["a", "b", "c", "d"],
        "ran": ran,
    }


def lines(*text: str) -> list[str]:
    """Spell the defects a row expects.

    Returns:
        The lines, as the union returns them.

    """
    return list(text)


class Parse(unittest.TestCase):
    """ORG_SHARD is k/n with 1 <= k <= n, or nothing."""

    def test_good(self) -> None:
        """1/2, 2/2 and 4/4 name shards; empty names none."""
        self.assertEqual((1, 2), shard.parse("1/2"))
        self.assertEqual((2, 2), shard.parse(" 2/2 "))
        self.assertEqual((4, 4), shard.parse("4/4"))
        self.assertIsNone(shard.parse(""))

    def test_bad(self) -> None:
        """Zero, k above n, and anything not two numbers: refused by name."""
        for raw in ("0/2", "3/2", "1/0", "x/2", "1", "1/2/3", "-1/2"):
            with self.subTest(raw=raw):
                with self.assertRaises(shard.ShardError) as caught:
                    shard.parse(raw)
                self.assertEqual(
                    f"ORG_SHARD={raw!r} is not a shard (k/n, 1 <= k <= n)",
                    str(caught.exception),
                )


class Union(unittest.TestCase):
    """The union check, planted: each defect red by name."""

    def test_whole(self) -> None:
        """Two shards that split the population exactly: green."""
        none: list[str] = []
        got = shard.union([doc(1, 2, ["a", "c"]), doc(2, 2, ["b", "d"])], {"t": 2})
        self.assertEqual(none, got)

    def test_a_shard_drops_an_item(self) -> None:
        """`d` ran nowhere: red, naming it."""
        got = shard.union([doc(1, 2, ["a", "c"]), doc(2, 2, ["b"])], {"t": 2})
        self.assertEqual(lines("t: r: ran on no shard: d"), got)

    def test_a_shard_repeats_an_item(self) -> None:
        """`c` ran on both: red, naming it."""
        got = shard.union([doc(1, 2, ["a", "c"]), doc(2, 2, ["b", "c", "d"])], {"t": 2})
        self.assertEqual(lines("t: r: ran on more than one shard: c"), got)

    def test_a_shard_reported_nothing(self) -> None:
        """Shard 2 of 2 is missing: red, naming it, and what it would have run."""
        got = shard.union([doc(1, 2, ["a", "c"])], {"t": 2})
        want = lines(
            "t: r: shard(s) 2 of 2 reported nothing", "t: r: ran on no shard: b, d"
        )
        self.assertEqual(want, got)

    def test_shards_disagree_on_the_population(self) -> None:
        """Two shards that chose from different lists: red."""
        got = shard.union(
            [doc(1, 2, ["a"], ["a", "b"]), doc(2, 2, ["b"], ["a", "b", "c"])], {"t": 2}
        )
        self.assertEqual(
            lines("t: r: the shards chose from 2 different populations"), got
        )

    def test_an_item_outside_the_population(self) -> None:
        """A shard ran what it did not choose from: red."""
        got = shard.union([doc(1, 2, ["a", "c", "z"]), doc(2, 2, ["b", "d"])], {"t": 2})
        self.assertEqual(lines("t: r: ran but is not in the population: z"), got)

    def test_a_sharded_task_with_no_runner_that_takes_a_share(self) -> None:
        """A task planned on 3 shards that nothing reported for: red."""
        got = shard.union([], {"t": 3, "ci:lint": 1})
        want = lines(
            "t: planned on 3 shards, and no shard reported what it ran:"
            " its command runs no runner that takes a share"
        )
        self.assertEqual(want, got)

    def test_a_task_the_plan_did_not_shard(self) -> None:
        """A report from a task the plan gave one runner: red."""
        got = shard.union([doc(1, 2, ["a", "b", "c", "d"])], {})
        self.assertEqual(lines("t: r: reported by a task the plan did not shard"), got)

    def test_the_command_line(self) -> None:
        """`union` reads the directory; a malformed count is the usage exit."""
        with tempfile.TemporaryDirectory() as scratch:
            self.assertEqual(0, shard.main(["union", scratch, "t=1"]))
            self.assertEqual(1, shard.main(["union", scratch, "t=2"]))
            self.assertEqual(2, shard.main(["union", scratch, "t=0"]))
            self.assertEqual(2, shard.main(["union"]))


SUITE = {
    f"test_{name}.py": (
        "import unittest\n\n\nclass T(unittest.TestCase):\n"
        "    def test_it(self):\n        self.assertTrue(True)\n"
    )
    for name in ("a", "b", "c", "d", "e")
}


class Guarded(unittest.TestCase):
    """The belt's test runner takes a share, run for real at 1/2 and 2/2."""

    @override
    def setUp(self) -> None:
        """Plant five test files and a reports directory."""
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        (self.root / "suite").mkdir()
        for name, body in SUITE.items():
            (self.root / "suite" / name).write_text(body, encoding="utf-8")
        self.reports = self.root / "reports"

    def shard_run(self, spec: str) -> subprocess.CompletedProcess[str]:
        """Run the runner over the suite as one shard.

        Returns:
            The finished run.

        """
        env = hermetic.env(
            ORG_SHARD=spec,
            ORG_SHARD_TASK="test:py",
            ORG_SHARD_REPORTS=str(self.reports),
            ORG_TEST_WORKERS="1",
        )
        return subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                "discover",
                "--start-directory",
                "suite",
                "--pattern",
                "test_*.py",
            ],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_two_shards_run_the_whole_suite_once(self) -> None:
        """1/2 and 2/2 between them run every file, none twice."""
        for spec in ("1/2", "2/2"):
            done = self.shard_run(spec)
            self.assertEqual(0, done.returncode, done.stderr)
        reports = shard.load(self.reports)
        self.assertEqual(2, len(reports))
        ran = sorted(
            decoded.as_str(r, "ran")
            for d in reports
            for r in decoded.as_array(d["ran"], "ran")
        )
        want = [f"test_{n}" for n in "abcde"]
        self.assertEqual(want, ran)
        none: list[str] = []
        self.assertEqual(none, shard.union(reports, {"test:py": 2}))

    def test_one_shard_alone_is_red(self) -> None:
        """Only 1/2 ran: the union names shard 2 and every file it held."""
        self.assertEqual(0, self.shard_run("1/2").returncode)
        got = shard.union(shard.load(self.reports), {"test:py": 2})
        self.assertEqual(2, len(got), got)
        self.assertIn("shard(s) 2 of 2 reported nothing", got[0])
        self.assertIn("ran on no shard: ", got[1])

    def test_a_malformed_shard_is_refused(self) -> None:
        """ORG_SHARD=0/2: exit 1, the value named, nothing run."""
        done = self.shard_run("0/2")
        self.assertEqual(1, done.returncode)
        self.assertIn("ORG_SHARD='0/2' is not a shard (k/n, 1 <= k <= n)", done.stderr)


if __name__ == "__main__":
    unittest.main()
