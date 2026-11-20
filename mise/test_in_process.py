#!/usr/bin/env python3
"""Tests for in_process.py and the law it holds the tree to (#1673).

The law: every function and method of a tracked Python helper, wherever
it lives, is reached in-process from a test module, or is listed in the
shrink-only debt file. The rows call the helper's functions in this
process, the rule the law states; one row runs it as a script, for the
script contract alone.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import contextlib
import io
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest import mock

import hermetic
import in_process

HERE = Path(__file__).resolve().parent
CANON = HERE.parent
GIT = hermetic.tool("git")

# A helper whose `main` calls `work`, run as a script by its block.
HELPER = """\
import sys


def work(n: int) -> int:
    return n + 1


def main() -> int:
    print(work(1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""
SCRIPT_ROW = 'import subprocess\nsubprocess.run(["python3", "mise/hello.py"])\n'
CALLS_WORK = "import hello\n\n\ndef test() -> None:\n    hello.work(1)\n"
CALLS_MAIN = "import hello\n\n\ndef test() -> None:\n    hello.main()\n"
ENTRY = "mise/hello.py:main"
UNREACHED = (
    f"{ENTRY}: no test reaches it in-process — call it, or a function that"
    ' names it, from a test module (CONTRIBUTING.md, "Testing standard")'
)
STALE = (
    f"{ENTRY}: listed in security/helper-import-debt.txt, but a test reaches"
    " it in-process now — remove the entry (mise run fix:in-process)"
)
GONE = (
    "mise/gone.py:main: listed in security/helper-import-debt.txt, but no such"
    " function is tracked — remove the entry (mise run fix:in-process)"
)


def unreached(helper: str, *tests: str) -> list[str]:
    """Judge one planted helper against planted test modules.

    Returns:
        The helper's unreached units, as `<path>:<unit>`.

    """
    sources = {"mise/hello.py": helper}
    sources |= {f"mise/test_hello_{i}.py": text for i, text in enumerate(tests)}
    return in_process.unreached(sources)


class Case(unittest.TestCase):
    """unittest's case, with an equality check mypy can type."""

    def same[T](self, got: T, want: T) -> None:
        """Assert two values of one type are equal."""
        self.assertEqual(got, want)


class Reach(Case):
    """What reaches a function in-process."""

    def test_a_leaf_call_and_a_script_row_leave_main_owed(self) -> None:
        """Fail-before: one in-process call to `work`, `main` only as a script."""
        self.same(unreached(HELPER, CALLS_WORK, SCRIPT_ROW), [ENTRY])

    def test_calling_main_in_process_reaches_what_it_leads_to(self) -> None:
        """`main` called in-process reaches `work` through it."""
        self.same(unreached(HELPER, CALLS_MAIN), [])

    def test_a_script_row_alone_reaches_nothing(self) -> None:
        """Run only as a script, every function is owed."""
        self.same(
            unreached(HELPER, SCRIPT_ROW), ["mise/hello.py:main", "mise/hello.py:work"]
        )

    def test_the_main_block_reaches_nothing(self) -> None:
        """The `__main__` block is the script contract, not module-level code."""
        self.same(
            unreached(HELPER, "import hello\n"),
            sorted([
                "mise/hello.py:main",
                "mise/hello.py:work",
            ]),
        )

    def test_a_callback_reference_is_reached(self) -> None:
        """A function a reached function names without calling is reached."""
        helper = (
            "def work(n: int) -> int:\n    return n + 1\n\n\n"
            "def main() -> list[int]:\n    return list(map(work, [1]))\n"
        )
        self.same(unreached(helper, CALLS_MAIN), [])

    def test_a_method_is_judged_as_a_function(self) -> None:
        """An unreached method is owed by its `Class.method` name."""
        helper = (
            "class Box:\n    def __init__(self, n: int) -> None:\n"
            "        self.n = n\n\n    def go(self) -> int:\n        return self.n\n"
        )
        self.same(
            unreached(helper, "import hello\nhello.Box(1)\n"), ["mise/hello.py:Box.go"]
        )
        self.same(unreached(helper, "import hello\nhello.Box(1).go()\n"), [])

    def test_an_aliased_import_and_a_from_import_reach(self) -> None:
        """However the module was imported, the call resolves to it."""
        self.same(unreached(HELPER, "import hello as h\nh.main()\n"), [])
        self.same(unreached(HELPER, "from hello import main as m\nm()\n"), [])

    def test_module_level_code_reaches_on_import(self) -> None:
        """A function the module's own top level calls runs when a test imports it."""
        helper = "def build() -> int:\n    return 1\n\n\nVALUE = build()\n"
        self.same(unreached(helper, "import hello\n"), [])
        self.same(unreached(helper), ["mise/hello.py:build"])

    def test_a_property_module_is_a_test_module(self) -> None:
        """prop_*.py reaches, and is never a helper."""
        sources = {"release/hello.py": HELPER, "release/prop_hello.py": CALLS_MAIN}
        self.same(in_process.unreached(sources), [])


class Verdict(Case):
    """The tree against the debt file."""

    def test_an_unreached_unlisted_function_is_named(self) -> None:
        """Red, by `<path>:<function>`."""
        sources = {"mise/hello.py": HELPER, "mise/test_hello.py": CALLS_WORK}
        owing = in_process.unreached(sources)
        self.same(in_process.verdict(sources, owing, {}), [UNREACHED])

    def test_a_listed_unreached_function_is_green(self) -> None:
        """The debt file is the one exception."""
        sources = {"mise/hello.py": HELPER, "mise/test_hello.py": CALLS_WORK}
        owing = in_process.unreached(sources)
        debt = {ENTRY: in_process.NOTE}
        self.same(in_process.verdict(sources, owing, debt), [])

    def test_a_listed_function_now_reached_is_red_as_stale(self) -> None:
        """A debt entry for a function a test reaches is red."""
        sources = {"mise/hello.py": HELPER, "mise/test_hello.py": CALLS_MAIN}
        self.same(in_process.verdict(sources, [], {ENTRY: in_process.NOTE}), [STALE])

    def test_an_entry_for_a_gone_function_is_red(self) -> None:
        """An entry naming no tracked function is red."""
        self.same(
            in_process.verdict({}, [], {"mise/gone.py:main": in_process.NOTE}), [GONE]
        )


class Debt(Case):
    """Reading and settling the debt file."""

    def test_the_file_reads_back_what_settle_writes(self) -> None:
        """Generated with no file: every unreached function, with the note."""
        text, said = in_process.settle(["mise/a.py:main", "mise/b.py:Box.go"], None)
        self.assertTrue(text.startswith(in_process.HEADER))
        self.same(
            in_process.read_debt(text),
            {"mise/a.py:main": in_process.NOTE, "mise/b.py:Box.go": in_process.NOTE},
        )
        self.same(
            said,
            "fix:in-process: wrote security/helper-import-debt.txt, 2 entry(ies)",
        )

    def test_an_existing_file_only_shrinks(self) -> None:
        """A stale entry goes, its note kept for the rest; nothing is added."""
        debt = {"mise/a.py:main": "# #9 convert", "mise/gone.py:x": in_process.NOTE}
        text, said = in_process.settle(["mise/a.py:main", "mise/new.py:y"], debt)
        self.same(in_process.read_debt(text), {"mise/a.py:main": "# #9 convert"})
        self.same(
            said,
            "fix:in-process: 1 entry(ies) removed; it never adds one to an"
            " existing file",
        )


class Tree(Case):
    """A planted git tree, judged and settled through main(), in-process."""

    @override
    def setUp(self) -> None:
        """Plant a tree with one helper, one leaf call and a script row."""
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.git("init", "-q")
        self.track("mise/hello.py", HELPER)
        self.track("mise/test_hello.py", CALLS_WORK + SCRIPT_ROW)

    def git(self, *args: str) -> None:
        """Run git in the planted tree, scrubbed of the caller's git state."""
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [GIT, *args], cwd=self.root, env=hermetic.env(), check=True
        )

    def track(self, path: str, text: str) -> None:
        """Write a file into the planted tree and track it."""
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git("add", path)

    def main(self, *argv: str) -> tuple[int, str, str]:
        """Run the helper's entry point on the planted tree, in this process.

        Returns:
            Its exit status, what it printed and what it printed to stderr.

        """
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = in_process.main(["--root", str(self.root), *argv])
        return status, out.getvalue(), err.getvalue()

    def test_no_helper_skips_clean(self) -> None:
        """A tree with no Python helper tracked is not judged."""
        self.git("rm", "-q", "--cached", "mise/hello.py")
        self.same(
            self.main(),
            (
                0,
                "lint:in-process: no Python helper tracked, skipped\n",
                "",
            ),
        )

    def test_fix_writes_the_file_and_the_lint_is_then_green(self) -> None:
        """No file: red; fix writes it; then green, with the counts."""
        self.same(self.main(), (1, "", f"lint:in-process: {UNREACHED}\n"))
        self.same(
            self.main("--write"),
            (
                0,
                (
                    "fix:in-process: wrote security/helper-import-debt.txt,"
                    " 1 entry(ies)\n"
                ),
                "",
            ),
        )
        self.same(
            self.main(),
            (
                0,
                (
                    "lint:in-process: 2 function(s) in 1 helper module(s), 1 reached"
                    " in-process, 1 listed in security/helper-import-debt.txt\n"
                ),
                "",
            ),
        )

    def test_a_converted_helper_reds_until_fix_removes_it(self) -> None:
        """Calling `main` in-process makes its entry stale; fix removes it."""
        self.main("--write")
        self.track("mise/test_hello.py", CALLS_MAIN)
        self.same(self.main(), (1, "", f"lint:in-process: {STALE}\n"))
        self.same(
            self.main("--write"),
            (
                0,
                (
                    "fix:in-process: 1 entry(ies) removed; it never adds one to an"
                    " existing file\n"
                ),
                "",
            ),
        )
        self.same(self.main()[0], 0)
        debt = (self.root / in_process.DEBT).read_text(encoding="utf-8")
        self.same(in_process.read_debt(debt), {})

    def test_an_argument_it_does_not_take_is_refused(self) -> None:
        """Anything but `--write` and `--root DIR` is a usage error, exit 2."""
        status, out, err = self.main("--nope")
        self.same((status, out), (2, ""))
        self.assertTrue(err.startswith("usage: in_process.py [--write] [--root DIR]"))

    def test_a_helper_in_a_directory_of_any_name_is_judged(self) -> None:
        """The law names no directory: a helper in tools/ is judged too."""
        self.track("tools/sweep.py", "def sweep() -> int:\n    return 0\n")
        self.main("--write")
        debt = (self.root / in_process.DEBT).read_text(encoding="utf-8")
        self.same(sorted(in_process.read_debt(debt)), [ENTRY, "tools/sweep.py:sweep"])
        self.track("tools/test_sweep.py", "import sweep\nsweep.sweep()\n")
        status, _, err = self.main()
        self.same(status, 1)
        self.assertIn("tools/sweep.py:sweep: listed in", err)

    def test_vendored_python_is_not_judged(self) -> None:
        """A helper under vendor/ is someone else's code."""
        self.track("vendor/lib/thing.py", "def thing() -> int:\n    return 0\n")
        self.main("--write")
        debt = (self.root / in_process.DEBT).read_text(encoding="utf-8")
        self.same(sorted(in_process.read_debt(debt)), [ENTRY])

    def test_git_variables_of_the_caller_do_not_reach_the_listing(self) -> None:
        """Under a hook's GIT_DIR and GIT_INDEX_FILE, root is still listed."""
        planted = {"GIT_DIR": "/nowhere/.git", "GIT_INDEX_FILE": "/nowhere/index"}
        with mock.patch.dict(os.environ, planted):
            listed = in_process.tracked(self.root)
        self.same(listed, ["mise/hello.py", "mise/test_hello.py"])

    def test_the_script_contract(self) -> None:
        """Run as a script: red, by name, exit 1 (the one end-to-end row)."""
        done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [sys.executable, str(HERE / "in_process.py"), "--root", str(self.root)],
            env=hermetic.env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.same(done.returncode, 1)
        self.same(done.stderr, f"lint:in-process: {UNREACHED}\n")


class Law(Case):
    """The canon keeps the law, against its own debt file."""

    def test_the_canon_keeps_the_law(self) -> None:
        """Every helper function is reached in-process, or listed as debt."""
        paths = [
            p for p in hermetic.tracked(CANON, "*.py") if not p.startswith("vendor/")
        ]
        self.same(in_process.check(CANON, paths), [])


if __name__ == "__main__":
    unittest.main()
