#!/usr/bin/env python3
"""The gate's install table holds every tool every gate task calls (#1410).

The gate installs only the belt tools `mise/gate-tools.toml` says the
tree needs. The cost of a list is that a lint starts calling a tool the
table does not name, or guards on a file the table's condition misses,
and the tool is absent where the lint runs. These rows move that into the
canon's own gate: for every task `ci` reaches, they read the task body
and every belt script it runs, find each belt tool it calls, find the
guard it skips on, and fail when the table lacks the tool or gives it a
condition narrower than the guard. The reading follows
`release/test_job_installs.py` (#1333), which holds release jobs to the
tools their steps call.

A call is a tool's command at command position (line start, after a
separator, behind `xargs`), with quoted literals blanked so a message
naming a tool is not a call; a Python helper calls what it hands
`subprocess` as a quoted list element, what any belt module it
imports does, and what a belt script it runs calls (#1911); any Python
helper calls python3.

A GUARD is what a task tests before its first "skipped" exit: the
pathspecs it hands `org_tracked` (mise/tracked.sh, #1986) or a `git
ls-files --error-unmatch` presence check (none at all is every file), `compgen
-G`, and `[[ -f | -d | -e ]]`. A tool called before that exit, or by a
task with no such exit, must be unconditional. Reading a guard can only
widen a condition, never narrow it: an unreadable guard is "always".

The helper's rows are trees the gate meets: Go only, Rust, pgrx, a
Dockerfile, the canon's own tree, an empty tree.

GIT ENVIRONMENT IS SCRUBBED (#857): git exports `GIT_DIR` and
`GIT_INDEX_FILE` to hooks in a linked worktree, and a planted tree that
inherits them is staged into the caller's real index when the suite runs
from pre-push. Every git here runs without any `GIT_*` variable.

stdlib `unittest`, matching the other mise/test_*.py.
"""

import ast
import contextlib
import dataclasses
import io
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import decoded
import gate_tools
import hermetic
import typed_std

CANON = Path(__file__).resolve().parent.parent
# A workflow step runs the helper outside mise, so its findings carry the
# script's own name (findings.task_name, #1934).
STEP = "gate_tools.py"
BELT = CANON / "mise"
GIT = hermetic.tool("git")


# What `ci` runs besides the `lint:*` wildcard (its `depends`), and the
# tasks the gate job's own steps run: the squash-message step's
# commits:check and the declared-break step's break:check (#1906).
ROOTS = (
    "coverage:check",
    "test:pgrx",
    "test:python-properties",
    "test:typescript-properties",
    "commits:check",
    "break:check",
)

# Tasks that run other tasks by a name computed at run time, and the
# prefixes they reach: lint:scaffold-assembly runs every fix:* and lint:*
# task inside a repository assembled from scaffold/. A new dynamic
# dispatch fails the coverage row until it is named here.
DISPATCH = {"lint:scaffold-assembly": ("lint:", "fix:"), "audit": ("audit:",)}

SCRIPT = re.compile(r"(?<![\w.-])([\w-]+\.(?:sh|py))\b")
SKIP = re.compile(r"\bexit 0\b")


# A task, as the belt's config decodes it; and every task by name.
Task = dict[str, object]
Tasks = dict[str, Task]


def belt() -> dict[str, object]:
    """Read the belt's config.

    Returns:
        mise/config.toml, decoded.

    """
    return decoded.load_toml((BELT / "config.toml").read_text("utf-8"))


def belt_tasks() -> Tasks:
    """Read the belt's tasks.

    Returns:
        Each task of mise/config.toml by name, decoded.

    """
    tasks = decoded.as_object(belt()["tasks"], "the belt's [tasks]")
    return {name: decoded.as_object(task, name) for name, task in tasks.items()}


def belt_tools() -> dict[str, object]:
    """Read the belt's tool pins.

    Returns:
        mise/config.toml's [tools], decoded.

    """
    return decoded.as_object(belt()["tools"], "the belt's [tools]")


def found(pattern: re.Pattern[str] | str, text: str) -> list[str]:
    """Read every match's first group, as `re.findall` gives one group.

    Returns:
        The group's text per match, "" where it took no part.

    """
    return [typed_std.group(m, 1) or "" for m in re.finditer(pattern, text)]


def body(task: Task) -> str:
    """Read a task's run text.

    Returns:
        The `run` string, a list's scripts joined by lines; a `{ task }`
        entry in the list is a reference, not a script (#1667).

    """
    run = task.get("run", "")
    if isinstance(run, str):
        return run
    entries = decoded.as_array(run, "a task's run")
    return "\n".join(entry for entry in entries if isinstance(entry, str))


