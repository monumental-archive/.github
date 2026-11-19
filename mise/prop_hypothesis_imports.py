#!/usr/bin/env python3
"""hypothesis_imports.py's importer test, on modules nobody wrote down.

The properties of `test_hypothesis_imports.py`'s fixed rows (#1548),
over generated modules:

- Invariance (V): a module imports Hypothesis exactly when one of its
  statements is an absolute `import hypothesis…` or `from
  hypothesis… import`, wherever it sits (top level, a function, a
  `try`); the same text in a string, a lookalike module name
  (`hypothesis_x`, `hypothesisx`) or a relative import never makes it
  one.
- Refusal (R): `main` names every file it is given, as an importer or
  as unreadable, or says nothing of it, and never raises, whatever
  bytes the file holds.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import ast
import contextlib
import io
import tempfile
import unittest
from itertools import starmap
from pathlib import Path

import hypothesis_imports as hi
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

IMPORTERS = [
    "import hypothesis",
    "import hypothesis.strategies as st",
    "import os, hypothesis",
    "from hypothesis import given",
    "from hypothesis.strategies import text",
]
OTHERS = [
    "import os",
    "import hypothesis_x",
    "import hypothesisx.y",
    "from hypothesisx import y",
    "from . import hypothesis",
    "from .hypothesis import given",
    "x = 'import hypothesis'",
    "'''from hypothesis import given'''",
    "# import hypothesis",
    "pass",
]


def wrapped(statement: str, how: str) -> str:
    """Place a statement at top level, in a function or in a `try`.

    Returns:
        Module source holding the statement.

    """
    if how == "def":
        return f"def f():\n    {statement}\n    pass\n"
    if how == "try":
        return f"try:\n    {statement}\n    pass\nexcept ImportError:\n    pass\n"
    return f"{statement}\n"


STATEMENT = st.tuples(
    st.sampled_from(IMPORTERS + OTHERS),
    st.sampled_from(["top", "def", "try"]),
)


class Invariance(unittest.TestCase):
    """An importer is a statement, wherever it sits."""

    @given(st.lists(STATEMENT, max_size=6))
    @example([("from . import hypothesis", "top")])
    @example([("x = 'import hypothesis'", "def")])
    def test_importer_exactly_when_a_statement_imports(
        self, statements: list[tuple[str, str]]
    ) -> None:
        """Strings, lookalikes and relative imports never count."""
        source = "".join(starmap(wrapped, statements))
        expected = any(s in IMPORTERS for s, _ in statements)
        self.assertEqual(hi.imports_hypothesis(ast.parse(source)), expected, source)


class Refusal(unittest.TestCase):
    """Any file is answered, never a traceback."""

    @given(st.binary(max_size=200))
    @example(b"\xff\xfe")
    @example(b"x = 1\x00")
    @example(b"(" * 300)
    def test_main_answers_any_bytes(self, data: bytes) -> None:
        """Exit 0; the file is named or not, never an exception."""
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "m.py"
            path.write_bytes(data)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                status = hi.main([str(path)])
        self.assertEqual(status, 0)
        for line in out.getvalue().splitlines():
            self.assertTrue(line.startswith(str(path)))


if __name__ == "__main__":
    unittest.main()
