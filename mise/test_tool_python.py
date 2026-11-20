#!/usr/bin/env python3
"""Table tests for tool_python.py: the tool venv sits on the belt's Python (#1788).

Each row plants a tool venv (an entry point and the `python` beside
it) and a PATH holding a link to that entry point and, unless the row
removes it, a `python3`, then calls `main` in this process. A refusal
row asserts the whole line, so the wording the four sites share is
pinned here once.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import override

import hermetic
import tool_python

LABEL = "probe"

# The suite's interpreter, resolved: the planted PATH holds no mise.
PYTHON3 = os.path.realpath(hermetic.tool("python3"))

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


class ToolPython(unittest.TestCase):
    """One row per branch of the check."""

    @override
    def setUp(self) -> None:
        """Plant a venv with its entry point, and a PATH linking to it."""
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.venv = self.tmp / "venv" / "bin"
        self.venv.mkdir(parents=True)
        self.entry = self.venv / "coverage"
        self.write(self.entry, GONE)
        self.bins = self.tmp / "bin"
        self.bins.mkdir()
        (self.bins / "coverage").symlink_to(self.entry)
        (self.bins / "python3").symlink_to(PYTHON3)
        self.suite = tool_python.ask(PYTHON3, tool_python.VERSION)
        self.assertTrue(self.suite)

    @staticmethod
    def write(path: Path, text: str) -> None:
        """Write an executable file."""
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def call(self, *argv: str, **env: str) -> hermetic.Called:
        """Call `main` with the planted PATH.

        Returns:
            Its status and streams.

        """
        environ = hermetic.env(PATH=str(self.bins), **env)
        return hermetic.call_main(
            lambda: tool_python.main([LABEL, "coverage", *argv]), env=environ
        )

    def test_no_venv_python_is_no_interpreter(self) -> None:
        """The venv's `python` does not exist: refused, "no interpreter"."""
        done = self.call()
        self.assertEqual(
            (done.returncode, done.stdout, done.stderr),
            (1, "", refusal("no interpreter", self.suite)),
        )

    def test_a_gone_shebang_interpreter_is_no_interpreter(self) -> None:
        """The venv's `python` names a missing interpreter: refused, no traceback."""
        self.write(self.venv / "python", GONE)
        done = self.call("--site", "coverage")
        self.assertEqual(
            (done.returncode, done.stdout, done.stderr),
            (1, "", refusal("no interpreter", self.suite)),
        )

    def test_no_python3_is_missing(self) -> None:
        """No `python3` on PATH: refused, the suite's side "missing"."""
        (self.venv / "python").symlink_to(PYTHON3)
        (self.bins / "python3").unlink()
        done = self.call()
        self.assertEqual(
            (done.returncode, done.stdout, done.stderr),
            (1, "", refusal(self.suite, "missing")),
        )

    def test_another_version_names_both(self) -> None:
        """The venv answers another version: refused, both named."""
        self.write(self.venv / "python", OTHER)
        done = self.call()
        self.assertEqual(
            (done.returncode, done.stdout, done.stderr),
            (1, "", refusal("3.0.0 (stub interpreter)", self.suite)),
        )

    def test_a_module_the_venv_cannot_import_is_no_interpreter(self) -> None:
        """`--site` for a module the venv lacks: refused, as the import fails."""
        (self.venv / "python").symlink_to(PYTHON3)
        done = self.call("--site", "no_such_module_1788")
        self.assertEqual(
            (done.returncode, done.stdout, done.stderr),
            (1, "", refusal("no interpreter", self.suite)),
        )

    def test_the_command_absent_is_missing(self) -> None:
        """No entry point on PATH: the "missing" line, with the remedy."""
        (self.bins / "coverage").unlink()
        done = self.call()
        self.assertEqual(
            (done.returncode, done.stdout, done.stderr),
            (
                1,
                "",
                (
                    f"{LABEL}: coverage missing — the belt pins pipx:coverage;"
                    " run: mise install\n"
                ),
            ),
        )

    def test_matching_versions_print_the_interpreter(self) -> None:
        """The venv runs on the suite's python3: its interpreter printed."""
        (self.venv / "python").symlink_to(PYTHON3)
        done = self.call()
        self.assertEqual(
            (done.returncode, done.stdout, done.stderr),
            (0, f"{self.venv / 'python'}\n", ""),
        )

    def test_matching_versions_with_site_print_the_package_directory(self) -> None:
        """With `--site`: the directory the module's package is imported from."""
        (self.venv / "python").symlink_to(PYTHON3)
        site = self.tmp / "site"
        (site / "probe_pkg").mkdir(parents=True)
        (site / "probe_pkg" / "__init__.py").write_text("", encoding="utf-8")
        done = self.call("--site", "probe_pkg", PYTHONPATH=str(site))
        self.assertEqual(
            (done.returncode, done.stdout, done.stderr), (0, f"{site}\n", "")
        )


if __name__ == "__main__":
    unittest.main()
