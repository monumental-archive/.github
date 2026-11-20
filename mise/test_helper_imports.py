#!/usr/bin/env python3
"""The law: no tracked Python file loads another by path (#1634).

A helper loaded through `importlib` or `runpy` arrives as an untyped
module, so neither its caller nor mypy can check one use of it: 2,439
of mypy's findings on #1604 were that one pattern. Every helper is a
module now, so a file in its own directory imports it by name, and a
file in another root names that root from its own `__file__` with a
`sys.path` entry, then imports it the same way.

The row reads every tracked `.py` and refuses, by file and line, a call
that loads a file by path: `spec_from_file_location`, `run_path`,
`run_module` or a `SourceFileLoader`. Calls are read from the syntax
tree, so a fixture's text naming one is not a load.

One file is excepted, with why. Nothing else is.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import hermetic

HERE = Path(__file__).resolve().parent
CANON = HERE.parent

LOADERS = frozenset({
    "spec_from_file_location",
    "run_path",
    "run_module",
    "SourceFileLoader",
})

EXCEPTED = {
    # The properties runner loads a repository's own prop_*.py files by
    # the paths it discovered, each under a name of its own so two files
    # with one stem in two directories stay apart. It loads tests, never
    # a helper, and its report names them by that module name.
    "mise/python_properties.py": "loads the discovered property files",
}


def loads(text: str) -> list[int]:
    """Find the lines that load a file by path.

    Returns:
        The line of each such call, in order.

    """
    found: list[int] = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else None
        if isinstance(func, ast.Name):
            name = func.id
        if name in LOADERS:
            found.append(node.lineno)
    return sorted(found)


def offenders(root: Path) -> list[str]:
    """List every load by path in the tracked Python under root.

    Returns:
        `path:line` for each load outside the excepted file.

    """
    listed = hermetic.tracked(root, "*.py")
    out: list[str] = []
    for path in listed:
        if path in EXCEPTED:
            continue
        text = (root / path).read_text(encoding="utf-8")
        out.extend(f"{path}:{line}" for line in loads(text))
    return out


class TheLaw(unittest.TestCase):
    """No tracked Python file loads another by path."""

    def test_no_tracked_file_loads_another_by_path(self) -> None:
        """Every helper is imported by name, in tests and helpers alike."""
        none: list[str] = []
        self.assertEqual(none, offenders(CANON))

    def test_the_excepted_file_still_loads(self) -> None:
        """An exception that no longer applies is removed, not kept."""
        root = CANON
        for path in EXCEPTED:
            text = (root / path).read_text(encoding="utf-8")
            self.assertTrue(loads(text), f"{path} no longer loads by path")

    def test_each_loader_is_found(self) -> None:
        """Every spelling of a load by path is read as one."""
        text = (
            "import importlib.util, runpy\n"
            "from importlib.machinery import SourceFileLoader\n"
            "a = importlib.util.spec_from_file_location('a', 'a.py')\n"
            "b = runpy.run_path('b.py')\n"
            "c = runpy.run_module('c')\n"
            "d = SourceFileLoader('d', 'd.py')\n"
        )
        lines: list[int] = [3, 4, 5, 6]
        self.assertEqual(lines, loads(text))

    def test_a_name_in_a_string_is_not_a_load(self) -> None:
        """A fixture naming a loader in its text loads nothing."""
        none: list[int] = []
        self.assertEqual(none, loads("X = 'runpy.run_path(\"a.py\")'\nimport b\n"))


def sibling_line() -> str:
    """Read the sibling-root line a canon test file carries.

    Returns:
        The `sys.path` statement from release/test_check_go_stamps.py.

    """
    text = (CANON / "release" / "test_check_go_stamps.py").read_text("utf-8")
    return next(ln for ln in text.splitlines() if ln.startswith("sys.path."))


def run_user(*, own: bool) -> str:
    """Plant release/user.py using the line, and run it.

    `mise/shared.py` always exists; with `own`, so does
    `release/shared.py`.

    Returns:
        What user.py printed: which shared it imported.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in ("mise", "release"):
            (root / name).mkdir()
        (root / "mise" / "shared.py").write_text("WHERE = 'sibling'\n", "utf-8")
        if own:
            (root / "release" / "shared.py").write_text("WHERE = 'own'\n", "utf-8")
        (root / "release" / "user.py").write_text(
            "import sys\n"
            "from pathlib import Path\n"
            f"{sibling_line()}\n"
            "import shared\n"
            "print(shared.WHERE)\n",
            "utf-8",
        )
        done = subprocess.run(
            [sys.executable, str(root / "release" / "user.py")],
            capture_output=True,
            text=True,
            check=True,
            env=hermetic.env(),
        )
        return done.stdout


class TheSiblingRoot(unittest.TestCase):
    """The canon's sibling-root line: appended, never ahead of home."""

    def test_the_files_own_directory_wins(self) -> None:
        """A neighbour of the same name is found before the sibling's."""
        self.assertEqual("own\n", run_user(own=True))

    def test_the_sibling_root_is_reached(self) -> None:
        """With no neighbour of that name, the sibling root's is imported."""
        self.assertEqual("sibling\n", run_user(own=False))


if __name__ == "__main__":
    unittest.main()
