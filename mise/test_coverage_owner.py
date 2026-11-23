#!/usr/bin/env python3
"""Rows for coverage_owner.py, a run's claim on its build directory (#2062).

A row per branch of `claim`: no file, this run's own claim, a dead
pid's run, a missing gate-named run, a live other run, and two
claimants racing for one empty directory.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coverage_owner
import hermetic

OWNER = Path(__file__).resolve().parent / "coverage_owner.py"
SH = hermetic.tool("sh")


def dead_pid() -> int:
    """Start a process and wait for it to end.

    Returns:
        A pid no process holds any more.

    """
    done = subprocess.run(
        [SH, "-c", "echo $$"], capture_output=True, text=True, check=True
    )
    return int(done.stdout)


def claim(target: Path, run: Path) -> subprocess.CompletedProcess[str]:
    """Run the helper's `claim` as the belt does.

    Returns:
        The finished process, text mode.

    """
    return subprocess.run(
        [sys.executable, str(OWNER), "claim", str(target), str(run)],
        env=hermetic.env(MISE_TASK_NAME="test"),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def owner(target: Path) -> str:
    """Read the claim a build directory records.

    Returns:
        The run directory the owner file names.

    """
    return (target / coverage_owner.OWNER).read_text(encoding="utf-8")


class Claim(unittest.TestCase):
    """A row per branch of `claim`."""

    @override
    def setUp(self) -> None:
        """Make a temporary directory for the build directory and the runs."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.target = self.tmp / "target" / "llvm-cov-target-0123"
        self.run_dir = self.tmp / "mine"
        self.run_dir.mkdir()

    def plant(self, held: Path) -> None:
        """Record `held` as the build directory's owner."""
        self.target.mkdir(parents=True)
        (self.target / coverage_owner.OWNER).write_text(f"{held}\n", encoding="utf-8")

    def test_no_file_writes_this_run(self) -> None:
        """No claim yet, and no directory: made, and claimed for this run."""
        out = claim(self.target, self.run_dir)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertEqual("", out.stderr)
        self.assertEqual(f"{self.run_dir}\n", owner(self.target))

    def test_this_runs_own_claim_is_kept(self) -> None:
        """A second call in the same run finds its own claim: 0."""
        self.plant(self.run_dir)
        out = claim(self.target, self.run_dir)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertEqual(f"{self.run_dir}\n", owner(self.target))

    def test_a_dead_pids_run_is_taken_over(self) -> None:
        """`org-coverage-<pid>` present, its pid gone: not live, taken over."""
        dead = self.tmp / f"org-coverage-{dead_pid()}"
        dead.mkdir()
        self.plant(dead)
        out = claim(self.target, self.run_dir)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertEqual(f"{self.run_dir}\n", owner(self.target))

    def test_a_missing_gate_named_run_is_taken_over(self) -> None:
        """A gate-named run whose directory is gone: not live, taken over."""
        self.plant(self.tmp / "coverage-run")
        out = claim(self.target, self.run_dir)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertEqual(f"{self.run_dir}\n", owner(self.target))

    def test_a_live_other_run_is_refused_by_name(self) -> None:
        """The test's own pid, its directory present: 1, and nothing written."""
        other = self.tmp / f"org-coverage-{os.getpid()}"
        other.mkdir()
        self.plant(other)
        out = claim(self.target, self.run_dir)
        self.assertEqual(1, out.returncode)
        self.assertEqual(
            f"test: {self.target} is being measured by another run ({other});"
            " wait for it to finish\n",
            out.stderr,
        )
        self.assertEqual(f"{other}\n", owner(self.target))

    def test_a_present_gate_named_run_is_live(self) -> None:
        """Any other name is live while its directory exists."""
        other = self.tmp / "coverage-run"
        other.mkdir()
        self.plant(other)
        out = claim(self.target, self.run_dir)
        self.assertEqual(1, out.returncode)
        self.assertIn(f"another run ({other})", out.stderr)

    def test_two_concurrent_claimants_one_wins(self) -> None:
        """Claimants started side by side on one empty directory: exactly one."""
        runs = [self.tmp / f"run-{n}" for n in range(8)]
        for run in runs:
            run.mkdir()
        procs = [
            subprocess.Popen(
                [sys.executable, str(OWNER), "claim", str(self.target), str(run)],
                env=hermetic.env(MISE_TASK_NAME="test"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for run in runs
        ]
        codes: list[int] = [proc.wait(timeout=60) for proc in procs]
        expected: list[int] = [0] + [1] * (len(runs) - 1)
        ranked = sorted(codes)
        self.assertEqual(expected, ranked)
        winner = runs[codes.index(0)]
        self.assertEqual(f"{winner}\n", owner(self.target))


class Live(unittest.TestCase):
    """The sweep's liveness rule, read directly."""

    def test_a_pid_another_user_holds_is_live(self) -> None:
        """Pid 1, not ours to signal, is live, as `ps -p` reads it."""
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "org-coverage-1"
            run.mkdir()
            self.assertTrue(coverage_owner.live(str(run)))


if __name__ == "__main__":
    unittest.main()
