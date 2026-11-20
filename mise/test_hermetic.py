#!/usr/bin/env python3
"""Tests for hermetic.py and the law it holds the suite to (#1514).

The helper: a variable the suite's process carries reaches a fixture
only if INHERITED or a named tool extension names it; a row's override
always does. Its reader names the files a checkout tracks (#1618).
The law: no
test file in the four discovery roots builds a fixture's environment by
copying the caller's, or asks git for a plain listing of what a tree
tracks instead of calling hermetic.tracked (#1618).

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import ast
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING, Literal, override
from unittest import mock

import call_names
import hermetic
import typed_std

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

HERE = Path(__file__).resolve().parent

CANON = HERE.parent
GIT = hermetic.tool("git")
ROOTS = ("mise", "release", ".github/rekor-monitor", "settings")

# The release job's step inputs, the runner's ambient, and the
# provisioning the gate exports: each has reached, or could reach, a
# script under test by inheritance (#1508).
PLANTED = {
    "VERSION": "1.69.4",
    "PLAN": "{}",
    "APP_SLUG": "canon-release",
    "GITHUB_REF": "refs/heads/main",
    "RUNNER_TEMP": "/runner-temp",
    "ORG_PGRX_MAJORS": "17,18",
    "GIT_DIR": "/nowhere/.git",
    "GIT_INDEX_FILE": "/nowhere/.git/index",
}

# A whole-mapping read of the environment, by method (#1638).
WHOLE_READS = frozenset({"copy", "items", "keys", "values"})


def environment_names(module: ast.Module) -> tuple[set[str], set[str]]:
    """Name what the file binds to `os` and to the environment itself.

    `import os` and `import os as x` bind the module; `from os import
    environ` (aliased or not) and `name = <the environment>` bind the
    environment, followed until nothing new is bound.

    Returns:
        The names bound to `os`, and the names bound to `os.environ`.

    """
    modules: set[str] = set()
    environs: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            modules |= {a.asname or a.name for a in node.names if a.name == "os"}
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            environs |= {a.asname or a.name for a in node.names if a.name == "environ"}
    assigned = [
        (t.id, node.value)
        for node in ast.walk(module)
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    ]
    grew = True
    while grew:
        grew = False
        for name, value in assigned:
            if name not in environs and is_environment(value, modules, environs):
                environs.add(name)
                grew = True
    return modules, environs


def is_environment(node: ast.AST, modules: set[str], environs: set[str]) -> bool:
    """Say whether an expression IS the process environment, whole.

    Returns:
        True for `os.environ` through any binding of `os`, or a name
        bound to the environment.

    """
    if isinstance(node, ast.Name):
        return node.id in environs
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id in modules
    )


def copies(source: str) -> list[int]:
    """Find every line where a file reads the environment whole (#1638).

    A copy is a dict display with `**` the environment; `dict(...)`
    given it, plainly or as `**`; `.copy()`, `.items()`, `.keys()` or
    `.values()` on it; it as either side of `|`, the right of `|=`, or
    an argument of `.update(`; a comprehension or `for` over it. One
    variable read (`os.environ["X"]`, `.get("X")`, `in`) is not a copy,
    nor is `mock.patch.dict(os.environ, env, clear=True)`, which
    REPLACES the environment: it calls an attribute `dict`, never the
    bare name.

    Returns:
        The line of each copy, sorted.

    """
    module = ast.parse(source)
    modules, environs = environment_names(module)

    def env(node: ast.AST | None) -> bool:
        return node is not None and is_environment(node, modules, environs)

    return sorted(line_of(node) for node in ast.walk(module) if reads_whole(node, env))


def line_of(node: ast.AST) -> int:
    """Name the line a node `reads_whole` accepts stands on.

    Returns:
        A comprehension's iterable's line; any other node's own.

    Raises:
        TypeError: a node that carries no line, which `reads_whole`
            never accepts.

    """
    if isinstance(node, ast.comprehension):
        return node.iter.lineno
    if isinstance(node, (ast.expr, ast.stmt)):
        return node.lineno
    msg = f"{type(node).__name__} carries no line"
    raise TypeError(msg)


def reads_whole(node: ast.AST, env: Callable[[ast.AST | None], bool]) -> bool:
    """Say whether one node reads the environment whole (see `copies`).

    Returns:
        True for each shape `copies` names.

    """
    if isinstance(node, ast.Dict):
        return any(
            k is None and env(v) for k, v in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.Call):
        func = node.func
        given = [*node.args, *(k.value for k in node.keywords if k.arg is None)]
        if isinstance(func, ast.Name):
            return func.id == "dict" and any(env(a) for a in given)
        return isinstance(func, ast.Attribute) and (
            (func.attr in WHOLE_READS and env(func.value))
            or (func.attr == "update" and any(env(a) for a in given))
        )
    if isinstance(node, (ast.BinOp, ast.AugAssign)) and isinstance(node.op, ast.BitOr):
        sides = [node.left, node.right] if isinstance(node, ast.BinOp) else [node.value]
        return any(env(side) for side in sides)
    if isinstance(node, (ast.comprehension, ast.For, ast.AsyncFor)):
        return env(node.iter)
    return False


# What starts a process, by module: `subprocess.run(...)`, `os.execv(...)`.
# What starts a process, by its module's own dotted name, however the
# file imported it (call_names.qualifier): exact names, and the `os`
# families by prefix (`os.execve`, `os.spawnlpe`, `os.posix_spawnp`).
STARTS = frozenset({
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.check_output",
    "subprocess.check_call",
    "subprocess.call",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
})
STARTS_BY_PREFIX = ("os.exec", "os.spawn", "os.posix_spawn")
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)

# What a row expects, named so each carries its element type.
NOTHING: list[object] = []
LINE_TWO: list[int] = [2]
NO_NAMES: set[str] = set()


def dotted(node: ast.AST) -> str | None:
    """Spell a name or one attribute of a name: `env`, `self.env`.

    Returns:
        The spelling, or None for anything else.

    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def starts_process(spelled: str | None) -> bool:
    """Say whether a call, by its dotted name, starts a process.

    Returns:
        True for a name in STARTS or under a STARTS_BY_PREFIX family.

    """
    return spelled in STARTS or (spelled or "").startswith(STARTS_BY_PREFIX)


def given_environment(call: ast.Call, spelled: str) -> list[ast.expr]:
    """Find the environment a process-starting call is handed.

    Returns:
        Its `env=` argument; for `os.posix_spawn` and `os.posix_spawnp`,
        the third by place; for `os.execve` and the other `os.exec*` and
        `os.spawn*` names ending in `e`, the last by place.

    """
    given = [k.value for k in call.keywords if k.arg == "env"]
    if spelled.startswith("os.posix_spawn"):
        given += call.args[2:3]
    elif spelled.startswith(("os.exec", "os.spawn")) and spelled.endswith("e"):
        given += call.args[-1:]
    return given


class Environments:
    """Where each child environment in one test file comes from (#1542)."""

    def __init__(self, source: str) -> None:
        """Read the file's assignments, returns and parameters by scope."""
        self.module = ast.parse(source)
        self.qualify = call_names.qualifier(self.module)
        self.parent: dict[ast.AST, ast.AST] = {}
        # (scope, name) -> values; scope is a function or the module.
        self.assigned: dict[tuple[ast.AST, str], list[ast.AST]] = {}
        self.returns: dict[str, list[ast.AST]] = {}
        self.params: dict[str, list[str]] = {}
        for node in ast.walk(self.module):
            for child in ast.iter_child_nodes(node):
                self.parent[child] = node
        for node in ast.walk(self.module):
            if isinstance(node, FUNCTIONS):
                a = node.args
                self.params[node.name] = [
                    x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)
                ]
                self.returns[node.name] = [
                    n.value
                    for n in ast.walk(node)
                    if isinstance(n, ast.Return) and n.value
                ]
            elif (
                isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
            ):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    name = dotted(target)
                    if name is not None:
                        scope = self.module if "." in name else self.scope(node)
                        self.assigned.setdefault((scope, name), []).append(node.value)

    def scope(self, node: ast.AST) -> ast.AST:
        """Name the function a node is in, or the module.

        Returns:
            The innermost enclosing function, else the module.

        """
        while node in self.parent:
            node = self.parent[node]
            if isinstance(node, FUNCTIONS):
                return node
        return self.module

    def values(self, name: str, at: ast.AST) -> list[ast.AST] | None:
        """Find what a name holds where it is read.

        Returns:
            Its assigned values in the nearest scope that assigns it, or
            None when no scope does (a parameter, or a global import).

        """
        scope = self.module if "." in name else self.scope(at)
        while True:
            found = self.assigned.get((scope, name))
            if found is not None:
                return found
            if scope is self.module:
                return None
            scope = self.scope(scope)

    def hermetic(self, node: ast.AST, seen: frozenset[str]) -> bool | None:
        """Say whether an environment starts from hermetic.env.

        Returns:
            True when it does, False when it is built otherwise, None
            when it is a parameter, judged where it is passed.

        """
        if isinstance(node, ast.Call):
            return self.called(node, seen)
        start = self.start(node)
        if start is not None:
            return start is not False and bool(self.hermetic(start, seen))
        name = dotted(node)
        if name is None:
            return False
        if name in seen:
            return True
        found = self.values(name, node)
        if found is None:
            return None if "." not in name else False
        return all(self.hermetic(v, seen | {name}) for v in found)

    @staticmethod
    def start(node: ast.AST) -> ast.AST | Literal[False] | None:
        """Find the environment a composite expression starts from.

        Returns:
            The left of `|`, the first `**` of a display, or the mapping
            a comprehension filters; False for a display or comprehension
            that starts from nothing; None for any other node, which
            `hermetic` then judges itself (a conditional expression is
            not followed, so it is refused).

        """
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return node.left
        if isinstance(node, ast.Dict):
            first = node.keys[0] is None if node.keys else False
            return node.values[0] if first else False
        if isinstance(node, ast.DictComp):
            source = node.generators[0].iter
            if isinstance(source, ast.Call) and isinstance(source.func, ast.Attribute):
                return source.func.value
            return False
        return None

    def called(self, call: ast.Call, seen: frozenset[str]) -> bool:
        """Say whether a call returns an environment from hermetic.env.

        Returns:
            True for `hermetic.env(...)`, an alias of it, `dict()` of
            one, or a function of this file whose every return is one.

        """
        name = dotted(call.func) or ""
        if name == "hermetic.env":
            return True
        if name == "dict":
            return bool(call.args) and bool(self.hermetic(call.args[0], seen))
        function = name.rpartition(".")[2]
        if function in self.returns and function not in seen:
            found = self.returns[function]
            return bool(found) and all(
                self.hermetic(r, seen | {function}) for r in found
            )
        aliases = self.values(name, call) if name else None
        if not aliases:
            return False
        return all(dotted(a) == "hermetic.env" for a in aliases)

    def built_by_hand(self) -> list[int]:
        """Find every child environment not taken from hermetic.env.

        Returns:
            The line of each, sorted.

        """
        refused: set[int] = set()
        pending: list[tuple[str, str]] = []
        done: set[tuple[str, str]] = set()

        def judge(value: ast.expr, at: ast.AST) -> None:
            verdict = self.hermetic(value, frozenset())
            function = self.scope(at)
            if verdict is None and isinstance(function, FUNCTIONS):
                pending.append((function.name, dotted(value) or ""))
            elif not verdict:
                refused.add(value.lineno)

        calls = [n for n in ast.walk(self.module) if isinstance(n, ast.Call)]
        for call in calls:
            spelled = self.qualify(call.func)
            if not starts_process(spelled):
                continue
            for value in given_environment(call, spelled or ""):
                judge(value, call)
        while pending:
            forwarded = pending.pop()
            if forwarded in done:
                continue
            done.add(forwarded)
            for value, call in self.passed(*forwarded, calls):
                judge(value, call)
        return sorted(refused)

    def passed(
        self, function: str, param: str, calls: list[ast.Call]
    ) -> list[tuple[ast.expr, ast.Call]]:
        """Find what each call of a file's own function passes a parameter.

        Returns:
            Each passed value with its call.

        """
        names = self.params[function]
        bound = 1 if names[:1] in (["self"], ["cls"]) else 0
        out: list[tuple[ast.expr, ast.Call]] = []
        for call in calls:
            if (dotted(call.func) or "").rpartition(".")[2] != function:
                continue
            given = [k.value for k in call.keywords if k.arg == param]
            index = names.index(param) - (
                bound if isinstance(call.func, ast.Attribute) else 0
            )
            if not given and 0 <= index < len(call.args):
                given = [call.args[index]]
            out += [(value, call) for value in given]
        return out


