#!/usr/bin/env python3
"""Name what a call calls, however its module was imported (#1542, #1499).

A law that reads calls from a syntax tree must see `subprocess.run`
whether the file wrote `import subprocess`, `import subprocess as sp`
or `from subprocess import run as go`. `qualifier(tree)` reads the
file's imports once and returns a function that spells a call's target
as its module's own dotted name:

    qualify = call_names.qualifier(tree)
    qualify(call.func)    # "subprocess.run" for sp.run(...) and go(...)

A name the file never imported is spelled as written, so a bare `run`
stays `run` and is no one's `subprocess.run`. Read by the hermetic law
(mise/test_hermetic.py) and the job-installs law
(release/test_job_installs.py), so the two cannot disagree about what a
call is.

Stdlib only.
"""

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def aliases(tree: ast.AST) -> dict[str, str]:
    """Map each name the file's imports bind to the dotted name it means.

    Returns:
        `sp` to `subprocess` for `import subprocess as sp`, `go` to
        `subprocess.run` for `from subprocess import run as go`, and
        `os` to `os` for `import os.path`.

    """
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bound[alias.asname] = alias.name
                else:
                    top = alias.name.partition(".")[0]
                    bound[top] = top
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                bound[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return bound


def qualifier(tree: ast.AST) -> Callable[[ast.expr], str | None]:
    """Read a file's imports once, for spelling its calls.

    Returns:
        A function from a call's target to its dotted name, or None
        when the target is not a name or a chain of attributes on one.

    """
    bound = aliases(tree)

    def qualify(func: ast.expr) -> str | None:
        attrs: list[str] = []
        while isinstance(func, ast.Attribute):
            attrs.append(func.attr)
            func = func.value
        if not isinstance(func, ast.Name):
            return None
        return ".".join([bound.get(func.id, func.id), *reversed(attrs)])

    return qualify
