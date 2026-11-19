#!/usr/bin/env python3
"""The law: a helper's logic is tested in the process that runs the tests (#1673).

mutmut runs a mutant only when the function's `__module__` equals the
module in the mutant's name (mutmut 3.8.1, `trampoline.py:109`), and a
script's functions are in `__main__`. So a function whose tests only run
its helper as a script in a child process never runs a mutant, whatever
the child's environment carries, and its mutants come out "no tests":
mutmut keeps its statistics in the process that runs the tests.
Coverage and the type checker see the same gap. Measured 2026-11-20 on
a two-file project (`answer(n): return n + 1`, mutant `n - 1`):

    run as a script, MUTANT_UNDER_TEST unset           prints 2
    run as a script, the mutant's own name             prints 2, the original
    run as a script, `__main__.` + the mutant's name   prints 0
    imports the module, the mutant's own name          prints 0, the mutant

THE UNIT IS THE FUNCTION, AND REACH IS FOLLOWED (amendment on #1673). A
helper is every tracked `.py` outside `vendor/` that is not a
`test_*.py` or `prop_*.py`, wherever it lives (the law names no
directory: a repository's helpers in `tools/` are judged like the
canon's in `mise/`); a test module is every tracked one that is. A
helper's units are its top-level functions and its classes' methods
(`Class.method`). A unit is REACHED when:

- a test module calls it in-process: the call's target, spelled by its
  module's real name however the module was imported
  (`call_names.qualifier`, the one resolver), is `<helper>.<function>`
  or `<helper>.<Class>…`; a test module that imports the helper reaches
  its methods by name wherever it spells `.method`, since an instance's
  class is not in the syntax tree;
- a reached unit of the same helper names it, by a call or a bare
  reference (a callback counts): a function by its name, a method by
  `.method`, a class's dunder methods by the class's name;
- the helper's module-level code names it, when a test imports the
  helper (importing runs that code in the test's process). The
  `if __name__ == "__main__":` block is not module-level code here: a
  function only it names is reached only if a test calls it; the block
  itself is the script contract's one end-to-end row.

A helper is owed while any of its units is unreached. The unreached
units are listed in `security/helper-import-debt.txt` as
`<path>:<unit>`, each with the issue that converts its tests: the shape
of the task-shell debt (#1544). Red, by name, on an unreached unit not
listed, and on an entry whose unit is now reached or is gone.
`--write` (fix:in-process) generates the file when it does not exist,
and otherwise only removes stale entries: it never adds one.

Stdlib only, and the helpers beside it.
"""

import ast
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import call_names

DEBT = "security/helper-import-debt.txt"
NOTE = "# #1673 unassigned"
LABEL = "lint:in-process"
FIX = "mise run fix:in-process"
HEADER = f"""\
# Helper import debt (#1673): every function or method of a tracked
# Python helper (outside vendor/, not a test_*.py or prop_*.py) that no
# test reaches in-process, as the tree had it when lint:in-process
# landed. A function reached only through a script in a child process is
# one mutation cannot judge (mutmut runs a mutant only in the module it was
# named for, never in a script's `__main__`), and coverage and the type
# checker cannot see it. mise/in_process.py says what reaches a function.
#
# The list only shrinks. lint:in-process is red on an unreached function
# that is not listed, and on an entry whose function is now reached or is
# gone; fix:in-process removes a stale entry. It writes entries only when
# this file does not exist: a new helper is tested in-process from its
# first commit (CONTRIBUTING.md, "Testing standard").
#
# One entry per line, two tab-separated fields: `<path>:<function>` (a
# method as `<Class>.<method>`) and a `#` note naming the issue that
# converts its tests. The issue that converts them deletes the entries in
# the same pull request. An entry no issue names yet carries `{NOTE}`.
#
# Generated from the tree by the lint's own reader (mise/in_process.py),
# never typed.
"""

Def = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass
class Helper:
    """One helper module's units and what its import runs."""

    path: str
    units: dict[str, Def] = field(default_factory=dict)
    classes: dict[str, list[str]] = field(default_factory=dict)
    on_import: set[str] = field(default_factory=set)


def is_test(path: str) -> bool:
    """Say whether a tracked file is a test module.

    Returns:
        True for `test_*.py` and `prop_*.py`.

    """
    return PurePosixPath(path).name.startswith(("test_", "prop_"))


def module(path: str) -> str:
    """Name the module a helper is imported as: its stem.

    Each directory is put on `sys.path` and its helpers imported by
    name (#1634), so the stem is the name every test spells.

    Returns:
        The stem.

    """
    return PurePosixPath(path).stem


def names_in(node: ast.AST) -> set[str]:
    """Name every name and attribute a piece of code spells.

    Returns:
        Each `Name`'s id and each `Attribute`'s attr.

    """
    found: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            found.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            found.add(sub.attr)
    return found