def source_text(path: str) -> str:
    """Read a tracked file of the canon.

    Returns:
        The file's text.

    """
    return (CANON / path).read_text(encoding="utf-8")


def listed_test_files() -> list[str]:
    """Name every test file in the four discovery roots.

    Returns:
        Their paths, relative to the canon.

    """
    return hermetic.tracked(
        CANON,
        *(f"{r}/test_*.py" for r in ROOTS),
        *(f"{r}/prop_*.py" for r in ROOTS),
    )


# What `ls-files` may carry and still be a plain listing of what a tree
# tracks (#1618): `hermetic.tracked`'s own question. Any other option
# asks git something else (`--others`, `--modified`, `--stage`, ...).
PLAIN_LISTING = frozenset({"-z", "--full-name", "--"})


def plain_listings(source: str) -> list[int]:
    """Find every line where a test asks git for the files a tree tracks.

    The law asks what the listing is, not whose tree it is (#1618's
    amendment): an argument list holding `ls-files` whose later words
    are all pathspecs, `-z`, `--full-name` or `--` is a plain listing,
    and a test makes it through `hermetic.tracked(root, ...)` whatever
    the root: the canon, a parameter, a scratch repository. The list is
    read wherever it is written: a process start, a name, the argument
    of a file's own `git()` wrapper.

    Returns:
        The line of each plain listing, sorted.

    """
    found: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        words = [e.value if isinstance(e, ast.Constant) else None for e in node.elts]
        if "ls-files" not in words:
            continue
        after = words[words.index("ls-files") + 1 :]
        options = [w for w in after if isinstance(w, str) and w.startswith("-")]
        if all(w in PLAIN_LISTING for w in options):
            found.add(node.lineno)
    return sorted(found)


