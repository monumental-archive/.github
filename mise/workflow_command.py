#!/usr/bin/env python3
"""The one writer of GitHub workflow commands in the canon's Python (#1660).

`::error::`, `::warning::` and `::notice::` are read by the runner to the
end of the line. A helper that printed one with text it did not write —
a manifest field, a tag, a registry's answer — let a newline in that text
end the command and begin a line of the text's author's choosing,
itself a workflow command if they liked (found by the random-seed audit:
a package.json repository of one newline split pack-wasm's refusal).

So every command goes out through here, escaped by construction, as the
runner unescapes it: the message's `%`, CR and LF as `%25`, `%0D` and
`%0A`; a property's value (`file`, `line`, `title`) also its `:` and `,`
as `%3A` and `%2C`. One line, flushed. Nothing else in the canon's
Python prints one; mise/test_workflow_command.py holds that law over the
tracked helpers.

A helper in another directory reaches this one the way #1643 set out:
`sys.path.append` of the sibling root, then `import workflow_command`.
"""

import sys
from typing import TextIO

# The runner's own escapes, in the order it reverses them: `%` first,
# so an escape this writes is never escaped twice.
_DATA = (("%", "%25"), ("\r", "%0D"), ("\n", "%0A"))
_PROPERTY = (*_DATA, (":", "%3A"), (",", "%2C"))


def escape_data(text: str) -> str:
    """Escape a command's message.

    Returns:
        The text with `%`, CR and LF escaped.

    """
    for raw, escaped in _DATA:
        text = text.replace(raw, escaped)
    return text


def escape_property(text: str) -> str:
    """Escape a command property's value.

    Returns:
        The text with `%`, CR, LF, `:` and `,` escaped.

    """
    for raw, escaped in _PROPERTY:
        text = text.replace(raw, escaped)
    return text


def render(
    command: str,
    message: str,
    *,
    file: str | None = None,
    line: int | None = None,
    title: str | None = None,
) -> str:
    """Render one workflow command as the runner reads it.

    Returns:
        `::command props::message`, one line.

    """
    props = [
        f"{key}={escape_property(str(value))}"
        for key, value in (("file", file), ("line", line), ("title", title))
        if value is not None
    ]
    head = f"{command} {','.join(props)}" if props else command
    return f"::{head}::{escape_data(message)}"


def _emit(text: str, stream: TextIO | None) -> None:
    """Print one rendered command, flushed, to the stream (stdout by default)."""
    print(text, file=sys.stdout if stream is None else stream, flush=True)


def error(
    message: str,
    *,
    file: str | None = None,
    line: int | None = None,
    title: str | None = None,
    stream: TextIO | None = None,
) -> None:
    """Print `::error::message`, escaped."""
    _emit(render("error", message, file=file, line=line, title=title), stream)


def warning(
    message: str,
    *,
    file: str | None = None,
    line: int | None = None,
    title: str | None = None,
    stream: TextIO | None = None,
) -> None:
    """Print `::warning::message`, escaped."""
    _emit(render("warning", message, file=file, line=line, title=title), stream)


def notice(
    message: str,
    *,
    file: str | None = None,
    line: int | None = None,
    title: str | None = None,
    stream: TextIO | None = None,
) -> None:
    """Print `::notice::message`, escaped."""
    _emit(render("notice", message, file=file, line=line, title=title), stream)
