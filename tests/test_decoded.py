"""decoded.py's refusals and readers, and the law that it alone parses (#1676).

The rows: each refusal of `load_json` and `load_toml` by its message
(a duplicate key, `NaN`, `Infinity`, overflow, depth, bytes that are
not UTF-8, a syntax error), and each reader narrowing or refusing by
`where`.

The law: no tracked `.py` parses JSON or TOML outside `mise/decoded.py`.
It is read from the syntax tree with import aliases resolved, so
`import json as j`, `from json import loads` and
`from tomllib import load as l` are each the same parse; `json.dumps`
writes and is free. A parse anywhere else is red by file and line. The
list of files not yet moved (#1544's shape) was emptied by #1604's
slices and deleted by its last step, so no file is excused.
"""

import ast
import json
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

import pytest

from belt import decoded
from tests import support

if TYPE_CHECKING:
    from pathlib import Path

HERE = support.MISE
CANON = HERE.parent
DEEP = 100_000
# A raising reader: a decoded value and where it came from.
Reader = Callable[[object, str], object]

# The parses the law refuses outside decoded.py, fully qualified.
PARSES = frozenset({
    "json.load",
    "json.loads",
    "json.JSONDecoder",
    "json.decoder.JSONDecoder",
    "tomllib.load",
    "tomllib.loads",
})

# The one file that parses.
LOADER = "mise/decoded.py"


def dotted(node: ast.expr) -> str | None:
    """Spell an attribute chain rooted at a name.

    Returns:
        `a.b.c`, or None when the chain is not rooted at a name.

    """
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def imported(tree: ast.Module) -> tuple[dict[str, str], list[int]]:
    """Resolve what each imported name stands for.

    Returns:
        Each local name mapped to the qualified name it binds, and the
        line of each import that binds a parse directly.

    """
    names: dict[str, str] = {}
    found: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                names[alias.asname or top] = alias.name if alias.asname else top
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                qualified = f"{node.module}.{alias.name}"
                if qualified in PARSES:
                    found.append(node.lineno)
                else:
                    names[alias.asname or alias.name] = qualified
    return names, found