def which_calls(source: str) -> list[int]:
    """Find every line where a test looks a tool up itself (#1684).

    A call is judged by what it calls, however the file imported it
    (call_names.qualifier): `shutil.which`, `s.which` under `import
    shutil as s`, and `which` or its alias under `from shutil import
    which`. A name for `shutil.which` that is never called, as in
    `mock.patch.object(shutil, "which", ...)`, looks nothing up.

    Returns:
        The line of each call, sorted.

    """
    module = ast.parse(source)
    qualify = call_names.qualifier(module)
    return sorted(
        node.lineno
        for node in ast.walk(module)
        if isinstance(node, ast.Call) and qualify(node.func) == "shutil.which"
    )


# What copies or links a file, by its module's dotted name (#1714):
# the first argument, or `src=`, is what is planted.
PLANTERS = frozenset({
    "shutil.copy",
    "shutil.copy2",
    "shutil.copyfile",
    "shutil.copytree",
    "os.symlink",
    "os.link",
})
# The Path methods that plant: `link.symlink_to(target)`.
PATH_PLANTERS = frozenset({"symlink_to", "hardlink_to"})
# A fixture directory: what a test keeps under it is the test's own.
FIXTURES = "testdata"


def from_tree(value: ast.AST, rooted: set[str]) -> bool:
    """Say whether an expression is a path in the canon's own tree.

    Returns:
        True when it reads `__file__` or a rooted name, and names no
        fixture directory on the way.

    """
    names = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
    fixture = any(
        isinstance(n, ast.Constant) and n.value == FIXTURES for n in ast.walk(value)
    )
    return bool(names & (rooted | {"__file__"})) and not fixture


