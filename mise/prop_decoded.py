#!/usr/bin/env python3
"""decoded.py's loaders, over inputs nobody wrote down (#1676).

The properties of `test_decoded.py`'s rows:

- Totality (T): `load_json` and `load_toml` of any bytes and any text
  return a value or raise `UndecodableError`, and nothing else.
- Round trip (R): `load_json(json.dumps(v))` is `v` for any finite JSON
  value with unique keys, which is everything the canon writes.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import json
import unittest
from collections.abc import Callable

import decoded
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

# A JSON scalar the canon could write: no NaN, no infinity.
SCALAR: st.SearchStrategy[object] = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(),
)


def nested(children: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Wrap values in an array or an object with unique string keys.

    Returns:
        The strategy one level deeper.

    """
    arrays: st.SearchStrategy[object] = st.lists(children, max_size=4)
    objects: st.SearchStrategy[object] = st.dictionaries(
        st.text(), children, max_size=4
    )
    return st.one_of(arrays, objects)


VALUE: st.SearchStrategy[object] = st.recursive(SCALAR, nested, max_leaves=20)
# One value of every kind, nested.
SAMPLE: dict[str, object] = {"a": [1, 2.5, None, "x"], "b": {"": True}}

# The loaders, each judged the same way.
Loader = Callable[[str | bytes], object]
LOADERS: tuple[Loader, ...] = (decoded.load_json, decoded.load_toml)


def answers(load: Loader, source: str | bytes) -> bool:
    """Load source; a value or the one refusal is an answer.

    Returns:
        True once the loader returned or refused; anything else raises.

    """
    try:
        load(source)
    except decoded.UndecodableError:
        return True
    return True


class Totality(unittest.TestCase):
    """T: a value or UndecodableError, and nothing else, from any input."""

    @given(st.binary())
    @example(b"\xff")
    @example(b'{"a": 1, "a": 2}')
    @example(b"[NaN]")
    @example(b"[1e999]")
    @example(b"[" * 100_000)
    def test_any_bytes(self, source: bytes) -> None:
        """Both loaders answer any bytes."""
        for load in LOADERS:
            self.assertTrue(answers(load, source))

    @given(st.text())
    @example("a = " + "[" * 10_000 + "]" * 10_000)
    @example("1" * 5_000)
    def test_any_text(self, source: str) -> None:
        """Both loaders answer any text."""
        for load in LOADERS:
            self.assertTrue(answers(load, source))


class RoundTrip(unittest.TestCase):
    """R: what json.dumps writes, load_json reads back unchanged."""

    @given(VALUE)
    @example(SAMPLE)
    def test_dumps_then_load(self, value: object) -> None:
        """Every finite value with unique keys survives the trip."""
        self.assertEqual(value, decoded.load_json(json.dumps(value)))
        self.assertEqual(value, decoded.load_json(json.dumps(value).encode()))


if __name__ == "__main__":
    unittest.main()