def reached(name: str, tasks: Tasks) -> list[str]:
    """Name the tasks a task runs: its `depends`, and `mise run <name>`.

    Returns:
        Task names, wildcards expanded, dynamic dispatch by DISPATCH.

    """
    task = tasks[name]
    out: list[str] = []
    depends = decoded.as_array(task["depends"], name) if "depends" in task else []
    for dep in depends:
        dep_name = (
            dep
            if isinstance(dep, str)
            else decoded.as_str(decoded.as_object(dep, name).get("task", ""), name)
        )
        if dep_name.endswith("*"):
            out += [t for t in tasks if t.startswith(dep_name[:-1])]
        elif dep_name in tasks:
            out.append(dep_name)
    code = "\n".join(
        ln
        for ln in gate_tools.strip_literals(body(task)).splitlines()
        if not re.match(r"\s*#", ln)
    )
    out += [t for t in found(r"mise run ([\w:-]+)", code) if t in tasks]
    return out


def closure(names: list[str], tasks: Tasks) -> list[str]:
    """Name every task the given tasks reach, themselves included.

    Returns:
        Task names, each once, sorted.

    """
    todo = list(names)
    seen: set[str] = set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo += reached(name, tasks)
    return sorted(seen)


def gate_tasks(tasks: Tasks | None = None) -> list[str]:
    """Name every task the gate reaches.

    Returns:
        `lint:*`, ROOTS, and all they run by name, each once, sorted. A
        task a DISPATCH entry runs by a computed name runs in another
        tree, so `derivation` charges its calls to the dispatcher.

    """
    tasks = tasks if tasks is not None else belt_tasks()
    return closure([t for t in tasks if t.startswith("lint:")] + list(ROOTS), tasks)


def expand(text: str, seen: set[str]) -> list[tuple[str, str]]:
    """Inline every belt script a body runs, at the line that runs it.

    Returns:
        (kind, line) pairs in run order: kind "sh" for shell, or "py:<file>"
        for a line standing for a Python helper run there.

    """
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        if re.match(r"\s*#", line):
            continue
        out.append(("sh", line))
        for name in found(SCRIPT, gate_tools.strip_literals(line) + " " + line):
            path = BELT / name
            if name in seen or not path.is_file():
                continue
            seen.add(name)
            if name.endswith(".py"):
                out.append((f"py:{name}", "python3"))
                for script in spawned(name):
                    if script not in seen:
                        seen.add(script)
                        out += expand(_source(script), seen)
            else:
                out += expand(path.read_text("utf-8"), seen)
    return out


_SOURCES: dict[str, str] = {}


def _source(name: str) -> str:
    if name not in _SOURCES:
        _SOURCES[name] = (BELT / name).read_text("utf-8")
    return _SOURCES[name]


IMPORT = re.compile(r"^(?:import|from) (\w+)", re.MULTILINE)


def belt_imports(name: str) -> list[str]:
    """Name a Python helper and every belt module it imports, transitively.

    A helper calls what a belt module it imports spawns: embedded_shell.py
    reads `run:` values through workflow_yaml.py, which runs yq (#1846).

    Returns:
        File names in the belt, the helper's own first.

    """
    out = [name]
    todo = [name]
    while todo:
        for module in found(IMPORT, _source(todo.pop())):
            imported = f"{module}.py"
            if imported not in out and (BELT / imported).is_file():
                out.append(imported)
                todo.append(imported)
    return out


SPAWNS = re.compile(r"^(?:import|from) subprocess", re.MULTILINE)
BELT_SCRIPT = re.compile(r"(?<![\w.-])([\w-]+\.sh)\b")


def spawned(name: str) -> list[str]:
    """Name the belt shell scripts a Python helper runs (#1911).

    A helper, or a belt module it imports, that imports subprocess and
    names a belt `.sh` in a string of its code runs that script:
    citation_check.py runs citation.sh, whose findings call jq. Its
    docstrings are prose, never a run. A module that spawns nothing only
    names a script (testing_adoption.py's table).

    Returns:
        Script names in the belt, each once, in the order found.

    """
    out: list[str] = []
    for module in belt_imports(name):
        text = _source(module)
        if not SPAWNS.search(text):
            continue
        tree = ast.parse(text)
        docs = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in docs:
                continue
            for script in found(BELT_SCRIPT, node.value):
                if script not in out and (BELT / script).is_file():
                    out.append(script)
    return out


# The belt's venv check (#1788): `tool_python.py LABEL COMMAND` finds
# COMMAND on PATH and runs the venv's interpreter beside it, so the line
# uses COMMAND's tool though COMMAND is an argument there.
TOOL_PYTHON = "tool_python.py"


def venv_command(line: str) -> str | None:
    """Read the COMMAND a line hands `tool_python.py`.

    The line is split into shell words; the words after the script, up
    to the first shell operator, are its arguments. `--site` and its
    value go, in either form and wherever they stand; the second
    positional left is COMMAND.

    Returns:
        COMMAND, or None where the line runs no `tool_python.py` or
        hands it no COMMAND.

    """
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    words = list(lexer)
    starts = [i for i, w in enumerate(words) if w.endswith(TOOL_PYTHON)]
    if not starts:
        return None
    positionals: list[str] = []
    skip = False
    for word in words[starts[0] + 1 :]:
        if word and all(c in lexer.punctuation_chars for c in word):
            break
        if skip:
            skip = False
        elif word == "--site":
            skip = True
        elif not word.startswith("--site="):
            positionals.append(word)
    return positionals[1] if len(positionals) > 1 else None