def bindings(scope: ast.AST) -> list[tuple[str, ast.AST]]:
    """Read what one scope binds to a plain name, and from what.

    A module's are its own statements; a function's are every
    assignment or `for` in its body, nested blocks included.

    Returns:
        Each (name, value) pair, a `for` target bound to its iterable.

    """
    nodes = scope.body if isinstance(scope, ast.Module) else list(ast.walk(scope))
    out: list[tuple[str, ast.AST]] = []
    for node in nodes:
        if isinstance(node, ast.Assign):
            out += [(t.id, node.value) for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                out.append((node.target.id, node.value))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            targets = [n for n in ast.walk(node.target) if isinstance(n, ast.Name)]
            out += [(t.id, node.iter) for t in targets]
    return out


def tree_names(scope: ast.AST, outer: set[str]) -> set[str]:
    """Name what is a path in the canon's tree, in one scope.

    `outer` is what the module roots; a name the scope binds itself is
    rooted only by its own bindings.

    Returns:
        The rooted names visible in the scope.

    """
    bound = bindings(scope)
    own = {name for name, _ in bound}
    rooted = outer - own
    grew = True
    while grew:
        grew = False
        for name, value in bound:
            if name not in rooted and from_tree(value, rooted):
                rooted.add(name)
                grew = True
    return rooted


def planted_source(call: ast.Call, spelled: str | None) -> ast.AST | None:
    """Find what a call copies or links, when it is a planting call.

    Returns:
        The source path expression, or None for any other call.

    """
    func = call.func
    if spelled in PLANTERS:
        keyword = "src"
    elif isinstance(func, ast.Attribute) and func.attr in PATH_PLANTERS:
        keyword = "target"
    else:
        return None
    given = [*call.args[:1], *(k.value for k in call.keywords if k.arg == keyword)]
    return given[0] if given else None


def plants_from_tree(source: str) -> list[int]:
    """Find every line where a test links or copies the belt or canon (#1714).

    A planting call is `shutil.copy`, `copy2`, `copyfile`, `copytree`,
    `os.symlink` or `os.link` however imported (call_names.qualifier),
    or `Path.symlink_to` / `hardlink_to`. Its source is from the tree
    when it reads `__file__` or a name bound from it, in the module or
    in the call's own function; a fixture under `testdata/`, and a file
    the test wrote itself, are the test's own.

    Returns:
        The line of each such call, sorted.

    """
    module = ast.parse(source)
    qualify = call_names.qualifier(module)
    rooted = tree_names(module, set())
    parent = {
        child: node for node in ast.walk(module) for child in ast.iter_child_nodes(node)
    }

    def scope_of(node: ast.AST) -> ast.AST:
        while node in parent:
            node = parent[node]
            if isinstance(node, FUNCTIONS):
                return node
        return module

    scopes: dict[ast.AST, set[str]] = {}
    lines: list[int] = []
    for call in (n for n in ast.walk(module) if isinstance(n, ast.Call)):
        planted = planted_source(call, qualify(call.func))
        if planted is None:
            continue
        scope = scope_of(call)
        if scope not in scopes:
            scopes[scope] = rooted if scope is module else tree_names(scope, rooted)
        if from_tree(planted, scopes[scope]):
            lines.append(call.lineno)
    return sorted(lines)


# Excepted by name: the helper itself, and this file, which plants and
# names the spellings it looks for.
EXCEPTED = frozenset({"mise/hermetic.py", "mise/test_hermetic.py"})


class Env(unittest.TestCase):
    """What hermetic.env lets through."""

    def test_planted_variables_do_not_reach_a_fixture(self) -> None:
        """The job's inputs and git's own variables stay with the suite."""
        with mock.patch.dict(os.environ, PLANTED):
            env = hermetic.env("/fixture-home")
        reached = sorted(set(PLANTED) & set(env))
        self.assertEqual(NOTHING, reached)

    def test_only_the_allow_list_is_inherited(self) -> None:
        """Every inherited name is INHERITED's, or one the helper sets."""
        set_here = {
            "HOME",
            "GIT_EDITOR",
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
        }
        with mock.patch.dict(os.environ, PLANTED):
            env = hermetic.env()
        extra = set(env) - set(hermetic.INHERITED) - set_here
        self.assertEqual(NO_NAMES, extra)

    def test_a_rows_override_reaches_the_fixture(self) -> None:
        """A row states its own input, and that wins over the suite's."""
        with mock.patch.dict(os.environ, PLANTED):
            env = hermetic.env("/fixture-home", VERSION="2.0.0")
        self.assertEqual("2.0.0", env["VERSION"])

    def test_home_is_the_fixtures_or_the_suites(self) -> None:
        """A given home wins; without one the suite's HOME is kept."""
        suite: dict[str, str] = {"HOME": "/suite-home"}
        with mock.patch.dict(os.environ, suite):
            self.assertEqual("/fixture-home", hermetic.env("/fixture-home")["HOME"])
            self.assertEqual("/suite-home", hermetic.env()["HOME"])

    def test_each_tool_adds_exactly_its_names(self) -> None:
        """A tool extension adds its variables and nothing else."""
        every = {n for names in hermetic.TOOLS.values() for n in names}
        planted = dict.fromkeys(every | set(hermetic.INHERITED), "x")
        with mock.patch.dict(os.environ, planted):
            base = set(hermetic.env())
            for tool, names in hermetic.TOOLS.items():
                with self.subTest(tool=tool):
                    self.assertEqual(
                        set(names) - base, set(hermetic.env(tools=(tool,))) - base
                    )

    def test_an_unknown_tool_is_refused(self) -> None:
        """A tool nobody measured has no extension to borrow."""
        with self.assertRaises(KeyError):
            hermetic.env(tools=("no-such-tool",))


class Tracked(unittest.TestCase):
    """hermetic.tracked, in a checkout (#1618)."""

    @override
    def setUp(self) -> None:
        """Plant a repository with tracked files in and out of `vendor/`."""
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        for name in ("a.py", "pkg/b.py", "pkg/c.sh", "vendor/v.py", "d.py"):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text("x\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", ".")

    def git(self, *args: str) -> None:
        """Run git in the planted repository."""
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run([GIT, *args], cwd=self.root, env=hermetic.env(), check=True)

    def test_a_checkout_lists_what_git_tracks(self) -> None:
        """The pathspecs select from the tracked files, excludes kept."""
        specs = (":!vendor/**", "*.py", "*.sh")
        selected: list[str] = ["a.py", "d.py", "pkg/b.py", "pkg/c.sh"]
        self.assertEqual(hermetic.tracked(self.root, *specs), selected)

    def test_an_empty_listing_is_red_by_name(self) -> None:
        """Files asked for and none listed is never a pass."""
        with self.assertRaises(RuntimeError) as raised:
            hermetic.tracked(self.root, "*.rs")
        self.assertEqual(
            str(raised.exception),
            f"hermetic.tracked: git lists no tracked file under {self.root}"
            " for ['*.rs']",
        )


def on_path(path: str) -> AbstractContextManager[object]:
    """Replace the suite's PATH for one block.

    Returns:
        The patch, entered by the row.

    """
    values: dict[str, str] = {"PATH": path}
    return mock.patch.dict(os.environ, values)


class Tool(unittest.TestCase):
    """hermetic.tool and hermetic.optional_tool (#1684)."""

    @override
    def setUp(self) -> None:
        """Plant a directory holding one executable, `planted-tool`."""
        self.bin = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.bin)
        (self.bin / "planted-tool").write_text("#!/bin/sh\n", encoding="utf-8")
        (self.bin / "planted-tool").chmod(0o755)

    def test_a_present_tool_is_its_absolute_path(self) -> None:
        """Found on PATH, the path is absolute."""
        with on_path(str(self.bin)):
            found = hermetic.tool("planted-tool")
        self.assertEqual(str(self.bin / "planted-tool"), found)
        self.assertTrue(Path(found).is_absolute())

    def test_a_relative_path_entry_still_gives_an_absolute_path(self) -> None:
        """A relative PATH entry is made absolute and normal, never resolved."""
        with on_path(os.path.relpath(self.bin)):
            found = hermetic.tool("planted-tool")
        self.assertEqual(str(self.bin / "planted-tool"), found)

    def test_a_link_is_not_resolved(self) -> None:
        """A mise shim is a link that dispatches on its own name."""
        shims = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, shims)
        (shims / "shim").symlink_to(self.bin / "planted-tool")
        self.assertEqual(str(shims / "shim"), hermetic.tool("shim", path=str(shims)))

    def test_a_missing_tool_is_red_by_name(self) -> None:
        """The message names the tool, where it looked, and the remedy."""
        with (
            on_path(str(self.bin)),
            self.assertRaises(hermetic.MissingToolError) as raised,
        ):
            hermetic.tool("no-such-tool")
        self.assertEqual(
            "hermetic.tool: no-such-tool is not on PATH;"
            " the belt provides it: run mise install",
            str(raised.exception),
        )

    def test_the_search_path_is_honoured(self) -> None:
        """`path=` is searched instead of the suite's PATH, both ways."""
        empty = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, empty)
        with on_path(str(empty)):
            found = hermetic.tool("planted-tool", path=str(self.bin))
        self.assertEqual(str(self.bin / "planted-tool"), found)
        with (
            on_path(str(self.bin)),
            self.assertRaises(hermetic.MissingToolError) as raised,
        ):
            hermetic.tool("planted-tool", path=str(empty))
        self.assertIn(f"the search path {str(empty)!r}", str(raised.exception))

    def test_an_optional_tool_is_none_when_absent(self) -> None:
        """Absence is the row's decision: None, never a raise."""
        with on_path(str(self.bin)):
            self.assertIsNone(hermetic.optional_tool("no-such-tool"))
            self.assertEqual(
                str(self.bin / "planted-tool"),
                hermetic.optional_tool("planted-tool"),
            )


