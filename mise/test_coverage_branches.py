#!/usr/bin/env python3
"""Table tests for the Rust branch-coverage floor (#1359).

`audit:coverage-branches` holds the `rust-branches` line of
`.coverage-floor` on the Monday cron, measured by
`mise/coverage-measure.sh`'s `rust-branches` leg on the repository's
dated nightly; `coverage:adopt` writes that line. The bodies under test
are the tasks' own `run` strings, read out of mise/config.toml with
tomllib and run by bash in a planted git tree, and they call the real
measurement script, the real `coverage_floor.py` and the real
`pgrx-env.sh`. Only `cargo` and `rustup` are stubs: cargo answers
`metadata` with a workspace holding no extension and `llvm-cov` with
the numbers the row planted, and logs every `llvm-cov` call with the
toolchain it ran under, so a row can assert which flags reached it.

Each row asserts the message, not just the exit status: a planted row
scored on exit alone can pass for the wrong reason.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import tomllib
import unittest
from decimal import Decimal
from pathlib import Path

import coverage_floor as cf
import decoded
import hermetic

CANON = Path(__file__).resolve().parent.parent
BELT = CANON / "mise"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")

CONFIG = tomllib.loads((BELT / "config.toml").read_text(encoding="utf-8"))


NIGHTLY = "nightly-2026-07-20"

BRANCH_RUN = f"{NIGHTLY} llvm-cov --workspace --locked --branch --json --summary-only"
SKIP = "audit:coverage-branches: no rust-branches line in .coverage-floor, skipped\n"

# The main floor, measured 82.0 minus the band; the branch line beside it,
# measured 72.0 minus the band. Both hold to the derived-state law.
MAIN = """\
# band: 2
# measured: 82.0
# derived: v1.0.0
80.0
"""
BRANCH = """\
# rust-branches measured: 72.0
rust-branches 70.0
"""

# cargo: `metadata` is a workspace with no pgrx extension, so the shared
# provisioning returns after one call; `llvm-cov show-env` answers as
# cargo-llvm-cov 0.9.1 does, unlogged; every other `llvm-cov` logs its
# argv and the toolchain it ran under, and prints the planted totals.
# With `--branch` it reports branches; without, branches read as an
# uninstrumented run does (count 0, measured on stable 1.97.1). With
# `--lcov` (the line leg's `report`, #1528, #1653) it writes the report
# to `--output-path` instead, the planted line figure as lines hit of
# 100 found.
CARGO = """\
#!/usr/bin/env bash
case "$1" in
  metadata)
    printf '{"packages": [], "workspace_members": [], "workspace_root": "%s",' "${PWD}"
    printf ' "target_directory": "%s/target"}\\n' "${PWD}"
    ;;
  llvm-cov)
    if [[ $2 == show-env ]]; then
      echo "export LLVM_PROFILE_FILE='${CARGO_TARGET_DIR}/tree-%p-%8m.profraw'"
      exit 0
    fi
    echo "${RUSTUP_TOOLCHAIN:-stable} $*" >> "${STUB_LOG}"
    if [[ " $* " == *" --lcov "* ]]; then
      out=""
      while [[ $# -gt 0 ]]; do
        [[ $1 == --output-path ]] && out=$2
        shift
      done
      printf 'SF:a.rs\\nLF:100\\nLH:%s\\nend_of_record\\n' "${STUB_LINES%.*}" > "${out}"
      exit 0
    fi
    if [[ " $* " == *" --branch "* ]]; then
      count=${STUB_BR_COUNT} pct=${STUB_BR}
    else
      count=0 pct=0
    fi
    printf '{"data": [{"totals": {"lines": {"percent": %s},' "${STUB_LINES}"
    printf ' "branches": {"count": %s, "percent": %s}}}]}\\n' "${count}" "${pct}"
    ;;
  *) exit 1 ;;
esac
"""

# rustup: the stable toolchain and the planted nightly both carry
# llvm-tools; `toolchain list` names the nightly only when it is planted.
RUSTUP = """\
#!/usr/bin/env bash
case "$1 $2" in
  "component list") echo "llvm-tools-x86_64-unknown-linux-gnu" ;;
  "toolchain list")
    echo "1.99.1-x86_64-unknown-linux-gnu"
    [[ -z ${STUB_INSTALLED} ]] || echo "${STUB_INSTALLED}-x86_64-unknown-linux-gnu"
    ;;
  *) exit 1 ;;
