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
- the tree the belt package sits in is added last, so `belt` resolves
  for the modules it delivers (`belt.typed_hypothesis`), unless it is
  one of those roots already.

The repository's top directory is mypy's base already and is not
repeated, nor is the belt's tree when it is that directory. Two
tracked files that would be one module under these roots are refused
by name: mypy would stop on them ("Duplicate module"), and
an import of that name would reach whichever root came first. A `.pyi`
beside its own `.py` is that module's stub, not a second module.

    belt python-roots <belt-root> <file>...

Prints the roots joined by `:`, or names the clashes and exits 1.
"""

import argparse
import os
import sys
from pathlib import PurePosixPath

from belt import findings

# The belt and at least one file.


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
    # The belt's tree is mypy's base already in the canon, whose top is
    # the working directory, or one of the roots.
    known = {os.path.realpath(r) for r in [".", *found]}
    if os.path.realpath(belt) not in known:
        found.append(belt)
    return found, clashes


class Args(argparse.Namespace):
    """The command's arguments, typed as parsed."""

    belt_root: str
    files: list[str]


def main(argv: list[str]) -> int:
    """Print the roots, or name the clashes.

    Returns:
        0 with the roots printed, 1 on a clash, 2 on bad usage.

    """
    parser = argparse.ArgumentParser(
        prog="belt python-roots", description="Print the roots, or name the clashes"
    )
    parser.add_argument("belt_root", metavar="BELT_ROOT")
    parser.add_argument("files", nargs="+", metavar="FILE")
    args = parser.parse_args(argv, namespace=Args())
    found, clashes = roots(args.files, args.belt_root)
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
