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
- `request(url, data=, headers=, method=)`: the one place the canon
  builds a request, for an http or https URL only; any other scheme
  (`file:`, a custom one) is refused by name, never opened (#2177).
- `urlopen(request, timeout=, opener=)`: one HTTP answer, typed as a
  `Response` (#1682). typeshed types what `OpenerDirector.open` returns
  as `Any`; this is the one place the canon calls it, through urllib's
  default opener or the one a caller hands in (one that never
  redirects). Redirects, proxies and `HTTPError` stay urllib's.
- `on_path(name, env)`: a tool's absolute path on the PATH the process
  is given, asked as it starts, so no process starts a bare name.

Stdlib only; a module of the belt package, imported as `from belt
import typed_std` (#2177).
"""

import io
import os
import shutil
import sys
import urllib.request
from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

if TYPE_CHECKING:
    import re
    from collections.abc import Mapping


class NoStdinError(RuntimeError):
    """The process was started with no standard input to read."""


class NotAResponseError(TypeError):
    """An opener answered something that is not an HTTP response."""


class NotHttpError(NotAResponseError):
    """A URL that is not http or https: nothing could answer it as HTTP."""


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


def request(
    url: str,
    *,
    data: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    method: str | None = None,
) -> urllib.request.Request:
    """Build one request to an http or https URL.

    Returns:
        The request, its scheme one of the two.

    Raises:
        NotHttpError: the URL names any other scheme.

    """
    scheme, _, rest = url.partition("://")
    given = dict(headers or {})
    if scheme == "https":
        return urllib.request.Request(
            f"https://{rest}", data=data, headers=given, method=method
        )
    if scheme == "http":
        return urllib.request.Request(
            f"http://{rest}", data=data, headers=given, method=method
        )
    msg = f"{url} is not an http or https URL"
    raise NotHttpError(msg)


def on_path(name: str, env: Mapping[str, str] | None = None) -> str:
    """Find a tool on PATH as the process starts, never at import.

    The PATH is the one the process will be given, `env`'s when the
    caller hands one in, as `subprocess` itself resolves a bare name.

    Returns:
        Its absolute path, or `name` itself when that PATH holds none, so
        the start fails by the tool's own name.

    """
    path = (os.environ if env is None else env).get("PATH")
    return shutil.which(name, path=path) or name


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
    chosen = urllib.request.build_opener() if opener is None else opener
    answer: object = chosen.open(request, timeout=timeout)
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
