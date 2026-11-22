#!/usr/bin/env python3
"""Rows for `measured`, the belt command a test task runs through (#1667).

Draft, unpushed: the rows #1667's Done-when names for `measured` itself.
"""

import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_cache_key
import hermetic

BELT = Path(__file__).resolve().parent
MEASURED = BELT / "measured"
GIT = hermetic.tool("git")
SH = hermetic.tool("sh")
# What a process gains by being exec'd through a bash script rather than
# directly: the shell's own bookkeeping, and macOS's text encoding,
# which the system adds to every process. Nothing a caller passes.
SHELL_ADDED = frozenset({"PWD", "OLDPWD", "SHLVL", "_", "__CF_USER_TEXT_ENCODING"})
# What a row expects to differ: nothing.
NOTHING: dict[str, str] = {}
ENV = hermetic.tool("env")
# The child prints its environment NUL-separated, so a row compares it
# whole, values with newlines included.
DUMP = [ENV, "-0"]


def environment(dump: str) -> dict[str, str]:
    """Read an `env -0` listing.

    Returns:
        Each variable's value by name.

    """
    pairs = (entry.partition("=") for entry in dump.split("\0") if entry)
    return {name: value for name, _, value in pairs}


def plant(root: Path, *, floor: bool) -> Path:
    """Make a git tree, with or without a `.coverage-floor`.

    Returns:
        The tree.

    """
    tree = root / "tree"
    tree.mkdir()
    subprocess.run([GIT, "init", "-q", str(tree)], check=True, env=hermetic.env())
    if floor:
        (tree / ".coverage-floor").write_text("python 50.0\n")
    return tree


def unchanged(given: dict[str, str], seen: dict[str, str]) -> dict[str, str]:
    """Compare what a child saw with what it was given.

    Returns:
        Every variable that was added, changed or dropped, beyond the
        shell's own bookkeeping; empty when the environment came
        through unchanged.

    """
    names = (set(given) | set(seen)) - SHELL_ADDED
    return {n: seen.get(n, "<dropped>") for n in names if given.get(n) != seen.get(n)}