def python_calls(text: str, commands: list[str]) -> bool:
    """Say whether Python source spawns one of the commands.

    A helper that imports no subprocess spawns nothing; one that does
    names its command as a quoted list element (`["shfmt", "-d", f]`).

    Returns:
        True when it does.

    """
    if not re.search(r"^(?:import|from) subprocess", text, re.MULTILINE):
        return False
    return any(re.search(rf'[\[(,]\s*"{re.escape(c)}"', text) for c in commands)


def first_call(lines: list[tuple[str, str]], cmds: list[str]) -> int | None:
    """Find where an expanded body first calls one of the commands.

    Returns:
        The index into `lines`, or None.

    """
    shell = "\n".join(text for _, text in lines)
    at = gate_tools.shell_calls(shell, cmds)
    for i, (kind, _) in enumerate(lines):
        if at is not None and i >= at:
            break
        if kind.startswith("py:"):
            helper = belt_imports(kind[3:])
            if any(python_calls(_source(f), cmds) for f in helper):
                return i
            if "python3" in cmds:
                return i
            if kind == f"py:{TOOL_PYTHON}" and venv_command(lines[i - 1][1]) in cmds:
                return i
    return at


def first_skip(lines: list[tuple[str, str]]) -> int | None:
    """Find a body's first skip: an `exit 0` a "skipped" line announces.

    Returns:
        The index into `lines`, or None.

    """
    for i, (_, text) in enumerate(lines):
        if SKIP.search(text) and any(
            "skipped" in t for _, t in lines[max(0, i - 3) : i + 1]
        ):
            return i
    return None


FUNCTION = re.compile(r"^(\w+)\(\)\s*\{")


def called_only(text: str) -> str:
    """Drop the shell functions a body defines but never calls.

    A sourced selector file (mise/action-files.sh, #2007) defines one
    function per selection; a task calls the one it means. Read whole,
    every function's pathspecs would join every caller's guard, so the
    workflow templates would become a condition of lint:zizmor. A
    function counts when it is named outside every definition, or inside
    one that counts.

    Returns:
        The text with each uncalled function's definition removed.

    """
    lines = text.split("\n")
    spans: dict[str, tuple[int, int]] = {}
    for start, line in enumerate(lines):
        match = FUNCTION.match(line)
        if match is None:
            continue
        stop = start
        if not line.rstrip().endswith("}"):
            ends = [n for n in range(start + 1, len(lines)) if lines[n].rstrip() == "}"]
            stop = ends[0] if ends else len(lines) - 1
        spans[typed_std.group(match, 1) or ""] = (start, stop)
    inside = {n for start, stop in spans.values() for n in range(start, stop + 1)}
    called = {
        name
        for name in spans
        for n, line in enumerate(lines)
        if n not in inside and re.search(rf"\b{name}\b", line)
    }
    grew = True
    while grew:
        bodies = [
            line
            for name in called
            for line in lines[spans[name][0] + 1 : spans[name][1] + 1]
        ]
        more = {
            name
            for name in spans
            if name not in called and any(re.search(rf"\b{name}\b", b) for b in bodies)
        }
        called |= more
        grew = bool(more)
    dropped = {
        n
        for name, (start, stop) in spans.items()
        if name not in called
        for n in range(start, stop + 1)
    }
    return "\n".join(line for n, line in enumerate(lines) if n not in dropped)


def guard_paths(lines: list[tuple[str, str]]) -> set[str]:
    """Read the pathspecs a body tests before it may skip.

    Returns:
        The literal pathspecs; "*" where `org_tracked` lists every file.

    """
    paths: set[str] = set()
    # A `\`-continued command is one command: lint:biome's selection runs
    # onto a second line, and reading line by line lost `*.json` and the
    # rest of it (#1410: biome disabled on stele, which tracks JSON only).
    joined = re.sub(r"\\\n", " ", "\n".join(text for _, text in lines))
    joined = called_only(joined)
    for text in joined.splitlines():
        # `org_tracked() {` is the producer's definition, not a call, and
        # `org_tracked_read`'s first word is the array it fills.
        for seg in found(
            r"(?:\bgit ls-files|\borg_tracked_read\s+\S+|\borg_tracked\b(?!\(\)))"
            r"([^|)>;&]*)",
            text,
        ):
            words = [
                typed_std.group(m, 1)
                or typed_std.group(m, 2)
                or typed_std.group(m, 3)
                or ""
                for m in re.finditer(r'"([^"]*)"|\'([^\']*)\'|(\S+)', seg)
            ]
            dynamic = any("$" in w for w in words)
            kept = [w for w in words if not w.startswith(("-", ":!", "$")) and w]
            kept = [w for w in kept if "$" not in w and w != "\\"]
            every: list[str] = ["*"]
            paths.update(kept or ([] if dynamic else every))
        paths.update(found(r'compgen -G "([^"$]+)"', text))
        paths.update(found(r"-[fde] ([\w./-]+)", text))
    return paths


