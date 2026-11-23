#!/usr/bin/env python3
"""Rows for coverage_owner.py, a run's claim on its build directory (#2062).

A row per branch of `claim`: no file, this run's own claim, a dead
pid's run, a missing gate-named run, a live other run, and two
claimants racing for one empty directory. The claim's cache directory
tag (#2070): a new directory, a restored untagged one, a valid tag
kept. And `clean`: a cargo-llvm-cov that reports a refused `cargo
clean` as a warning is red, one that cleaned is not.

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
# What cargo 1.97.1 wrote into a target directory it created, read off
# a real build (#2070); the claim must leave the same bytes.
CARGO_TAG = (
    "Signature: 8a477f597d28d172789f06886806bc55\n"
    "# This file is a cache directory tag created by cargo.\n"
    "# For information about cache directory tags see https://bford.info/cachedir/\n"
)
# A stand-in cargo answering `llvm-cov clean` as cargo-llvm-cov 0.9.1
# does over a directory cargo refuses (edtf run 45197553295): the
# failed cargo as a warning, its own status 0. STUB_CLEAN says how.
CARGO = """#!/usr/bin/env bash
echo "$*" >> "${STUB_LOG}"
dir=${CARGO_TARGET_DIR}
case "${STUB_CLEAN}" in
  refused)
    echo "warning: process didn't exit successfully:" \\
      "\\`cargo clean --target-dir ${dir}\\` (exit status: 101)" >&2
    echo "--- stderr" >&2
    echo "error: cannot clean \\`${dir}\\`:" \\
      "missing or invalid \\`CACHEDIR.TAG\\` file" >&2
    ;;
  failed) exit 3 ;;
  *) ;;
esac
"""


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


class Tag(unittest.TestCase):
    """Cargo refuses to clean a directory with no tag: the claim leaves one."""

    @override
    def setUp(self) -> None:
        """Make a temporary directory for the build directory and the run."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.target = self.tmp / "target" / "llvm-cov-target-0123"
        self.run_dir = self.tmp / "mine"
        self.run_dir.mkdir()

    def tag(self) -> str:
        """Read the build directory's cache directory tag.

        Returns:
            Its text.

        """
        return (self.target / coverage_owner.TAG).read_text(encoding="utf-8")

    def test_a_new_directory_carries_cargos_tag(self) -> None:
        """The planted row: on main the claim made the directory untagged."""
        out = claim(self.target, self.run_dir)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertEqual(CARGO_TAG, self.tag())

    def test_a_restored_untagged_directory_is_tagged(self) -> None:
        """A cache saved by main's claim holds the directory with no tag."""
        self.target.mkdir(parents=True)
        (self.target / "stale").write_text("from an earlier build\n")
        out = claim(self.target, self.run_dir)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertEqual(CARGO_TAG, self.tag())
        self.assertTrue((self.target / "stale").exists())

    def test_an_invalid_tag_is_replaced(self) -> None:
        """A tag with the wrong signature is what cargo calls invalid."""
        self.target.mkdir(parents=True)
        (self.target / coverage_owner.TAG).write_text("Signature: wrong\n")
        out = claim(self.target, self.run_dir)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertEqual(CARGO_TAG, self.tag())

    def test_a_valid_tag_is_kept(self) -> None:
        """Cargo's own tag, whatever its comments say, is left as it is."""
        self.target.mkdir(parents=True)
        mine = "Signature: 8a477f597d28d172789f06886806bc55\n# written by cargo\n"
        (self.target / coverage_owner.TAG).write_text(mine)
        out = claim(self.target, self.run_dir)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertEqual(mine, self.tag())

    def test_a_refusal_writes_no_tag(self) -> None:
        """Another live run's directory is left exactly as it holds it."""
        other = self.tmp / f"org-coverage-{os.getpid()}"
        other.mkdir()
        self.target.mkdir(parents=True)
        (self.target / coverage_owner.OWNER).write_text(f"{other}\n")
        out = claim(self.target, self.run_dir)
        self.assertEqual(1, out.returncode)
        self.assertFalse((self.target / coverage_owner.TAG).exists())


class Clean(unittest.TestCase):
    """`clean` is red when cargo-llvm-cov did not clean (#2070)."""

    @staticmethod
    def clean(how: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run `clean` beside a stand-in cargo answering as `how` says.

        Returns:
            The finished process and the stand-in's calls.

        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            stubs = base / "stubs"
            stubs.mkdir()
            (stubs / "cargo").write_text(CARGO, encoding="utf-8")
            (stubs / "cargo").chmod(0o755)
            log = base / "cargo.log"
            out = subprocess.run(
                [sys.executable, str(OWNER), "clean"],
                env=hermetic.env(
                    MISE_TASK_NAME="test",
                    PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
                    STUB_LOG=str(log),
                    STUB_CLEAN=how,
                    CARGO_TARGET_DIR="/work/target/llvm-cov-target-0123",
                ),
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            calls = log.read_text().splitlines() if log.exists() else []
        return out, calls

    def test_a_clean_that_ran_is_green(self) -> None:
        """Nothing reported: 0, and the workspace clean was the call."""
        out, calls = self.clean("ok")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertEqual("", out.stderr)
        expected: list[str] = ["llvm-cov clean --workspace"]
        self.assertEqual(expected, calls)

    def test_a_refused_clean_is_red_by_name(self) -> None:
        """The planted row: cargo-llvm-cov exits 0 over a refused clean."""
        out, _ = self.clean("refused")
        self.assertEqual(1, out.returncode)
        self.assertIn("missing or invalid `CACHEDIR.TAG` file\n", out.stderr)
        self.assertIn(
            "test: the workspace clean of /work/target/llvm-cov-target-0123 did"
            " not happen — its old executables would join the figure with zero"
            " hits (#2061): cargo-llvm-cov reported the failure above as a"
            " warning\n",
            out.stderr,
        )

    def test_a_failed_cargo_llvm_cov_is_its_status(self) -> None:
        """cargo-llvm-cov's own failure is passed through unchanged."""
        out, _ = self.clean("failed")
        self.assertEqual(3, out.returncode)


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
