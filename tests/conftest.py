"""The hooks every belt test runs under, and its fixtures (#2177).

Two hooks hold every test, unasked. They are hooks, not autouse
fixtures: ruff's `pytest-fixture-autouse` reds an autouse fixture and
has no code, so a fixture would need a suppression.

- THE ENVIRONMENT (#1508, #857). Before a test runs, `os.environ` is
  cleared to `support.environment()`: the variables INHERITED names, HOME and
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

- `env`: `support.environment`, the one environment a process a test
  starts is given, for the processes that remain (git, stele, a shell
  body);
- the tree planters (#1714, #2007, #1877): `belt` and `canon` plant a
  whole belt or canon tree, every tracked file a link with only the
  row's stubs replaced; `plant` links any tracked names; `track` makes
  a planted tree a repository; `rustc` writes the stand-in compiler.
  Each is a factory with nothing of its own to set up or tear down, so
  each is session-scoped, which a Hypothesis property can take too;
- `as_task`: the task a helper runs under, named for the finding
  writer, with nothing recorded.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

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
    wanted = support.environment()
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


@fixture(scope="session")
def env() -> support.Env:
    """Give a row the environment a process it starts is given.

    Returns:
        `support.environment`.

    """
    return support.environment


@fixture(scope="session")
def plant() -> support.Plant:
    """Give a row the planter of tracked names.

    Returns:
        A planter: each name a link to the canon's file, then each stub
        written over its link with the real file's mode (a stub with no
        real file has the default mode), so a row that edits one never
        writes through to the canon. Names and stubs are paths from the
        canon's top; it returns `dest`.

    """

    def planted(dest: Path, names: list[str], stubs: Mapping[str, str]) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        for name in names:
            link = dest / name
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(support.CANON / name)
        for name, text in stubs.items():
            path = dest / name
            real = support.CANON / name
            path.unlink(missing_ok=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            if real.exists():
                path.chmod(real.stat().st_mode & 0o7777)
        return dest

    return planted


@fixture(scope="session")
def belt(plant: support.Plant) -> support.Belt:
    """Give a row the belt planter: the canon's `mise/` and `belt/` (#1714, #2177).

    Each file is a link to the real one, so a helper that gains an
    import, or a script that starts a new sibling, finds it with no
    change to the row. `stubs` (a name under `mise/`, or `belt/<path>`
    for the package, to its text) are what the row means to replace; a
    row whose point is a file MISSING removes it after planting.

        belt_dir = belt(tmp / "tree", stubs={"pgrx-env.sh": STUB})

    Returns:
        A planter returning `dest/mise`, the planted belt directory
        (ORG_BELT_DIR); `dest` is its ORG_CANON_DIR.

    """

    def planted(dest: Path | str, *, stubs: Mapping[str, str] | None = None) -> Path:
        names = support.tracked(support.CANON, "mise/", "belt/")
        named = {
            (name if name.startswith("belt/") else f"mise/{name}"): text
            for name, text in (stubs or {}).items()
        }
        return plant(Path(dest), names, named) / "mise"

    return planted


@fixture(scope="session")
def canon(plant: support.Plant) -> support.Canon:
    """Give a row the canon planter: `mise/`, `belt/`, `release/` (#1714).

    `also` are pathspecs from the canon's top (an action directory,
    `security/identity.toml`), for a row that plants `.org-canon` or a
    canon checkout. Every file is a link, and `stubs` (a path from the
    canon's top to its text) replace theirs, as `belt`'s do.

    Returns:
        A planter returning `dest`, as a Path.

    """

    def planted(
        dest: Path | str, *also: str, stubs: Mapping[str, str] | None = None
    ) -> Path:
        names = support.tracked(support.CANON, "mise/", "belt/", "release/", *also)
        return plant(Path(dest), names, stubs or {})

    return planted


@fixture(scope="session")
def track() -> support.Track:
    """Give a row the tracker: a tree made a repository of its files (#2007).

    A belt task reads the files git tracks, never a directory's walk, so
    a row that plants files for a task body to read tracks them, under
    `env`. Idempotent: a tree already a repository is added to.

    Returns:
        The tracker; git missing from PATH is a RuntimeError.

    """

    def tracking(tree: Path | str) -> None:
        git = support.tool("git")
        for args in (("init", "-q"), ("add", "-A")):
            subprocess.run(
                [git, "-C", str(tree), *args],
                env=support.environment(),
                capture_output=True,
                check=True,
            )

    return tracking


@fixture(scope="session")
def rustc() -> support.Rustc:
    """Give a row the stand-in `rustc` writer (#1877).

    Returns:
        The writer: `bin_dir/rustc`, answering `rustc -vV` as
        `support.rustc_verbose` renders it; it returns the stub's path.

    """

    def written(bin_dir: Path) -> Path:
        stub = bin_dir / "rustc"
        stub.write_text(support.RUSTC_STUB, encoding="utf-8")
        stub.chmod(0o755)
        return stub

    return written


@fixture
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
