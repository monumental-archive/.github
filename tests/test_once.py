"""Table tests for once.py: a command at most once per marker (#2061).

Each row runs the helper as `measured` does, as a process, with a
command that appends a line to the row's log, and asserts what the log
and the marker hold afterwards.
"""

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import once
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

ONCE = support.MISE / "once.py"
SH = support.tool("sh")
# The task whose call the rows stand in for (#1934).
TASK = "test"


@pytest.fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


def lines(*logged: str) -> list[str]:
    """Name the log a row expects.

    Returns:
        The lines, in order.

    """
    return list(logged)


@pytest.mark.usefixtures("_set_up_test_once")
class TestOnce:
    """One branch per row: run, skip, fail and retry, race."""

    @pytest.fixture
    def _set_up_test_once(self, tmp_path: Path) -> None:
        """Make the row's scratch, its log and its marker's path."""
        self.tmp = tmp_path
        self.log = self.tmp / "log"
        self.marker = self.tmp / "rust"

    def start(self, line: str) -> subprocess.Popen[str]:
        """Start the helper on a shell line, its marker the row's.

        Returns:
            The running helper.

        """
        return subprocess.Popen(
            [sys.executable, str(ONCE), str(self.marker), "--", SH, "-c", line],
            env=support.env(LOG=str(self.log)),
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
        assert self.call('echo ran >> "${LOG}"') == 0
        assert lines("ran") == self.ran()
        assert self.marker.is_dir()

    def test_a_second_call_skips_the_command(self) -> None:
        """The marker present: 0 at once, the command not run."""
        assert self.call('echo ran >> "${LOG}"') == 0
        assert self.call('echo again >> "${LOG}"') == 0
        assert lines("ran") == self.ran()

    def test_a_failure_is_the_status_and_the_next_call_runs_again(self) -> None:
        """Exit 3: the helper's status is 3, no marker, and the next call runs."""
        assert self.call('echo failed >> "${LOG}"; exit 3') == 3
        assert not self.marker.exists()
        assert self.call('echo ran >> "${LOG}"') == 0
        assert lines("failed", "ran") == self.ran()
        assert self.marker.is_dir()

    def test_two_concurrent_callers_run_it_once(self) -> None:
        """Side by side: one runs it, the other waits for it and skips."""
        line = 'echo start >> "${LOG}"; sleep 1; echo end >> "${LOG}"'
        helpers = [self.start(line) for _ in range(2)]
        for helper in helpers:
            _, err = helper.communicate(timeout=60)
            assert helper.returncode == 0, err
        assert lines("start", "end") == self.ran()

    def test_no_separator_is_a_usage_error(self) -> None:
        """Arguments without `--` or a command: 2, nothing run."""
        for argv in ([str(self.marker)], [str(self.marker), "--"], ["a", "b", "c"]):
            assert once.main(argv) == 2
        assert not self.marker.exists()