esac
"""


def measuring_step(task: str) -> str:
    """Read the step of a coverage task that measures (#1667).

    `coverage:adopt` is a run list: create the floor, `test` by
    reference, then the measurement, which is the step these rows run.

    Returns:
        The task's script, or its run list's last one.

    """
    belt = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(belt["tasks"], "the belt's [tasks]")
    run = decoded.as_object(tasks[task], task)["run"]
    if isinstance(run, str):
        return run
    steps = decoded.as_array(run, f"{task}'s run")
    return decoded.as_str(steps[-1], f"{task}'s last step")


def run_task(  # ruff: ignore[too-many-arguments] -- one keyword per planted fact
    task: str,
    *,
    floor: str | None = MAIN + BRANCH,
    cargo_toml: bool = True,
    branches: str = "75.5",
    count: str = "40",
    lines: str = "90.0",
    toolchain: str | None = NIGHTLY,
    installed: str | None = NIGHTLY,
    exclude: str = "",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run one task's body in a planted Rust tree with stub tools.

    Returns:
        The finished process, and the temporary directory holding the
        tree (`tree/`) and the cargo log (`llvm-cov.log`).

    """
    tmp = Path(tempfile.mkdtemp())
    tree = tmp / "tree"
    stubs = tmp / "stubs"
    tree.mkdir()
    stubs.mkdir()
    for name, text in (("cargo", CARGO), ("rustup", RUSTUP)):
        (stubs / name).write_text(text, encoding="utf-8")
        (stubs / name).chmod(0o755)
    log = tmp / "llvm-cov.log"
    log.touch()

    tracked = []
    if cargo_toml:
        (tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        tracked.append("Cargo.toml")
        # What the test task's measured `cargo test` left (#1653): this
        # run's marker and one profile.
        (tmp / "run" / "rust").mkdir(parents=True)
        profiles = tree / "target" / "llvm-cov-target"
        profiles.mkdir(parents=True)
        (profiles / "tree-1-1.profraw").write_text("", encoding="utf-8")
    if floor is not None:
        (tree / ".coverage-floor").write_text(floor, encoding="utf-8")
        tracked.append(".coverage-floor")

    # The stubs stand in for the Rust toolchain, so rustup's own selection
    # is left out of the fixture as well (#1514: the rest is hermetic).
    env = {k: v for k, v in hermetic.env().items() if not k.startswith("RUSTUP_")}
    env |= {
        "ORG_BELT_DIR": str(BELT),
        "PATH": f"{stubs}{os.pathsep}{env.get('PATH', '')}",
        "STUB_LOG": str(log),
        "STUB_BR": branches,
        "STUB_BR_COUNT": count,
        "STUB_LINES": lines,
        "STUB_INSTALLED": installed or "",
        "COVERAGE_EXCLUDE": exclude,
        "ORG_COVERAGE_RUN": str(tmp / "run"),
        # Outside the tree, as the gate job names it (#1628).
        "ORG_COVERAGE_REPORTS": str(tmp / "reports"),
    }
    if toolchain is not None:
        env["FUZZ_TOOLCHAIN"] = toolchain
    subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [GIT, "init", "-q"], cwd=tree, env=env, check=True
    )
    if tracked:
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [GIT, "add", *tracked], cwd=tree, env=env, check=True
        )
    done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [BASH, "-euo", "pipefail", "-c", measuring_step(task)],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return done, tmp


# The line leg's one call, its report in the row's reports directory:
# it renders the test task's own measured run (#1653).
LINE_RUN = "stable llvm-cov report --lcov --output-path <reports>/lcov.info"


class BranchCase(unittest.TestCase):
    """Runs a task and removes the planted tree afterwards."""

    def run_task(
        self, task: str, **plant: str | bool | None
    ) -> tuple[subprocess.CompletedProcess[str], Path, list[str]]:
        """Run a task and schedule its tree for removal.

        Returns:
            The finished process, the planted tree, and one line per
            `cargo llvm-cov` call: the toolchain, then its arguments,
            the reports directory written as `<reports>`.

        """
        done, tmp = run_task(task, **plant)  # type: ignore[arg-type]
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        log = (tmp / "llvm-cov.log").read_text(encoding="utf-8")
        calls = log.replace(str(tmp / "reports"), "<reports>").splitlines()
        return done, tmp / "tree", calls


