#!/usr/bin/env python3
"""security_config.py's record comparison, on configurations nobody wrote.

The properties of `test_security_config.py`'s fixed rows (#1548), over
generated records and live configurations:

- Round trip (T): a value written by `describe` decodes to itself.
- Invariance (V): a live configuration stating exactly the record, plus
  any server-owned fields, in any key order, agrees; changing one
  stated value, or dropping it, or setting a field the record does not
  state, is exactly one finding naming that field.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import unittest

import decoded
import security_config as sc
from hypothesis import strategies as st
from typed_hypothesis import given

SCALAR: st.SearchStrategy[object] = (
    st.none() | st.booleans() | st.integers() | st.text(max_size=8)
)


def nested(inner: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Wrap values in an array or an object.

    Returns:
        The strategy one level deeper.

    """
    arrays: st.SearchStrategy[object] = st.lists(inner, max_size=3)
    objects: st.SearchStrategy[object] = st.dictionaries(
        st.text(max_size=6), inner, max_size=3
    )
    return arrays | objects


def unowned(field: str) -> bool:
    """Tell whether a field is neither the server's nor the record's own.

    Returns:
        True when it is a configuration field.

    """
    return field not in sc.SERVER_FIELDS | sc.OWN_FIELDS


JSON: st.SearchStrategy[object] = st.recursive(SCALAR, nested, max_leaves=8)
FIELD = st.from_regex(r"[a-z][a-z_]{2,15}", fullmatch=True).filter(unowned)
RECORD: st.SearchStrategy[dict[str, object]] = st.dictionaries(
    FIELD, JSON, min_size=1, max_size=6
)


class Describe(unittest.TestCase):
    """The record's rendering decodes to the value."""

    @given(JSON)
    def test_round_trip(self, value: object) -> None:
        """load_json(describe(v)) == v."""
        self.assertEqual(decoded.load_json(sc.describe(value)), value)


class Compare(unittest.TestCase):
    """Agreement, or one finding per field."""

    @given(st.data(), RECORD)
    def test_the_record_itself_agrees(
        self, data: st.DataObject, record: dict[str, object]
    ) -> None:
        """Any key order, server fields beside it: no finding."""
        server: dict[str, object] = dict.fromkeys(
            data.draw(st.sets(st.sampled_from(sorted(sc.SERVER_FIELDS)))), 1
        )
        pairs = [*record.items(), *server.items()]
        items = data.draw(st.permutations(pairs))
        own: dict[str, object] = {"name": "org default", "description": "x"}
        found = sc.compare_record({**record, **own}, dict(items), 7)
        self.assertEqual(found, list[str]())

    @given(st.data(), RECORD, st.sampled_from(["change", "drop", "extra"]), FIELD)
    def test_one_difference_one_finding(
        self, data: st.DataObject, record: dict[str, object], how: str, extra: str
    ) -> None:
        """The changed, dropped or unstated field is named once."""
        live = dict(record)
        field = data.draw(st.sampled_from(sorted(record)))
        if how == "change":
            nested_value: list[object] = [live[field]]
            live[field] = nested_value
        elif how == "drop":
            del live[field]
        else:
            if extra in record:
                return
            live[extra] = True
            field = extra
        found = sc.compare_record(record, live, 7)
        self.assertEqual(len(found), 1, found)
        self.assertIn(f"{field}=", found[0])


if __name__ == "__main__":
    unittest.main()
