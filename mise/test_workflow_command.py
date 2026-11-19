#!/usr/bin/env python3
"""The one writer of workflow commands, and the law that keeps it one (#1660).

Rows for `mise/workflow_command.py`: what it writes for a message that
carries a newline, a CR or a `%`, and for each property; and the row
#1660 names, a helper that quoted outside text into a refusal printing
it as ONE line.

The law: read from each file's syntax tree, no Python file in the canon
other than the module holds a string that begins any workflow command:
`::`, a command name, then `::` or a space (a plain literal, or the
literal head of an f-string). No list of names: the next one is
refused too. Test and property files are exempt: they hold the commands
they expect to read. The walk is the tree on disk, not `git ls-files`:
this suite runs with git aimed at a decoy repository.

stdlib `unittest`, matching the rest of mise/.
"""

import ast
import io
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING

import workflow_command

if TYPE_CHECKING:
    from collections.abc import Callable

# pgrx_images, the helper #1660's row names, in release/. Appended, so
# this file's own directory and the standard library come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "release"))

import pgrx_images

CANON = Path(__file__).resolve().parents[1]
# Any workflow command: `::` and a command name, then `::` or the space
# before properties. A name not yet used is refused without a list.
COMMAND = re.compile(r"::[A-Za-z][A-Za-z0-9_-]*(::| )")
MODULE = Path("mise/workflow_command.py")
# Directories the walk never enters: tool state, vendored trees, caches.
SKIP = {".git", "node_modules", "target", "vendor", "__pycache__", ".venv"}


def written(call: Callable[[], None]) -> str:
    """Capture what one writer call prints to stdout.

    Returns:
        The output.

    """
    out = io.StringIO()
    with redirect_stdout(out):
        call()
    return out.getvalue()


def command_strings(source: str) -> list[int]:
    """Find the lines holding a string that begins a workflow command.

    Returns:
        The line of each plain literal, or f-string whose literal head,
        that begins a workflow command.

    """
    tree = ast.parse(source)
    # An f-string's literal pieces are read through the f-string: only
    # its head begins the printed text.
    inner = {
        id(part)
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        for part in node.values
    }
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) not in inner:
            if isinstance(node.value, str) and COMMAND.match(node.value):
                lines.add(node.lineno)
        elif isinstance(node, ast.JoinedStr) and node.values:
            first = node.values[0]
            if (
                isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and COMMAND.match(first.value)
            ):
                lines.add(node.lineno)
    return sorted(lines)


def python_files(root: Path) -> list[Path]:
    """List the canon's Python files a helper may live in.

    Returns:
        Every `.py` below `root`, as a path relative to it, skipping
        tool state and test and property files.

    """
    found: list[Path] = []
    for here, dirs, files in os.walk(root):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in SKIP and (d == ".github" or not d.startswith("."))
        )
        for name in sorted(files):
            if not name.endswith(".py") or name.startswith(("test_", "prop_")):
                continue
            found.append((Path(here) / name).relative_to(root))
    return found


def findings(root: Path) -> list[str]:
    """Hold every Python file below `root` to the law.

    Returns:
        `file:line` for each workflow command written outside the module.

    """
    out: list[str] = []
    for rel in python_files(root):
        if rel == MODULE:
            continue
        source = (root / rel).read_text(encoding="utf-8")
        out += [f"{rel}:{line}" for line in command_strings(source)]
    return out


