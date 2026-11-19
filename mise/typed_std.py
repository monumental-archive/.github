#!/usr/bin/env python3
"""Two standard-library values, typed where typeshed leaves them `Any` (#1676).

Under `mise/mypy.ini` almost every helper meets two values that are not
parsed documents and still arrive as `Any`: a regular expression's group
(typeshed types a group that did not take part as `Any`) and standard
input (`sys.stdin` is `TextIO | Any`). Each is read here once, typed,
with no cast, so no helper writes its own reader.

- `group(match, group)`: the group's text by its span, or None for a
  group that did not take part. For `findall` or `groups`, a helper
  iterates `finditer` and calls `group`.
- `stdin_text()`: all of standard input, refusing by name a process
  started without one.

stdlib only; a sibling root reaches it the way #1634 settled, by
appending the `mise` root to `sys.path` from its own `__file__`.
"""

import io
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import re


class NoStdinError(RuntimeError):
    """The process was started with no standard input to read."""


def group(match: re.Match[str], group: int | str = 0) -> str | None:
    """Read one group of a match by its span.

    Returns:
        The group's text, or None when the group did not take part.

    """
    start, end = match.span(group)
    if start < 0:
        return None
    return match.string[start:end]


def stdin_text() -> str:
    """Read all of standard input.

    Returns:
        The text.

    Raises:
        NoStdinError: the process has no standard input.

    """
    stream: object = sys.stdin
    if not isinstance(stream, io.TextIOBase):
        msg = "no standard input: the process was started without one"
        raise NoStdinError(msg)
    return stream.read()
