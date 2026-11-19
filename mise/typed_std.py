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
- `urlopen(request, timeout=, opener=)`: one HTTP answer, typed as a
  `Response` (#1682). typeshed types what `urllib.request.urlopen` and
  `OpenerDirector.open` return as `Any`; this is the one place the
  canon calls either. Redirects, proxies and `HTTPError` stay urllib's,
  and a caller that needs its own opener (one that never redirects)
  hands it in.

stdlib only; a sibling root reaches it the way #1634 settled, by
appending the `mise` root to `sys.path` from its own `__file__`.
"""

import io
import sys
import urllib.request
from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

if TYPE_CHECKING:
    import re


class NoStdinError(RuntimeError):
    """The process was started with no standard input to read."""


class NotAResponseError(TypeError):
    """An opener answered something that is not an HTTP response."""


@runtime_checkable
class Response(Protocol):
    """What the canon reads of an HTTP answer: no more."""

    status: int

    def read(self) -> bytes:
        """Read the body."""
        ...

    def getheader(self, name: str, default: str | None = None) -> str | None:
        """Read one header."""
        ...

    def __enter__(self) -> Self:
        """Enter the answer's context, which closes it on leaving."""
        ...

    def __exit__(self, *exc: object) -> None:
        """Close the answer."""
        ...


def urlopen(
    request: urllib.request.Request,
    *,
    timeout: float,
    opener: urllib.request.OpenerDirector | None = None,
) -> Response:
    """Make one request through urllib, or through the opener handed in.

    Returns:
        The answer.

    Raises:
        NotAResponseError: what came back is not an HTTP response.

    """
    answer: object = (
        # ruff: ignore[suspicious-url-open-usage] - each caller builds its Request from a URL it names
        urllib.request.urlopen(request, timeout=timeout)
        if opener is None
        else opener.open(request, timeout=timeout)
    )
    if not isinstance(answer, Response):
        msg = (
            f"{request.full_url} answered {type(answer).__name__}, not an HTTP response"
        )
        raise NotAResponseError(msg)
    return answer


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
