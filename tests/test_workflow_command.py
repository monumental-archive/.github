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
they expect to read. The files are the tree's tracked ones, listed
through support.tracked (#1618).
"""

import ast
import io
import re
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import workflow_command
from tests import support

if TYPE_CHECKING:
    pass


from belt.release import pgrx_images

CANON = support.CANON
# Any workflow command: `::` and a command name, then `::` or the space
# before properties. A name not yet used is refused without a list.
COMMAND = re.compile(r"::[A-Za-z][A-Za-z0-9_-]*(::| )")
MODULE = Path("mise/workflow_command.py")
# Directories the walk never enters: tool state, vendored trees, caches.
SKIP = {"vendor", "testdata"}


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
    """List the canon's tracked Python files a helper may live in.

    Returns:
        Every tracked `.py`, as a path relative to the root, less test
        and property files and vendored or test-data trees.

    """
    return [
        Path(name)
        for name in support.tracked(root, "*.py")
        if not Path(name).name.startswith(("test_", "prop_"))
        and not SKIP.intersection(Path(name).parts)
    ]


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


# What the writer prints, one line each time.
def test_a_newline_cannot_begin_a_second_command(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """#1660's row: a message with a newline and a command is one line."""
    workflow_command.error("a\n::error::b")
    assert capfd.readouterr().out == "::error::a%0A::error::b\n"


def test_cr_and_percent_are_escaped_percent_first(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """`%0A` written as text stays text: `%` is escaped before the rest."""
    workflow_command.warning("50%\r%0A")
    assert capfd.readouterr().out == "::warning::50%25%0D%250A\n"


def test_plain_text_is_unchanged(capfd: pytest.CaptureFixture[str]) -> None:
    """Text with no `%`, CR or LF goes out as it was."""
    workflow_command.notice("published v1.2.3 — done")
    assert capfd.readouterr().out == "::notice::published v1.2.3 — done\n"


def test_properties_also_escape_colon_and_comma(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """file, line and title, in that order, each value escaped."""
    workflow_command.error("m", file="a:b,c", line=3, title="t\n%")
    assert capfd.readouterr().out == "::error file=a%3Ab%2Cc,line=3,title=t%0A%25::m\n"


def test_a_masked_value_with_a_newline_is_one_line(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The whole value is masked: one line, read back whole.

    Unescaped, the runner masked only up to the newline.
    """
    workflow_command.mask("abc\ndef%")
    out = capfd.readouterr().out
    assert out == "::add-mask::abc%0Adef%25\n"
    assert (
        out.removeprefix("::add-mask::")
        .removesuffix("\n")
        .replace("%0A", "\n")
        .replace("%25", "%")
    ) == "abc\ndef%"


def test_a_group_title_is_escaped_and_closed(capfd: pytest.CaptureFixture[str]) -> None:
    """The group title is escaped, and endgroup is the bare command."""
    workflow_command.group("log\n::error::x")
    assert capfd.readouterr().out == "::group::log%0A::error::x\n"
    workflow_command.endgroup(stream=None)
    assert capfd.readouterr().out == "::endgroup::\n"


def test_group_then_endgroup_in_process(capfd: pytest.CaptureFixture[str]) -> None:
    """Called directly, the two write the group's start and its end."""
    workflow_command.group("npm debug log\n::error::x")
    workflow_command.endgroup()
    out = capfd.readouterr().out
    assert out == "::group::npm debug log%0A::error::x\n::endgroup::\n"


def test_the_stream_is_the_callers(capfd: pytest.CaptureFixture[str]) -> None:
    """stream= takes the line, and stdout gets nothing."""
    err = io.StringIO()
    workflow_command.notice("x", stream=err)
    assert capfd.readouterr().out == ""
    assert err.getvalue() == "::notice::x\n"


# #1660's helper row: pgrx_images quotes the major it was given.
def test_the_refusal_is_one_line(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A major carrying a newline refuses on one line, the text escaped.

    On main this printed two lines, the second `::error::b`.
    """
    with tempfile.TemporaryDirectory() as tmp:
        table = Path(tmp) / "pgrx-base-images.toml"
        table.write_text("[build]\n", "utf-8")
        monkeypatch.setenv("MISE_TASK_NAME", "")
        monkeypatch.setenv("ORG_FINDINGS", "")
        monkeypatch.setattr(sys, "argv", ["pgrx_images.py"])
        code = pgrx_images.main(
            [
                "base-image",
                "--pg",
                "a\n::error::b",
                "--table",
                str(table),
            ]
        )
        err = capfd.readouterr().err
    assert code == 1
    assert err == (
        f"::error::pgrx_images.py: pga%0A::error::b has no build image in {table}\n"
    )


# No Python file but the module writes a workflow command.
def test_only_the_module_writes_one() -> None:
    """Every finding is a file and line to route through the module."""
    assert findings(CANON) == list[str]()


def test_the_law_sees_each_shape() -> None:
    """A literal, an f-string head and a concatenation are each found."""
    source = (
        'print("::error::x")\n'
        'say(f"::warning::{y}")\n'
        'm = ("::notice::a" " b")\n'
        'n = "a ::error:: in the middle"\n'
        'o = f"{z}::error::"\n'
    )
    assert command_strings(source) == list[int]([1, 2, 3])


def test_any_command_name_is_refused_by_line() -> None:
    """add-mask, group, debug and set-output, each on its own line."""
    source = (
        'say(f"::add-mask::{token}")\n'
        'say("::group::log")\n'
        'print("::debug::x")\n'
        'print(f"::set-output name=x::{v}")\n'
        'say("::endgroup::")\n'
    )
    assert command_strings(source) == list[int]([1, 2, 3, 4, 5])


def test_double_colons_mid_text_are_allowed() -> None:
    """`::` that does not begin a command name is no command."""
    source = (
        'a = "std::vec::Vec"\n'
        'b = "a ::error:: x"\n'
        'c = ":: spaced"\n'
        'd = "::9digit::x"\n'
        'e = f"{x}::group::"\n'
    )
    assert command_strings(source) == list[int]()