class Escapes(unittest.TestCase):
    """What the writer prints, one line each time."""

    def test_a_newline_cannot_begin_a_second_command(self) -> None:
        """#1660's row: a message with a newline and a command is one line."""
        self.assertEqual(
            written(lambda: workflow_command.error("a\n::error::b")),
            "::error::a%0A::error::b\n",
        )

    def test_cr_and_percent_are_escaped_percent_first(self) -> None:
        """`%0A` written as text stays text: `%` is escaped before the rest."""
        self.assertEqual(
            written(lambda: workflow_command.warning("50%\r%0A")),
            "::warning::50%25%0D%250A\n",
        )

    def test_plain_text_is_unchanged(self) -> None:
        """Text with no `%`, CR or LF goes out as it was."""
        self.assertEqual(
            written(lambda: workflow_command.notice("published v1.2.3 — done")),
            "::notice::published v1.2.3 — done\n",
        )

    def test_properties_also_escape_colon_and_comma(self) -> None:
        """file, line and title, in that order, each value escaped."""
        self.assertEqual(
            written(
                lambda: workflow_command.error("m", file="a:b,c", line=3, title="t\n%")
            ),
            "::error file=a%3Ab%2Cc,line=3,title=t%0A%25::m\n",
        )

    def test_a_masked_value_with_a_newline_is_one_line(self) -> None:
        """The whole value is masked: one line, read back whole.

        Unescaped, the runner masked only up to the newline.
        """
        out = written(lambda: workflow_command.mask("abc\ndef%"))
        self.assertEqual(out, "::add-mask::abc%0Adef%25\n")
        self.assertEqual(
            out
            .removeprefix("::add-mask::")
            .removesuffix("\n")
            .replace("%0A", "\n")
            .replace("%25", "%"),
            "abc\ndef%",
        )

    def test_a_group_title_is_escaped_and_closed(self) -> None:
        """The group title is escaped, and endgroup is the bare command."""
        self.assertEqual(
            written(lambda: workflow_command.group("log\n::error::x")),
            "::group::log%0A::error::x\n",
        )
        self.assertEqual(written(workflow_command.endgroup), "::endgroup::\n")

    def test_the_stream_is_the_callers(self) -> None:
        """stream= takes the line, and stdout gets nothing."""
        err = io.StringIO()
        self.assertEqual(written(lambda: workflow_command.notice("x", stream=err)), "")
        self.assertEqual(err.getvalue(), "::notice::x\n")


class AHelperQuotingOutsideText(unittest.TestCase):
    """#1660's helper row: pgrx_images quotes the major it was given."""

    def test_the_refusal_is_one_line(self) -> None:
        """A major carrying a newline refuses on one line, the text escaped.

        On main this printed two lines, the second `::error::b`.
        """
        with tempfile.TemporaryDirectory() as tmp:
            table = Path(tmp) / "pgrx-base-images.toml"
            table.write_text("[build]\n", "utf-8")
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                code = pgrx_images.main([
                    "base-image",
                    "--pg",
                    "a\n::error::b",
                    "--table",
                    str(table),
                ])
        self.assertEqual(code, 1)
        self.assertEqual(
            err.getvalue(),
            f"::error::pga%0A::error::b has no build image in {table}\n",
        )


class TheLaw(unittest.TestCase):
    """No Python file but the module writes a workflow command."""

    def test_only_the_module_writes_one(self) -> None:
        """Every finding is a file and line to route through the module."""
        self.assertEqual(findings(CANON), list[str]())

    def test_the_law_sees_each_shape(self) -> None:
        """A literal, an f-string head and a concatenation are each found."""
        source = (
            'print("::error::x")\n'
            'say(f"::warning::{y}")\n'
            'm = ("::notice::a" " b")\n'
            'n = "a ::error:: in the middle"\n'
            'o = f"{z}::error::"\n'
        )
        self.assertEqual(command_strings(source), list[int]([1, 2, 3]))

    def test_any_command_name_is_refused_by_line(self) -> None:
        """add-mask, group, debug and set-output, each on its own line."""
        source = (
            'say(f"::add-mask::{token}")\n'
            'say("::group::log")\n'
            'print("::debug::x")\n'
            'print(f"::set-output name=x::{v}")\n'
            'say("::endgroup::")\n'
        )
        self.assertEqual(command_strings(source), list[int]([1, 2, 3, 4, 5]))

    def test_double_colons_mid_text_are_allowed(self) -> None:
        """`::` that does not begin a command name is no command."""
        source = (
            'a = "std::vec::Vec"\n'
            'b = "a ::error:: x"\n'
            'c = ":: spaced"\n'
            'd = "::9digit::x"\n'
            'e = f"{x}::group::"\n'
        )
        self.assertEqual(command_strings(source), list[int]())


if __name__ == "__main__":
    unittest.main()
