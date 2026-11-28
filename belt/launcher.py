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
usage line.
"""

import importlib
import importlib.util
import sys

PACKAGES = ("belt", "belt.release", "belt.release.pgrx", "belt.rekor_monitor")
USAGE = 'usage: "${ORG_PYTHON}" -I "${ORG_BELT_ROOT}/belt" <command> [<argument>…]'
EXIT_USAGE = 2


def launch(argv: list[str]) -> int:
    """Run one command's `main` with the rest of the arguments.

    Returns:
        The command's status, or 2 when there is no such command.

    """
    if not argv or argv[0].startswith("-"):
        print(f"belt: no command — {USAGE}", file=sys.stderr)
        return EXIT_USAGE
    command, rest = argv[0], argv[1:]
    name = command.replace("-", "_")
    if "_" in command or not name.isidentifier():
        print(f"belt: unknown command '{command}' — {USAGE}", file=sys.stderr)
        return EXIT_USAGE
    for package in PACKAGES:
        qualified = f"{package}.{name}"
        if importlib.util.find_spec(qualified) is None:
            continue
        module = importlib.import_module(qualified)
        run = getattr(module, "main", None)
        if not callable(run):
            break
        sys.argv = [command, *rest]
        status = run(rest)
        return status if isinstance(status, int) else 0
    print(f"belt: unknown command '{command}' — {USAGE}", file=sys.stderr)
    return EXIT_USAGE
