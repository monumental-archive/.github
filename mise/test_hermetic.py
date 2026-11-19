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
import re
import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
hermetic = SimpleNamespace(**runpy.run_path(str(HERE / "hermetic.py")))

CANON = HERE.parent
GIT = shutil.which("git") or "git"
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

# The five ways a test file copies the caller's environment. `dict(` is
# anchored so `mock.patch.dict(os.environ, env, clear=True)` is not
# counted: that REPLACES the process environment with `env`, it does not
# copy the caller's into a fixture.
COPIES = re.compile(
    r"os\.environ\.items\(\)|\{\*\*os\.environ|(?<![\w.])dict\(os\.environ"
    r"|os\.environ\.copy\(\)|os\.environ \|"
)


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


class TheLaw(unittest.TestCase):
    """No test file copies the caller's environment (#1514 item 3)."""

    def test_no_test_file_copies_the_callers_environment(self) -> None:
        """Every fixture environment is built through hermetic.env."""
        listed = listed_test_files()
        offenders = [
            path
            for path in listed
            if path not in EXCEPTED
            and COPIES.search((CANON / path).read_text(encoding="utf-8"))
        ]
        self.assertEqual([], offenders)

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
