"""pg_schema.py's normalised surface, on schemas nobody wrote down.

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
import io
import tempfile
from pathlib import Path

import pytest
from hypothesis import example
from hypothesis import strategies as st

from belt import pg_schema as ps
from belt.typed_hypothesis import given
from tests import support

WORD = st.from_regex(r"[A-Za-z0-9_(),'\"]{1,8}", fullmatch=True)
STATEMENT = st.lists(WORD, min_size=1, max_size=5)
GAP = st.sampled_from([" ", "\n", "\t", "  \n  "])
COMMENT = st.sampled_from([
    "",
    "-- src/lib.rs:33\n",
    "/* generated */",
    "/*\n multi\n*/",
])


def draw_spelt(draw: st.DrawFn, statements: list[list[str]]) -> str:
    """Write statements in any order, with comments and whitespace.

    Returns:
        Raw SQL text.

    """
    order = draw(st.permutations(statements))
    out: list[str] = []
    for words in order:
        out.extend((
            draw(COMMENT),
            draw(GAP).join(words),
            draw(GAP) + ";" + draw(GAP),
        ))
    return "".join(out)


spelt = st.composite(draw_spelt)


class TestNormalise:
    """The surface is the set of statements, nothing else."""

    @given(st.text(max_size=300))
    @example("a /* x */ - - b;")
    @example("/--x\n*;")
    def test_twice_is_once(self, text: str) -> None:
        """A normalised surface is its own normal form."""
        once = ps.normalise(text)
        assert ps.normalise(once) == once

    @given(st.data(), st.lists(STATEMENT, min_size=1, max_size=6))
    def test_spelling_does_not_matter(
        self, data: st.DataObject, statements: list[list[str]]
    ) -> None:
        """Two spellings of the same statements are one surface."""
        one = data.draw(spelt(statements))
        two = data.draw(spelt(statements))
        assert ps.normalise(one) == ps.normalise(two)
        expected = "\n".join(sorted(" ".join(s) + ";" for s in statements)) + "\n"
        assert ps.normalise(one) == expected

    @given(st.data(), st.lists(STATEMENT, min_size=1, max_size=6), WORD)
    def test_a_changed_statement_changes_it(
        self, data: st.DataObject, statements: list[list[str]], word: str
    ) -> None:
        """Appending a word to one statement is a different surface."""
        changed = [list(s) for s in statements]
        changed[data.draw(st.integers(0, len(changed) - 1))].append(word)
        assert ps.normalise(data.draw(spelt(statements))) != ps.normalise(
            data.draw(spelt(changed))
        )


class TestRefusal:
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
            with (
                pytest.MonkeyPatch.context() as patched,
                contextlib.redirect_stderr(err),
            ):
                support.name_task(patched, "lint:pg-schema")
                got = ps.surface(str(path))
        if got is None:
            assert "lint:pg-schema:" in err.getvalue()
        else:
            assert got.endswith(";\n")
