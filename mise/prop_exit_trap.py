#!/usr/bin/env python3
"""exit_trap.py's guard check, on scripts nobody wrote down.

The properties of `test_exit_trap.py`'s fixed rows (#1548), over
generated scripts:

- Invariance (V): a script whose EXIT trap is the guard and whose last
  statement is the mark is clean, whatever the cleanup says and
  whatever comments, blank lines and commands surround it; dropping
  the mark, or the guard, or using the `[[ ]]` guard without bash, is
  exactly one finding, at the line that carries it.
- Refusal (R): `findings` judges any text and never raises.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import unittest

import exit_trap as et
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

# A cleanup command: anything a single-quoted trap may carry.
CLEANUP = st.from_regex(r'[a-z][a-z0-9 /"$._{}-]{0,20}', fullmatch=True)
FILLER = st.lists(
    st.sampled_from(["", "# a comment", "echo hi", "  x=1", "\t# indented", "cd /"]),
    max_size=4,
)


def draw_script(draw: st.DrawFn) -> tuple[list[str], int, int]:
    """Draw a guarded script.

    Returns:
        Its lines, the 1-based line of the trap and of the mark.

    """
    bash = draw(st.booleans())
    guard = (et.BASH_GUARD if bash else et.GUARD).format(cleanup=draw(CLEANUP))
    lines = ["#!/usr/bin/env bash" if bash else "#!/bin/sh"]
    lines += draw(FILLER)
    lines.append(et.PREASSIGN)
    lines.append(guard)
    trap = len(lines)
    lines += draw(FILLER)
    lines.append(et.MARK + draw(st.sampled_from(["", " # done", "  "])))
    mark = len(lines)
    lines += draw(
        st.lists(st.sampled_from(["", "# trailing comment", "   "]), max_size=3)
    )
    return lines, trap, mark


script = st.composite(draw_script)


def judged(lines: list[str]) -> list[str]:
    """Judge a script as exit-trap does a file.

    Returns:
        Its findings.

    """
    source = "\n".join(lines) + "\n"
    bash = bool(et.BASH_SHEBANG_RE.match(lines[0]))
    return list(et.findings("s.sh", 0, "", source, bash=bash))


class Guard(unittest.TestCase):
    """The guard and the mark, wherever they sit."""

    @given(script())
    def test_guarded_is_clean(self, drawn: tuple[list[str], int, int]) -> None:
        """No finding."""
        lines, _, _ = drawn
        self.assertEqual(judged(lines), list[str](), lines)

    @given(script())
    def test_no_mark_is_one_finding(self, drawn: tuple[list[str], int, int]) -> None:
        """Without the mark the last statement is named."""
        lines, _, mark = drawn
        lines[mark - 1] = "true"
        found = judged(lines)
        self.assertEqual(len(found), 1, lines)
        self.assertIn(f"s.sh:{mark}: has an EXIT trap", found[0])

    @given(script(), CLEANUP)
    @example(
        (["#!/bin/sh", "rc=0", "trap 'rm -f x' EXIT", "org_done=1"], 3, 4), "rm -f x"
    )
    def test_an_unguarded_trap_is_one_finding(
        self, drawn: tuple[list[str], int, int], cleanup: str
    ) -> None:
        """A bare trap is named at its line."""
        lines, trap, _ = drawn
        lines[trap - 1] = f"trap '{cleanup}' EXIT"
        found = judged(lines)
        named = [f"s.sh:{trap}: EXIT trap without the completion guard"]
        self.assertEqual(found, named)

    @given(script(), CLEANUP)
    def test_bash_guard_needs_bash(
        self, drawn: tuple[list[str], int, int], cleanup: str
    ) -> None:
        """The `[[ ]]` guard under a non-bash shebang is named at its line."""
        lines, trap, _ = drawn
        lines[0] = "#!/bin/sh"
        lines[trap - 1] = et.BASH_GUARD.format(cleanup=cleanup)
        found = judged(lines)
        self.assertEqual(len(found), 1, lines)
        self.assertIn(f"s.sh:{trap}: EXIT trap guard uses [[ ]]", found[0])


class Refusal(unittest.TestCase):
    """Any text is judged."""

    @given(st.text(max_size=300), st.sampled_from(["bash", "sh"]))
    @example("trap x EXIT", "sh")
    def test_findings_judges_any_text(self, source: str, shell: str) -> None:
        """A list of findings, each naming the path."""
        for line in et.findings("s.sh", 0, "", source, bash=shell == "bash"):
            self.assertTrue(line.startswith("s.sh:"))


if __name__ == "__main__":
    unittest.main()
