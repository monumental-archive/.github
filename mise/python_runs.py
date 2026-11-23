#!/usr/bin/env python3
"""Read what a Python helper runs, exactly, from its syntax tree (#2037).

Two laws ask what a belt or release helper runs: the release job
installs (release/test_job_installs.py, #1333) and the gate's install
table (mise/test_gate_tools.py, #1410). The first holds a list to
cover its calls; the second is held equal to its callers, so a reader
that guesses is red there in both directions. Both read a helper here,
and nothing here guesses: a helper names what it runs in shapes that
say so, and only those shapes are read.

- A SCRIPT runs when it is the literal name handed to
  `belt_scripts.script(home, "x.sh")`, the one way a helper reaches a
  script beside it. A file name in a message, a table or a usage line
  runs nothing.
- A TOOL is called when its command is a literal tool lookup
  (`shutil.which("x")`, `hermetic.tool("x")`, or a module function
  that hands its parameter to `shutil.which`), or the literal first
  element (one word) of a list or tuple, wherever it stands: an argv,
  a module constant splatted later (`[*SHFMT, "-"]`), a list a
  wrapper runs (`rust_task.run([cargo, ...])`). A module name bound
  to a string constant reads as that string.
- A TASK runs when it is a string inside a subprocess call's
  arguments (`mise run audit:deny`, #1903).
- An IMPORT is every `import` and `from ... import`, at any depth.

A tool named only in a message string, a docstring, a comment, a
variable or a function name is no call. A command computed at run
time is not read at all, and needs no list: the gate table is held
both ways, so a row the reader cannot charge is red, and a tool the
table omits is not installed, so the task fails in the gate.

Stdlib plus mise/call_names.py.
"""

import ast
from typing import NamedTuple

import call_names

# The subprocess calls whose first argument is the command (#1499).
SUBPROCESS = frozenset(
    f"subprocess.{name}" for name in ("run", "check_output", "check_call", "Popen")
)

# The calls that find a tool by its command name.
LOOKUPS = frozenset({"shutil.which", "hermetic.tool", "hermetic.optional_tool"})

# The one way a helper names a script it runs (mise/belt_scripts.py).
LOCATOR = "belt_scripts.script"

# Its arguments: the directory, then the script's file name.
LOCATOR_ARGS = 2


class Runs(NamedTuple):
    """What a helper runs: commands (tools and imports), scripts, tasks."""

    commands: set[str]
    imports: set[str]
    scripts: list[str]
    tasks: list[str]


def parsed(text: str, name: str = "a helper") -> ast.Module:
    """Parse a helper, or refuse it by name.

    Returns:
        The helper's syntax tree.

    Raises:
        ValueError: the parser cannot read the helper. Read as "calls
            nothing", it would hide a real call.

    """
    try:
        return ast.parse(text)
    except SyntaxError as error:
        message = f"{name}: the parser cannot read it ({error})"
        raise ValueError(message) from error


def constants(tree: ast.Module) -> dict[str, str]:
    """Read the module-level names bound to a string constant.

    Returns:
        Each such name, with its string.

    """
    out = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            out[node.targets[0].id] = node.value.value
    return out


def literal(node: ast.expr | None, names: dict[str, str]) -> str | None:
    """Read a string literal, or a module name bound to one.

    Returns:
        The string, or None for anything else.

    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id)
    return None


def finders(tree: ast.Module) -> set[str]:
    """Name the calls in this helper that find a tool by its command.

    `LOOKUPS`, and each module function whose first parameter it hands
    to `shutil.which` (mutants_python.py's `which(name)`).

    Returns:
        The calls, as `call_names` spells them.

    """
    qualify = call_names.qualifier(tree)
    out = set(LOOKUPS)
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.args.args:
            continue
        param = node.args.args[0].arg
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and qualify(call.func) == "shutil.which"
                and call.args
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id == param
            ):
                out.add(node.name)
    return out


def calls(tree: ast.Module) -> set[str]:
    """Name the tools a helper calls (see the module docstring).

    Returns:
        Each command named by a lookup or heading a list or tuple, and
        the first word of a string a subprocess call runs.

    """
    qualify = call_names.qualifier(tree)
    names = constants(tree)
    found = finders(tree)
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            head = literal(node.elts[0], names)
        elif isinstance(node, ast.Call) and node.args:
            called = qualify(node.func)
            head = literal(node.args[0], names) if called in found else None
            if called in SUBPROCESS and head is None:
                words = (literal(node.args[0], {}) or "").split()
                head = words[0] if words else None
        else:
            head = None
        # A command is one word; a list headed by a sentence is prose.
        if head and len(head.split()) == 1 and head == head.strip():
            out.add(head)
    return out


def imports(tree: ast.Module) -> set[str]:
    """Name the top-level module of every absolute import (#1903).

    A helper that imports a belt tool's module needs that tool
    installed, as `hypothesis` is.

    Returns:
        The first dotted part of each `import x.y` and `from x.y import`.

    """
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def scripts(tree: ast.Module) -> list[str]:
    """Name the scripts a helper runs: the literals `script()` is handed.

    Returns:
        Each file name, in tree order.

    """
    qualify = call_names.qualifier(tree)
    names = constants(tree)
    return [
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and qualify(node.func) == LOCATOR
        and len(node.args) == LOCATOR_ARGS
        and (name := literal(node.args[1], names))
    ]


def tasks(tree: ast.Module) -> list[str]:
    """Name every string inside a subprocess call's arguments (#1903).

    A task a subprocess call runs (`["mise", "run", "audit:deny"]`) is
    among them; a follower keeps the ones that are tasks.

    Returns:
        The strings, in tree order.

    """
    qualify = call_names.qualifier(tree)
    return [
        c.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and qualify(node.func) in SUBPROCESS
        for arg in [*node.args, *(k.value for k in node.keywords)]
        for c in ast.walk(arg)
        if isinstance(c, ast.Constant) and isinstance(c.value, str)
    ]


def python_code(text: str, name: str = "a helper") -> Runs:
    """Read what a Python helper runs from its syntax tree alone.

    Returns:
        Its commands (`calls` and `imports`), its imports alone, its
        scripts and the strings its subprocess calls are handed.

    """
    tree = parsed(text, name)
    imported = imports(tree)
    return Runs(calls(tree) | imported, imported, scripts(tree), tasks(tree))