def is_main_block(node: ast.stmt) -> bool:
    """Say whether a statement is `if __name__ == "__main__":`.

    Returns:
        True for that test, in either order.

    """
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    sides = [node.test.left, *node.test.comparators]
    named = any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides)
    main = any(isinstance(s, ast.Constant) and s.value == "__main__" for s in sides)
    return named and main


def read_helper(path: str, source: str) -> Helper:
    """Read a helper's units and the names its module-level code spells.

    Returns:
        The helper.

    """
    helper = Helper(path)
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            helper.units[node.name] = node
            for part in [*node.decorator_list, node.args]:
                helper.on_import |= names_in(part)
        elif isinstance(node, ast.ClassDef):
            methods = helper.classes.setdefault(node.name, [])
            for part in node.body:
                if isinstance(part, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    unit = f"{node.name}.{part.name}"
                    helper.units[unit] = part
                    methods.append(unit)
                    for bit in [*part.decorator_list, part.args]:
                        helper.on_import |= names_in(bit)
                else:
                    helper.on_import |= names_in(part)
            for part in [*node.bases, *node.keywords, *node.decorator_list]:
                helper.on_import |= names_in(part)
        elif not is_main_block(node):
            helper.on_import |= names_in(node)
    return helper


def units_named(helper: Helper, name: str) -> set[str]:
    """Map one spelled name to the helper's units it reaches.

    Returns:
        The function of that name, every method of that name, and,
        for a class's name, its dunder methods.

    """
    found = {name} if name in helper.units else set()
    found |= {u for u in helper.units if "." in u and u.partition(".")[2] == name}
    for unit in helper.classes.get(name, []):
        method = unit.partition(".")[2]
        if method.startswith("__") and method.endswith("__"):
            found.add(unit)
    return found


def reach(helper: Helper, seeds: set[str]) -> set[str]:
    """Follow every name a reached unit spells, from the seed names.

    Returns:
        The reached units.

    """
    reached: set[str] = set()
    names = set(seeds)
    seen: set[str] = set()
    while names - seen:
        name = (names - seen).pop()
        seen.add(name)
        for unit in units_named(helper, name) - reached:
            reached.add(unit)
            names |= names_in(helper.units[unit])
    return reached


def test_seeds(source: str, stems: set[str]) -> tuple[dict[str, set[str]], set[str]]:
    """Read what one test module reaches in each helper it calls.

    Returns:
        Per helper stem, the names its in-process calls spell after the
        module's name; and the stems the file imports, whose methods its
        `.method` spellings reach.

    """
    tree = ast.parse(source)
    bound = call_names.aliases(tree)
    qualify = call_names.qualifier(tree)
    imported = {v.partition(".")[0] for v in bound.values()} & stems
    seeds: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        head = node.func
        while isinstance(head, ast.Attribute):
            head = head.value
        if not isinstance(head, ast.Name) or head.id not in bound:
            continue
        spelled = qualify(node.func)
        if spelled is None:
            continue
        stem, _, rest = spelled.partition(".")
        if stem in stems and rest:
            seeds.setdefault(stem, set()).update(rest.split("."))
    return seeds, imported


def unreached(sources: dict[str, str]) -> list[str]:
    """Name every helper unit no test reaches in-process.

    Returns:
        `<path>:<unit>` for each, sorted.

    """
    helpers = {
        module(p): read_helper(p, text) for p, text in sources.items() if not is_test(p)
    }
    seeds: dict[str, set[str]] = {stem: set() for stem in helpers}
    imported: set[str] = set()
    attrs: dict[str, set[str]] = {stem: set() for stem in helpers}
    for path, text in sources.items():
        if not is_test(path):
            continue
        called, imports = test_seeds(text, set(helpers))
        for stem, names in called.items():
            seeds[stem] |= names
        imported |= imports | set(called)
        spelled = {
            n.attr for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Attribute)
        }
        for stem in imports:
            attrs[stem] |= spelled
    found: list[str] = []
    for stem, helper in helpers.items():
        start = set(seeds[stem])
        if stem in imported:
            start |= helper.on_import
            methods = {u.partition(".")[2] for u in helper.units if "." in u}
            start |= attrs[stem] & methods
        reached = reach(helper, start)
        found += [f"{helper.path}:{u}" for u in helper.units if u not in reached]
    return sorted(found)


def read_debt(text: str) -> dict[str, str]:
    """Read the debt file's entries.

    Returns:
        Each listed `<path>:<unit>`, mapped to its note.

    """
    entries: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        entry, _, note = line.partition("\t")
        entries[entry.strip()] = note.strip()
    return entries


def units_of(sources: dict[str, str]) -> set[str]:
    """Name every helper unit in the sources.

    Returns:
        `<path>:<unit>` for each.

    """
    return {
        f"{p}:{u}"
        for p, text in sources.items()
        if not is_test(p)
        for u in read_helper(p, text).units
    }


