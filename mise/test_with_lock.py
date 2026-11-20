#!/usr/bin/env python3
"""Tests for with_lock.py, the kernel-held command lock (#1481).

The lock is between processes, so a holder is always a separate
process. The caller under test is called in the test process (#1697),
where mutation, coverage and the type checker see it: it waits while a
holder has the lock, says so and runs nothing past its bound, and hands
back its command's own exit status. One row starts the helper itself as
both processes: the script contract.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import override

import hermetic
import with_lock

HELPER = Path(__file__).resolve().with_name("with_lock.py")


class LockCase(unittest.TestCase):
    """A row with its own lock file and scratch directory.

    Every helper runs with the row's directory as its cwd: a process
    left in a deleted directory by an earlier test in the same run
    must not reach these rows' exact-stderr assertions.
    """

    @override
    def setUp(self) -> None:
        """Make a scratch directory for the lock and the markers."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.lock = self.root / "lock"

    def start(self, *command: str, timeout: str = "600") -> subprocess.Popen[str]:
        """Start the helper running `command` under the row's lock.

        Returns:
            The running helper.

        """
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.Popen(
            [
                sys.executable,
                str(HELPER),
                "--timeout",
                timeout,
                "--message",
                "waited too long for the row's lock",
                str(self.lock),
                "--",
                *command,
            ],
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def hold(self, held: Path, seconds: float) -> subprocess.Popen[str]:
        """Hold the row's lock from a separate, non-helper process.

        Returns:
            The running holder; `held` appears once it has the lock.

        """
        script = (
            "import fcntl, os, pathlib, sys, time\n"
            "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\n"
            "pathlib.Path(sys.argv[2]).touch()\n"
            "time.sleep(float(sys.argv[3]))\n"
        )
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.Popen(
            [sys.executable, "-c", script, str(self.lock), str(held), str(seconds)],
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def call(self, *command: str, timeout: str = "600") -> hermetic.Called:
        """Call the helper in-process running `command` under the lock.

        Returns:
            The status and both streams.

        """
        return hermetic.call_main(
            lambda: with_lock.main([
                *("--timeout", timeout),
                *("--message", "waited too long for the row's lock"),
                *(str(self.lock), "--", *command),
            ]),
            cwd=self.root,
        )

    @staticmethod
    def finish(task: subprocess.Popen[str]) -> tuple[int, str]:
        """Wait for the helper and close its pipes.

        Returns:
            (exit status, stderr).

        """
        _, err = task.communicate(timeout=60)
        return task.returncode, err

    def wait_for(self, marker: Path) -> None:
        """Block until `marker` exists, failing the row after 30 s."""
        deadline = time.monotonic() + 30
        while not marker.exists():
            self.assertLess(time.monotonic(), deadline, f"{marker} never appeared")
            time.sleep(0.05)


class HeldLockMakesTheNextCallerWait(LockCase):
    """A second caller runs only after the first has let the lock go."""

    def test_the_second_command_starts_after_the_first_ends(self) -> None:
        """The events interleave only one way: start, end, start, end."""
        log = self.root / "log"
        held = self.root / "held"
        first = self.start(
            "sh",
            "-c",
            f'echo start >> "{log}"; touch "{held}"; sleep 1; echo end >> "{log}"',
        )
        self.wait_for(held)
        second = self.start("sh", "-c", f'echo start >> "{log}"; echo end >> "{log}"')
        self.assertEqual((0, ""), self.finish(first))
        self.assertEqual((0, ""), self.finish(second))
        want: list[str] = ["start", "end", "start", "end"]
        self.assertEqual(want, log.read_text().split())


class TimeoutSaysSoAndRunsNothing(LockCase):
    """Past its bound a caller exits non-zero with the message."""

    def test_a_timed_out_caller_prints_the_message_and_runs_nothing(self) -> None:
        """The command under the lock never runs once the bound has passed."""
        held = self.root / "held"
        ran = self.root / "ran"
        holder = self.hold(held, 3)
        self.wait_for(held)
        done = self.call("touch", str(ran), timeout="0.5")
        self.assertEqual(75, done.returncode)
        self.assertEqual("waited too long for the row's lock\n", done.stderr)
        self.assertFalse(ran.exists())
        self.finish(holder)


class ExitStatusPassesThrough(LockCase):
    """The helper exits with the command's own status."""

    def test_the_commands_status_is_the_helpers(self) -> None:
        """A zero, a one and a seven come back as themselves."""
        for status in (0, 1, 7):
            with self.subTest(status=status):
                self.assertEqual(
                    status, self.call("sh", "-c", f"exit {status}").returncode
                )

    def test_the_lock_file_is_left_for_the_next_caller(self) -> None:
        """Nobody removes the lock file: the kernel lock is on it, not it."""
        self.assertEqual(0, self.call("true").returncode)
        self.assertTrue(self.lock.exists())
        self.assertEqual(0, self.lock.stat().st_size)


class Usage(LockCase):
    """A command line the helper cannot read is exit 2, named."""

    def test_no_separator_is_the_usage(self) -> None:
        """Without `--` there is no command to run."""
        done = hermetic.call_main(lambda: with_lock.main([str(self.lock), "true"]))
        self.assertEqual(
            (2, "with-lock: usage: with_lock.py <lockfile> -- <command…>\n"),
            (done.returncode, done.stderr),
        )

    def test_no_command_after_the_separator_is_refused(self) -> None:
        """`--` with nothing after it names what is missing."""
        done = hermetic.call_main(lambda: with_lock.main([str(self.lock), "--"]))
        self.assertEqual(
            (2, "with-lock: no command after --\n"), (done.returncode, done.stderr)
        )

    def test_the_default_message_names_the_wait_and_the_file(self) -> None:
        """No --message: the timeout says how long, on which file."""
        held = self.root / "held"
        holder = self.hold(held, 3)
        self.wait_for(held)
        done = hermetic.call_main(
            lambda: with_lock.main(["--timeout", "0.2", str(self.lock), "--", "true"])
        )
        self.finish(holder)
        self.assertEqual(
            (75, f"with-lock: waited 0.2s for {self.lock}\n"),
            (done.returncode, done.stderr),
        )


if __name__ == "__main__":
    unittest.main()
