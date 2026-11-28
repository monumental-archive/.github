"""The launcher: one way to run a belt command (#2177).

`launch` is called in process for its dispatch. A few rows run the
launcher as a process, the one test of belt Python that does: only a
real process shows that `-I` keeps a planted `belt` out, and that a
command's status reaches the shell.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import launcher
from tests import support

if TYPE_CHECKING:
    from pathlib import Path

PYTHON = os.environ.get("ORG_PYTHON") or sys.executable
EXIT_USAGE = 2
USAGE = "usage: belt [-h] command\n"
ROOTS_USAGE = "usage: belt python-roots [-h] BELT_ROOT FILE [FILE ...]\n"
ROOTS_REFUSED = (
    ROOTS_USAGE + "belt python-roots: error: the following arguments are required:"
    " BELT_ROOT, FILE\n"
)


def run(
    *argv: str, make_env: support.Env, **env: str
) -> subprocess.CompletedProcess[str]:
    """Run the launcher as the call sites do.

    Returns:
        The finished process.

    """
    return subprocess.run(
        [PYTHON, "-I", str(support.PACKAGE), *argv],
        env=make_env(**env),
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_planted_belt_on_pythonpath_never_answers(
    tmp_path: Path, env: support.Env
) -> None:
    """-I: a `belt` package on PYTHONPATH is not the one that runs."""
    planted = tmp_path / "belt"
    planted.mkdir()
    (planted / "__init__.py").write_text("", encoding="utf-8")
    (planted / "subject_budget.py").write_text(
        "def main(argv):\n    print('planted')\n    return 0\n", encoding="utf-8"
    )
    done = run(
        "subject-budget", "--no-such-flag", PYTHONPATH=str(tmp_path), make_env=env
    )
    assert "planted" not in done.stdout
    assert done.stderr == (
        "usage: belt subject-budget [-h]\n"
        "belt subject-budget: error: unrecognized arguments: --no-such-flag\n"
    )
    assert done.returncode == EXIT_USAGE


def test_an_unknown_command_exits_2_with_its_message(env: support.Env) -> None:
    """A name no module has: 2, the usage line."""
    done = run("no-such-command", make_env=env)
    assert done.returncode == EXIT_USAGE
    assert done.stderr == (f"{USAGE}belt: error: unknown command 'no-such-command'\n")
    assert not done.stdout


def test_a_commands_status_reaches_the_shell(env: support.Env) -> None:
    """`python-roots` with no file is its own usage error, 2, through the shell."""
    done = subprocess.run(
        [
            support.tool("bash"),
            "-c",
            f'"{PYTHON}" -I "{support.PACKAGE}" python-roots; echo "status $?"',
        ],
        env=env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.stdout == "status 2\n"
    assert done.stderr == ROOTS_REFUSED


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        pytest.param(
            [],
            f"{USAGE}belt: error: the following arguments are required: command\n",
            id="no command",
        ),
        pytest.param(
            ["--no-such-flag"],
            f"{USAGE}belt: error: the following arguments are required: command\n",
            id="a flag",
        ),
        pytest.param(
            ["subject_budget"],
            f"{USAGE}belt: error: unknown command 'subject_budget'\n",
            id="underscores",
        ),
        pytest.param(
            ["not-a.name"],
            f"{USAGE}belt: error: unknown command 'not-a.name'\n",
            id="not an identifier",
        ),
        pytest.param(
            ["tree"],
            f"{USAGE}belt: error: unknown command 'tree'\n",
            id="a module with no main",
        ),
    ],
)
def test_a_call_that_names_no_command_is_a_usage_error(
    argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each way of naming no command: 2 and the usage line, nothing run."""
    with pytest.raises(SystemExit) as ended:
        launcher.launch(argv)
    assert ended.value.code == EXIT_USAGE
    written = capsys.readouterr()
    assert written.err == message
    assert not written.out


def test_a_command_gets_its_arguments_and_its_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The rest of argv is the command's; argv[0] becomes the command."""
    monkeypatch.setattr(sys, "argv", ["belt"])
    with pytest.raises(SystemExit) as ended:
        launcher.launch(["python-roots"])
    assert ended.value.code == EXIT_USAGE
    assert sys.argv == ["python-roots"]
    assert capsys.readouterr().err == ROOTS_REFUSED


def test_a_release_command_is_found_in_its_package(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The packages are searched in order: belt.release's matrix answers."""
    with pytest.raises(SystemExit) as ended:
        launcher.launch(["matrix"])
    assert ended.value.code == EXIT_USAGE
    assert capsys.readouterr().err == (
        "usage: belt matrix [-h] {targets,pgrx-extension,pgrx-images,base-images}"
        " ...\nbelt matrix: error: the following arguments are required: mode\n"
    )


def test_every_command_name_is_one_modules() -> None:
    """No module name is shared by two packages: a name is one command."""
    owners: dict[str, list[str]] = {}
    for package in launcher.PACKAGES:
        spec = importlib.util.find_spec(package)
        assert spec is not None
        assert spec.submodule_search_locations is not None
        for where in spec.submodule_search_locations:
            for name in sorted(os.listdir(where)):
                if name.endswith(".py") and not name.startswith("__"):
                    owners.setdefault(name[:-3], []).append(package)
    shared = {name: found for name, found in owners.items() if len(found) > 1}
    assert shared == {}
