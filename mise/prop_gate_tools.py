#!/usr/bin/env python3
"""gate_tools.py's pathspecs and call finder, on inputs nobody wrote down.

The properties of `test_gate_tools.py`'s fixed rows (#1548), over
generated paths, pathspecs and task bodies:

- Invariance (V): a literal pathspec names exactly its own path and
  the paths below it, never a sibling that only shares its prefix; a
  path matches every glob made from it by turning segments into `*`,
  because a glob's `*` crosses `/`.
- Idempotence (I): blanking literals twice is blanking them once, and
  never changes the number of lines.
- Invariance (V): a command run at the start of a line, behind a pipe
  or `xargs`, is found at that line; the same word inside a quoted
  literal or a comment is not.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import unittest

import gate_tools as gt
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

SEGMENT = st.from_regex(r"[a-z0-9_.-]{1,6}", fullmatch=True).filter(
    lambda s: s not in {".", ".."}
)
PATH = st.lists(SEGMENT, min_size=1, max_size=4)
COMMAND = st.sampled_from(["shfmt", "taplo", "cargo-deny", "jq"])
SHELLISH = st.text(alphabet="ab $'\"\\\n#|;()`", max_size=120)


class Matches(unittest.TestCase):
    """git's default pathspec meaning."""

    @given(PATH, PATH, st.sampled_from(["", "/"]))
    def test_a_literal_names_itself_and_below(
        self, base: list[str], below: list[str], slash: str
    ) -> None:
        """Itself and its descendants; a prefix sibling never."""
        pattern = "/".join(base) + slash
        path = "/".join(base)
        self.assertTrue(gt.matches(pattern, path))
        self.assertTrue(gt.matches(pattern, f"{path}/{'/'.join(below)}"))
        self.assertFalse(gt.matches(pattern, f"{path}x"))

    @given(PATH, st.data())
    def test_a_path_matches_its_globs(
        self, segments: list[str], data: st.DataObject
    ) -> None:
        """Segments turned into `*`, or one leading `*` for directories."""
        parts = [s if not data.draw(st.booleans()) else "*" for s in segments]
        if len(parts) > 1 and data.draw(st.booleans()):
            parts = ["*", parts[-1]]
        if "*" not in parts:
            parts[0] = "*"
        self.assertTrue(gt.matches("/".join(parts), "/".join(segments)))


class StripLiterals(unittest.TestCase):
    """Blanking literals is stable and keeps every line."""

    @given(SHELLISH)
    @example("x='a\"b' \"c'd\"")
    @example('"$(shfmt)" \\\n y')
    def test_twice_is_once(self, text: str) -> None:
        """Same line count; a second pass changes nothing."""
        once = gt.strip_literals(text)
        self.assertEqual(
            len(once.splitlines()), len(text.replace("\\\n", " \n").splitlines())
        )
        self.assertEqual(gt.strip_literals(once), once)


class ShellCalls(unittest.TestCase):
    """A run command is found; a quoted or commented word is not."""

    @given(
        st.lists(
            st.tuples(
                st.sampled_from([
                    "run",
                    "pipe",
                    "xargs",
                    "single",
                    "double",
                    "comment",
                    "none",
                ]),
                COMMAND,
            ),
            max_size=6,
        ),
    )
    def test_first_call_line(self, rows: list[tuple[str, str]]) -> None:
        """The first line that runs a command, or None."""
        forms = {
            "run": "{c} --check x",
            "pipe": "git ls-files | {c} -",
            "xargs": "git ls-files | xargs -0 {c}",
            "single": "echo '{c} is a tool'",
            "double": 'echo "{c} is a tool"',
            "comment": "# {c} runs here",
            "none": "echo nothing",
        }
        lines = [forms[how].format(c=cmd) for how, cmd in rows]
        wanted = sorted({cmd for _, cmd in rows})
        expected = next(
            (i for i, (how, _) in enumerate(rows) if how in {"run", "pipe", "xargs"}),
            None,
        )
        self.assertEqual(
            gt.shell_calls("\n".join(lines), wanted or ["shfmt"]), expected, lines
        )


if __name__ == "__main__":
    unittest.main()
