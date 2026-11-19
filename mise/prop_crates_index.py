#!/usr/bin/env python3
"""crates-index.py's index paths, on crate names nobody wrote down.

The properties of `test_crates_index.py`'s fixed rows (#1548), over
generated crate names:

- Invariance (V): a name's record is the lowercased name, under
  cargo's bucket for its length, whatever its case: two spellings
  that differ only in case share a record, and two names that differ
  otherwise never do.
- Refusal (R): `main` answers any argument list with a URL (0) or its
  own usage or empty-name refusal (2), and never raises.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path

from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

_SPEC = importlib.util.spec_from_file_location(
    "crates_index",
    Path(__file__).with_name("crates-index.py"),
)
ci = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ci)

# crates.io names: ASCII letters, digits, `-` and `_`, letter first.
NAME = st.from_regex(r"[A-Za-z][A-Za-z0-9_-]{0,63}", fullmatch=True)


def bucket(lower: str) -> str:
    """Cargo's directory for a lowercased name, from its documentation.

    Returns:
        The directory part of the record's path.

    """
    if len(lower) <= 2:  # ruff: ignore[magic-value-comparison] - cargo's buckets
        return str(len(lower))
    if len(lower) == 3:  # ruff: ignore[magic-value-comparison] - cargo's buckets
        return f"3/{lower[0]}"
    return f"{lower[0:2]}/{lower[2:4]}"


class Invariance(unittest.TestCase):
    """The record follows the crate, not the spelling."""

    @given(NAME)
    @example("A")
    @example("abc")
    @example("Serde")
    def test_record_is_the_lowercased_name_in_its_bucket(self, name: str) -> None:
        """Bucket by length, then the lowercased name."""
        lower = name.lower()
        self.assertEqual(ci.index_path(name), f"{bucket(lower)}/{lower}")
        self.assertEqual(ci.index_path(name.swapcase()), ci.index_path(name))
        self.assertEqual(ci.index_url(name), f"{ci.INDEX}/{ci.index_path(name)}")

    @given(NAME, NAME)
    def test_distinct_crates_distinct_records(self, one: str, two: str) -> None:
        """Names that differ beyond case never share a record."""
        if one.lower() == two.lower():
            return
        self.assertNotEqual(ci.index_path(one), ci.index_path(two))


class Refusal(unittest.TestCase):
    """Any argument list gets an answer by name."""

    @given(st.lists(st.text(max_size=20), max_size=3))
    @example(["url", ""])
    @example(["url"])
    def test_main_answers_any_argv(self, argv: list[str]) -> None:
        """A URL and 0, or a refusal on stderr and 2."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = ci.main(argv)
        self.assertIn(status, (0, 2))
        if status == 0:
            self.assertTrue(out.getvalue().startswith(ci.INDEX))
        else:
            self.assertTrue(err.getvalue().strip())


if __name__ == "__main__":
    unittest.main()
