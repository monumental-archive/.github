#!/usr/bin/env python3
"""tsconfig-dials.py's level check, on configs nobody wrote down.

The properties of `test_tsconfig_dials.py`'s fixed rows (#1548), over
generated flag files and resolved configs:

- Round trip (T): a level written as tsc-flags.txt lines (a bare flag
  for on, `--flag false` for the undefined-default dials, comments and
  blank lines between) reads back as the same level.
- Invariance (V): a config stating the level, in any key order and
  with any other keys beside it, has no finding; removing a dial or
  flipping it is exactly one finding, naming that dial.
- Refusal (R): whatever tsc's resolved config holds, `main` answers 0
  or 1 with its own sentence, never a traceback (#1555: a top-level
  array raised AttributeError).

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from hypothesis import example
from hypothesis import strategies as st
from org_hypothesis import given

_SPEC = importlib.util.spec_from_file_location(
    "tsconfig_dials",
    Path(__file__).with_name("tsconfig-dials.py"),
)
td = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(td)

KEY = st.from_regex(r"[a-z][A-Za-z]{0,15}", fullmatch=True)
LEVEL = st.dictionaries(KEY, st.booleans(), min_size=1, max_size=8)
OTHER = st.dictionaries(
    st.from_regex(r"_[A-Za-z]{0,8}", fullmatch=True),  # never a KEY
    st.one_of(st.booleans(), st.integers(), st.text(max_size=5)),
    max_size=4,
)


@st.composite
def flags_file(draw: st.DrawFn, level: dict[str, bool]) -> str:
    """Write a level as tsc-flags.txt lines.

    Returns:
        The file's text.

    """
    lines = []
    for key, on in level.items():
        lines.append(draw(st.sampled_from(["", "# a note", "   "])))
        flag = f"--{key}" if on else f"--{key} false"
        lines.append(flag + draw(st.sampled_from(["", "  # why", " "])))
    return "\n".join(lines) + "\n"


class Level(unittest.TestCase):
    """The flag file is the level."""

    @given(st.data(), LEVEL)
    def test_flags_read_back(self, data: st.DataObject, level: dict[str, bool]) -> None:
        """Every dial, with its value, and nothing unreadable."""
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "tsc-flags.txt"
            path.write_text(data.draw(flags_file(level)), encoding="utf-8")
            required, unreadable = td.dials(path)
        self.assertEqual(required, level)
        self.assertEqual(unreadable, [])


class Judge(unittest.TestCase):
    """A stated level passes; a weaker one is named."""

    @given(st.data(), LEVEL, OTHER)
    def test_stated_level_in_any_order_passes(
        self, data: st.DataObject, level: dict[str, bool], other: dict
    ) -> None:
        """Key order and other keys change nothing."""
        items = data.draw(st.permutations(list(level.items()) + list(other.items())))
        self.assertEqual(td.judge(dict(items), level), [])

    @given(st.data(), LEVEL, st.sampled_from(["drop", "flip"]))
    def test_one_weaker_dial_is_one_finding(
        self, data: st.DataObject, level: dict[str, bool], how: str
    ) -> None:
        """Absent or flipped, it is named, once."""
        key = data.draw(st.sampled_from(sorted(level)))
        stated = dict(level)
        if how == "drop":
            del stated[key]
        else:
            stated[key] = not stated[key]
        found = td.judge(stated, level)
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith(f'"{key}"'))


JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=8),
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.text(max_size=8), inner, max_size=3)
    ),
    max_leaves=10,
)


class Refusal(unittest.TestCase):
    """Any resolved config is judged, never a traceback."""

    @given(
        st.one_of(
            JSON,
            st.fixed_dictionaries({"compilerOptions": JSON}),
            st.fixed_dictionaries({"compilerOptions": st.dictionaries(KEY, JSON)}),
        )
    )
    @example([])
    def test_main_answers_any_config(self, resolved: object) -> None:
        """0, or 1 with the reason on stderr."""
        with tempfile.TemporaryDirectory() as scratch:
            flags = Path(scratch) / "tsc-flags.txt"
            flags.write_text("--strict\n--exactOptionalPropertyTypes false\n", "utf-8")
            out, err = io.StringIO(), io.StringIO()
            old = sys.stdin
            sys.stdin = io.StringIO(json.dumps(resolved))
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    status = td.main(["--flags", str(flags), "--name", "t"])
            finally:
                sys.stdin = old
        self.assertIn(status, (0, 1))
        if status:
            self.assertIn("tsconfig-dials: ", err.getvalue())


if __name__ == "__main__":
    unittest.main()
