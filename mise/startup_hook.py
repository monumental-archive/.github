#!/usr/bin/env python3
"""Write a belt start-up hook that never ends the chain (#1542).

A leg reaches the Python processes a suite starts through a
`sitecustomize.py` on PYTHONPATH: coverage's measurement starts the
tracer in each (coverage_measure.sh), and the mutation leg's plan
does the same per test file (mutants_python.py `plan`). Python imports
only the FIRST `sitecustomize` it finds, so nested legs shadowed each
other (#1542: coverage's hook, prepended inside a mutated suite, hid
the mutation leg's), and a repository's own `sitecustomize` was hidden
by either.

Every belt hook is written here, with the leg's work first and then the
chain: the next `sitecustomize` on `sys.path` after the hook's own
directory, found by the import system's own path finder over the
entries as they stood before the work ran, is run in turn. None found
is the common case and does nothing. An error in the next hook
propagates as it would had that hook been first. So the order and the
depth of nesting do not matter.

    startup_hook.py DIR < WORK     # the shell's form: work on stdin

    startup_hook.write(directory, work)

Stdlib only.
"""

import sys
from pathlib import Path

import typed_std

# The chain, appended to every hook after its work. `__file__` is the
# running hook's own file, whether Python imported it at start-up or a
# hook before it ran it here; the path is read as it stood before the
# work, so a tool's appended site directory is never searched.
CHAIN = """

def _belt_chain(entries):
    import importlib.machinery
    import importlib.util
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    seen = False
    after = []
    for entry in entries:
        if seen:
            after.append(entry)
        elif entry and os.path.abspath(entry) == here:
            seen = True
    spec = importlib.machinery.PathFinder.find_spec("sitecustomize", after)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


_belt_chain(_belt_entries)
"""
# What the hook runs before its work: the path as it stands.
SNAPSHOT = "import sys as _belt_sys\n_belt_entries = list(_belt_sys.path)\n"


def source(work: str) -> str:
    """Compose a hook from the leg's work.

    Returns:
        The hook's text: the path's snapshot, the work, then the chain.

    """
    return SNAPSHOT + work.rstrip("\n") + "\n" + CHAIN


def write(directory: Path, work: str) -> None:
    """Write `directory/sitecustomize.py` for the leg's work."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sitecustomize.py").write_text(source(work), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Write the hook into the directory named, its work read from stdin.

    Returns:
        0, or 2 when the directory is not named.

    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: startup_hook.py DIR < WORK", file=sys.stderr)
        return 2
    write(Path(args[0]), typed_std.stdin_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
