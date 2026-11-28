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


class ClaimState:
    """A row per branch of `claim`."""

    def __init__(self, tmp_path: Path) -> None:
        """Make a temporary directory for the build directory and the runs."""
        self.tmp = Path(str(tmp_path)).resolve()
        self.target = self.tmp / "target" / "llvm-cov-target-0123"
        self.run_dir = self.tmp / "mine"
        self.run_dir.mkdir()

    def plant(self, held: Path) -> None:
        """Record `held` as the build directory's owner."""
        self.target.mkdir(parents=True)
        (self.target / coverage_owner.OWNER).write_text(f"{held}\n", encoding="utf-8")


@fixture
def claim_state(tmp_path: Path) -> ClaimState:
    """Give each row its own ClaimState."""
    return ClaimState(tmp_path)


def test_no_file_writes_this_run(claim_state: ClaimState, claim: Claim) -> None:
    """No claim yet, and no directory: made, and claimed for this run."""
    out = claim(claim_state.target, claim_state.run_dir)
    assert out.returncode == 0, out.stderr
    assert out.stderr == ""
    assert f"{claim_state.run_dir}\n" == owner(claim_state.target)


def test_this_runs_own_claim_is_kept(claim_state: ClaimState, claim: Claim) -> None:
    """A second call in the same run finds its own claim: 0."""
    claim_state.plant(claim_state.run_dir)
    out = claim(claim_state.target, claim_state.run_dir)
    assert out.returncode == 0, out.stderr
    assert f"{claim_state.run_dir}\n" == owner(claim_state.target)


def test_a_dead_pids_run_is_taken_over(claim_state: ClaimState, claim: Claim) -> None:
    """`org-coverage-<pid>` present, its pid gone: not live, taken over."""
    dead = claim_state.tmp / f"org-coverage-{dead_pid()}"
    dead.mkdir()
    claim_state.plant(dead)
    out = claim(claim_state.target, claim_state.run_dir)
    assert out.returncode == 0, out.stderr
    assert f"{claim_state.run_dir}\n" == owner(claim_state.target)


def test_a_missing_gate_named_run_is_taken_over(
    claim_state: ClaimState, claim: Claim
) -> None:
    """A gate-named run whose directory is gone: not live, taken over."""
    claim_state.plant(claim_state.tmp / "coverage-run")
    out = claim(claim_state.target, claim_state.run_dir)
    assert out.returncode == 0, out.stderr
    assert f"{claim_state.run_dir}\n" == owner(claim_state.target)


def test_a_live_other_run_is_refused_by_name(
    claim_state: ClaimState, claim: Claim
) -> None:
    """The test's own pid, its directory present: 1, and nothing written."""
    other = claim_state.tmp / f"org-coverage-{os.getpid()}"
    other.mkdir()
    claim_state.plant(other)
    out = claim(claim_state.target, claim_state.run_dir)
    assert out.returncode == 1
    assert (
        f"test: {claim_state.target} is being measured by another run ({other});"
        " wait for it to finish\n"
    ) == out.stderr
    assert f"{other}\n" == owner(claim_state.target)


def test_a_present_gate_named_run_is_live(
    claim_state: ClaimState, claim: Claim
) -> None:
    """Any other name is live while its directory exists."""
    other = claim_state.tmp / "coverage-run"
    other.mkdir()
    claim_state.plant(other)
    out = claim(claim_state.target, claim_state.run_dir)
    assert out.returncode == 1
    assert f"another run ({other})" in out.stderr


def test_two_concurrent_claimants_one_wins(claim_state: ClaimState) -> None:
    """Claimants started side by side on one empty directory: exactly one."""
    runs = [claim_state.tmp / f"run-{n}" for n in range(8)]
    for run in runs:
        run.mkdir()
    with ThreadPoolExecutor(max_workers=len(runs)) as pool:
        held = list(
            pool.map(
                lambda run: coverage_owner.claim(claim_state.target, str(run)), runs
            )
        )
    won = [run for run, by in zip(runs, held, strict=True) if by is None]
    assert len(won) == 1, held
    winner = won[0]
    assert f"{winner}\n" == owner(claim_state.target)


