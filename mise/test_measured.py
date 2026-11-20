#!/usr/bin/env python3
"""Rows for `measured`, the belt command a test task runs through (#1667).

Draft, unpushed: the rows #1667's Done-when names for `measured` itself.
"""

import os
import signal
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
    subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [GIT, "init", "-q", str(tree)], check=True, env=hermetic.env()
    )
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
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
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
            out = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
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
    done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
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


STUB = """#!/bin/sh
echo call >> "{counter}"
echo "  executable: {python}"
echo "  coverage_module: {site}/coverage/__init__.py"
"""


class CoverageIsAskedOncePerRun(unittest.TestCase):
    """Where coverage lives is asked once per run, then read from the run."""

    def test_two_calls_in_one_run_start_coverage_once(self) -> None:
        """A counting `coverage` stub is started by the first call only."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), floor=True)
            bin_dir, counter = Path(tmp) / "bin", Path(tmp) / "calls"
            bin_dir.mkdir()
            python = hermetic.tool("python3")
            site = Path(tmp) / "site"
            stub = bin_dir / "coverage"
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


if __name__ == "__main__":
    unittest.main()