class AuditRows(BranchCase):
    """`audit:coverage-branches`: held, red by name, and both skips."""

    TASK = "audit:coverage-branches"

    def test_a_floor_held(self) -> None:
        """A measurement at or above the line holds it, by name."""
        done, _, calls = self.run_task(self.TASK)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "audit:coverage-branches: rust-branches holds the floor 70.0 (75.5%)\n",
            done.stdout,
        )
        self.assertEqual(
            [BRANCH_RUN],
            calls,
        )

    def test_below_the_floor_reds_by_name(self) -> None:
        """One tenth below the line is red, naming the leg and both numbers."""
        done, _, _ = self.run_task(self.TASK, branches="69.9")
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertIn(
            "audit:coverage-branches: rust-branches: 69.9% is below"
            " the derived floor 70.0%\n",
            done.stderr,
        )

    def test_no_such_line_skips_clean(self) -> None:
        """A floor with no rust-branches line measures nothing at all."""
        done, _, calls = self.run_task(self.TASK, floor=MAIN)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(SKIP, done.stdout)
        self.assertEqual([], calls)

    def test_no_floor_file_skips_clean(self) -> None:
        """A repository that adopted no floor owes no branch figure either."""
        done, _, calls = self.run_task(self.TASK, floor=None)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(SKIP, done.stdout)
        self.assertEqual([], calls)

    def test_no_cargo_toml_skips_clean(self) -> None:
        """A tree with no Cargo.toml has no Rust branch to measure."""
        done, _, calls = self.run_task(self.TASK, cargo_toml=False)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "audit:coverage-branches: no Cargo.toml, skipped\n", done.stdout
        )
        self.assertEqual([], calls)

    def test_coverage_exclude_is_refused_as_the_rust_leg_refuses_it(self) -> None:
        """The removed variable reds the branch run by name, before any run (#1653)."""
        done, _, calls = self.run_task(self.TASK, exclude="lab-wasm lab-cli")
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertIn(
            "audit:coverage-branches: COVERAGE_EXCLUDE is set (lab-wasm lab-cli) —"
            " it is removed (#1653):\n  the rust leg measures what the test task's"
            " own command builds, so the command\n",
            done.stderr,
        )
        self.assertEqual([], calls)

    def test_an_undeclared_nightly_reds_by_name(self) -> None:
        """A committed line with no FUZZ_TOOLCHAIN cannot be held, and says so."""
        done, _, calls = self.run_task(self.TASK, toolchain=None)
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "audit:coverage-branches: FUZZ_TOOLCHAIN is not declared — Rust branch"
            " coverage needs a nightly\n",
            done.stderr,
        )
        self.assertEqual([], calls)

    def test_a_declared_nightly_not_installed_reds_by_name(self) -> None:
        """The nightly rides the pin; a missing one is never installed mid-run."""
        done, _, calls = self.run_task(self.TASK, installed=None)
        self.assertEqual(1, done.returncode)
        self.assertIn(
            f"audit:coverage-branches: FUZZ_TOOLCHAIN='{NIGHTLY}' is declared but"
            " not installed\n",
            done.stderr,
        )
        self.assertEqual([], calls)

    def test_no_branches_counted_is_refused(self) -> None:
        """Count 0 is an uninstrumented run's shape, never a 0% to compare."""
        done, _, _ = self.run_task(self.TASK, count="0", branches="0")
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "audit:coverage-branches: the Rust report counted no branches"
            " (count '0') — a\n",
            done.stderr,
        )

    def test_a_typed_line_is_refused_as_drift(self) -> None:
        """A rust-branches number typed over its record reds before measuring."""
        typed = MAIN + "# rust-branches measured: 72.0\nrust-branches 60.0\n"
        done, _, calls = self.run_task(self.TASK, floor=typed)
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "rust-branches floor 60.0 disagrees with its own record: measured"
            " 72.0 under band 2 and the cap 90 derives 70.0.",
            done.stderr,
        )
        self.assertEqual([], calls)


class GateRows(BranchCase):
    """The gate's coverage:check never reaches the nightly or `--branch`."""

    def test_check_measures_lines_on_stable_beside_a_branch_line(self) -> None:
        """A committed rust-branches line changes nothing the gate runs."""
        done, tree, calls = self.run_task("coverage:check")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:check: rust leg holds the floor 80.0 (90%)\n"
            f"coverage:check: upload {tree.parent / 'reports' / 'lcov.info'}\n",
            done.stdout,
        )
        self.assertEqual(
            [LINE_RUN],
            calls,
        )


class AdoptRows(BranchCase):
    """`coverage:adopt` derives the line from a measurement, never a guess."""

    def test_adopt_with_the_nightly_writes_a_derived_line(self) -> None:
        """Both legs measured; the file parses with the branch line derived."""
        done, tree, calls = self.run_task(
            "coverage:adopt", floor=None, branches="73.48"
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn(
            "coverage-floor: adopted .coverage-floor at 88.0 (measured 90.0),"
            " rust-branches 71.4 (measured 73.4)\n",
            done.stdout,
        )
        parsed, problems = cf.parse(
            (tree / ".coverage-floor").read_text(encoding="utf-8")
        )
        self.assertEqual([], problems)
        self.assertEqual(
            {"rust-branches": (Decimal("71.4"), Decimal("73.4"))}, parsed.separate
        )
        self.assertEqual(
            [
                LINE_RUN,
                BRANCH_RUN,
            ],
            calls,
        )

    def test_adopt_without_the_nightly_writes_no_line(self) -> None:
        """No FUZZ_TOOLCHAIN: the main floor only, and the reason printed."""
        done, tree, calls = self.run_task("coverage:adopt", floor=None, toolchain=None)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn(
            "coverage:adopt: no FUZZ_TOOLCHAIN declared, so no rust-branches line"
            " (branch coverage needs the dated nightly)\n",
            done.stdout,
        )
        text = (tree / ".coverage-floor").read_text(encoding="utf-8")
        self.assertNotIn("rust-branches", text)
        self.assertEqual(
            [LINE_RUN],
            calls,
        )

    def test_adopt_cannot_lower_the_branch_line(self) -> None:
        """Below its floor, nothing is written."""
        done, tree, _ = self.run_task("coverage:adopt", branches="69.9")
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "coverage-floor: rust-branches measured 69.9% is below its floor 70.0%.",
            done.stderr,
        )
        self.assertEqual(
            MAIN + BRANCH, (tree / ".coverage-floor").read_text(encoding="utf-8")
        )


if __name__ == "__main__":
    unittest.main()
