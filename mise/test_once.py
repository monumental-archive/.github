#!/usr/bin/env python3
"""Table tests for once.py: a command at most once per marker (#2061).

Each row runs the helper as `measured` does, as a process, with a
command that appends a line to the row's log, and asserts what the log
and the marker hold afterwards.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import hermetic
import once

ONCE = Path(__file__).resolve().parent / "once.py"
SH = hermetic.tool("sh")
# The task whose call the rows stand in for (#1934).
TASK = "test"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


def lines(*logged: str) -> list[str]:
    """Name the log a row expects.

    Returns:
        The lines, in order.

    """
    return list(logged)


class Once(unittest.TestCase):
    """One branch per row: run, skip, fail and retry, race."""

    @override
    def setUp(self) -> None:
        """Make the row's scratch, its log and its marker's path."""
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.log = self.tmp / "log"
        self.marker = self.tmp / "rust"

    def start(self, line: str) -> subprocess.Popen[str]:
        """Start the helper on a shell line, its marker the row's.

        Returns:
            The running helper.

        """
        return subprocess.Popen(
            [sys.executable, str(ONCE), str(self.marker), "--", SH, "-c", line],
            env=hermetic.env(LOG=str(self.log)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def call(self, line: str) -> int:
        """Run the helper on a shell line to the end.

        Returns:
            Its exit status.

        """
        helper = self.start(line)
        helper.communicate(timeout=60)
        return helper.returncode

    def ran(self) -> list[str]:
        """Read the row's log.

        Returns:
            One line per time the command ran.

        """
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_the_first_call_runs_the_command_and_writes_the_marker(self) -> None:
        """No marker: the command runs, then the marker is made."""
        self.assertEqual(0, self.call('echo ran >> "${LOG}"'))
        self.assertEqual(lines("ran"), self.ran())
        self.assertTrue(self.marker.is_dir())

    def test_a_second_call_skips_the_command(self) -> None:
        """The marker present: 0 at once, the command not run."""
        self.assertEqual(0, self.call('echo ran >> "${LOG}"'))
        self.assertEqual(0, self.call('echo again >> "${LOG}"'))
        self.assertEqual(lines("ran"), self.ran())

    def test_a_failure_is_the_status_and_the_next_call_runs_again(self) -> None:
        """Exit 3: the helper's status is 3, no marker, and the next call runs."""
        self.assertEqual(3, self.call('echo failed >> "${LOG}"; exit 3'))
        self.assertFalse(self.marker.exists())
        self.assertEqual(0, self.call('echo ran >> "${LOG}"'))
        self.assertEqual(lines("failed", "ran"), self.ran())
        self.assertTrue(self.marker.is_dir())

    def test_two_concurrent_callers_run_it_once(self) -> None:
        """Side by side: one runs it, the other waits for it and skips."""
        line = 'echo start >> "${LOG}"; sleep 1; echo end >> "${LOG}"'
        helpers = [self.start(line) for _ in range(2)]
        for helper in helpers:
            _, err = helper.communicate(timeout=60)
            self.assertEqual(0, helper.returncode, err)
        self.assertEqual(lines("start", "end"), self.ran())

    def test_no_separator_is_a_usage_error(self) -> None:
        """Arguments without `--` or a command: 2, nothing run."""
        for argv in ([str(self.marker)], [str(self.marker), "--"], ["a", "b", "c"]):
            with self.subTest(argv=argv):
                self.assertEqual(2, once.main(argv))
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
