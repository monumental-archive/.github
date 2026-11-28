"""The launcher's dispatch (#2177): a command's name to its module's `main`.

`belt/__main__.py` sets the path and calls `launch`; everything that
decides is here, so a test calls it in process.

`<command>` is a module's name with dashes for underscores
(`subject-budget` is `belt.subject_budget`), looked up in the belt's
packages in the order PACKAGES names them; a name is one module's in
the whole package (tests/test_launcher.py holds them to that). The
module's `main(argv)` is called with the arguments after the command,
and its status is the process's. `sys.argv[0]` becomes the command, so
a helper that names itself (the finding writer's fallback task name)
names what was typed. An unknown command, or none, exits 2 with the
usage line, by argparse.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import sys
from typing import Protocol, runtime_checkable

PACKAGES = ("belt", "belt.release", "belt.release.pgrx", "belt.rekor_monitor")


@runtime_checkable
class Command(Protocol):
    """A command module: what the launcher calls."""

    def main(self, argv: list[str]) -> int:
        """Run the command."""
        ...


class Args(argparse.Namespace):
    """The launcher's one argument, typed as parsed."""

    command: str


def launch(argv: list[str]) -> int:
    """Run one command's `main` with the rest of the arguments.

    Only the command is the launcher's to parse: everything after it is
    handed to the command's own parser as typed, `--` included.

    Returns:
        The command's status; argparse's 2 when there is no such command.

    """
    parser = argparse.ArgumentParser(
        prog="belt", description="Run one of the belt's commands"
    )
    parser.add_argument("command", help="a module's name, dashes for underscores")
    command, rest = parser.parse_args(argv[:1], namespace=Args()).command, argv[1:]
    module = found(command)
    if module is None:
        parser.error(f"unknown command {command!r}")
    sys.argv = [command, *rest]
    return module.main(rest)


def found(command: str) -> Command | None:
    """Find the module a command names, in the belt's packages in order.

    Returns:
        The module, or None when no package holds one by that name.

    """
    name = command.replace("-", "_")
    if "_" in command or not name.isidentifier():
        return None
    for package in PACKAGES:
        qualified = f"{package}.{name}"
        if importlib.util.find_spec(qualified) is None:
            continue
        module: object = importlib.import_module(qualified)
        return module if isinstance(module, Command) else None
    return None