def derivation(tasks: Tasks | None = None) -> dict[str, list[tuple[str, str]]]:
    """Find, for every belt tool, each gate task that calls it and its guard.

    Returns:
        Belt key to (task, guard) pairs; the guard is "always" where the
        call comes before any skip, else its pathspecs joined by spaces.

    """
    tasks = tasks if tasks is not None else belt_tasks()
    table = gate_tools.load_table()
    tools = belt_tools()
    out: dict[str, list[tuple[str, str]]] = {key: [] for key in tools}
    gate = gate_tasks(tasks)
    for name in gate:
        lines = expand(body(tasks[name]), set())
        skip = first_skip(lines)
        guard = guard_paths(lines[: skip + 1]) if skip is not None else set()
        inner: list[list[tuple[str, str]]] = []
        if name in DISPATCH:
            prefixes = DISPATCH[name]
            dispatched = closure([t for t in tasks if t.startswith(prefixes)], tasks)
            inner = [expand(body(tasks[t]), set()) for t in dispatched]
        for key in tools:
            cmds = gate_tools.commands(key, table.get(key, gate_tools.Row()))
            at = first_call(lines, cmds)
            if at is None and any(first_call(x, cmds) is not None for x in inner):
                at = len(lines)
            if at is None:
                continue
            if skip is None or at <= skip or not guard:
                out[key].append((name, "always"))
            else:
                out[key].append((name, " ".join(sorted(guard))))
    return out


def drift(
    table: dict[str, gate_tools.Row], found: dict[str, list[tuple[str, str]]]
) -> list[str]:
    """Hold the table to the derivation.

    Returns:
        One line per tool a gate task calls that the table lacks, per
        task the row does not record, and per guard its condition misses.

    """
    out = []
    for key, uses in sorted(found.items()):
        row = table.get(key)
        if row is None:
            out += [
                f"{task} calls {key}; mise/gate-tools.toml has no row"
                for task, _ in uses
            ]
            continue
        when = row.when
        for task, guard in uses:
            if task not in row.tasks:
                out.append(f"{task} calls {key}; its row does not list the task")
            if when is None:
                continue
            if guard == "always":
                out.append(
                    f"{task} calls {key} before any guard; its row must carry no `when`"
                )
                continue
            missing = sorted(set(guard.split()) - set(when))
            if missing:
                out.append(
                    f"{task} runs {key} on a tree tracking {' '.join(missing)};"
                    " its row's `when` does not install it there"
                )
    for key, row in sorted(table.items()):
        stale = sorted(set(row.tasks) - {t for t, _ in found.get(key, [])})
        out += [f"{key}: its row lists {t}, which does not call it" for t in stale]
        if row.when == () and found.get(key):
            out.append(f"{key}: its row says no gate task calls it")
    return out