def verdict(
    sources: dict[str, str], owing: list[str], debt: dict[str, str]
) -> list[str]:
    """Judge the tree against the debt file.

    Returns:
        One message per unreached unit not listed, and per stale entry.

    """
    found = [
        f"{entry}: no test reaches it in-process — call it, or a function"
        ' that names it, from a test module (CONTRIBUTING.md, "Testing standard")'
        for entry in owing
        if entry not in debt
    ]
    every = units_of(sources)
    for entry in sorted(debt):
        if entry not in every:
            found.append(
                f"{entry}: listed in {DEBT}, but no such function is tracked —"
                f" remove the entry ({FIX})"
            )
        elif entry not in owing:
            found.append(
                f"{entry}: listed in {DEBT}, but a test reaches it in-process now —"
                f" remove the entry ({FIX})"
            )
    return found


def settle(owing: list[str], debt: dict[str, str] | None) -> tuple[str, str]:
    """Write the debt file's new text.

    Returns:
        The text, and the line saying what changed: with no file, every
        unreached unit is listed; with one, stale entries are removed and
        nothing is added.

    """
    if debt is None:
        kept = dict.fromkeys(owing, NOTE)
        said = f"fix:in-process: wrote {DEBT}, {len(kept)} entry(ies)"
    else:
        kept = {entry: note for entry, note in debt.items() if entry in owing}
        gone = len(debt) - len(kept)
        said = (
            f"fix:in-process: {gone} entry(ies) removed; it never adds one to"
            " an existing file"
        )
    body = "".join(f"{entry}\t{note}\n" for entry, note in sorted(kept.items()))
    return HEADER + body, said


def read_sources(root: Path, paths: list[str]) -> dict[str, str]:
    """Read the listed files under root.

    Returns:
        Each path, mapped to its text.

    """
    return {p: (root / p).read_text(encoding="utf-8") for p in paths}


def check(root: Path, paths: list[str]) -> list[str]:
    """Judge the listed files under root against root's debt file.

    Returns:
        One message per unreached unit not listed, and per stale entry.

    """
    sources = read_sources(root, paths)
    debt_file = root / DEBT
    debt = (
        read_debt(debt_file.read_text(encoding="utf-8")) if debt_file.exists() else {}
    )
    return verdict(sources, unreached(sources), debt)


def tracked(root: Path) -> list[str]:
    """Name the tracked `.py` files outside `vendor/`.

    Returns:
        Their paths, relative to root; empty when none is tracked.

    Raises:
        RuntimeError: git is not on PATH.

    """
    git = shutil.which("git")
    if git is None:
        msg = f"{LABEL}: git is not on PATH"
        raise RuntimeError(msg)
    # git's own variables are not inherited: under a hook or a test run git
    # exports GIT_DIR or GIT_INDEX_FILE, and a listing of root under them
    # names another tree's files, or none (#857, measured on #1705's gate).
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    # ruff: ignore[subprocess-without-shell-equals-true]
    listed = subprocess.run(
        [git, "ls-files", "-z", "--", "*.py", ":!vendor/**"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(p for p in listed.stdout.split("\0") if p)


def write_debt(root: Path, paths: list[str]) -> str:
    """Settle root's debt file from the listed files.

    Returns:
        The line saying what changed.

    """
    debt_file = root / DEBT
    old = debt_file.read_text(encoding="utf-8") if debt_file.exists() else None
    owing = unreached(read_sources(root, paths))
    text, said = settle(owing, None if old is None else read_debt(old))
    debt_file.parent.mkdir(parents=True, exist_ok=True)
    debt_file.write_text(text, encoding="utf-8")
    return said


def summary(root: Path, paths: list[str]) -> str:
    """Count what the law judged.

    Returns:
        The green summary line.

    """
    sources = read_sources(root, paths)
    total = len(units_of(sources))
    owing = len(unreached(sources))
    helpers = len([p for p in paths if not is_test(p)])
    return (
        f"{LABEL}: {total} function(s) in {helpers} helper module(s),"
        f" {total - owing} reached in-process, {owing} listed in {DEBT}"
    )


def main(argv: list[str] | None = None) -> int:
    """Judge the tree, or settle the debt file with `--write`.

    Returns:
        0 when the tree keeps the law (or the file was written), 1 when
        not, 2 on arguments it does not take.

    """
    given = sys.argv[1:] if argv is None else argv
    write = "--write" in given
    flag, *value = [a for a in given if a != "--write"] or ["--root", "."]
    if flag != "--root" or len(value) != 1:
        print(
            f"usage: in_process.py [--write] [--root DIR], not {given}", file=sys.stderr
        )
        return 2
    root = Path(value[0])
    paths = tracked(root)
    if all(is_test(p) for p in paths):
        word = "fix:in-process" if write else LABEL
        print(f"{word}: no Python helper tracked, skipped")
        return 0
    if write:
        print(write_debt(root, paths))
        return 0
    found = check(root, paths)
    for line in found:
        print(f"{LABEL}: {line}", file=sys.stderr)
    if found:
        return 1
    print(summary(root, paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
