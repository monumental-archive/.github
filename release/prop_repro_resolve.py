#!/usr/bin/env python3
"""repro_resolve.py's manifest reader, on any JSON.

The properties of `test_repro_resolve.py`'s fixed rows (#1556), over
generated evidence manifests:

- V: `first_entry_name` reads the first entry and only the first: what
  follows it, and the order of what follows it, never moves the answer,
  and the answer is the first entry's `name` as `jq -r` prints it
  (a string as itself, null or false as nothing, anything else as its
  JSON), or nothing where the first entry is not an object.
- R: on any JSON document and any text, it answers or refuses by name
  (`StepError`); it never dies with anything else.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import repro_resolve as resolve
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

# A JSON scalar: JSON has no NaN or Infinity, and the manifest is read
# strictly (#1676), so a float that is not finite is not one.
SCALAR = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text()
)
JSON = st.recursive(
    SCALAR,
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.text(max_size=6), inner, max_size=3)
    ),
    max_leaves=12,
)
ENTRY = st.fixed_dictionaries(
    {}, optional={"name": JSON, "type": st.just("build-subject") | JSON}
)


def written(text: str) -> Path:
    """Write text to a fresh scratch file; the caller unlinks it.

    Returns:
        Its path.

    """
    fd, name = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    path = Path(name)
    path.write_text(text, encoding="utf-8")
    return path


def read(doc: object) -> str:
    """Read one manifest's first entry name.

    Returns:
        The answer.

    """
    path = written(json.dumps(doc))
    try:
        return resolve.first_entry_name(path)
    finally:
        path.unlink()


def model(first: object) -> str:
    """Say what `jq -r '.entries[0].name // empty'` prints for one entry.

    Returns:
        The name as text, or nothing.

    """
    if not isinstance(first, dict):
        return ""
    name = first.get("name")
    if name is None or name is False:
        return ""
    return name if isinstance(name, str) else json.dumps(name)


class OnlyTheFirstEntry(unittest.TestCase):
    """V: the first entry decides; the rest, in any order, do not."""

    @given(ENTRY | JSON, st.lists(ENTRY | JSON, max_size=4), st.randoms())
    def test_the_rest_never_moves_it(
        self, first: object, rest: list[object], shuffle: object
    ) -> None:
        """Same first entry, any tail in any order: the same answer."""
        alone = read({"entries": [first]})
        self.assertEqual(alone, model(first))
        shuffle.shuffle(rest)
        self.assertEqual(read({"entries": [first, *rest]}), alone)


class AnyInput(unittest.TestCase):
    """R: answered or refused by name on anything."""

    @given(st.fixed_dictionaries({}, optional={"entries": SCALAR | JSON}) | JSON)
    @example({"entries": True})
    @example({"entries": {"a": 1}})
    def test_any_json(self, doc: object) -> None:
        """Any JSON document: an answer, or the step's own refusal."""
        self.read_text(json.dumps(doc))

    @given(st.text())
    def test_any_text(self, text: str) -> None:
        """Any text: an answer, or the step's own refusal."""
        self.read_text(text)

    def read_text(self, text: str) -> None:
        """Hold one manifest's reading to a str or a StepError."""
        path = written(text)
        try:
            name = resolve.first_entry_name(path)
        except resolve.StepError as refused:
            self.assertTrue(str(refused).startswith(f"{path} is not "), refused)
        else:
            self.assertIsInstance(name, str)
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
