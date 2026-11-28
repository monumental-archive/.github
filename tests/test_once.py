"""Table tests for once.py: a command at most once per marker (#2061).

Each row calls the helper's `main` in this process, as `measured`
starts it, with a command that appends a line to the row's log, and
asserts what the log and the marker hold afterwards. The race is two
threads: the lock is `flock`, which each open of the lock file holds
apart, as two processes would.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

from belt import once
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

SH = support.tool("sh")
# The task whose call the rows stand in for (#1934).
TASK = "test"


@fixture
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


class Once:
    """One branch per row: run, skip, fail and retry, race."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Make the row's scratch, its log and its marker's path."""
        self.tmp = tmp_path
        self.log = self.tmp / "log"
        self.marker = self.tmp / "rust"
        monkeypatch.setenv("LOG", str(self.log))

    def argv(self, line: str) -> list[str]:
        """Spell the helper's argv for a shell line, its marker the row's.

        Returns:
            The argv.

        """
        return [str(self.marker), "--", SH, "-c", line]

    def ran(self) -> list[str]:
        """Read the row's log.

        Returns:
            One line per time the command ran.

        """
        return self.log.read_text().splitlines() if self.log.exists() else []


@fixture
def once_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Once:
    """Give each row its own Once."""
    return Once(tmp_path, monkeypatch)


def test_the_first_call_runs_the_command_and_writes_the_marker(
    once_state: Once,
) -> None:
    """No marker: the command runs, then the marker is made."""
    assert once.main(once_state.argv('echo ran >> "${LOG}"')) == 0
    assert lines("ran") == once_state.ran()
    assert once_state.marker.is_dir()


def test_a_second_call_skips_the_command(once_state: Once) -> None:
    """The marker present: 0 at once, the command not run."""
    assert once.main(once_state.argv('echo ran >> "${LOG}"')) == 0
    assert once.main(once_state.argv('echo again >> "${LOG}"')) == 0
    assert lines("ran") == once_state.ran()


def test_a_failure_is_the_status_and_the_next_call_runs_again(once_state: Once) -> None:
    """Exit 3: the helper's status is 3, no marker, and the next call runs."""
    assert once.main(once_state.argv('echo failed >> "${LOG}"; exit 3')) == 3
    assert not once_state.marker.exists()
    assert once.main(once_state.argv('echo ran >> "${LOG}"')) == 0
    assert lines("failed", "ran") == once_state.ran()
    assert once_state.marker.is_dir()


def test_two_concurrent_callers_run_it_once(once_state: Once) -> None:
    """Side by side: one runs it, the other waits for it and skips."""
    line = 'echo start >> "${LOG}"; sleep 1; echo end >> "${LOG}"'
    with ThreadPoolExecutor(max_workers=2) as pool:
        argv = once_state.argv(line)
        statuses = list(pool.map(once.main, [argv, argv]))
    assert statuses == [0, 0]
    assert lines("start", "end") == once_state.ran()


def test_no_separator_is_a_usage_error(once_state: Once) -> None:
    """Arguments without `--` or a command: 2, nothing run."""
    for argv in (
        [str(once_state.marker)],
        [str(once_state.marker), "--"],
        ["a", "b", "c"],
    ):
        assert once.main(argv) == 2
    assert not once_state.marker.exists()
