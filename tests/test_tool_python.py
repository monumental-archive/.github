"""Table tests for tool_python.py: the tool venv sits on the belt's Python (#1788).

Each row plants a tool venv (an entry point and the `python` beside
it) and a PATH holding a link to that entry point and, unless the row
removes it, a `python3`, then calls `main` in this process. A refusal
row asserts the whole line, so the wording the four sites share is
pinned here once.
"""

import os
from typing import TYPE_CHECKING

from belt import tool_python
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

LABEL = "probe"

# The suite's interpreter, resolved: the planted PATH holds no mise.
PYTHON3 = os.path.realpath(support.tool("python3"))

# A start whose interpreter is gone: execve fails before any code runs.
GONE = "#!/nowhere/python\n"

# A venv built on another Python: its interpreter answers another version.
OTHER = '#!/bin/sh\necho "3.0.0 (stub interpreter)"\n'


def refusal(have: str, want: str) -> str:
    """Write the one refusal for `coverage`.

    Returns:
        The line, as stderr carries it.

    """
    return (
        f"{LABEL}: coverage runs on '{have}' but the suite's python3 is"
        f" '{want}' — the tool venv must be built on the belt's python pin"
        " (reinstall: mise install -f pipx:coverage)\n"
    )


class ToolPython:
    """One row per branch of the check."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant a venv with its entry point, and a PATH linking to it."""
        self.make_env = env
        self.tmp = tmp_path
        self.venv = self.tmp / "venv" / "bin"
        self.venv.mkdir(parents=True)
        self.entry = self.venv / "coverage"
        write(self.entry, GONE)
        self.bins = self.tmp / "bin"
        self.bins.mkdir()
        (self.bins / "coverage").symlink_to(self.entry)
        (self.bins / "python3").symlink_to(PYTHON3)
        self.suite = tool_python.ask(PYTHON3, tool_python.VERSION)
        assert self.suite

    def enter(self, monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
        """Set the planted PATH, and the caller's label as its task's name.

        The properties scripts pass their task's own name as the label, so
        a refusal reads `<label>: …` (#1934).
        """
        environ = self.make_env(PATH=str(self.bins), MISE_TASK_NAME=LABEL, **env)
        for name, value in environ.items():
            monkeypatch.setenv(name, value)


@fixture
def tool_python_state(tmp_path: Path, env: support.Env) -> ToolPython:
    """Give each row its own ToolPython.

    Returns:
        The row's ToolPython.

    """
    return ToolPython(tmp_path, env=env)


def write(path: Path, text: str) -> None:
    """Write an executable file."""
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def test_no_venv_python_is_no_interpreter(
    tool_python_state: ToolPython,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The venv's `python` does not exist: refused, "no interpreter"."""
    tool_python_state.enter(monkeypatch)
    status = tool_python.main([LABEL, "coverage"])
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        refusal("no interpreter", tool_python_state.suite),
    )


def test_a_gone_shebang_interpreter_is_no_interpreter(
    tool_python_state: ToolPython,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The venv's `python` names a missing interpreter: refused, no traceback."""
    write(tool_python_state.venv / "python", GONE)
    tool_python_state.enter(monkeypatch)
    status = tool_python.main([LABEL, "coverage", "--site", "coverage"])
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        refusal("no interpreter", tool_python_state.suite),
    )


def test_no_python3_is_missing(
    tool_python_state: ToolPython,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `python3` on PATH: refused, the suite's side "missing"."""
    (tool_python_state.venv / "python").symlink_to(PYTHON3)
    (tool_python_state.bins / "python3").unlink()
    tool_python_state.enter(monkeypatch)
    status = tool_python.main([LABEL, "coverage"])
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        refusal(tool_python_state.suite, "missing"),
    )


def test_another_version_names_both(
    tool_python_state: ToolPython,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The venv answers another version: refused, both named."""
    write(tool_python_state.venv / "python", OTHER)
    tool_python_state.enter(monkeypatch)
    status = tool_python.main([LABEL, "coverage"])
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        refusal("3.0.0 (stub interpreter)", tool_python_state.suite),
    )


def test_a_module_the_venv_cannot_import_is_no_interpreter(
    tool_python_state: ToolPython,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--site` for a module the venv lacks: refused, as the import fails."""
    (tool_python_state.venv / "python").symlink_to(PYTHON3)
    tool_python_state.enter(monkeypatch)
    status = tool_python.main([LABEL, "coverage", "--site", "no_such_module_1788"])
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        refusal("no interpreter", tool_python_state.suite),
    )


def test_the_command_absent_is_missing(
    tool_python_state: ToolPython,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No entry point on PATH: the "missing" line, with the remedy."""
    (tool_python_state.bins / "coverage").unlink()
    tool_python_state.enter(monkeypatch)
    status = tool_python.main([LABEL, "coverage"])
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        (
            f"{LABEL}: coverage missing — the belt pins pipx:coverage;"
            " run: mise install\n"
        ),
    )


def test_matching_versions_print_the_interpreter(
    tool_python_state: ToolPython,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The venv runs on the suite's python3: its interpreter printed."""
    (tool_python_state.venv / "python").symlink_to(PYTHON3)
    tool_python_state.enter(monkeypatch)
    status = tool_python.main([LABEL, "coverage"])
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        f"{tool_python_state.venv / 'python'}\n",
        "",
    )


def test_matching_versions_with_site_print_the_package_directory(
    tool_python_state: ToolPython,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With `--site`: the directory the module's package is imported from."""
    (tool_python_state.venv / "python").symlink_to(PYTHON3)
    site = tool_python_state.tmp / "site"
    (site / "probe_pkg").mkdir(parents=True)
    (site / "probe_pkg" / "__init__.py").write_text("", encoding="utf-8")
    tool_python_state.enter(monkeypatch, PYTHONPATH=str(site))
    status = tool_python.main([LABEL, "coverage", "--site", "probe_pkg"])
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, f"{site}\n", "")