class Belt(unittest.TestCase):
    """hermetic.belt and hermetic.canon (#1714)."""

    @override
    def setUp(self) -> None:
        """Give each row a scratch directory."""
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_every_tracked_belt_file_is_a_link_to_it(self) -> None:
        """The whole belt, nothing more, each file the real one by link."""
        belt = hermetic.belt(self.tmp / "belt")
        names = {n.removeprefix("mise/") for n in hermetic.tracked(CANON, "mise/")}
        planted = {str(p.relative_to(belt)) for p in belt.rglob("*") if not p.is_dir()}
        self.assertEqual(names, planted)
        unlinked = [
            n
            for n in sorted(names)
            if not (belt / n).is_symlink()
            or (belt / n).resolve() != (CANON / "mise" / n).resolve()
        ]
        self.assertEqual(list[str](), unlinked)

    def test_a_stub_replaces_its_link_and_keeps_the_mode(self) -> None:
        """A stub is a file of its own, executable where the real one is."""
        name = "action-files.sh"
        real = CANON / "mise" / name
        mode = real.stat().st_mode & 0o7777
        self.assertTrue(mode & 0o100, f"{name} is not executable in the belt")
        belt = hermetic.belt(
            self.tmp / "belt", stubs={name: "stub\n", "new.txt": "new\n"}
        )
        self.assertFalse((belt / name).is_symlink())
        self.assertEqual("stub\n", (belt / name).read_text(encoding="utf-8"))
        self.assertEqual(mode, (belt / name).stat().st_mode & 0o7777)
        self.assertNotEqual("stub\n", real.read_text(encoding="utf-8"))
        self.assertEqual("new\n", (belt / "new.txt").read_text(encoding="utf-8"))

    def test_a_file_added_to_the_belt_appears_unasked(self) -> None:
        """A new helper, or a new sibling a script starts, needs no row change."""
        scratch = self.tmp / "canon"
        (scratch / "mise").mkdir(parents=True)
        (scratch / "mise" / "a.py").write_text("x\n", encoding="utf-8")
        self.git(scratch, "init", "-q")
        self.git(scratch, "add", ".")
        with mock.patch.object(hermetic, "CANON", scratch):
            before = hermetic.belt(self.tmp / "before")
            (scratch / "mise" / "sub").mkdir()
            (scratch / "mise" / "sub" / "b.sh").write_text("x\n", encoding="utf-8")
            self.git(scratch, "add", ".")
            after = hermetic.belt(self.tmp / "after")
        found: list[str] = sorted(
            str(p.relative_to(before)) for p in before.rglob("*.*")
        )
        grown: list[str] = sorted(str(p.relative_to(after)) for p in after.rglob("*.*"))
        once: list[str] = ["a.py"]
        both: list[str] = ["a.py", "sub/b.sh"]
        self.assertEqual(once, found)
        self.assertEqual(both, grown)
        self.assertEqual(
            scratch / "mise" / "sub" / "b.sh", (after / "sub" / "b.sh").readlink()
        )

    def test_a_canon_holds_mise_release_and_what_the_row_names(self) -> None:
        """`mise/` and `release/` always; anything else only when named."""
        tree = hermetic.canon(
            self.tmp / "canon",
            "security/identity.toml",
            stubs={"release/planted.py": "x\n"},
        )
        for name in (
            "mise/hermetic.py",
            "release/caller_pins.py",
            "security/identity.toml",
        ):
            with self.subTest(name):
                self.assertEqual(CANON / name, (tree / name).readlink())
        self.assertFalse((tree / "slsa").exists())
        self.assertFalse((tree / "release" / "planted.py").is_symlink())

    @staticmethod
    def git(cwd: Path, *args: str) -> None:
        """Run git in a scratch canon."""
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run([GIT, *args], cwd=cwd, env=hermetic.env(), check=True)


