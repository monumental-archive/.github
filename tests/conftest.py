"""The hooks every belt test runs under, and its fixtures (#2177).

Two hooks hold every test, unasked. They are hooks, not autouse
fixtures: ruff's `pytest-fixture-autouse` reds an autouse fixture and
has no code, so a fixture would need a suppression.

- THE ENVIRONMENT (#1508, #857). Before a test runs, `os.environ` is
  cleared to `support.env()`: the variables INHERITED names, HOME and
  a git identity. In-process code sees nothing from the job the suite
  runs in (a release job's `VERSION`, a hook's `GIT_DIR`, the gate's
  `MISE_TASK_NAME` and `ORG_FINDINGS`), and a process the test starts
  inherits the same set. Restored at its teardown.
- THE WORKING DIRECTORY (#1491). A test that changes directory and
  does not change back fails, by name, at its teardown (after its
  fixtures, so `monkeypatch.chdir` has been undone), and the suite's
  directory is restored for the next test. A test that needs another
  directory takes `monkeypatch.chdir`, which restores it.

And these are asked for by name:

- `env`: `support.env`, the one environment a process a test starts
  is given, for the processes that remain (git, stele, a shell body);
- `call_main`: a belt command's `main` called in this process, its
  environment, directory and standard input the row's for the call,
  both streams read, a `SystemExit` its status;
- `as_task`: the task a helper runs under, named for the finding
  writer, with nothing recorded.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.support import Called

# The fixture tests run pytest on planted trees (tests/test_conftest.py,
# tests/test_pytest_config.py).
pytest_plugins = ("pytester",)


ENVIRON: pytest.StashKey[dict[str, str]] = pytest.StashKey()
DIRECTORY: pytest.StashKey[str] = pytest.StashKey()


def _setup(item: pytest.Item) -> None:
    """Hold the directory and clear the environment before a test.

    Runs first, so every fixture the test takes is set up in the
    environment it is given: the variables INHERITED names, HOME and a
    git identity.
    """
    item.stash[DIRECTORY] = os.getcwd()
    item.stash[ENVIRON] = dict(os.environ)
    wanted = support.env()
    os.environ.clear()
    os.environ.update(wanted)


def _teardown(item: pytest.Item) -> None:
    """Restore the environment, and fail a test that moved the directory.

    Runs last, after the test's fixtures are torn down, so a
    `monkeypatch.chdir` has been undone before the directory is read.

    Raises:
        pytest.fail.Exception: the directory moved, named with the test.

    """
    os.environ.clear()
    os.environ.update(item.stash[ENVIRON])
    start = item.stash[DIRECTORY]
    try:
        now = os.getcwd()
    except OSError:
        now = "a directory that no longer exists"
    if now != start:
        os.chdir(start)
        pytest.fail(
            f"{item.nodeid} left the working directory at {now}"
            f" (the suite runs from {start}); take monkeypatch.chdir",
            pytrace=False,
        )


# Called with the function, the marker returns it typed; as a
# decorator mypy reads it as Any.
pytest_runtest_setup = pytest.hookimpl(_setup, tryfirst=True)
pytest_runtest_teardown = pytest.hookimpl(_teardown, trylast=True)


@pytest.fixture
def env() -> Callable[..., dict[str, str]]:
    """Give a row the environment a process it starts is given.

    Returns:
        `support.env`.

    """
    return support.env


@pytest.fixture
def call_main() -> Callable[..., Called]:
    """Call a command's `main` in this process, as a script row ran it.

    `support.call`: `run` makes the call, written out in the test
    (`lambda: helper.main([...])`); `env`, `cwd`, `stdin` and `command`
    are the child's it stands for, each restored afterwards; a
    `SystemExit` (argparse's usage error) is the status.

    Returns:
        The caller.

    """
    return support.call


@pytest.fixture
def as_task(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Name the task a helper runs under, recording nothing (#1934).

    The finding writer reads MISE_TASK_NAME and ORG_FINDINGS. Unnamed,
    a finding names the running program; an empty ORG_FINDINGS records
    nothing.

    Returns:
        The namer: `as_task("lint:belt-shell")`.

    """

    def name(task: str) -> None:
        monkeypatch.setenv("MISE_TASK_NAME", task)
        monkeypatch.setenv("ORG_FINDINGS", "")

    return name