class Table(unittest.TestCase):
    """The table covers every tool every gate task calls."""

    def test_table_names_every_belt_tool(self) -> None:
        """One row per [tools] key, no more, no fewer."""
        tools = sorted(belt_tools())
        rows = sorted(gate_tools.load_table())
        self.assertEqual(tools, rows)

    def test_table_holds_the_tasks(self) -> None:
        """The real table against the real task bodies."""
        none: list[str] = []
        self.assertEqual(none, drift(gate_tools.load_table(), derivation()))

    def test_every_dynamic_dispatch_is_named(self) -> None:
        """A task that runs a computed task name is in DISPATCH."""
        tasks = belt_tasks()
        dynamic = sorted(
            t for t in tasks if re.search(r'mise run "\$\{?\w+\}?"', body(tasks[t]))
        )
        named = sorted(DISPATCH)
        self.assertEqual(named, dynamic)

    def test_a_new_tool_call_is_named(self) -> None:
        """Planted: a lint starts calling a tool the table has no row for."""
        table = gate_tools.load_table()
        del table["aqua:hadolint/hadolint"]
        self.assertIn(
            (
                "lint:hadolint calls aqua:hadolint/hadolint;"
                " mise/gate-tools.toml has no row"
            ),
            drift(table, derivation()),
        )

    def test_a_narrow_condition_is_named(self) -> None:
        """Planted: hadolint's row forgets the `*.Dockerfile` form."""
        table = gate_tools.load_table()
        row = table["aqua:hadolint/hadolint"]
        when = tuple(w for w in row.when or () if w != "*.Dockerfile")
        table["aqua:hadolint/hadolint"] = dataclasses.replace(row, when=when)
        self.assertIn(
            (
                "lint:hadolint runs aqua:hadolint/hadolint on a tree tracking"
                " *.Dockerfile; its row's `when` does not install it there"
            ),
            drift(table, derivation()),
        )

    def test_a_call_before_the_guard_is_named(self) -> None:
        """Planted: a task calls a conditional tool before it can skip."""
        tasks = belt_tasks()
        tasks["lint:hadolint"] = {
            "run": "hadolint --version\n" + body(tasks["lint:hadolint"])
        }
        self.assertIn(
            (
                "lint:hadolint calls aqua:hadolint/hadolint before any guard;"
                " its row must carry no `when`"
            ),
            drift(gate_tools.load_table(), derivation(tasks)),
        )

    def test_a_planted_task_call_is_named(self) -> None:
        """Planted: a new lint calls golangci-lint behind no guard."""
        tasks = belt_tasks()
        tasks["lint:planted"] = {"run": "golangci-lint run ./..."}
        self.assertIn(
            (
                "lint:planted calls aqua:golangci/golangci-lint; its row does"
                " not list the task"
            ),
            drift(gate_tools.load_table(), derivation(tasks)),
        )

    def test_a_message_is_not_a_call(self) -> None:
        """A tool named in a quoted message or a comment is not called."""
        lines = expand(
            '# hadolint would\necho "run hadolint first"\nbad="${bad} (hadolint)"\n'
            "hadolint=$(state hadolint)\nif [[ ${hadolint} == x ]]; then :; fi\n",
            set(),
        )
        self.assertIsNone(first_call(lines, ["hadolint"]))

    def test_a_continued_selection_is_read_whole(self) -> None:
        """A backslash-continued `org_tracked` is one guard (#1410, lint:biome)."""
        lines = expand(
            "files=() && while IFS= read -r -d '' f; do files+=(\"${f}\"); done"
            ' < <(org_tracked ":!vendor/**" "*.js" \\\n  "*.json" "*.css")\n'
            '[[ -n ${files[0]+set} ]] || {\n  echo "x: skipped"\n  exit 0\n}\n',
            set(),
        )
        skip = first_skip(lines)
        if skip is None:
            self.fail("the planted body never skips")
        selected: set[str] = {"*.js", "*.json", "*.css"}
        self.assertEqual(selected, guard_paths(lines[: skip + 1]))

    def test_a_sourced_selector_guards_with_its_own_pathspec(self) -> None:
        """Only the function a body calls joins its guard (#2007).

        mise/action-files.sh defines three selectors; a task calling
        workflow_files is guarded on the workflows alone, and one calling
        workflow_template_files on the templates alone. Read whole, every
        selector's pathspecs would join every caller's guard.
        """
        skip = '[[ -n ${f[0]+set} ]] || {\n  echo "x: skipped"\n  exit 0\n}\n'
        source = '. "${ORG_BELT_DIR}/action-files.sh"\n'
        for call, want in (
            ("workflow_files", {".github/workflows/*.y*ml"}),
            ("workflow_template_files", {"workflow-templates/*.y*ml"}),
            (
                "action_files",
                {
                    ".github/workflows/*.y*ml",
                    "action.yml",
                    "action.yaml",
                    "*/action.yml",
                    "*/action.yaml",
                },
            ),
        ):
            with self.subTest(call=call):
                lines = expand(f"{source}f=() && {call} f\n{skip}", set())
                at = first_skip(lines)
                if at is None:
                    self.fail("the planted body never skips")
                self.assertEqual(want, guard_paths(lines[: at + 1]))

    def test_an_uncalled_function_is_dropped_and_a_nested_one_kept(self) -> None:
        """A function counts when called, or called by one that is."""
        text = (
            'a() { org_tracked_read "$1" one; }\n'
            'b() {\n  a "$1"\n}\n'
            'c() { org_tracked_read "$1" three; }\n'
            "f=() && b f\n"
        )
        self.assertEqual(
            called_only(text),
            'a() { org_tracked_read "$1" one; }\nb() {\n  a "$1"\n}\nf=() && b f\n',
        )

    def test_xargs_and_cargo_subcommands_are_calls(self) -> None:
        """`xargs -0 tool` and `cargo deny` are calls."""
        for text, cmd in (
            ('xargs -0 hadolint "$f"', "hadolint"),
            ("cargo deny check bans", "cargo-deny"),
            ("out=$(jq -r .x f)", "jq"),
            ("FOO=1 stele verify", "stele"),
        ):
            with self.subTest(text=text):
                self.assertEqual(0, first_call(expand(text, set()), [cmd]))

    def test_a_helper_calls_what_its_belt_import_spawns(self) -> None:
        """embedded_shell.py runs yq through workflow_yaml.py (#1846).

        Read alone, the helper names no yq; the table's yq row would be
        stale and a consumer's gate would lack the parser.
        """
        text = '"${ORG_PYTHON}" "${ORG_BELT_DIR}/embedded_shell.py" ${files}'
        self.assertIn("workflow_yaml.py", belt_imports("embedded_shell.py"))
        self.assertEqual(1, first_call(expand(text, set()), ["yq"]))

    def test_a_helper_calls_what_a_belt_script_it_runs_calls(self) -> None:
        """Planted: a helper runs a belt script; the script's tool is called.

        Read alone, the helper spawns no tool, and the table's row for
        the script's tool would be stale (#1911: citation_check.py runs
        citation.sh, whose findings call jq). A script only named in a
        docstring, or by a module that spawns nothing, is not run.
        """
        with tempfile.TemporaryDirectory() as tmp:
            belt_dir = Path(tmp)
            (belt_dir / "planted.sh").write_text("hadolint Dockerfile\n", "utf-8")
            (belt_dir / "named.sh").write_text("shfmt -d x\n", "utf-8")
            (belt_dir / "runs.py").write_text(
                '"""Runs planted.sh, never named.sh."""\n'
                "import subprocess\n"
                'subprocess.run(["bash", "planted.sh"], check=True)\n',
                "utf-8",
            )
            (belt_dir / "names.py").write_text('SCRIPT = "named.sh"\n', "utf-8")
            here = sys.modules[__name__]
            with (
                mock.patch.object(here, "BELT", belt_dir),
                mock.patch.dict(_SOURCES, clear=True),
            ):
                self.assertEqual("planted.sh", " ".join(spawned("runs.py")))
                self.assertEqual("", " ".join(spawned("names.py")))
                runs = expand('"${ORG_PYTHON}" "${ORG_BELT_DIR}/runs.py"', set())
                self.assertEqual(2, first_call(runs, ["hadolint"]))
                self.assertIsNone(first_call(runs, ["shfmt"]))
                names = expand('"${ORG_PYTHON}" "${ORG_BELT_DIR}/names.py"', set())
                self.assertIsNone(first_call(names, ["shfmt"]))

    def test_the_belt_helpers_that_run_belt_scripts(self) -> None:
        """The belt's real helpers: run, and only named.

        rust_lint.py runs the clippy ladder and the pgrx provisioning;
        testing_adoption.py names the mutation scripts and runs none.
        """
        ran = " ".join(sorted(spawned("rust_lint.py")))
        self.assertEqual("clippy-ladder.sh pgrx-env.sh", ran)
        self.assertEqual("", " ".join(spawned("testing_adoption.py")))

    def test_the_venv_check_uses_its_command(self) -> None:
        """`tool_python.py LABEL COMMAND` uses COMMAND's tool (#1788)."""
        run = '"${ORG_PYTHON}" "${belt}/tool_python.py"'
        for text, used in (
            (f'v=$({run} "${{label}}" hypothesis) || exit 1', True),
            (f"{run} x hypothesis --site hypothesis > /dev/null || exit 1", True),
            (f"{run} --site hypothesis x hypothesis", True),
            (f"{run} --site=hypothesis x hypothesis", True),
            (f"{run} x coverage --site hypothesis", False),
            (f"{run} --site hypothesis x coverage", False),
            (f"{run} --site=x y hypothesis", True),
            (f"{run} x coverage", False),
        ):
            with self.subTest(text=text):
                at = first_call(expand(text, set()), ["hypothesis"])
                self.assertEqual(1 if used else None, at)
        # A shell operator ends the arguments: the words after it are
        # another command's, not a COMMAND.
        self.assertIsNone(venv_command(f"v=$({run} x) || y hypothesis"))

    def test_jq_the_gate_calls_is_a_pinned_belt_tool(self) -> None:
        """Jq was a gate dependency that was never pinned (#390).

        The runner image happens to carry a jq, so nothing reddened: a
        call to a command no [tools] key provides is invisible to the
        derivation above, which only looks for the tools the belt has.
        So the callers are found by the command's own name.
        """
        tasks = belt_tasks()
        callers = [
            name
            for name in gate_tasks(tasks)
            if first_call(expand(body(tasks[name]), set()), ["jq"]) is not None
        ]
        none: list[str] = []
        self.assertNotEqual(none, callers)
        table = gate_tools.load_table()
        providers = [
            key
            for key in belt_tools()
            if "jq" in gate_tools.commands(key, table.get(key, gate_tools.Row()))
        ]
        jq: list[str] = ["jq"]
        self.assertEqual(jq, providers, f"gate tasks call jq: {callers}")