class TheLaw(unittest.TestCase):
    """No test file copies the caller's environment (#1514 item 3)."""

    def test_no_test_file_copies_the_callers_environment(self) -> None:
        """Every fixture environment is built through hermetic.env."""
        offenders = [
            f"{path}:{line}"
            for path in listed_test_files()
            if path not in EXCEPTED
            for line in copies(source_text(path))
        ]
        self.assertEqual(NOTHING, offenders)

    def test_no_test_builds_a_child_environment_by_hand(self) -> None:
        """Every child environment starts from hermetic.env (#1542)."""
        offenders = [
            f"{path}:{line}"
            for path in listed_test_files()
            if path not in EXCEPTED
            for line in Environments(source_text(path)).built_by_hand()
        ]
        self.assertEqual(NOTHING, offenders)

    def test_each_way_to_build_an_environment_is_judged(self) -> None:
        """Planted: hand-built environments refused by line; derived ones pass."""
        head = "import subprocess\n"
        refused = {
            "display": head + 'subprocess.run(a, env={"PATH": p})\n',
            "dict()": head + "subprocess.run(a, env=dict(PATH=p))\n",
            "a name": head + 'e = {"PATH": p}\nsubprocess.run(a, env=e)\n',
            "an attribute": head
            + 'self.env = {"A": "1"}\nsubprocess.run(a, env=self.env)\n',
            "a wrapper's parameter": head
            + "def run(env):\n    subprocess.run(a, env=env)\n"
            + 'run({"PATH": p})\n',
            "os.exec": (
                "import os\nos.execve(p, a, {'PATH': p})\nos.execvpe(p, a, env={})\n"
            ),
        }
        lines = {
            "display": [2],
            "dict()": [2],
            "a name": [3],
            "an attribute": [3],
            "a wrapper's parameter": [4],
            "os.exec": [2, 3],
        }
        for name, source in refused.items():
            with self.subTest(name):
                self.assertEqual(lines[name], Environments(source).built_by_hand())

        allowed = {
            "the call": head + "subprocess.run(a, env=hermetic.env(A='1'))\n",
            "extended": head + "subprocess.run(a, env=hermetic.env() | {'A': '1'})\n",
            "spread": head + "subprocess.run(a, env={**hermetic.env(), 'A': '1'})\n",
            "a name, then edited": head
            + "e = hermetic.env()\ne.pop('A', None)\nsubprocess.run(a, env=e)\n",
            "a wrapper": head
            + "def clean():\n    return hermetic.env(A='1')\n"
            + "subprocess.run(a, env=clean())\n",
            "an alias": head + "h = hermetic.env\nsubprocess.run(a, env=h())\n",
            "a filter": head
            + "e = {k: v for k, v in hermetic.env().items() if k}\n"
            + "subprocess.run(a, env=e)\n",
            "a wrapper's parameter": head
            + "def run(env):\n    subprocess.run(a, env=env)\n"
            + "run(hermetic.env())\n",
            "no env": head + "subprocess.run(a)\n",
            "not a process": "f(env={'A': '1'})\n",
        }
        for name, source in allowed.items():
            with self.subTest(name):
                self.assertEqual(NOTHING, Environments(source).built_by_hand())

    def test_every_spelling_of_a_process_start_is_judged(self) -> None:
        """Planted: each import form and each starter, refused at its line."""
        hand = "{'PATH': p}"
        refused = {
            "import as": f"import subprocess as sp\nsp.run(a, env={hand})\n",
            "from import": f"from subprocess import run\nrun(a, env={hand})\n",
            "from import as": (
                f"from subprocess import Popen as Spawn\nSpawn(a, env={hand})\n"
            ),
            "os as": f"import os as o\no.execve(p, a, {hand})\n",
            "from os": f"from os import execve\nexecve(p, a, {hand})\n",
            "posix_spawn": f"import os\nos.posix_spawn(p, a, {hand})\n",
            "posix_spawnp env=": f"import os\nos.posix_spawnp(p, a, env={hand})\n",
            "asyncio exec": (
                f"import asyncio\nasyncio.create_subprocess_exec(p, env={hand})\n"
            ),
            "asyncio shell": (
                "from asyncio import create_subprocess_shell as sh\n"
                f"sh(p, env={hand})\n"
            ),
            "conditional": (
                "import subprocess\n"
                f"subprocess.run(a, env=hermetic.env() if c else {hand})\n"
            ),
        }
        for name, source in refused.items():
            with self.subTest(name):
                self.assertEqual(LINE_TWO, Environments(source).built_by_hand())
        allowed = {
            "import as": "import subprocess as sp\nsp.run(a, env=hermetic.env())\n",
            "posix_spawn": "import os\nos.posix_spawn(p, a, hermetic.env())\n",
            "a local run": "def run(a, env):\n    pass\nrun(a, env={'A': '1'})\n",
        }
        for name, source in allowed.items():
            with self.subTest(name):
                self.assertEqual(NOTHING, Environments(source).built_by_hand())

    def test_every_spelling_of_a_copy_is_found(self) -> None:
        """Planted: each way to read the environment whole, at its line."""
        planted = {
            "display": "import os\nenv = {**os.environ}\n",
            "multi-line display": (
                "import os\nenv = {\n    **os.environ,\n    'A': '1',\n}\n"
            ),
            "spaced display": "import os\nenv = { **os.environ }\n",
            "dict(env)": "import os\nenv = dict(os.environ)\n",
            "dict(**env)": "import os\nenv = dict(**os.environ)\n",
            "copy": "import os\nenv = os.environ.copy()\n",
            "items": "import os\nenv = dict(os.environ.items())\n",
            "keys": "import os\nnames = list(os.environ.keys())\n",
            "values": "import os\nvals = list(os.environ.values())\n",
            "or": "import os\nenv = os.environ | {'A': '1'}\n",
            "or, no space": "import os\nenv = os.environ|{'A': '1'}\n",
            "or, right": "import os\nenv = {'A': '1'} | os.environ\n",
            "or-assign": "import os\nenv = {}\nenv |= os.environ\n",
            "update": "import os\nenv = {}\nenv.update(os.environ)\n",
            "update **": "import os\nenv = {}\nenv.update(**os.environ)\n",
            "comprehension": (
                "import os\nenv = {k: v for k, v in\n    os.environ.items()}\n"
            ),
            "comprehension over keys": "import os\nenv = {k: 1 for k in os.environ}\n",
            "for": "import os\nenv = {}\nfor k in os.environ:\n    env[k] = 1\n",
            "from os import environ": "from os import environ\nenv = {**environ}\n",
            "from os import environ as e": (
                "from os import environ as e\nenv = e.copy()\n"
            ),
            "import os as x": "import os as x\nenv = dict(x.environ)\n",
            "name = os.environ": "import os\nsrc = os.environ\nenv = {**src}\n",
        }
        lines = {
            "display": [2],
            "multi-line display": [2],
            "spaced display": [2],
            "dict(env)": [2],
            "dict(**env)": [2],
            "copy": [2],
            "items": [2],
            "keys": [2],
            "values": [2],
            "or": [2],
            "or, no space": [2],
            "or, right": [2],
            "or-assign": [3],
            "update": [3],
            "update **": [3],
            "comprehension": [3],
            "comprehension over keys": [2],
            "for": [3],
            "from os import environ": [2],
            "from os import environ as e": [2],
            "import os as x": [2],
            "name = os.environ": [3],
        }
        for name, source in planted.items():
            with self.subTest(name):
                self.assertEqual(lines[name], copies(source))

    def test_a_single_read_is_not_a_copy(self) -> None:
        """One variable, or a replacement of the whole, is allowed."""
        allowed = {
            "key": "import os\nx = os.environ['A']\n",
            "get": "import os\nx = os.environ.get('A', '')\n",
            "in": "import os\nx = 'A' in os.environ\n",
            "patch.dict": (
                "import os\nfrom unittest import mock\n"
                "with mock.patch.dict(os.environ, env, clear=True):\n    pass\n"
            ),
            "set one": "import os\nos.environ['A'] = '1'\n",
        }
        for name, source in allowed.items():
            with self.subTest(name):
                self.assertEqual(NOTHING, copies(source))

    def test_no_test_file_looks_a_tool_up_itself(self) -> None:
        """Every lookup is hermetic.tool or hermetic.optional_tool (#1684)."""
        offenders = [
            f"{path}:{line}"
            for path in listed_test_files()
            if path not in EXCEPTED
            for line in which_calls(source_text(path))
        ]
        self.assertEqual(list[str](), offenders)

    def test_every_spelling_of_a_lookup_is_found(self) -> None:
        """Planted: each way to call `shutil.which`, at its line."""
        planted = {
            "shutil.which": "import shutil\nX = shutil.which('git')\n",
            "with a fallback": "import shutil\nX = shutil.which('git') or 'git'\n",
            "from shutil import which": "from shutil import which\nX = which('git')\n",
            "from shutil import which as w": (
                "from shutil import which as w\nX = w('git')\n"
            ),
            "import shutil as s": "import shutil as s\nX = s.which('git')\n",
            "in a row": (
                "import shutil\nclass T:\n    def f(self):\n"
                "        return shutil.which('git', path='/bin')\n"
            ),
            "in a decorator": (
                "import shutil\nimport unittest\n"
                "@unittest.skipUnless(shutil.which('dash'), 'no dash')\n"
                "class T(unittest.TestCase):\n    pass\n"
            ),
        }
        lines = {
            "shutil.which": [2],
            "with a fallback": [2],
            "from shutil import which": [2],
            "from shutil import which as w": [2],
            "import shutil as s": [2],
            "in a row": [4],
            "in a decorator": [3],
        }
        for name, source in planted.items():
            with self.subTest(name):
                self.assertEqual(lines[name], which_calls(source))

    def test_the_fixture_lookup_is_allowed(self) -> None:
        """The fixture's lookups, a patched `which`, and another `which` pass."""
        allowed = {
            "tool": "import hermetic\nX = hermetic.tool('git')\n",
            "optional_tool": "import hermetic\nX = hermetic.optional_tool('dash')\n",
            "patched, not called": (
                "import shutil\nfrom unittest import mock\n"
                "with mock.patch.object(shutil, 'which', return_value=None):\n"
                "    pass\n"
            ),
            "a local which": "def which(x):\n    return x\nX = which('git')\n",
        }
        for name, source in allowed.items():
            with self.subTest(name):
                self.assertEqual(list[int](), which_calls(source))

    def test_no_test_file_plants_the_belt_or_canon_itself(self) -> None:
        """Every planted belt or canon is hermetic.belt or hermetic.canon (#1714)."""
        offenders = [
            f"{path}:{line}"
            for path in listed_test_files()
            if path not in EXCEPTED
            for line in plants_from_tree(source_text(path))
        ]
        self.assertEqual(list[str](), offenders)

    def test_every_spelling_of_a_plant_is_found(self) -> None:
        """Planted: each way to link or copy the tree, at its line."""
        head = "import shutil\nfrom pathlib import Path\nBELT = Path(__file__).parent\n"
        planted = {
            "symlink_to": (head + "Path('x').symlink_to(BELT / 'a.py')\n", [4]),
            "hardlink_to": (head + "Path('x').hardlink_to(BELT / 'a.py')\n", [4]),
            "copy": (head + "shutil.copy(BELT / 'a.py', 'x')\n", [4]),
            "copy2": (head + "shutil.copy2(BELT / 'a.py', 'x')\n", [4]),
            "copyfile": (head + "shutil.copyfile(BELT / 'a.py', 'x')\n", [4]),
            "copytree": (head + "shutil.copytree(BELT, 'x')\n", [4]),
            "src=": (head + "shutil.copy(src=BELT / 'a', dst='x')\n", [4]),
            "os.symlink": (head + "import os\nos.symlink(BELT / 'a', 'x')\n", [5]),
            "from shutil import copy as c": (
                (
                    "from shutil import copy as c\nfrom pathlib import Path\n"
                    "HERE = Path(__file__).parent\nc(HERE / 'a', 'x')\n"
                ),
                [4],
            ),
            "a constant": (head + "S = BELT / 'a.sh'\nshutil.copy2(S, 'x')\n", [5]),
            "a local in a row": (
                head + "def f():\n    canon = Path(__file__).parent.parent\n"
                "    shutil.copyfile(canon / 'REUSE.toml', 'x')\n",
                [6],
            ),
            "a loop over the tree": (
                head + "def f():\n    for p in BELT.iterdir():\n"
                "        Path('x').symlink_to(p)\n",
                [6],
            ),
        }
        for name, (source, lines) in planted.items():
            with self.subTest(name):
                self.assertEqual(lines, plants_from_tree(source))

    def test_the_fixture_and_the_tests_own_files_are_allowed(self) -> None:
        """The fixture's planting, a file the row wrote, a fixture, a tool."""
        head = "import shutil\nfrom pathlib import Path\nBELT = Path(__file__).parent\n"
        allowed = {
            "belt": head + "import hermetic\nhermetic.belt('x', stubs={})\n",
            "a file the row wrote": (
                head + "def f(tmp):\n    src = tmp / 'a'\n    src.write_text('x')\n"
                "    shutil.copy(src, tmp / 'b')\n"
            ),
            "a fixture": (
                head + "DATA = BELT / 'testdata' / 'x'\nshutil.copy(DATA / 'a', 'y')\n"
            ),
            "a tool": head
            + "import hermetic\nPath('x').symlink_to(hermetic.tool('git'))\n",
            "a local of the same name": (
                head + "def f(tmp):\n    BELT = tmp\n    shutil.copy(BELT / 'a', 'x')\n"
            ),
        }
        for name, source in allowed.items():
            with self.subTest(name):
                self.assertEqual(list[int](), plants_from_tree(source))

    def test_no_test_file_asks_git_for_a_plain_listing(self) -> None:
        """Every listing of what a tree tracks goes through tracked (#1618)."""
        offenders = [
            f"{path}:{line}"
            for path in listed_test_files()
            for line in plain_listings(source_text(path))
        ]
        self.assertEqual(NOTHING, offenders)

    def test_each_plain_listing_is_refused_whatever_the_root(self) -> None:
        """Planted: the parameter that leaked, cwd=, -C, a scratch tree, a wrapper."""
        refused = {
            "a parameter": (
                "def judge(root):\n"
                '    run([GIT, "-C", str(root), "ls-files", "--", "*.py"])\n'
            ),
            "cwd": 'run([GIT, "ls-files"], cwd=CANON)\n',
            "-C": 'run([GIT, "-C", str(CANON), "ls-files", "-z", "*.sh"])\n',
            "a scratch tree": 'tree = make()\nrun([GIT, "ls-files"], cwd=tree)\n',
            "a wrapper": 'files = git(["ls-files"], linked, home)\n',
            "a name": 'argv = (GIT, "ls-files", "--full-name")\nrun(argv)\n',
        }
        lines = {"a parameter": [2], "a scratch tree": [2]}
        for name, source in refused.items():
            with self.subTest(name):
                self.assertEqual(lines.get(name, [1]), plain_listings(source))
        allowed = {
            "another question": (
                'run([GIT, "ls-files", "--others", "--modified",'
                ' "--exclude-standard"])\n'
            ),
            "the index": 'run([GIT, "ls-files", "--stage", "a"])\n',
            "tracked": 'hermetic.tracked(tree, "*.py")\n',
            "text": 'X = "git ls-files"\n',
        }
        for name, source in allowed.items():
            with self.subTest(name):
                self.assertEqual(list[int](), plain_listings(source))


