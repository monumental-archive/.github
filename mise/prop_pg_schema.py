#!/usr/bin/env python3
"""pg-schema.py's normalised surface, on schemas nobody wrote down.

The properties of `test_pg_schema.py`'s fixed rows (#1548), over
generated `cargo pgrx schema` output:

- Idempotence (I): normalising a normalised surface changes nothing.
- Invariance (V): the order of statements, pgrx's source-location
  comments, block comments and whitespace never change the surface;
  changing one statement's text always does.
- Refusal (R): `surface` reads any bytes and answers with a surface or
  None after its own message, never a traceback.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

_SPEC = importlib.util.spec_from_file_location(
    "pg_schema",
    Path(__file__).with_name("pg-schema.py"),
)
ps = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ps)

WORD = st.from_regex(r"[A-Za-z0-9_(),'\"]{1,8}", fullmatch=True)
STATEMENT = st.lists(WORD, min_size=1, max_size=5)
GAP = st.sampled_from([" ", "\n", "\t", "  \n  "])
COMMENT = st.sampled_from([
    "",
    "-- src/lib.rs:33\n",
    "/* generated */",
    "/*\n multi\n*/",
])


@st.composite
def spelt(draw: st.DrawFn, statements: list[list[str]]) -> str:
    """Write statements in any order, with comments and whitespace.

    Returns:
        Raw SQL text.

    """
    order = draw(st.permutations(statements))
    out = []
    for words in order:
        out.extend((
            draw(COMMENT),
            draw(GAP).join(words),
            draw(GAP) + ";" + draw(GAP),
        ))
    return "".join(out)


class Normalise(unittest.TestCase):
    """The surface is the set of statements, nothing else."""

    @given(st.text(max_size=300))
    @example("a /* x */ - - b;")
    @example("/--x\n*;")
    def test_twice_is_once(self, text: str) -> None:
        """A normalised surface is its own normal form."""
        once = ps.normalise(text)
        self.assertEqual(ps.normalise(once), once)

    @given(st.data(), st.lists(STATEMENT, min_size=1, max_size=6))
    def test_spelling_does_not_matter(
        self, data: st.DataObject, statements: list[list[str]]
    ) -> None:
        """Two spellings of the same statements are one surface."""
        one = data.draw(spelt(statements))
        two = data.draw(spelt(statements))
        self.assertEqual(ps.normalise(one), ps.normalise(two))
        expected = "\n".join(sorted(" ".join(s) + ";" for s in statements)) + "\n"
        self.assertEqual(ps.normalise(one), expected)

    @given(st.data(), st.lists(STATEMENT, min_size=1, max_size=6), WORD)
    def test_a_changed_statement_changes_it(
        self, data: st.DataObject, statements: list[list[str]], word: str
    ) -> None:
        """Appending a word to one statement is a different surface."""
        changed = [list(s) for s in statements]
        changed[data.draw(st.integers(0, len(changed) - 1))].append(word)
        self.assertNotEqual(
            ps.normalise(data.draw(spelt(statements))),
            ps.normalise(data.draw(spelt(changed))),
        )


class Refusal(unittest.TestCase):
    """Any generated file: a surface, or a refusal by name."""

    @given(st.binary(max_size=200))
    @example(b"\xff;")
    @example(b"-- only a comment\n")
    def test_surface_answers_any_bytes(self, data: bytes) -> None:
        """A non-empty surface, or None and a message."""
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "raw.sql"
            path.write_bytes(data)
            with contextlib.redirect_stderr(err):
                got = ps.surface("lint:pg-schema", str(path))
        if got is None:
            self.assertIn("lint:pg-schema:", err.getvalue())
        else:
            self.assertTrue(got.endswith(";\n"))


if __name__ == "__main__":
    unittest.main()