def clean_env() -> dict[str, str]:
    """Copy the environment without git's repository variables.

    Returns:
        os.environ minus every `GIT_*` name (#857).

    """
    return hermetic.env()


def git_tree(files: dict[str, str]) -> Path:
    """Plant a git tree with the files tracked.

    Returns:
        The tree's root, removed at process exit by tempfile.

    """
    root = Path(tempfile.mkdtemp(prefix="gate-tools-"))
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, "utf-8")
    subprocess.run([GIT, "-C", str(root), "init", "-q"], check=True, env=clean_env())
    subprocess.run([GIT, "-C", str(root), "add", "-A"], check=True, env=clean_env())
    return root


def install(root: Path) -> set[str]:
    """Run the helper against a tree.

    Returns:
        The belt keys it names.

    """
    tracked = gate_tools.tracked_files(root)
    reasons = gate_tools.needed(
        gate_tools.load_table(),
        tracked,
        gate_tools.caller_text(root, tracked),
        gate_tools.pinned_names(root),
    )
    return {k for k, why in reasons.items() if why.startswith("needed")}


def always() -> set[str]:
    """Name the tools every tree gets.

    Returns:
        Each key whose row carries no `when`, and what they install.

    """
    return install(git_tree({}))


WORKFLOW = {".github/workflows/ci.yml": "on: push\n", "README.md": "# x\n"}


