#!/usr/bin/env python3
"""Tests for with-lock.py, the kernel-held command lock (#1481).

Every row runs the real helper as a separate process, because the lock
is between processes: a second caller waits while a first holds it, a
caller that waits past its bound says so and runs nothing, and a
command's own exit status comes back unchanged.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import time
import unittest
from pathlib import Path

HELPER = Path(__file__).resolve().with_name("with-lock.py")


class LockCase(unittest.TestCase):
    """A row with its own lock file and scratch directory.

    Every helper runs with the row's directory as its cwd: a process
    left in a deleted directory by an earlier test in the same run
    must not reach these rows' exact-stderr assertions.
    """

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
        self.assertEqual(["start", "end", "start", "end"], log.read_text().split())


class TimeoutSaysSoAndRunsNothing(LockCase):
    """Past its bound a caller exits non-zero with the message."""

    def test_a_timed_out_caller_prints_the_message_and_runs_nothing(self) -> None:
        """The command under the lock never runs once the bound has passed."""
        held = self.root / "held"
        ran = self.root / "ran"
        first = self.start("sh", "-c", f'touch "{held}"; sleep 3')
        self.wait_for(held)
        code, err = self.finish(self.start("touch", str(ran), timeout="0.5"))
        self.assertEqual(75, code)
        self.assertEqual("waited too long for the row's lock\n", err)
        self.assertFalse(ran.exists())
        self.finish(first)


class ExitStatusPassesThrough(LockCase):
    """The helper exits with the command's own status."""

    def test_the_commands_status_is_the_helpers(self) -> None:
        """A zero, a one and a seven come back as themselves."""
        for status in (0, 1, 7):
            with self.subTest(status=status):
                code, _ = self.finish(self.start("sh", "-c", f"exit {status}"))
                self.assertEqual(status, code)

    def test_the_lock_file_is_left_for_the_next_caller(self) -> None:
        """Nobody removes the lock file: the kernel lock is on it, not it."""
        self.finish(self.start("true"))
        self.assertTrue(self.lock.exists())
        self.assertEqual(0, self.lock.stat().st_size)


if __name__ == "__main__":
    unittest.main()
