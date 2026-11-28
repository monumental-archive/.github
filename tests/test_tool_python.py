"""Table tests for tool_python.py: the tool venv sits on the belt's Python (#1788).

Each row plants a tool venv (an entry point and the `python` beside
it) and a PATH holding a link to that entry point and, unless the row
removes it, a `python3`, then calls `main` in this process. A refusal
row asserts the whole line, so the wording the four sites share is
pinned here once.
"""

import os
from typing import TYPE_CHECKING

import pytest

from belt import tool_python
from tests import support

if TYPE_CHECKING:
    from pathlib import Path

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


@pytest.mark.usefixtures("_set_up_test_tool_python")
class TestToolPython:
    """One row per branch of the check."""

    @pytest.fixture
    def _set_up_test_tool_python(self, tmp_path: Path) -> None:
        """Plant a venv with its entry point, and a PATH linking to it."""
        self.tmp = tmp_path
        self.venv = self.tmp / "venv" / "bin"
        self.venv.mkdir(parents=True)
        self.entry = self.venv / "coverage"
        self.write(self.entry, GONE)
        self.bins = self.tmp / "bin"
        self.bins.mkdir()
        (self.bins / "coverage").symlink_to(self.entry)
        (self.bins / "python3").symlink_to(PYTHON3)
        self.suite = tool_python.ask(PYTHON3, tool_python.VERSION)
        assert self.suite

    @staticmethod
    def write(path: Path, text: str) -> None:
        """Write an executable file."""
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def call(
        self, *argv: str, capfd: pytest.CaptureFixture[str], **env: str
    ) -> tuple[int, str, str]:
        """Call `main` with the planted PATH.

        Returns:
            Its status and streams.

        """
        # The caller's label is its task's own name, as the properties
        # scripts pass it: the refusal reads `<label>: …` (#1934).
        environ = support.env(PATH=str(self.bins), MISE_TASK_NAME=LABEL, **env)
        with pytest.MonkeyPatch.context() as patched:
            for name, value in environ.items():
                patched.setenv(name, value)
            status = tool_python.main([LABEL, "coverage", *argv])
        out, err = capfd.readouterr()
        return status, out, err

    def test_no_venv_python_is_no_interpreter(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The venv's `python` does not exist: refused, "no interpreter"."""
        status, out, err = self.call(capfd=capfd)
        assert (status, out, err) == (
            1,
            "",
            refusal("no interpreter", self.suite),
        )

    def test_a_gone_shebang_interpreter_is_no_interpreter(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The venv's `python` names a missing interpreter: refused, no traceback."""
        self.write(self.venv / "python", GONE)
        status, out, err = self.call("--site", "coverage", capfd=capfd)
        assert (status, out, err) == (
            1,
            "",
            refusal("no interpreter", self.suite),
        )

    def test_no_python3_is_missing(self, capfd: pytest.CaptureFixture[str]) -> None:
        """No `python3` on PATH: refused, the suite's side "missing"."""
        (self.venv / "python").symlink_to(PYTHON3)
        (self.bins / "python3").unlink()
        status, out, err = self.call(capfd=capfd)
        assert (status, out, err) == (
            1,
            "",
            refusal(self.suite, "missing"),
        )

    def test_another_version_names_both(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The venv answers another version: refused, both named."""
        self.write(self.venv / "python", OTHER)
        status, out, err = self.call(capfd=capfd)
        assert (status, out, err) == (
            1,
            "",
            refusal("3.0.0 (stub interpreter)", self.suite),
        )

    def test_a_module_the_venv_cannot_import_is_no_interpreter(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """`--site` for a module the venv lacks: refused, as the import fails."""
        (self.venv / "python").symlink_to(PYTHON3)
        status, out, err = self.call("--site", "no_such_module_1788", capfd=capfd)
        assert (status, out, err) == (
            1,
            "",
            refusal("no interpreter", self.suite),
        )

    def test_the_command_absent_is_missing(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """No entry point on PATH: the "missing" line, with the remedy."""
        (self.bins / "coverage").unlink()
        status, out, err = self.call(capfd=capfd)
        assert (status, out, err) == (
            1,
            "",
            (
                f"{LABEL}: coverage missing — the belt pins pipx:coverage;"
                " run: mise install\n"
            ),
        )

    def test_matching_versions_print_the_interpreter(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The venv runs on the suite's python3: its interpreter printed."""
        (self.venv / "python").symlink_to(PYTHON3)
        status, out, err = self.call(capfd=capfd)
        assert (status, out, err) == (
            0,
            f"{self.venv / 'python'}\n",
            "",
        )

    def test_matching_versions_with_site_print_the_package_directory(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """With `--site`: the directory the module's package is imported from."""
        (self.venv / "python").symlink_to(PYTHON3)
        site = self.tmp / "site"
        (site / "probe_pkg").mkdir(parents=True)
        (site / "probe_pkg" / "__init__.py").write_text("", encoding="utf-8")
        status, out, err = self.call(
            "--site", "probe_pkg", PYTHONPATH=str(site), capfd=capfd
        )
        assert (status, out, err) == (0, f"{site}\n", "")