def parses(text: str) -> list[int]:
    """Find the lines that parse a document, aliases resolved.

    Returns:
        The line of each import or use of a parse, in order.

    """
    tree = ast.parse(text)
    names, found = imported(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        chain = dotted(node)
        if chain is None:
            continue
        first, _, rest = chain.partition(".")
        if first in names and f"{names[first]}.{rest}" in PARSES:
            found.append(node.lineno)
    return sorted(found)


def judge(root: Path) -> list[str]:
    """Hold the tracked Python under root to the law.

    Returns:
        One line per parse outside the loader, by file and line.

    """
    out: list[str] = []
    for path in support.tracked(root, "*.py"):
        if path == LOADER:
            continue
        lines = parses((root / path).read_text(encoding="utf-8"))
        out.extend(f"{path}:{line}: parses outside {LOADER}" for line in lines)
    return out


# Strict JSON: every input is a value or one named refusal.
def refused(source: str | bytes) -> str:
    """Load source and return the refusal's text.

    Returns:
        The message of the UndecodableError.

    """
    with pytest.raises(decoded.UndecodableError) as caught:
        decoded.load_json(source)
    return str(caught.value)


def test_a_document_decodes() -> None:
    """Strict JSON in, the same value out, from text or bytes."""
    text = '{"a": [1, 2.5, "x", true, null], "b": {}}'
    want = {"a": [1, 2.5, "x", True, None], "b": {}}
    assert want == decoded.load_json(text)
    assert want == decoded.load_json(text.encode())


def test_a_duplicate_key_is_refused() -> None:
    """I-JSON's unique keys (RFC 7493), at any depth."""
    assert refused('{"a": 1, "a": 2}') == 'duplicate key "a"'
    assert refused('[{"b": 1, "b": 1}]') == 'duplicate key "b"'


@pytest.mark.parametrize("name", ["NaN", "Infinity", "-Infinity"])
def test_nan_and_the_infinities_are_refused(name: object) -> None:
    """JSON has no NaN, Infinity or -Infinity."""
    assert f"{name} is not a JSON number" == refused(f"[{name}]")


def test_a_number_past_a_finite_float_is_refused() -> None:
    """1e999 would decode to inf; it is refused instead."""
    assert refused("[1e999]") == "1e999 does not fit a finite number"


def test_nesting_past_the_bound_is_refused() -> None:
    """MAX_DEPTH decodes and one more is refused, on every machine."""
    bound = decoded.MAX_DEPTH
    assert bound == 512
    deepest = "[" * bound + "]" * bound
    assert decoded.load_json(deepest) is not None
    message = f"nested deeper than {bound} levels"
    assert message == refused("[" * (bound + 1) + "]" * (bound + 1))
    objects = '{"a":' * (bound + 1) + "1" + "}" * (bound + 1)
    assert message == refused(objects)
    assert message == refused("[" * DEEP)


def test_a_bracket_inside_a_string_is_not_nesting() -> None:
    """Brackets in a string, past an escaped quote, do not count."""
    inside = "[" * (decoded.MAX_DEPTH + 1)
    plain: list[object] = [inside]
    assert plain == decoded.load_json(f'["{inside}"]')
    quoted = '"' + inside
    escaped: list[object] = [quoted]
    assert escaped == decoded.load_json(f'["\\{quoted}"]')


def test_an_unterminated_string_is_scanned_once() -> None:
    """2 MB of escaped quotes after one opening quote: one pass, json's refusal.

    The depth scan reads each character once, counted as it reads them,
    never by timing: a scan that looked for a whole string at each
    quote was quadratic on exactly this text.
    """
    text = '"' + '\\"' * 1_000_000
    assert refused(text) == "Unterminated string starting at: line 1 column 1 (char 0)"
    visits: list[str] = []

    def counted() -> Iterator[str]:
        """Yield the text's characters, recording each.

        Yields:
            Each character, once.

        """
        for char in text:
            visits.append(char)
            yield char

    decoded.within_depth(counted())
    assert len(text) == len(visits)


def test_a_stack_too_small_for_the_bound_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backstop: RecursionError under the bound is still a refusal."""

    def too_deep(*_args: object, **_kwargs: object) -> object:
        raise RecursionError

    monkeypatch.setattr("json.loads", too_deep)
    assert refused("[]") == "nested too deep to decode"


def test_bytes_that_are_not_utf8_are_refused() -> None:
    """Only UTF-8; json's own UTF-16 and UTF-32 detection is not used."""
    assert refused(b"[\xff]") == "not UTF-8: byte 1 cannot be decoded"
    # UTF-16 with its byte-order mark, which json.loads would accept.
    assert refused("[1]".encode("utf-16")) == "not UTF-8: byte 0 cannot be decoded"


def test_a_syntax_error_keeps_json_s_message() -> None:
    """The decoder's own text, which says where."""
    assert refused("[") == "Expecting value: line 1 column 2 (char 1)"


# TOML: a table or one named refusal; TOML's nan and inf stay.
def load_toml_refused(source: str | bytes) -> str:
    """Load source and return the refusal's text.

    Returns:
        The message of the UndecodableError.

    """
    with pytest.raises(decoded.UndecodableError) as caught:
        decoded.load_toml(source)
    return str(caught.value)


def test_load_toml_a_document_decodes() -> None:
    """A table in, the same table out; inf is TOML's own."""
    table = decoded.load_toml(b'a = 1\n[b]\nc = "x"\nd = inf\n')
    want: dict[str, object] = {"a": 1, "b": {"c": "x", "d": float("inf")}}
    assert want == table


def test_a_toml_error_keeps_tomllib_s_message() -> None:
    """The decoder's own text, which says where."""
    assert load_toml_refused("a =") == "Invalid value (at end of document)"


def test_load_toml_bytes_that_are_not_utf8_are_refused() -> None:
    """TOML is UTF-8."""
    assert load_toml_refused(b'a = "\xff"') == "not UTF-8: byte 5 cannot be decoded"


def test_nesting_too_deep_is_refused() -> None:
    """A depth the decoder cannot reach is a refusal, not a crash."""
    depth = 10_000
    source = "a = " + "[" * depth + "]" * depth
    assert load_toml_refused(source) == "nested too deep to decode"


# Each reader narrows a decoded value or refuses it by `where`.
def test_the_predicates() -> None:
    """is_object, is_array and is_strings, over every JSON kind."""
    values: list[object] = [{"a": 1}, {1: 1}, [1], ["a"], "a", 1, None]
    objects: list[bool] = [decoded.is_object(v) for v in values]
    arrays: list[bool] = [decoded.is_array(v) for v in values]
    strings: list[bool] = [decoded.is_strings(v) for v in values]
    no, yes = False, True
    want_objects: list[bool] = [yes, no, no, no, no, no, no]
    want_arrays: list[bool] = [no, no, yes, yes, no, no, no]
    want_strings: list[bool] = [no, no, no, yes, no, no, no]
    assert want_objects == objects
    assert want_arrays == arrays
    assert want_strings == strings


def test_each_raising_reader_names_where() -> None:
    """A wrong kind is refused with `where` and the kind it is not."""
    empty: list[object] = []
    rows: list[tuple[str, Reader, object, str]] = [
        ("as_object", decoded.as_object, empty, "the doc is not an object"),
        ("as_object", decoded.as_object, {1: 1}, "the doc is not an object"),
        ("as_array", decoded.as_array, {"a": 1}, "the doc is not a list"),
        ("as_str", decoded.as_str, 1, "the doc is not a string"),
        ("as_int", decoded.as_int, "1", "the doc is not an integer"),
        ("as_int", decoded.as_int, True, "the doc is not an integer"),
    ]
    for _name, reader, value, message in rows:
        with pytest.raises(decoded.UndecodableError) as caught:
            reader(value, "the doc")
        assert message == str(caught.value)


def test_each_raising_reader_returns_its_kind() -> None:
    """The right kind comes back as it went in."""
    table: dict[str, object] = {"a": 1}
    items: list[object] = [1]
    assert table == decoded.as_object(table, "w")
    assert items == decoded.as_array(items, "w")
    assert decoded.as_str("a", "w") == "a"
    assert decoded.as_int(3, "w") == 3


# Only decoded.py parses, and no file is excused.
def test_the_tree_holds_the_law() -> None:
    """No tracked file parses a document outside decoded.py."""
    none: list[str] = []
    assert none == judge(CANON)


def test_each_spelling_is_a_parse() -> None:
    """Every import spelling of a parse is found on its own line."""
    text = (
        "import json\n"
        "import json as j\n"
        "import tomllib as t\n"
        "from json import loads\n"
        "from tomllib import load as l\n"
        "from json import JSONDecoder\n"
        "import json.decoder\n"
        "a = json.loads('1')\n"
        "b = j.load(f)\n"
        "c = t.loads('')\n"
        "d = json.decoder.JSONDecoder()\n"
        "e = json.JSONDecoder\n"
    )
    want: list[int] = [4, 5, 6, 8, 9, 10, 11, 12]
    assert want == parses(text)


def test_writing_is_not_parsing() -> None:
    """json.dumps, and a parse named only in a string, are free."""
    text = "import json\nx = json.dumps({})\ny = 'json.loads(s)'\n"
    none: list[int] = []
    assert none == parses(text)


def test_the_loader_is_the_one_exception() -> None:
    """decoded.py parses, and it is the file the law excepts."""
    assert parses((CANON / LOADER).read_text(encoding="utf-8"))


def test_a_document_round_trips_through_the_loader() -> None:
    """What the canon writes with json.dumps, decoded.py reads back."""
    value: object = {"a": [1, 2.5, "x", None], "b": {"c": True}}
    assert value == decoded.load_json(json.dumps(value))