class Tag:
    """Cargo refuses to clean a directory with no tag: the claim leaves one."""

    def __init__(self, tmp_path: Path) -> None:
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


@fixture
def tag_state(tmp_path: Path) -> Tag:
    """Give each row its own Tag."""
    return Tag(tmp_path)


def test_a_new_directory_carries_cargos_tag(tag_state: Tag, claim: Claim) -> None:
    """The planted row: on main the claim made the directory untagged."""
    out = claim(tag_state.target, tag_state.run_dir)
    assert out.returncode == 0, out.stderr
    assert tag_state.tag() == CARGO_TAG


def test_a_restored_untagged_directory_is_tagged(tag_state: Tag, claim: Claim) -> None:
    """A cache saved by main's claim holds the directory with no tag."""
    tag_state.target.mkdir(parents=True)
    (tag_state.target / "stale").write_text("from an earlier build\n")
    out = claim(tag_state.target, tag_state.run_dir)
    assert out.returncode == 0, out.stderr
    assert tag_state.tag() == CARGO_TAG
    assert (tag_state.target / "stale").exists()


def test_an_invalid_tag_is_replaced(tag_state: Tag, claim: Claim) -> None:
    """A tag with the wrong signature is what cargo calls invalid."""
    tag_state.target.mkdir(parents=True)
    (tag_state.target / coverage_owner.TAG).write_text("Signature: wrong\n")
    out = claim(tag_state.target, tag_state.run_dir)
    assert out.returncode == 0, out.stderr
    assert tag_state.tag() == CARGO_TAG


def test_a_valid_tag_is_kept(tag_state: Tag, claim: Claim) -> None:
    """Cargo's own tag, whatever its comments say, is left as it is."""
    tag_state.target.mkdir(parents=True)
    mine = "Signature: 8a477f597d28d172789f06886806bc55\n# written by cargo\n"
    (tag_state.target / coverage_owner.TAG).write_text(mine)
    out = claim(tag_state.target, tag_state.run_dir)
    assert out.returncode == 0, out.stderr
    assert mine == tag_state.tag()


def test_a_refusal_writes_no_tag(tag_state: Tag, claim: Claim) -> None:
    """Another live run's directory is left exactly as it holds it."""
    other = tag_state.tmp / f"org-coverage-{os.getpid()}"
    other.mkdir()
    tag_state.target.mkdir(parents=True)
    (tag_state.target / coverage_owner.OWNER).write_text(f"{other}\n")
    out = claim(tag_state.target, tag_state.run_dir)
    assert out.returncode == 1
    assert not (tag_state.target / coverage_owner.TAG).exists()


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


# `clean` is red when cargo-llvm-cov did not clean (#2070).
def test_a_clean_that_ran_is_green(clean: Clean) -> None:
    """Nothing reported: 0, and the workspace clean was the call."""
    out, calls = clean("ok")
    assert out.returncode == 0, out.stderr
    assert out.stderr == ""
    expected: list[str] = ["llvm-cov clean --workspace"]
    assert expected == calls


def test_a_refused_clean_is_red_by_name(clean: Clean) -> None:
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


def test_a_failed_cargo_llvm_cov_is_its_status(clean: Clean) -> None:
    """cargo-llvm-cov's own failure is passed through unchanged."""
    out, _ = clean("failed")
    assert out.returncode == 3


# The sweep's liveness rule, read directly.
def test_a_pid_another_user_holds_is_live() -> None:
    """Pid 1, not ours to signal, is live, as `ps -p` reads it."""
    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp) / "org-coverage-1"
        run.mkdir()
        assert coverage_owner.live(str(run))