class Helper(unittest.TestCase):
    """The helper's list for the trees the gate meets."""

    def test_an_empty_tree_gets_the_unconditional_tools_only(self) -> None:
        """Nothing tracked: no conditional row fires."""
        table = gate_tools.load_table()
        unconditional = {k for k, r in table.items() if r.when is None}
        self.assertTrue(unconditional <= always())
        self.assertNotIn("aqua:hadolint/hadolint", always())
        self.assertNotIn("aqua:golangci/golangci-lint", always())

    def test_a_go_tree(self) -> None:
        """go.mod brings golangci-lint and nothing Rust."""
        got = install(git_tree({**WORKFLOW, "go.mod": "module x\n"}))
        self.assertIn("aqua:golangci/golangci-lint", got)
        self.assertNotIn("github:bnjbvr/cargo-machete", got)
        self.assertNotIn("aqua:EmbarkStudios/cargo-deny", got)

    def test_a_rust_tree(self) -> None:
        """Cargo.toml and Cargo.lock bring the Rust lints' tools."""
        got = install(
            git_tree({**WORKFLOW, "Cargo.toml": "[package]\n", "Cargo.lock": ""})
        )
        self.assertIn("github:bnjbvr/cargo-machete", got)
        self.assertIn("aqua:EmbarkStudios/cargo-deny", got)
        self.assertNotIn("aqua:golangci/golangci-lint", got)

    def test_a_pgrx_tree(self) -> None:
        """A pgrx crate with SQL brings the SQL linter."""
        got = install(
            git_tree({
                **WORKFLOW,
                "Cargo.toml": "[workspace]\n",
                "crates/x-pg/Cargo.toml": "[package]\n",
                "crates/x-pg/sql/x--1.0.sql": "select 1;\n",
            })
        )
        self.assertIn("pipx:sqlfluff", got)
        self.assertIn("aqua:astral-sh/uv", got)
        self.assertIn("github:bnjbvr/cargo-machete", got)

    def test_a_json_only_tree_needs_biome(self) -> None:
        """lint:biome lints JSON: renovate.json alone brings biome (#1410)."""
        self.assertIn(
            "aqua:biomejs/biome", install(git_tree({"renovate.json": "{}\n"}))
        )

    def test_a_dockerfile_tree(self) -> None:
        """Each Dockerfile form brings hadolint."""
        for name in (
            "Dockerfile",
            "docker/Dockerfile",
            "x.Dockerfile",
            "Dockerfile.ci",
        ):
            with self.subTest(name=name):
                got = install(git_tree({**WORKFLOW, name: "FROM x\n"}))
                self.assertIn("aqua:hadolint/hadolint", got)

    def test_the_canon_tree(self) -> None:
        """The canon runs lint:scaffold-assembly, which runs every task."""
        table = gate_tools.load_table()
        reached = {k for k, r in table.items() if r.when != ()}
        self.assertEqual(reached, install(CANON) & reached)

    def test_a_caller_task_brings_a_belt_tool(self) -> None:
        """A repository's own task calling hadolint gets it installed."""
        tree = {
            **WORKFLOW,
            "mise.toml": '[tasks.test]\nrun = "scripts/t.sh"\n',
            "scripts/t.sh": "#!/bin/sh\nhadolint --version\n",
        }
        self.assertIn("aqua:hadolint/hadolint", install(git_tree(tree)))
        tree["scripts/t.sh"] = "#!/bin/sh\necho hadolint\n"
        self.assertNotIn("aqua:hadolint/hadolint", install(git_tree(tree)))

    def test_a_caller_pin_is_never_disabled(self) -> None:
        """Mise disables by name: the repository's own pin would go too."""
        tree = {**WORKFLOW, "mise.toml": '[tools]\n"github:hadolint/hadolint" = "1"\n'}
        self.assertIn("aqua:hadolint/hadolint", install(git_tree(tree)))
        tree["mise.toml"] = '[tools]\ngo = "1.27.4"\n'
        self.assertNotIn("aqua:hadolint/hadolint", install(git_tree(tree)))

    def test_the_canon_pins_no_tool_of_its_own(self) -> None:
        """The canon's own mise.toml declares no [tools] (#1733).

        A tool the canon's own config pinned is never disabled, so the
        gate would install it for this repository alone; the belt pins
        no language, and the canon pins none either.
        """
        config = decoded.load_toml((CANON / "mise.toml").read_text("utf-8"))
        none: dict[str, object] = {}
        self.assertEqual(none, config.get("tools", none), "the canon's mise.toml pins")

    def test_the_disable_env(self) -> None:
        """The left-out keys, sorted, and their digest; nothing for none."""
        self.assertEqual("", gate_tools.disable_env([]))
        out = gate_tools.disable_env(["zizmor", "aqua:hadolint/hadolint"])
        lines = out.splitlines()
        self.assertEqual("MISE_DISABLE_TOOLS=aqua:hadolint/hadolint,zizmor", lines[0])
        self.assertRegex(lines[1], r"^ORG_GATE_BELT_DIGEST=[0-9a-f]{12}$")
        self.assertEqual(
            out, gate_tools.disable_env(["aqua:hadolint/hadolint", "zizmor"])
        )

    def test_the_disable_mode(self) -> None:
        """A Go tree leaves hadolint out; the canon leaves nothing out."""
        for root, left_out in (
            (git_tree({**WORKFLOW, "go.mod": "module x\n"}), True),
            (CANON, False),
        ):
            with self.subTest(root=root), tempfile.TemporaryDirectory() as d:
                env_file = Path(d) / "env"
                env_file.write_text("")
                result = subprocess.run(
                    [
                        sys.executable,
                        str(BELT / "gate_tools.py"),
                        "--root",
                        str(root),
                        "disable",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    env={**clean_env(), "GITHUB_ENV": str(env_file)},
                )
                self.assertEqual(0, result.returncode, result.stderr)
                written = env_file.read_text()
                self.assertIn(
                    "gate-tools: aqua:golangci/golangci-lint: needed", result.stdout
                )
                if left_out:
                    self.assertIn("aqua:hadolint/hadolint", written)
                    self.assertNotIn("golangci", written)
                else:
                    self.assertEqual("", written)

    def test_the_helper_refuses_a_table_out_of_step(self) -> None:
        """A belt key with no row is refused by name, not left out."""
        with tempfile.TemporaryDirectory() as d:
            text = (BELT / "gate-tools.toml").read_text("utf-8")
            stub = text.replace('[tools."aqua:hadolint/hadolint"]', "[x]")
            belt = hermetic.belt(d, stubs={"gate-tools.toml": stub})
            table = belt / "gate-tools.toml"
            result = subprocess.run(
                [
                    sys.executable,
                    str(BELT / "gate_tools.py"),
                    "--table",
                    str(table),
                    "list",
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=CANON,
                env=hermetic.env(),
            )
        self.assertEqual(1, result.returncode)
        self.assertIn(
            f"::error::{STEP}: aqua:hadolint/hadolint is in one of", result.stderr
        )


class Refusals(unittest.TestCase):
    """A table or belt config of the wrong shape is refused by name."""

    def run_main(self, table_text: str, config_text: str) -> tuple[int, str, Path]:
        """Run the helper in-process over a table and config written here.

        Returns:
            Exit status, stderr and the table's path.

        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        table = Path(tmp.name) / "gate-tools.toml"
        table.write_text(table_text, encoding="utf-8")
        (Path(tmp.name) / "config.toml").write_text(config_text, encoding="utf-8")
        err = io.StringIO()
        with (
            hermetic.as_task(""),
            mock.patch.object(sys, "argv", [STEP]),
            contextlib.redirect_stderr(err),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            status = gate_tools.main(["--table", str(table), "list"])
        return status, err.getvalue(), table

    def test_wrong_shapes_in_the_table(self) -> None:
        """Each shape the decoder refuses, with the field it names."""
        rows = [
            ("[tools\n", "Expected ']'"),
            ("tools = 1\n", "[tools] is not an object"),
            ("[tools]\nx = 1\n", "tools.x is not an object"),
            ('[tools.x]\ntasks = "lint:a"\n', "tools.x.tasks is not a list of strings"),
            ("[tools.x]\nwhen = [1]\n", "tools.x.when is not a list of strings"),
            ("[tools.x]\nfor = {}\n", "tools.x.for is not a list of strings"),
            ('[tools.x]\nrun = [""," ", 2]\n', "tools.x.run is not a list of strings"),
        ]
        for text, message in rows:
            with self.subTest(text=text):
                status, err, table = self.run_main(text, "[tools]\n")
                self.assertEqual(1, status)
                self.assertTrue(err.startswith(f"::error::{STEP}: {table}: "), msg=err)
                self.assertIn(message, err)

    def test_belt_tools_not_a_table(self) -> None:
        """The belt config's [tools] is read through the same decoder."""
        status, err, table = self.run_main("[tools]\n", "tools = []\n")
        self.assertEqual(1, status)
        self.assertEqual(
            f"::error::{STEP}: {table.parent / 'config.toml'}: [tools] "
            "is not an object\n",
            err,
        )

    def test_a_well_formed_row_decodes(self) -> None:
        """Absent fields take their defaults; `for` is the row's needs."""
        with tempfile.TemporaryDirectory() as d:
            table = Path(d) / "t.toml"
            table.write_text(
                '[tools.a]\ntasks = ["lint:a"]\nwhen = []\nfor = ["b"]\n'
                '[tools.b]\nrun = ["b1"]\n',
                encoding="utf-8",
            )
            got = gate_tools.load_table(table)
        rows: dict[str, gate_tools.Row] = {
            "a": gate_tools.Row(tasks=("lint:a",), when=(), needs=("b",)),
            "b": gate_tools.Row(run=("b1",)),
        }
        self.assertEqual(rows, got)
        b1: list[str] = ["b1"]
        self.assertEqual(b1, gate_tools.commands("b", got["b"]))
        a: list[str] = ["a"]
        self.assertEqual(a, gate_tools.commands("a", got["a"]))


if __name__ == "__main__":
    unittest.main()
