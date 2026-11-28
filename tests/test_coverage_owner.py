"""Rows for coverage_owner.py, a run's claim on its build directory (#2062).

A row per branch of `claim`: no file, this run's own claim, a dead
pid's run, a missing gate-named run, a live other run, and two
claimants racing for one empty directory. The claim's cache directory
tag (#2070): a new directory, a restored untagged one, a valid tag
kept. And `clean`: a cargo-llvm-cov that reports a refused `cargo
clean` as a warning is red, one that cleaned is not.
"""

import os
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from belt import coverage_owner
from belt.typed_pytest import fixture
from tests import support

SH = support.tool("sh")
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


type Claim = Callable[[Path, Path], subprocess.CompletedProcess[str]]


@fixture
def claim(capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> Claim:
    """Run the helper's `claim` as the belt does, in process.

    Returns:
        The claimer: `claim(target, run)`, giving the status and what it
        wrote as a finished process's.

    """
    monkeypatch.setenv("MISE_TASK_NAME", "test")

    def run(target: Path, run_dir: Path) -> subprocess.CompletedProcess[str]:
        status = coverage_owner.main(["claim", str(target), str(run_dir)])
        out, err = capfd.readouterr()
        return subprocess.CompletedProcess([], status, out, err)

    return run


def owner(target: Path) -> str:
    """Read the claim a build directory records.

    Returns:
        The run directory the owner file names.

    """
    return (target / coverage_owner.OWNER).read_text(encoding="utf-8")


@pytest.mark.usefixtures("_set_up_test_claim")
class TestClaim:
    """A row per branch of `claim`."""

    @fixture
    def _set_up_test_claim(self, tmp_path: Path) -> None:
        """Make a temporary directory for the build directory and the runs."""
        self.tmp = Path(str(tmp_path)).resolve()
        self.target = self.tmp / "target" / "llvm-cov-target-0123"
        self.run_dir = self.tmp / "mine"
        self.run_dir.mkdir()

    def plant(self, held: Path) -> None:
        """Record `held` as the build directory's owner."""
        self.target.mkdir(parents=True)
        (self.target / coverage_owner.OWNER).write_text(f"{held}\n", encoding="utf-8")

    def test_no_file_writes_this_run(self, claim: Claim) -> None:
        """No claim yet, and no directory: made, and claimed for this run."""
        out = claim(self.target, self.run_dir)
        assert out.returncode == 0, out.stderr
        assert out.stderr == ""
        assert f"{self.run_dir}\n" == owner(self.target)

    def test_this_runs_own_claim_is_kept(self, claim: Claim) -> None:
        """A second call in the same run finds its own claim: 0."""
        self.plant(self.run_dir)
        out = claim(self.target, self.run_dir)
        assert out.returncode == 0, out.stderr
        assert f"{self.run_dir}\n" == owner(self.target)

    def test_a_dead_pids_run_is_taken_over(self, claim: Claim) -> None:
        """`org-coverage-<pid>` present, its pid gone: not live, taken over."""
        dead = self.tmp / f"org-coverage-{dead_pid()}"
        dead.mkdir()
        self.plant(dead)
        out = claim(self.target, self.run_dir)
        assert out.returncode == 0, out.stderr
        assert f"{self.run_dir}\n" == owner(self.target)

    def test_a_missing_gate_named_run_is_taken_over(self, claim: Claim) -> None:
        """A gate-named run whose directory is gone: not live, taken over."""
        self.plant(self.tmp / "coverage-run")
        out = claim(self.target, self.run_dir)
        assert out.returncode == 0, out.stderr
        assert f"{self.run_dir}\n" == owner(self.target)

    def test_a_live_other_run_is_refused_by_name(self, claim: Claim) -> None:
        """The test's own pid, its directory present: 1, and nothing written."""
        other = self.tmp / f"org-coverage-{os.getpid()}"
        other.mkdir()
        self.plant(other)
        out = claim(self.target, self.run_dir)
        assert out.returncode == 1
        assert (
            f"test: {self.target} is being measured by another run ({other});"
            " wait for it to finish\n"
        ) == out.stderr
        assert f"{other}\n" == owner(self.target)

    def test_a_present_gate_named_run_is_live(self, claim: Claim) -> None:
        """Any other name is live while its directory exists."""
        other = self.tmp / "coverage-run"
        other.mkdir()
        self.plant(other)
        out = claim(self.target, self.run_dir)
        assert out.returncode == 1
        assert f"another run ({other})" in out.stderr

    def test_two_concurrent_claimants_one_wins(self) -> None:
        """Claimants started side by side on one empty directory: exactly one."""
        runs = [self.tmp / f"run-{n}" for n in range(8)]
        for run in runs:
            run.mkdir()
        with ThreadPoolExecutor(max_workers=len(runs)) as pool:
            held = list(
                pool.map(lambda run: coverage_owner.claim(self.target, str(run)), runs)
            )
        won = [run for run, by in zip(runs, held, strict=True) if by is None]
        assert len(won) == 1, held
        winner = won[0]
        assert f"{winner}\n" == owner(self.target)


@pytest.mark.usefixtures("_set_up_test_tag")
class TestTag:
    """Cargo refuses to clean a directory with no tag: the claim leaves one."""

    @fixture
    def _set_up_test_tag(self, tmp_path: Path) -> None:
        """Make a temporary directory for the build directory and the run."""
        self.tmp = Path(str(tmp_path)).resolve()
        self.target = self.tmp / "target" / "llvm-cov-target-0123"
        self.run_dir = self.tmp / "mine"
        self.run_dir.mkdir()

    def tag(self) -> str:
        """Read the build directory's cache directory tag.

        Returns:
            Its text.

        """
        return (self.target / coverage_owner.TAG).read_text(encoding="utf-8")

    def test_a_new_directory_carries_cargos_tag(self, claim: Claim) -> None:
        """The planted row: on main the claim made the directory untagged."""
        out = claim(self.target, self.run_dir)
        assert out.returncode == 0, out.stderr
        assert self.tag() == CARGO_TAG

    def test_a_restored_untagged_directory_is_tagged(self, claim: Claim) -> None:
        """A cache saved by main's claim holds the directory with no tag."""
        self.target.mkdir(parents=True)
        (self.target / "stale").write_text("from an earlier build\n")
        out = claim(self.target, self.run_dir)
        assert out.returncode == 0, out.stderr
        assert self.tag() == CARGO_TAG
        assert (self.target / "stale").exists()

    def test_an_invalid_tag_is_replaced(self, claim: Claim) -> None:
        """A tag with the wrong signature is what cargo calls invalid."""
        self.target.mkdir(parents=True)
        (self.target / coverage_owner.TAG).write_text("Signature: wrong\n")
        out = claim(self.target, self.run_dir)
        assert out.returncode == 0, out.stderr
        assert self.tag() == CARGO_TAG

    def test_a_valid_tag_is_kept(self, claim: Claim) -> None:
        """Cargo's own tag, whatever its comments say, is left as it is."""
        self.target.mkdir(parents=True)
        mine = "Signature: 8a477f597d28d172789f06886806bc55\n# written by cargo\n"
        (self.target / coverage_owner.TAG).write_text(mine)
        out = claim(self.target, self.run_dir)
        assert out.returncode == 0, out.stderr
        assert mine == self.tag()

    def test_a_refusal_writes_no_tag(self, claim: Claim) -> None:
        """Another live run's directory is left exactly as it holds it."""
        other = self.tmp / f"org-coverage-{os.getpid()}"
        other.mkdir()
        self.target.mkdir(parents=True)
        (self.target / coverage_owner.OWNER).write_text(f"{other}\n")
        out = claim(self.target, self.run_dir)
        assert out.returncode == 1
        assert not (self.target / coverage_owner.TAG).exists()


type Clean = Callable[[str], tuple[subprocess.CompletedProcess[str], list[str]]]


@fixture
def clean(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> Clean:
    """Run `clean` in process beside a stand-in cargo answering as told.

    Returns:
        The cleaner: `clean(how)`, giving the status and what it wrote
        as a finished process's, and the stand-in's calls.

    """
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "cargo").write_text(CARGO, encoding="utf-8")
    (stubs / "cargo").chmod(0o755)
    log = tmp_path / "cargo.log"
    for name, value in {
        "MISE_TASK_NAME": "test",
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "CARGO_TARGET_DIR": "/work/target/llvm-cov-target-0123",
    }.items():
        monkeypatch.setenv(name, value)

    def run(how: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        monkeypatch.setenv("STUB_CLEAN", how)
        status = coverage_owner.main(["clean"])
        out, err = capfd.readouterr()
        calls = log.read_text().splitlines() if log.exists() else []
        return subprocess.CompletedProcess([], status, out, err), calls

    return run


class TestClean:
    """`clean` is red when cargo-llvm-cov did not clean (#2070)."""

    def test_a_clean_that_ran_is_green(self, clean: Clean) -> None:
        """Nothing reported: 0, and the workspace clean was the call."""
        out, calls = clean("ok")
        assert out.returncode == 0, out.stderr
        assert out.stderr == ""
        expected: list[str] = ["llvm-cov clean --workspace"]
        assert expected == calls

    def test_a_refused_clean_is_red_by_name(self, clean: Clean) -> None:
        """The planted row: cargo-llvm-cov exits 0 over a refused clean."""
        out, _ = clean("refused")
        assert out.returncode == 1
        assert "missing or invalid `CACHEDIR.TAG` file\n" in out.stderr
        assert (
            "test: the workspace clean of /work/target/llvm-cov-target-0123 did"
            " not happen — its old executables would join the figure with zero"
            " hits (#2061): cargo-llvm-cov reported the failure above as a"
            " warning\n"
        ) in out.stderr

    def test_a_failed_cargo_llvm_cov_is_its_status(self, clean: Clean) -> None:
        """cargo-llvm-cov's own failure is passed through unchanged."""
        out, _ = clean("failed")
        assert out.returncode == 3


class TestLive:
    """The sweep's liveness rule, read directly."""

    def test_a_pid_another_user_holds_is_live(self) -> None:
        """Pid 1, not ours to signal, is live, as `ps -p` reads it."""
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "org-coverage-1"
            run.mkdir()
            assert coverage_owner.live(str(run))
