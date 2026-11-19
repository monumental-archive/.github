#!/usr/bin/env python3
"""Tests for hermetic.py and the law it holds the suite to (#1514).

The helper: a variable the suite's process carries reaches a fixture
only if INHERITED or a named tool extension names it; a row's override
always does. Its reader names the files a tree tracks, from a checkout
or from a gitignored copy of the tree inside it (#1618). The law: no
test file in the four discovery roots builds a fixture's environment by
copying the caller's, or lists the canon's tree with `git ls-files`.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import ast
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING, override
from unittest import mock

import call_names
import hermetic

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

    return sorted(
        node.iter.lineno if isinstance(node, ast.comprehension) else node.lineno
        for node in ast.walk(module)
        if reads_whole(node, env)
    )


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


def given_environment(call: ast.Call, spelled: str) -> list[ast.AST]:
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
    def start(node: ast.AST) -> ast.AST | bool | None:
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
            called = isinstance(source, ast.Call) and isinstance(
                source.func, ast.Attribute
            )
            return source.func.value if called else False
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
        return bool(aliases) and all(dotted(a) == "hermetic.env" for a in aliases)

    def built_by_hand(self) -> list[int]:
        """Find every child environment not taken from hermetic.env.

        Returns:
            The line of each, sorted.

        """
        refused: set[int] = set()
        pending: list[tuple[str, str]] = []
        done: set[tuple[str, str]] = set()

        def judge(value: ast.AST, at: ast.AST) -> None:
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
    ) -> list[tuple[ast.AST, ast.Call]]:
        """Find what each call of a file's own function passes a parameter.

        Returns:
            Each passed value with its call.

        """
        names = self.params[function]
        bound = 1 if names[:1] in (["self"], ["cls"]) else 0
        out = []
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


def rooted_names(module: ast.Module) -> set[str]:
    """Name every variable that is the canon's tree or a path in it.

    A name is rooted when what is assigned to it reads `__file__` or
    another rooted name: `HERE = Path(__file__).parent`, `CANON =
    HERE.parent`, a row's `root = _HERE.parent`.

    Returns:
        The rooted names, at every scope.

    """
    assigned: list[tuple[set[str], ast.AST]] = []
    for node in ast.walk(module):
        if isinstance(node, ast.Assign):
            targets = {
                n.id
                for t in node.targets
                for n in ast.walk(t)
                if isinstance(n, ast.Name)
            }
            assigned.append((targets, node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            assigned.append((
                {n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)},
                node.value,
            ))
    rooted = {"__file__"}
    grew = True
    while grew:
        grew = False
        for targets, value in assigned:
            if (
                targets - rooted
                and {n.id for n in ast.walk(value) if isinstance(n, ast.Name)} & rooted
            ):
                rooted |= targets
                grew = True
    return rooted


def lists_the_tree(source: str) -> bool:
    """Say whether a test file runs `git ls-files` in the canon's tree.

    A call whose argument list holds `"ls-files"` lists the tree when
    its `cwd=`, or the argument after `-C`, reads a rooted name. A
    listing in a scratch repository the row built reads no rooted name
    and is not judged.

    Returns:
        True when some call lists the canon's tree directly.

    """
    module = ast.parse(source)
    rooted = rooted_names(module)

    def reads_rooted(node: ast.AST) -> bool:
        return any(isinstance(n, ast.Name) and n.id in rooted for n in ast.walk(node))

    for call in (n for n in ast.walk(module) if isinstance(n, ast.Call)):
        for arg in call.args:
            if not isinstance(arg, (ast.List, ast.Tuple)):
                continue
            words = [e.value if isinstance(e, ast.Constant) else None for e in arg.elts]
            if "ls-files" not in words:
                continue
            where = [kw.value for kw in call.keywords if kw.arg == "cwd"]
            where += [arg.elts[i + 1] for i, w in enumerate(words[:-1]) if w == "-C"]
            if any(reads_rooted(w) for w in where):
                return True
    return False


# Excepted by name: the helper itself, and this file, which plants and
# names the spellings it looks for.
EXCEPTED = frozenset({"mise/hermetic.py", "mise/test_hermetic.py"})


class Env(unittest.TestCase):
    """What hermetic.env lets through."""

    def test_planted_variables_do_not_reach_a_fixture(self) -> None:
        """The job's inputs and git's own variables stay with the suite."""
        with mock.patch.dict(os.environ, PLANTED):
            env = hermetic.env("/fixture-home")
        self.assertEqual([], sorted(set(PLANTED) & set(env)))

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
        self.assertEqual(set(), set(env) - set(hermetic.INHERITED) - set_here)

    def test_a_rows_override_reaches_the_fixture(self) -> None:
        """A row states its own input, and that wins over the suite's."""
        with mock.patch.dict(os.environ, PLANTED):
            env = hermetic.env("/fixture-home", VERSION="2.0.0")
        self.assertEqual("2.0.0", env["VERSION"])

    def test_home_is_the_fixtures_or_the_suites(self) -> None:
        """A given home wins; without one the suite's HOME is kept."""
        with mock.patch.dict(os.environ, {"HOME": "/suite-home"}):
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
    """hermetic.tracked, in a checkout and in a copy of it (#1618)."""

    def setUp(self) -> None:
        """Plant a repository whose tree mutmut's way copies into `mutants/`."""
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        for name in ("a.py", "pkg/b.py", "pkg/c.sh", "vendor/v.py", "uncopied.py"):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text("x\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("mutants/\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", ".")
        # The copy: every tracked file but one, and one file only the
        # copy holds, the way mutmut writes mutants/.
        self.copy = self.root / "mutants"
        for name in ("a.py", "pkg/b.py", "pkg/c.sh", "vendor/v.py", ".gitignore"):
            (self.copy / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.root / name, self.copy / name)
        (self.copy / "mutmut-only.py").write_text("x\n", encoding="utf-8")

    def git(self, *args: str) -> None:
        """Run git in the planted repository."""
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run([GIT, *args], cwd=self.root, env=hermetic.env(), check=True)

    def test_a_gitignored_copy_lists_what_the_checkout_lists(self) -> None:
        """The row that fails before #1618: the copy's listing was empty."""
        specs = (":!vendor/**", "*.py", "*.sh")
        self.assertEqual(
            hermetic.tracked(self.root, *specs),
            ["a.py", "pkg/b.py", "pkg/c.sh", "uncopied.py"],
        )
        self.assertEqual(
            hermetic.tracked(self.copy, *specs), ["a.py", "pkg/b.py", "pkg/c.sh"]
        )

    def test_a_subdirectory_pathspec_is_anchored_at_the_top(self) -> None:
        """`pkg/*.py` from the copy names the copy's `pkg/b.py`."""
        self.assertEqual(hermetic.tracked(self.copy, "pkg/*.py"), ["pkg/b.py"])

    def test_an_empty_listing_is_red_by_name(self) -> None:
        """Files asked for and none listed is never a pass."""
        with self.assertRaises(RuntimeError) as raised:
            hermetic.tracked(self.copy, "*.rs")
        self.assertEqual(
            str(raised.exception),
            f"hermetic.tracked: git lists no tracked file under {self.copy}"
            " for ['*.rs']",
        )

    def test_magic_is_kept_and_anchored(self) -> None:
        """Every pathspec gains `top`, and keeps its own magic."""
        self.assertEqual(
            [hermetic.top(s) for s in ("*.py", ":!v/**", ":^v", ":(glob)**/x")],
            [":(top)*.py", ":(top,exclude)v/**", ":(top,exclude)v", ":(top,glob)**/x"],
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


class TheLaw(unittest.TestCase):
    """No test file copies the caller's environment (#1514 item 3)."""

    def test_no_test_file_copies_the_callers_environment(self) -> None:
        """Every fixture environment is built through hermetic.env."""
        offenders = [
            f"{path}:{line}"
            for path in listed_test_files()
            if path not in EXCEPTED
            for line in copies((CANON / path).read_text(encoding="utf-8"))
        ]
        self.assertEqual([], offenders)

    def test_no_test_builds_a_child_environment_by_hand(self) -> None:
        """Every child environment starts from hermetic.env (#1542)."""
        offenders = [
            f"{path}:{line}"
            for path in listed_test_files()
            if path not in EXCEPTED
            for line in Environments(
                (CANON / path).read_text(encoding="utf-8")
            ).built_by_hand()
        ]
        self.assertEqual([], offenders)

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
                self.assertEqual([], Environments(source).built_by_hand())

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
                self.assertEqual([2], Environments(source).built_by_hand())
        allowed = {
            "import as": "import subprocess as sp\nsp.run(a, env=hermetic.env())\n",
            "posix_spawn": "import os\nos.posix_spawn(p, a, hermetic.env())\n",
            "a local run": "def run(a, env):\n    pass\nrun(a, env={'A': '1'})\n",
        }
        for name, source in allowed.items():
            with self.subTest(name):
                self.assertEqual([], Environments(source).built_by_hand())

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
                self.assertEqual([], copies(source))

    def test_no_test_file_lists_the_canons_tree_directly(self) -> None:
        """Every listing of the canon's own tree goes through tracked (#1618)."""
        offenders = [
            path
            for path in listed_test_files()
            if lists_the_tree((CANON / path).read_text(encoding="utf-8"))
        ]
        self.assertEqual([], offenders)

    def test_the_law_sees_each_way_a_test_lists_the_tree(self) -> None:
        """Planted: `cwd=`, `-C` and a row-local root are found; a scratch is not."""
        head = "HERE = Path(__file__).parent\nCANON = HERE.parent\n"
        planted = {
            "cwd": head + 'run([GIT, "ls-files"], cwd=CANON)\n',
            "dash-C": head + 'run([GIT, "-C", str(CANON), "ls-files"])\n',
            "local": head + "def f():\n    root = HERE.parent\n"
            '    run([GIT, "ls-files", "*.py"], cwd=root)\n',
        }
        for name, source in planted.items():
            with self.subTest(name):
                self.assertTrue(lists_the_tree(source))
        scratch = head + 'tree = make()\nrun([GIT, "ls-files"], cwd=tree)\n'
        self.assertFalse(lists_the_tree(scratch))


if __name__ == "__main__":
    unittest.main()
