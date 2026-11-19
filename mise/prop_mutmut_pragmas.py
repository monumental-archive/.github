#!/usr/bin/env python3
"""mutmut-pragmas.py's pragma finder, on modules nobody wrote down.

The properties of `test_mutmut_pragmas.py`'s fixed rows (#1548), over
generated modules:

- Invariance (V): a pragma is reported exactly when it is a comment,
  at the line it is written on; the same text inside a string, a
  triple-quoted string across lines included, never is.
- Refusal (R): `main` answers any file with 0 and its records, or 1
  and the file named on stderr, and never raises, whatever bytes the
  file holds.

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
    "mutmut_pragmas",
    Path(__file__).with_name("mutmut-pragmas.py"),
)
mp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mp)

PRAGMA = "# pragma: no mutate"
# One logical statement each, as (source lines, the offsets of the lines
# that carry a pragma COMMENT).
PIECES = [
    (["x = 1"], []),
    ([f"x = 1  {PRAGMA}"], [0]),
    ([f"x = 1  {PRAGMA} -- a reason"], [0]),
    ([PRAGMA], [0]),
    ([f"s = '{PRAGMA}'"], []),
    ([f's = "{PRAGMA}"  {PRAGMA}'], [0]),
    (['s = """', PRAGMA, '"""'], []),
    (['s = """', PRAGMA, f'"""  {PRAGMA}'], [2]),
    (["y = (", f"    1,  {PRAGMA}", ")"], [1]),
    (["# pragma: mutate"], []),
]


def written(path: Path, data: bytes) -> Path:
    """Write bytes to a file.

    Returns:
        The path.

    """
    path.write_bytes(data)
    return path


class Invariance(unittest.TestCase):
    """A comment is a pragma; a string never is."""

    @given(st.lists(st.sampled_from(range(len(PIECES))), max_size=8))
    @example([6])
    @example([7, 1])
    def test_exactly_the_comment_lines(self, picks: list[int]) -> None:
        """Every pragma comment, at its line, and nothing else."""
        lines: list[str] = []
        expected: list[int] = []
        for pick in picks:
            body, marks = PIECES[pick]
            expected += [len(lines) + 1 + m for m in marks]
            lines += body
        with tempfile.TemporaryDirectory() as scratch:
            path = written(Path(scratch) / "m.py", ("\n".join(lines) + "\n").encode())
            found = mp.pragmas(path)
        self.assertEqual([line for line, _ in found], expected, lines)
        for _, text in found:
            self.assertTrue(text.startswith("#"))


class Refusal(unittest.TestCase):
    """Any file is answered by name, never a traceback."""

    @given(st.binary(max_size=200))
    @example(b"x = '\xff'\n")
    @example(b"\xef\xbb\xbf# coding: latin-1\n")
    @example(b"s = '''\n")
    def test_main_answers_any_bytes(self, data: bytes) -> None:
        """0 and records, or 1 and the file named on stderr."""
        with tempfile.TemporaryDirectory() as scratch:
            path = written(Path(scratch) / "m.py", data)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = mp.main([str(path)])
        self.assertIn(status, (0, 1))
        if status == 1:
            self.assertIn(str(path), err.getvalue())


if __name__ == "__main__":
    unittest.main()
