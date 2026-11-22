#!/usr/bin/env python3
"""Derive the import roots mypy reads a repository's Python from (#1634).

A file imports its neighbours by name: Python puts a script's own
directory on its path, and a file reaching a sibling root names that
root from its own `__file__`. mypy follows neither, so python-types.sh
hands it the same roots as MYPYPATH, derived here from the tracked
files and never listed:

- every directory holding a tracked `.py` or `.pyi` that is not inside
  a package is a root;
- a package (a directory with a tracked `__init__.py`) contributes the
  parent of its topmost package directory;
- the belt's own directory is added last, for the modules it delivers,
  unless it is one of those roots already.

The repository's top directory is mypy's base already and is not
repeated. Two tracked files that would be one module under these roots
are refused by name: mypy would stop on them ("Duplicate module"), and
an import of that name would reach whichever root came first. A `.pyi`
beside its own `.py` is that module's stub, not a second module.

    python_roots.py <belt-dir> <file>...

Prints the roots joined by `:`, or names the clashes and exits 1.
"""

import os
import sys
from pathlib import PurePosixPath

import findings

USAGE = "usage: python_roots.py <belt-dir> <file>..."
# The belt and at least one file.
MIN_ARGS = 2


def root_of(path: PurePosixPath, tracked: frozenset[str]) -> PurePosixPath:
    """Find the root a file is imported from.

    Returns:
        Its own directory, or above its topmost package directory.

    """
    root = path.parent
    while str(root) != "." and str(root / "__init__.py") in tracked:
        root = root.parent
    return root


def module_of(path: PurePosixPath, root: PurePosixPath) -> str:
    """Name the module a file is under its root.

    Returns:
        The dotted name; a package's `__init__` is the package.

    """
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__" and len(parts) > 1:
        parts.pop()
    return ".".join(parts)


def roots(files: list[str], belt: str) -> tuple[list[str], list[str]]:
    """Derive the roots and any clashes.

    Returns:
        The roots in first-seen order with the belt last, and one line
        per module name two files would share.

    """
    tracked = frozenset(files)
    found: list[str] = []
    owners: dict[str, dict[str, None]] = {}
    for name in files:
        path = PurePosixPath(name)
        root = root_of(path, tracked)
        if str(root) != "." and str(root) not in found:
            found.append(str(root))
        stem = str(path.with_suffix(""))
        owners.setdefault(module_of(path, root), {})[stem] = None
    clashes = [
        f"{module}: {', '.join(stems)}"
        for module, stems in sorted(owners.items())
        if len(stems) > 1
    ]
    # The belt is often one of the roots already (the canon's mise/).
    if os.path.realpath(belt) not in {os.path.realpath(r) for r in found}:
        found.append(belt)
    return found, clashes


def main(argv: list[str]) -> int:
    """Print the roots, or name the clashes.

    Returns:
        0 with the roots printed, 1 on a clash, 2 on bad usage.

    """
    if len(argv) < MIN_ARGS:
        findings.finding("", USAGE)
        return 2
    found, clashes = roots(argv[1:], argv[0])
    if clashes:
        findings.finding(
            "",
            "these tracked files would be one module,"
            " so an import of that name reaches only one of them:",
            clashes,
        )
        return 1
    sys.stdout.write(":".join(found) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