def run(tree: Path, argv: list[str], **env: str) -> subprocess.CompletedProcess[str]:
    """Run `measured` with argv in the tree.

    Returns:
        The finished process, text mode.

    """
    return subprocess.run(
        [str(MEASURED), *argv],
        cwd=tree,
        env=hermetic.env(**env),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


class NoMeasurementIsExec(unittest.TestCase):
    """Without a floor, or outside a measured run, `measured` is `exec "$@"`."""

    def test_no_floor_leaves_the_environment_unchanged(self) -> None:
        """A run directory but no floor: the child sees exactly what was passed."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), floor=False)
            given = {"ORG_COVERAGE_RUN": str(Path(tmp) / "run"), "PYTHONPATH": "src"}
            out = run(tree, DUMP, **given)
            self.assertEqual(0, out.returncode, out.stderr)
            seen = environment(out.stdout)
            self.assertEqual(NOTHING, unchanged(hermetic.env(**given), seen))

    def test_no_run_directory_leaves_the_environment_unchanged(self) -> None:
        """A floor but no run directory (outside mise): nothing is added."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), floor=True)
            out = run(tree, DUMP, PYTHONPATH="src")
            self.assertEqual(0, out.returncode, out.stderr)
            seen = environment(out.stdout)
            self.assertEqual(NOTHING, unchanged(hermetic.env(PYTHONPATH="src"), seen))

    def test_the_exit_status_is_the_commands(self) -> None:
        """Exec, not a wrapper: the status comes back unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), floor=False)
            out = run(tree, [SH, "-c", "exit 7"])
            self.assertEqual(7, out.returncode)

    def test_a_signal_is_the_commands(self) -> None:
        """A command killed by a signal is reported killed by that signal."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), floor=False)
            out = run(tree, [SH, "-c", "kill -TERM $$"])
            self.assertEqual(-signal.SIGTERM, out.returncode)

    def test_no_command_is_refused(self) -> None:
        """`measured` with nothing to run says how it is used."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), floor=False)
            out = run(tree, [])
            self.assertEqual(2, out.returncode)
            self.assertIn("measured: no command", out.stderr)


class FloorMergesNeverReplaces(unittest.TestCase):
    """With a floor and a run directory the measurement is merged in."""

    def measured_env(self, tmp: str, **given: str) -> dict[str, str]:
        """Run the dump through `measured` in a floored tree.

        Returns:
            The child's environment.

        """
        tree = plant(Path(tmp), floor=True)
        out = run(tree, DUMP, ORG_COVERAGE_RUN=str(Path(tmp) / "run"), **given)
        self.assertEqual(0, out.returncode, out.stderr)
        return environment(out.stdout)

    def test_the_task_pythonpath_is_kept_behind_the_hook(self) -> None:
        """The task's own PYTHONPATH stays, with the hook in front of it."""
        with tempfile.TemporaryDirectory() as tmp:
            seen = self.measured_env(tmp, PYTHONPATH="src:lib")
            run_dir = Path(tmp) / "run"
            self.assertEqual(f"{run_dir}/hook:src:lib", seen["PYTHONPATH"])
            self.assertEqual(
                str(run_dir / "coverage.rc"), seen["COVERAGE_PROCESS_START"]
            )

    def test_the_rc_file_names_the_run_and_the_root(self) -> None:
        """The run's rc file: its data file in the run, the tree as source."""
        with tempfile.TemporaryDirectory() as tmp:
            self.measured_env(tmp)
            rc = (Path(tmp) / "run" / "coverage.rc").read_text()
            tree = (Path(tmp) / "tree").resolve()
            self.assertIn(f"source = {tree}\n", rc)
            self.assertIn(f"data_file = {Path(tmp) / 'run' / 'python'}/.coverage\n", rc)
            self.assertIn("branch = true\n", rc)

    def test_the_hook_is_added_once(self) -> None:
        """A measured command run inside another adds nothing twice."""
        with tempfile.TemporaryDirectory() as tmp:
            hook = Path(tmp) / "run" / "hook"
            seen = self.measured_env(tmp, PYTHONPATH=f"{hook}:src")
            self.assertEqual(f"{hook}:src", seen["PYTHONPATH"])

    def test_a_task_plpgsql_report_is_kept(self) -> None:
        """An ORG_PLPGSQL_COVERAGE the task set is not replaced."""
        with tempfile.TemporaryDirectory() as tmp:
            seen = self.measured_env(tmp, ORG_PLPGSQL_COVERAGE="/own/report.json")
            self.assertEqual("/own/report.json", seen["ORG_PLPGSQL_COVERAGE"])

    def test_the_plpgsql_report_lands_in_the_run_directory(self) -> None:
        """Unset by the task, the report is named in the run directory."""
        with tempfile.TemporaryDirectory() as tmp:
            seen = self.measured_env(tmp)
            self.assertEqual(
                str(Path(tmp) / "run" / "plpgsql-coverage.json"),
                seen["ORG_PLPGSQL_COVERAGE"],
            )

    def test_it_runs_under_sh(self) -> None:
        """Named from an `sh -c` line, as a task with `shell = "sh -c"` does."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), floor=True)
            line = f'"{MEASURED}" sh -c \'printf %s "$PYTHONPATH"\''
            out = subprocess.run(
                [SH, "-c", line],
                cwd=tree,
                env=hermetic.env(
                    ORG_COVERAGE_RUN=str(Path(tmp) / "run"), PYTHONPATH="src"
                ),
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(0, out.returncode, out.stderr)
            self.assertEqual(f"{Path(tmp) / 'run' / 'hook'}:src", out.stdout)


def dead_pid() -> int:
    """Start a process and wait for it to end.

    Returns:
        A pid no process holds any more.

    """
    done = subprocess.run(
        [SH, "-c", "echo $$"], capture_output=True, text=True, check=True
    )
    return int(done.stdout)


class EarlierRunsAreSwept(unittest.TestCase):
    """The first call in a run removes run directories whose mise is gone."""

    def test_a_dead_run_is_removed_and_a_live_one_kept(self) -> None:
        """A sibling for a dead pid goes; one for a live pid stays."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), floor=True)
            dead = Path(tmp) / f"org-coverage-{dead_pid()}"
            live = Path(tmp) / f"org-coverage-{os.getpid()}"
            for old in (dead, live):
                (old / "python").mkdir(parents=True)
            this = Path(tmp) / f"org-coverage-{dead_pid()}"
            out = run(tree, [SH, "-c", "true"], ORG_COVERAGE_RUN=str(this))
            self.assertEqual(0, out.returncode, out.stderr)
            self.assertFalse(dead.exists(), "a dead run's directory was left")
            self.assertTrue(live.exists(), "a live run's directory was removed")
            self.assertTrue(this.is_dir(), "this run's directory was not made")


# The venv's interpreter, beside the `coverage` entry point: it counts
# its starts and runs the suite's python3 over a planted coverage
# package, so the belt's one check (tool_python.py, #1788) passes and
# names `{site}` as the directory coverage is imported from.
STUB = """#!/bin/sh
echo call >> "{counter}"
PYTHONPATH="{site}" exec "{python}" "$@"
"""


class CoverageIsAskedOncePerRun(unittest.TestCase):
    """Where coverage lives is asked once per run, then read from the run."""

    def test_two_calls_in_one_run_start_coverage_once(self) -> None:
        """A counting venv interpreter is started by the first call only."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), floor=True)
            bin_dir, counter = Path(tmp) / "bin", Path(tmp) / "calls"
            bin_dir.mkdir()
            python = hermetic.tool("python3")
            site = Path(tmp) / "site"
            (site / "coverage").mkdir(parents=True)
            (site / "coverage" / "__init__.py").write_text("")
            entry = bin_dir / "coverage"
            entry.write_text("#!/bin/sh\n")
            entry.chmod(0o755)
            stub = bin_dir / "python"
            stub.write_text(STUB.format(counter=counter, python=python, site=site))
            stub.chmod(0o755)
            path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
            given = {"ORG_COVERAGE_RUN": str(Path(tmp) / "run"), "PATH": path}
            first = run(tree, DUMP, **given)
            second = run(tree, DUMP, **given)
            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertEqual("call\n", counter.read_text())
            hook = (Path(tmp) / "run" / "hook" / "sitecustomize.py").read_text()
            # The work, through the belt's one writer: coverage's start-up
            # from its own site directory, then the chain (#1542).
            self.assertIn(f"sys.path.append({str(site)!r})\n", hook)
            self.assertIn("coverage.process_startup()\n", hook)
            self.assertIn("_belt_chain(_belt_entries)", hook)
            for out in (first, second):
                self.assertEqual(
                    str(Path(tmp) / "run" / "coverage.rc"),
                    environment(out.stdout)["COVERAGE_PROCESS_START"],
                )


class CoverageOnAnotherPython(unittest.TestCase):
    """A coverage venv with no interpreter: refused in the one wording (#1788)."""

    def test_a_venv_with_no_interpreter_is_refused(self) -> None:
        """No `python` beside the entry point: red, the remedy named."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), floor=True)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            entry = bin_dir / "coverage"
            entry.write_text("#!/bin/sh\n")
            entry.chmod(0o755)
            path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
            done = run(
                tree,
                DUMP,
                ORG_COVERAGE_RUN=str(Path(tmp) / "run"),
                PATH=path,
                MISE_TASK_NAME="test",
            )
            self.assertEqual(1, done.returncode)
            self.assertRegex(
                done.stderr,
                r"\Atest: coverage runs on 'no interpreter' but the suite's"
                r" python3 is '3\.[^']+' — the tool venv must be built on the"
                r" belt's python pin \(reinstall: mise install -f pipx:coverage\)\n\Z",
            )


GO_COVER = "-cover -coverpkg=./... -covermode=atomic -exec={belt}/go-test-exec"
GO_REFUSAL = (
    "which would measure Go a second way or\n"
    "  replace the measurement's own (#1651); drop it: the go leg measures every\n"
    "  go test with -cover -coverpkg=./... -covermode=atomic into the run\n"
)


class GoMerged(unittest.TestCase):
    """In a tree with go.mod, the cover flags join the command's GOFLAGS (#1651)."""

    @staticmethod
    def go_run(tmp: str, argv: list[str], **given: str) -> tuple[Path, str, int]:
        """Run `measured` in a floored Go tree.

        Returns:
            The run's directory, and what the command printed or refused.

        """
        tree = plant(Path(tmp), floor=True)
        (tree / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
        run_dir = Path(tmp) / "run"
        out = run(tree, argv, ORG_COVERAGE_RUN=str(run_dir), **given)
        return run_dir, out.stdout + out.stderr, out.returncode

    def test_the_tasks_own_goflags_are_kept_in_front(self) -> None:
        """The task's -trimpath stays; the cover flags and the program follow."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, out, status = self.go_run(tmp, DUMP, GOFLAGS="-trimpath")
            self.assertEqual(0, status, out)
            seen = environment(out)
            self.assertEqual("-trimpath " + GO_COVER.format(belt=BELT), seen["GOFLAGS"])
            self.assertEqual(str(run_dir / "go"), seen["GOCOVERDIR"])
            self.assertTrue((run_dir / "go").is_dir())

    def test_no_go_mod_leaves_goflags_alone(self) -> None:
        """A tree that is not Go is handed no Go flag."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), floor=True)
            out = run(tree, DUMP, ORG_COVERAGE_RUN=str(Path(tmp) / "run"), GOFLAGS="-x")
            self.assertEqual(0, out.returncode, out.stderr)
            seen = environment(out.stdout)
            self.assertEqual("-x", seen["GOFLAGS"])
            self.assertNotIn("GOCOVERDIR", seen)

    def test_a_measuring_flag_of_the_tasks_own_is_refused(self) -> None:
        """On its `go test`, in either spelling, or already in GOFLAGS."""
        for argv, given, named in (
            (
                ["go", "test", "./...", "-coverprofile=x.out"],
                {},
                "-coverprofile=x.out on its go test",
            ),
            ([SH, "-c", "go test --cover ./..."], {}, "--cover on its go test"),
            (
                [SH, "-c", "go vet ./... && /usr/local/go/bin/go test -exec=x ./..."],
                {},
                "-exec=x on its go test",
            ),
            (
                ["go", "test", "./..."],
                {"GOFLAGS": "-covermode=set"},
                "-covermode=set is in GOFLAGS",
            ),
            (
                ["go", "test", "./..."],
                {"GOCOVERDIR": "/elsewhere"},
                "the environment's GOCOVERDIR is set (GOCOVERDIR=/elsewhere)",
            ),
        ):
            with self.subTest(named=named), tempfile.TemporaryDirectory() as tmp:
                _, out, status = self.go_run(tmp, argv, **given)
                self.assertEqual(1, status, out)
                self.assertEqual(f"measured: {named}, {GO_REFUSAL}", out)

    def test_words_outside_a_go_test_are_not_go_flags(self) -> None:
        """`find -exec` and an `-args` tail are the command's, not go's."""
        for line in (
            "find . -name go.mod -exec true \\; ; printf ok",
            "printf ok; exit 0; go test ./... -args -cover",
        ):
            with self.subTest(line=line), tempfile.TemporaryDirectory() as tmp:
                _, out, status = self.go_run(tmp, [SH, "-c", line])
                self.assertEqual(0, status, out)
                self.assertEqual("ok", out)


# A stand-in cargo, enough to follow the Rust mechanism (#1653): it logs
# each call with the CARGO_TARGET_DIR it was started with, answers `cargo
# metadata` with the planted tree's target directory, and `llvm-cov
# show-env` as cargo-llvm-cov 0.9.1 does: the profile path is under the
# CARGO_TARGET_DIR it is given, or under `target/` without one (probed on
# release-lab). STUB_NO_TARGET answers a metadata with no target
# directory.
CARGO = """#!/usr/bin/env bash
echo "$* | CARGO_TARGET_DIR=${CARGO_TARGET_DIR:-}" >> "${STUB_LOG}"
case "$1 ${2:-}" in
  "metadata --no-deps")
    if [[ -n ${STUB_NO_TARGET:-} ]]; then
      echo '{"packages": []}'
    else
      printf '{"target_directory": "%s/target"}\\n' "${PWD}"
    fi
    ;;
  "llvm-cov show-env")
    dir=${CARGO_TARGET_DIR:-${PWD}/target}
    echo "export LLVM_PROFILE_FILE='${dir}/tree-%p-%8m.profraw'"
    echo "export RUSTC_WRAPPER=/stub/cargo-llvm-cov"
    echo "export CARGO_LLVM_COV_TARGET_DIR=${dir}"
    ;;
  "llvm-cov clean") ;;
  *) ;;
esac
"""
# The directory digest of the stand-in `rustc` the rows run beside
# (#1877), under the `stable` toolchain they set.
STABLE = gate_cache_key.directory_digest(hermetic.rustc_verbose("stable"))

# What a row expects when the stand-in cargo was never called.
NO_CALLS: list[str] = []
RUST_REFUSAL = (
    "which would measure Rust a second way or\n"
    "  replace the measurement's own (#1653); drop it: the rust leg measures every\n"
    "  cargo the test command starts, instrumented into a target directory of its own\n"
)


class RustInstrumented(unittest.TestCase):
    """In a tree with Cargo.toml the command's own cargo is instrumented (#1653)."""

    @staticmethod
    def rust_run(
        tmp: str, argv: list[str], *, run_name: str = "run", **given: str
    ) -> tuple[Path, subprocess.CompletedProcess[str], list[str]]:
        """Run `measured` in a floored Rust tree, beside a stand-in cargo.

        Returns:
            The tree, the finished process, and cargo's log of calls.

        """
        base = Path(tmp)
        tree = base / "tree"
        if not tree.exists():
            tree = plant(base, floor=True)
            (tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
            stubs = base / "stubs"
            stubs.mkdir()
            (stubs / "cargo").write_text(CARGO, encoding="utf-8")
            (stubs / "cargo").chmod(0o755)
            hermetic.rustc(stubs)
        log = base / "cargo.log"
        path = f"{base / 'stubs'}{os.pathsep}{os.environ['PATH']}"
        out = run(
            tree,
            argv,
            ORG_COVERAGE_RUN=str(base / run_name),
            PATH=path,
            STUB_LOG=str(log),
            **({"RUSTUP_TOOLCHAIN": "stable"} | given),
        )
        calls = log.read_text().splitlines() if log.exists() else []
        return tree.resolve(), out, calls

    def test_the_target_directory_is_set_before_show_env_is_asked(self) -> None:
        """`<target>/llvm-cov-target-<digest>`, exported, then show-env under it."""
        with tempfile.TemporaryDirectory() as tmp:
            tree, out, calls = self.rust_run(tmp, DUMP)
            self.assertEqual(0, out.returncode, out.stderr)
            seen = environment(out.stdout)
            target = f"{tree}/target/llvm-cov-target-{STABLE}"
            self.assertEqual(target, seen["CARGO_TARGET_DIR"])
            self.assertEqual(f"{target}/tree-%p-%8m.profraw", seen["LLVM_PROFILE_FILE"])
            self.assertEqual("/stub/cargo-llvm-cov", seen["RUSTC_WRAPPER"])
            self.assertIn(f"llvm-cov show-env --sh | CARGO_TARGET_DIR={target}", calls)

    def test_old_profiles_are_cleaned_once_per_run(self) -> None:
        """Two calls in one run clean once; the next run cleans again."""
        with tempfile.TemporaryDirectory() as tmp:
            calls: list[str] = []
            for run_name in ("run", "run", "next"):
                _, out, calls = self.rust_run(tmp, DUMP, run_name=run_name)
                self.assertEqual(0, out.returncode, out.stderr)
            target = f"{Path(tmp).resolve()}/tree/target/llvm-cov-target-{STABLE}"
            cleaned = [c for c in calls if c.startswith("llvm-cov clean")]
            twice: list[str] = [
                f"llvm-cov clean --profraw-only | CARGO_TARGET_DIR={target}"
            ] * 2
            self.assertEqual(twice, cleaned)

    def test_two_compilers_measure_into_two_directories(self) -> None:
        """Two `rustc` differing only in `release:`: two directories (#1877).

        On main both exported `<target>/llvm-cov-target`, so a bump's
        executables landed beside the old compiler's and the report read
        both.
        """
        seen = []
        for release in ("1.97.1", "1.100.0"):
            with tempfile.TemporaryDirectory() as tmp, self.subTest(release=release):
                tree, out, _ = self.rust_run(tmp, DUMP, RUSTUP_TOOLCHAIN=release)
                self.assertEqual(0, out.returncode, out.stderr)
                verbose = hermetic.rustc_verbose(release)
                digest = gate_cache_key.directory_digest(verbose)
                target = environment(out.stdout)["CARGO_TARGET_DIR"]
                self.assertEqual(f"{tree}/target/llvm-cov-target-{digest}", target)
                seen.append(target.rsplit("/", 1)[1])
        self.assertNotEqual(seen[0], seen[1])

    def test_no_cargo_toml_is_handed_no_rust_environment(self) -> None:
        """A floored tree that is not Rust: no cargo is asked anything."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), floor=True)
            out = run(tree, DUMP, ORG_COVERAGE_RUN=str(Path(tmp) / "run"))
            self.assertEqual(0, out.returncode, out.stderr)
            seen = environment(out.stdout)
            self.assertNotIn("CARGO_TARGET_DIR", seen)
            self.assertNotIn("LLVM_PROFILE_FILE", seen)

    def test_a_metadata_with_no_target_directory_is_refused(self) -> None:
        """Nowhere to measure into: refused by name, and nothing runs."""
        with tempfile.TemporaryDirectory() as tmp:
            _, out, calls = self.rust_run(
                tmp, [SH, "-c", "echo ran"], STUB_NO_TARGET="1"
            )
            self.assertEqual(1, out.returncode)
            self.assertEqual(
                "measured: cargo metadata named no target_directory — nowhere to"
                " measure Rust into\n",
                out.stderr,
            )
            self.assertEqual("", out.stdout)
            self.assertNotIn("show-env", "\n".join(calls))

    def test_a_task_that_measures_itself_is_refused(self) -> None:
        """Item 10's four, by message, before cargo is asked anything."""
        cargo_cmd = [SH, "-c", "cargo test && cargo llvm-cov --lcov"]
        for argv, given, named in (
            (
                DUMP,
                {"CARGO_TARGET_DIR": "/own"},
                "the environment's CARGO_TARGET_DIR is set (CARGO_TARGET_DIR=/own)",
            ),
            (
                DUMP,
                {"RUSTC_WRAPPER": "sccache"},
                "the environment's RUSTC_WRAPPER is set (RUSTC_WRAPPER=sccache)",
            ),
            (
                DUMP,
                {"LLVM_PROFILE_FILE": "/own/%p.profraw"},
                (
                    "the environment's LLVM_PROFILE_FILE is set"
                    " (LLVM_PROFILE_FILE=/own/%p.profraw)"
                ),
            ),
            (cargo_cmd, {}, "cargo llvm-cov is in the test command"),
            (
                ["/usr/bin/cargo", "+nightly", "llvm-cov", "test"],
                {},
                "cargo llvm-cov is in the test command",
            ),
            (
                ["/opt/bin/cargo-llvm-cov", "llvm-cov", "test"],
                {},
                "/opt/bin/cargo-llvm-cov is in the test command",
            ),
        ):
            with self.subTest(named=named), tempfile.TemporaryDirectory() as tmp:
                _, out, calls = self.rust_run(tmp, argv, **given)
                self.assertEqual(1, out.returncode, out.stderr)
                self.assertEqual(f"measured: {named}, {RUST_REFUSAL}", out.stderr)
                self.assertEqual(NO_CALLS, calls)

    def test_llvm_cov_outside_a_cargo_is_not_refused(self) -> None:
        """A word `llvm-cov` no cargo starts is the command's own."""
        with tempfile.TemporaryDirectory() as tmp:
            _, out, _ = self.rust_run(tmp, [SH, "-c", "echo llvm-cov"])
            self.assertEqual(0, out.returncode, out.stderr)
            self.assertEqual("llvm-cov\n", out.stdout)


if __name__ == "__main__":
    unittest.main()
