"""The fixtures every belt test runs under (#2177).

Two hold every test, unasked:

- THE ENVIRONMENT (#1508, #857). Before a test runs, `os.environ` is
  cleared to `hermetic.env()`: the variables INHERITED names, HOME and
  a git identity. In-process code sees nothing from the job the suite
  runs in (a release job's `VERSION`, a hook's `GIT_DIR`, the gate's
  `MISE_TASK_NAME` and `ORG_FINDINGS`), and a process the test starts
  inherits the same set. Restored afterwards.
- THE WORKING DIRECTORY (#1491). A test that changes directory and
  does not change back fails, by name, at its teardown, and the suite's
  directory is restored for the next test. A test that needs another
  directory takes `monkeypatch.chdir`, which restores it.

And these are asked for by name:

- `env`: `hermetic.env`, the one environment a process a test starts
  is given, for the processes that remain (git, stele, a shell body);
- `call_main`: a belt command's `main` called in this process, its
  environment, directory and standard input the row's for the call,
  both streams read, a `SystemExit` its status;
- `as_task`: the task a helper runs under, named for the finding
  writer, with nothing recorded.
"""

from __future__ import annotations

import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests import hermetic

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

# The fixture tests run pytest on planted trees (tests/test_conftest.py,
# tests/test_pytest_config.py).
pytest_plugins = ("pytester",)


# The directory guard is set up first, so it is torn down last: after
# `monkeypatch`, which the environment fixture takes, has undone a
# `monkeypatch.chdir`. pytest orders a plugin's autouse fixtures by
# name, so the guard's name sorts before the environment's.
@pytest.fixture(autouse=True)
def _directory_held(request: pytest.FixtureRequest) -> Generator[None]:
    """Fail a test that leaves the working directory elsewhere.

    Raises:
        pytest.fail.Exception: the directory moved, named with the test.

    """
    start = os.getcwd()
    yield
    try:
        now = os.getcwd()
    except OSError:
        now = "a directory that no longer exists"
    if now != start:
        os.chdir(start)
        pytest.fail(
            f"{request.node.nodeid} left the working directory at {now}"
            f" (the suite runs from {start}); take monkeypatch.chdir",
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def _hermetic_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the process environment to the one a fixture is given."""
    wanted = hermetic.env()
    for name in list(os.environ):
        if name not in wanted:
            monkeypatch.delenv(name)
    for name, value in wanted.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def env() -> Callable[..., dict[str, str]]:
    """Give a row the environment a process it starts is given.

    Returns:
        `hermetic.env`.

    """
    return hermetic.env


@dataclass(frozen=True)
class Called:
    """What an in-process call to a command's `main` returned and wrote."""

    returncode: int
    stdout: str
    stderr: str


@pytest.fixture
def call_main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> Callable[..., Called]:
    """Call a command's `main` in this process, as a script row ran it.

    `run` makes the call, written out in the test (`lambda:
    helper.main([...])`). `env`, when given, IS the process environment
    for the call; `cwd` the working directory; `stdin` what it reads.
    Each is restored afterwards. A `SystemExit` (argparse's usage error)
    is the status, as it would be for a child.

    Returns:
        The caller.

    """

    def call(
        run: Callable[[], int],
        *,
        env: dict[str, str] | None = None,
        cwd: Path | str | None = None,
        stdin: str = "",
    ) -> Called:
        capsys.readouterr()
        with monkeypatch.context() as patch:
            if env is not None:
                for name in list(os.environ):
                    patch.delenv(name)
                for name, value in env.items():
                    patch.setenv(name, value)
            if cwd is not None:
                patch.chdir(cwd)
            patch.setattr(sys, "stdin", io.StringIO(stdin))
            try:
                status = run()
            except SystemExit as exc:
                code = exc.code
                status = code if isinstance(code, int) else (0 if code is None else 1)
                if isinstance(code, str):
                    print(code, file=sys.stderr)
            written = capsys.readouterr()
        return Called(status, written.out, written.err)

    return call


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
