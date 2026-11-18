#!/usr/bin/env python3
"""belt-shell.py's body parsers, on inputs nobody wrote down (#1332).

The properties of `test_belt_shell.py`'s fixed rows, stated over
generated task bodies:

- `scrub` keeps every offset: same length, every character either
  itself or blanked, and a newline blanked only when a backslash
  escapes it (a continuation joins two lines into one command).
- `nested_runs` reports exactly the lines that execute `mise run`, by
  their line in the source: one standing as its own command, never one
  continued onto the line before as an argument. It counted lines in
  the scrubbed text until this file found it, so every run after a
  continuation was reported one line up and the `nested-mise:` marker
  check read the wrong line.
- `unshellify` undoes `shellify`, or refuses (None) when the body's own
  comments look like commented-out Tera. It never returns anything else.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import unittest
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

_SPEC = importlib.util.spec_from_file_location(
    "belt_shell",
    Path(__file__).with_name("belt-shell.py"),
)
bs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bs)

# Shell-shaped text: the characters scrub treats specially, plus ordinary
# ones, so quotes, escapes and comments open and close at random.
SHELL_TEXT = st.text(alphabet="ab $=#'\"\\\n\t;&|(){}", max_size=200)

# A filler word that is never a command prefix or an assignment, so a
# line of them is a command that is not `mise`.
WORD = st.from_regex(r"x[a-z]{0,6}", fullmatch=True)
FILLER = st.builds(
    lambda words, continued: " ".join(words) + (" \\" if continued and words else ""),
    st.lists(WORD, max_size=4),
    st.booleans(),
)
RUN = "mise run fix:x"

# A Tera statement line, and lines of ordinary text around it.
TERA = st.builds(
    lambda pad, inner: f"{pad}{{% {inner} %}}",
    st.sampled_from(["", "  ", "\t"]),
    st.from_regex(r"[a-z ]{0,10}", fullmatch=True),
)
LINE = st.one_of(
    TERA,
    st.text(alphabet="ab #{}%\t$", max_size=30),
)


class ScrubKeepsOffsets(unittest.TestCase):
    """An offset into the scrubbed text is an offset into the source."""

    @given(SHELL_TEXT)
    @example("foo \\\n  bar\nmise run x\n")
    @example('x="a\\\nb"\nmise run y\n')
    def test_every_offset_survives(self, source: str) -> None:
        """Same length; kept or blanked; only an escaped newline blanked."""
        scrubbed = bs.scrub(source)
        self.assertEqual(len(scrubbed), len(source))
        for i, (got, was) in enumerate(zip(scrubbed, source, strict=True)):
            self.assertIn(got, {was, " "})
            if was == "\n" and got != was:
                self.assertEqual(source[i - 1], "\\", f"newline {i} blanked unescaped")


class NestedRunsNamesTheLine(unittest.TestCase):
    """Every executed `mise run`, on the line it is written on."""

    @given(
        st.lists(st.one_of(FILLER, st.just(RUN)), max_size=12),
    )
    @example(["xa \\", "xb", RUN])
    @example(["xa \\", RUN])
    def test_exactly_the_executed_lines(self, lines: list[str]) -> None:
        """A run is executed unless the line before continues onto it."""
        source = "\n".join(lines) + "\n"
        expected = [
            i
            for i, line in enumerate(lines)
            if line == RUN and not (i and lines[i - 1].endswith("\\"))
        ]
        self.assertEqual(list(bs.nested_runs(source)), expected)


class ShellifyRoundTrip(unittest.TestCase):
    """`unshellify` restores what `shellify` commented out, or refuses."""

    @given(st.lists(LINE, max_size=10))
    def test_restores_or_refuses(self, lines: list[str]) -> None:
        """The body comes back line for line, or None when it cannot."""
        source = "\n".join(lines)
        shell, tera = bs.shellify(source)
        back = bs.unshellify(shell, tera)
        lookalikes = any(bs.UNTERA_RE.match(line) for line in source.splitlines())
        if lookalikes:
            self.assertIsNone(back)
        else:
            self.assertEqual(back, "\n".join(source.splitlines()) + "\n")


if __name__ == "__main__":
    unittest.main()
