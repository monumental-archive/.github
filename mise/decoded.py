#!/usr/bin/env python3
"""The one place the canon's Python parses a document (#1676).

Every input is a typed value or one named refusal, `UndecodableError`.
JSON is read strictly (RFC 8259, with I-JSON's unique keys, RFC 7493):
bytes that are not UTF-8, a syntax error, a duplicate key, `NaN` or
`Infinity`, a number that does not fit a finite float, and nesting
deeper than MAX_DEPTH are each refused. The depth is this module's own
bound, counted before decoding: the interpreter's stack is not the same
on every machine (100,000 open brackets overflow it on macOS and not on
the runner), and a document must read the same everywhere. TOML
refuses the same bytes, its own errors and nesting too deep for its
pure-Python reader, whose limit is the same everywhere; its `nan` and
`inf` are TOML and stay.

A decoded value is `object`, never `Any`: a caller narrows it with the
readers here (`is_object`, `as_object`, ...) into its own dataclass, so
a wrong shape is refused where it is read, by name. There is no reader
for a helper's own record: each helper decodes its shape itself.

stdlib only; a sibling root reaches it the way #1634 settled, by
appending the `mise` root to `sys.path` from its own `__file__`.
"""

import json
import math
import tomllib
from typing import TYPE_CHECKING, TypeIs

if TYPE_CHECKING:
    from collections.abc import Iterable

# The deepest JSON nesting load_json reads. The deepest document the
# canon reads today, a CycloneDX SBOM's nested components, is under 40.
MAX_DEPTH = 512


class UndecodableError(ValueError):
    """A document refused: the text says what was wrong, never where."""


def _text(source: str | bytes) -> str:
    """Decode bytes as UTF-8, or refuse them.

    Returns:
        The text.

    Raises:
        UndecodableError: the bytes are not UTF-8.

    """
    if isinstance(source, str):
        return source
    try:
        return source.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = f"not UTF-8: byte {exc.start} cannot be decoded"
        raise UndecodableError(msg) from None


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one JSON object, refusing a key it holds twice.

    Returns:
        The object.

    Raises:
        UndecodableError: a key appears twice.

    """
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            msg = f"duplicate key {json.dumps(key)}"
            raise UndecodableError(msg)
        out[key] = value
    return out


def _constant(name: str) -> float:
    """Refuse `NaN`, `Infinity` and `-Infinity`, which JSON does not have.

    Raises:
        UndecodableError: always.

    """
    msg = f"{name} is not a JSON number"
    raise UndecodableError(msg)


def _finite(text: str) -> float:
    """Read a JSON number with a fraction or exponent, refusing overflow.

    Returns:
        The finite float.

    Raises:
        UndecodableError: the number does not fit a finite float.

    """
    value = float(text)
    if not math.isfinite(value):
        msg = f"{text} does not fit a finite number"
        raise UndecodableError(msg)
    return value


def within_depth(chars: Iterable[str]) -> None:
    """Refuse JSON nested deeper than MAX_DEPTH, counted outside strings.

    It reads the text's characters in order, once; any iterable of them
    will do, which is how a row counts that it read each once.

    One pass over the characters, holding two bits of state (inside a
    string; just after a backslash in one), so it is linear in the text
    whatever the text holds: a scan that looked for a whole string at
    each quote started over at every quote of an unterminated one.

    Raises:
        UndecodableError: an array or object opens past the bound.

    """
    depth = 0
    in_string = False
    escaped = False
    for char in chars:
        if escaped:
            escaped = False
        elif in_string:
            escaped = char == "\\"
            in_string = char != '"'
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                msg = f"nested deeper than {MAX_DEPTH} levels"
                raise UndecodableError(msg)
        elif char in "]}":
            depth -= 1


def load_json(source: str | bytes) -> object:
    """Decode one strict JSON document.

    Returns:
        The value, to be narrowed by the caller.

    Raises:
        UndecodableError: the document is refused; the text says why.

    """
    text = _text(source)
    within_depth(text)
    try:
        value: object = json.loads(
            text,
            object_pairs_hook=_unique,
            parse_constant=_constant,
            parse_float=_finite,
        )
    except UndecodableError:
        raise
    except RecursionError:
        # The backstop: a stack smaller than MAX_DEPTH needs.
        msg = "nested too deep to decode"
        raise UndecodableError(msg) from None
    except ValueError as exc:
        # A syntax error (json's own message, kept as it is) or an
        # integer past the interpreter's digit limit.
        raise UndecodableError(str(exc)) from None
    return value


def load_toml(source: str | bytes) -> dict[str, object]:
    """Decode one TOML document.

    Returns:
        The top-level table.

    Raises:
        UndecodableError: the document is refused; the text says why.

    """
    text = _text(source)
    try:
        table: object = tomllib.loads(text)
    except RecursionError:
        msg = "nested too deep to decode"
        raise UndecodableError(msg) from None
    except ValueError as exc:
        raise UndecodableError(str(exc)) from None
    return as_object(table, "the document")


def is_object(value: object) -> TypeIs[dict[str, object]]:
    """Say whether a decoded value is an object keyed by strings.

    Returns:
        True for a dict whose every key is a str.

    """
    return isinstance(value, dict) and all(isinstance(k, str) for k in value)


def is_array(value: object) -> TypeIs[list[object]]:
    """Say whether a decoded value is an array.

    Returns:
        True for a list.

    """
    return isinstance(value, list)


def is_strings(value: object) -> TypeIs[list[str]]:
    """Say whether a decoded value is an array of strings.

    Returns:
        True for a list whose every item is a str.

    """
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def as_object(value: object, where: str) -> dict[str, object]:
    """Narrow a decoded value to an object keyed by strings.

    Returns:
        The object.

    Raises:
        UndecodableError: it is not one, naming `where`.

    """
    if not is_object(value):
        msg = f"{where} is not an object"
        raise UndecodableError(msg)
    return value


def as_array(value: object, where: str) -> list[object]:
    """Narrow a decoded value to an array.

    Returns:
        The array.

    Raises:
        UndecodableError: it is not one, naming `where`.

    """
    if not is_array(value):
        msg = f"{where} is not a list"
        raise UndecodableError(msg)
    return value


def as_str(value: object, where: str) -> str:
    """Narrow a decoded value to a string.

    Returns:
        The string.

    Raises:
        UndecodableError: it is not one, naming `where`.

    """
    if not isinstance(value, str):
        msg = f"{where} is not a string"
        raise UndecodableError(msg)
    return value


def as_int(value: object, where: str) -> int:
    """Narrow a decoded value to an integer; a bool is not one.

    Returns:
        The integer.

    Raises:
        UndecodableError: it is not one, naming `where`.

    """
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{where} is not an integer"
        raise UndecodableError(msg)
    return value
