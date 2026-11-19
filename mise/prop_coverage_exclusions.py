#!/usr/bin/env python3
"""coverage-exclusions.py's markers, on sources nobody wrote down.

The properties of `test_coverage_exclusions.py`'s fixed rows (#1548),
over generated sources in each language it reads:

- Invariance (V): a marker is a finding exactly when it is a comment,
  at the line it is written on, however its case and spacing are
  spelt; the same text inside a string literal never is.
- Refusal (R): `scan` reads any bytes under any suffix and answers
  with findings, never a traceback.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

_SPEC = importlib.util.spec_from_file_location(
    "coverage_exclusions",
    Path(__file__).with_name("coverage-exclusions.py"),
)
ce = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ce)


@st.composite
def python_marker(draw: st.DrawFn) -> str:
    """Spell a Python coverage pragma the way its rule accepts.

    Returns:
        The comment text, `#` first.

    """
    word = draw(st.sampled_from(["no cover", "no branch", "nocover", "no  branch"]))
    sep = draw(st.sampled_from([":", " ", ": ", ""]))
    text = f"#{draw(st.sampled_from(['', ' ']))}pragma{sep}{word}"
    flips = draw(st.lists(st.booleans(), min_size=len(text), max_size=len(text)))
    return "".join(c.swapcase() if f else c for c, f in zip(text, flips, strict=True))


# Per by-line language: a marker comment, and a code line it may follow.
LINE_MARKERS = {
    ".rs": (["#[coverage(off)]", "#![cfg_attr(coverage, x)]"], "let x = 1;"),
    ".ts": (
        ["// istanbul ignore next", "/* c8 ignore start */", "// v8 ignore"],
        "x();",
    ),
    ".go": (["//go:build !coverage", "// coverage:ignore"], "x := 1"),
}


class Python(unittest.TestCase):
    """A Python pragma counts as a comment only."""

    @given(
        st.lists(
            st.tuples(st.sampled_from(["comment", "string", "plain"]), python_marker()),
            max_size=8,
        )
    )
    @example([("string", "# pragma: no cover")])
    def test_comments_count_strings_do_not(self, rows: list[tuple[str, str]]) -> None:
        """Every comment marker at its line; no string marker at all."""
        lines, expected = [], []
        for number, (how, marker) in enumerate(rows, start=1):
            if how == "comment":
                lines.append(f"x = 1  {marker}")
                expected.append(number)
            elif how == "string":
                lines.append(f"x = {marker!r}")
            else:
                lines.append("x = 1")
        found = ce.python("m.py", "\n".join(lines) + "\n")
        self.assertEqual([f.line for f in found], expected, lines)


class ByLine(unittest.TestCase):
    """The other languages: a quoted marker is not a marker."""

    @given(
        st.sampled_from(sorted(LINE_MARKERS)),
        st.lists(
            st.tuples(
                st.sampled_from(["comment", "string", "plain"]), st.integers(0, 2)
            ),
            max_size=8,
        ),
    )
    @example(".ts", [("string", 0)])
    def test_quoted_markers_never_count(
        self, suffix: str, rows: list[tuple[str, int]]
    ) -> None:
        """Every marker line is found; the same text quoted is not."""
        markers, code = LINE_MARKERS[suffix]
        rules = {".rs": ce.RUST_RULES, ".ts": ce.SCRIPT_RULES, ".go": ce.GO_RULES}[
            suffix
        ]
        lines, expected = [], []
        for number, (how, pick) in enumerate(rows, start=1):
            marker = markers[pick % len(markers)]
            if how == "comment":
                lines.append(marker)
                expected.append(number)
            elif how == "string":
                lines.append(f'{code} s = "{marker}";')
            else:
                lines.append(code)
        found = ce.by_line(f"m{suffix}", "\n".join(lines), rules)
        self.assertEqual([f.line for f in found], expected, lines)


class Refusal(unittest.TestCase):
    """Any file, any language: findings, never a traceback."""

    @given(
        st.binary(max_size=200),
        st.sampled_from([".py", ".pyi", ".rs", ".ts", ".go", ".txt"]),
    )
    @example(b"x = '''\n# pragma: no cover\n", ".py")
    @example(b"\x00# pragma: no cover\n", ".py")
    def test_scan_answers_any_bytes(self, data: bytes, suffix: str) -> None:
        """A list of findings, each on a line the file has."""
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / f"m{suffix}"
            path.write_bytes(data)
            found = ce.scan(str(path))
        for finding in found:
            self.assertGreaterEqual(finding.line, 1)


if __name__ == "__main__":
    unittest.main()
