"""The law: no tracked Python file loads another by path (#1634).

A helper loaded through `importlib` or `runpy` arrives as an untyped
module, so neither its caller nor mypy can check one use of it: 2,439
of mypy's findings on #1604 were that one pattern. Every helper is a
module of the belt package now (#2177), imported by name.

The row reads every tracked `.py` and refuses, by file and line, a call
that loads a file by path: `spec_from_file_location`, `run_path`,
`run_module` or a `SourceFileLoader`. Calls are read from the syntax
tree, so a fixture's text naming one is not a load. Nothing is
excepted.
"""

import ast
from pathlib import Path

from tests import support

HERE = support.MISE
CANON = HERE.parent

LOADERS = frozenset({
    "spec_from_file_location",
    "run_path",
    "run_module",
    "SourceFileLoader",
})


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
        `path:line` for each load.

    """
    listed = support.tracked(root, "*.py")
    out: list[str] = []
    for path in listed:
        text = (root / path).read_text(encoding="utf-8")
        out.extend(f"{path}:{line}" for line in loads(text))
    return out


class TestTheLaw:
    """No tracked Python file loads another by path."""

    def test_no_tracked_file_loads_another_by_path(self) -> None:
        """Every helper is imported by name, in tests and helpers alike."""
        none: list[str] = []
        assert none == offenders(CANON)

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
        assert lines == loads(text)

    def test_a_name_in_a_string_is_not_a_load(self) -> None:
        """A fixture naming a loader in its text loads nothing."""
        none: list[int] = []
        assert none == loads("X = 'runpy.run_path(\"a.py\")'\nimport b\n")
