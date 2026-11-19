#!/usr/bin/env python3
"""mint-doi.py's readers and renderer, on any CITATION.cff and value.

The properties of `test_mint_doi.py`'s fixed rows (#1556):

- R: `read_citation` answers on any text at all, and `licence_ids` on
  any licence line answers or refuses by name (`RefusedError`); neither
  dies with anything else.
- T: an author and a concept DOI written in the generator's format are
  read back as written, whatever other lines surround them.
- V: `licence_ids` is order-insensitive: an expression and any
  reordering of its ids are accepted together or refused together, and
  when accepted they name the same ids.
- I: `compact` of what `compact` wrote, parsed back, is the same text,
  and parses back to the value it rendered.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import json
import unittest
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

_SPEC = importlib.util.spec_from_file_location(
    "mint_doi",
    Path(__file__).with_name("mint-doi.py"),
)
mint = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mint)

SPDX = st.from_regex(r"[A-Za-z0-9.+-]{1,12}", fullmatch=True)
# An id-shaped word, or one of the words the expression may not hold.
WORD = SPDX | st.sampled_from(["WITH", "OR", "AND", "or", "(MIT", "MIT)", "a/b"])
JOIN = st.sampled_from(["OR", "AND"])
NAME = st.from_regex(r"[A-Za-z][A-Za-z' -]{0,12}[A-Za-z]", fullmatch=True)
DOI = st.from_regex(r"10\.[0-9]{4,5}/zenodo\.[0-9]{1,9}", fullmatch=True)
NOISE = st.lists(
    st.text(st.characters(exclude_characters="\n\r"), max_size=20), max_size=4
)
JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(),
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.text(max_size=6), inner, max_size=3)
    ),
    max_leaves=12,
)


def ids_or_refusal(licence: str) -> list[str] | None:
    """Read a licence line's ids.

    Returns:
        The ids, or None when refused by name; a refusal that does not
        name the line is re-raised.

    """
    try:
        return mint.licence_ids(licence)
    except mint.RefusedError as refused:
        if not str(refused).startswith(f"CITATION.cff license '{licence}' "):
            raise
        return None


class AnyCitation(unittest.TestCase):
    """R: the citation readers answer, or refuse by name, on anything."""

    @given(st.text())
    def test_read_citation_answers(self, text: str) -> None:
        """Any text: three fields, never a raise."""
        cite = mint.read_citation(text)
        self.assertIsInstance(cite.licence, str)
        self.assertIsInstance(cite.concept_doi, str)

    @given(st.text())
    @example("MIT WITH")
    @example("")
    def test_licence_ids_answers_or_refuses(self, licence: str) -> None:
        """Any licence line: ids, or the step's own refusal."""
        ids = ids_or_refusal(licence)
        if ids is not None:
            self.assertTrue(ids)


class WrittenReadsBack(unittest.TestCase):
    """T: what the generator writes, the reader reads."""

    @given(NAME, NAME, DOI, NOISE, NOISE)
    def test_author_and_concept(
        self, family: str, given_: str, doi: str, before: list[str], after: list[str]
    ) -> None:
        """An author block and an identifiers block read back as written."""
        lines = [
            "cff-version: 1.2.0",
            "authors:",
            f'  - family-names: "{family}"',
            f'    given-names: "{given_}"',
            "identifiers:",
            "  - type: doi",
            f"    value: {doi}",
        ]
        text = "\n".join([
            *[n for n in before if "value:" not in n and not n.startswith("identif")],
            *lines,
            *after,
        ])
        cite = mint.read_citation(text)
        self.assertIn(f"{family}, {given_}", cite.creators)
        self.assertEqual(cite.concept_doi, doi)


class LicenceOrder(unittest.TestCase):
    """V: reordering the ids moves nothing but their order."""

    @given(st.lists(WORD, min_size=1, max_size=4), st.data())
    @example(["MIT", "Apache-2.0"], None)
    def test_order_insensitive(
        self, words: list[str], data: st.DataObject | None
    ) -> None:
        """Same verdict, same ids, whatever order the ids are in."""
        joins = (
            data.draw(st.lists(JOIN, min_size=len(words) - 1, max_size=len(words) - 1))
            if data
            else ["OR"] * (len(words) - 1)
        )
        moved = data.draw(st.permutations(words)) if data else list(reversed(words))

        def expression(ids: list[str]) -> str:
            parts = [ids[0]]
            for join, word in zip(joins, ids[1:], strict=True):
                parts += [join, word]
            return " ".join(parts)

        first, second = (
            ids_or_refusal(expression(words)),
            ids_or_refusal(expression(moved)),
        )
        self.assertEqual(first is None, second is None, (words, moved))
        if first is not None:
            self.assertEqual(sorted(first), sorted(second))
            self.assertEqual(first, [w.lower() for w in words])


class CompactIdempotent(unittest.TestCase):
    """I and T: compacting twice is compacting once, and reads back."""

    @given(JSON)
    def test_compact_twice(self, value: object) -> None:
        """compact(parse(compact(v))) == compact(v), and parses to v."""
        once = mint.compact(value)
        self.assertEqual(mint.compact(json.loads(once)), once)
        self.assertEqual(json.loads(once), value)


if __name__ == "__main__":
    unittest.main()