def _echo_main(argv: list[str]) -> int:
    """Stand for a helper's main: read every input a child would have.

    Returns:
        The status the row asks for, as argv's first word.

    Raises:
        SystemExit: when the row asks the stand-in to exit.

    """
    given = typed_std.stdin_text()
    print(f"cwd={Path.cwd().name} env={os.environ.get('PLANTED')} in={given}")
    print("to stderr", file=sys.stderr)
    if argv[:1] == ["exit"]:
        raise SystemExit(int(argv[1]))
    if argv[:1] == ["refuse"]:
        sys.exit("refused by name")
    return int(argv[0])


class CallMain(unittest.TestCase):
    """`call_main` hands a helper what a child had, then restores it."""

    def test_inputs_reach_the_call_and_are_restored(self) -> None:
        """Environment, working directory and stdin, each put back."""
        with tempfile.TemporaryDirectory() as tmp:
            where = Path(tmp) / "tree"
            where.mkdir()
            before = (dict(os.environ), Path.cwd(), sys.stdin)
            done = hermetic.call_main(
                lambda: _echo_main(["3"]),
                env={"PLANTED": "yes"},
                cwd=where,
                stdin="data",
            )
            after = (dict(os.environ), Path.cwd(), sys.stdin)
        self.assertEqual(
            hermetic.Called(3, "cwd=tree env=yes in=data\n", "to stderr\n"), done
        )
        self.assertEqual(before, after)

    def test_a_system_exit_is_the_status(self) -> None:
        """An exit code, or a message, as a child's would be."""
        self.assertEqual(
            4, hermetic.call_main(lambda: _echo_main(["exit", "4"])).returncode
        )
        refused = hermetic.call_main(lambda: _echo_main(["refuse"]))
        self.assertEqual(1, refused.returncode)
        self.assertTrue(refused.stderr.endswith("refused by name\n"), refused.stderr)

    def test_no_env_leaves_the_environment_as_it_is(self) -> None:
        """Without `env`, the call sees this process's own variables."""
        kept: dict[str, str] = {"PLANTED": "kept"}
        with mock.patch.dict(os.environ, kept):
            done = hermetic.call_main(lambda: _echo_main(["0"]))
        self.assertIn("env=kept", done.stdout)


if __name__ == "__main__":
    unittest.main()
