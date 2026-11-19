#!/usr/bin/env python3
"""regression-count.py's path and pin rules, on inputs nobody wrote down.

The properties of `test_regression_count.py`'s fixed rows (#1548), over
generated paths, diff lines and patches:

- Idempotence (I): blanking the pins of a line twice is blanking them
  once.
- Invariance (V): a patch whose added lines are its removed lines with
  only versions, hashes and dates changed is pin-only, in whatever
  order its lines come; one that changes any other word is not.
- Invariance (V): a test is a test by its basename wherever it sits,
  and anything under a `tests/` or `testdata/` directory is a test.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import unittest
from pathlib import Path

from hypothesis import strategies as st
from org_hypothesis import given

_SPEC = importlib.util.spec_from_file_location(
    "regression_count",
    Path(__file__).with_name("regression-count.py"),
)
rc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rc)

WORD = st.from_regex(r"[g-z_]{1,8}", fullmatch=True)  # never a hex run
PINS = {
    "ver": st.from_regex(r"v?[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,2}", fullmatch=True),
    "hash": st.from_regex(r"[0-9a-f]{40}", fullmatch=True),
    "date": st.from_regex(r"20[0-9]{2}-[01][0-9]-[0-3][0-9]", fullmatch=True),
}
SEGMENT = st.from_regex(r"[a-z][a-z0-9_-]{0,6}", fullmatch=True)


@st.composite
def pinned_line(draw: st.DrawFn) -> list[tuple[str | None, str]]:
    """Draw a line of words with pins among them.

    Returns:
        (pin kind or None, text) per word.

    """
    kinds = draw(st.lists(st.sampled_from([None, *PINS]), min_size=1, max_size=6))
    return [(k, draw(PINS[k]) if k else draw(WORD)) for k in kinds]


class Pins(unittest.TestCase):
    """Pins are blanked; nothing else is."""

    @given(st.text(max_size=80))
    def test_twice_is_once(self, line: str) -> None:
        """A normalised line is its own normal form."""
        once = rc.normalise_pin_line(line)
        self.assertEqual(rc.normalise_pin_line(once), once)

    @given(st.data(), st.lists(pinned_line(), min_size=1, max_size=5))
    def test_a_pin_bump_is_pin_only(
        self, data: st.DataObject, lines: list[list[tuple[str | None, str]]]
    ) -> None:
        """Every pin moved, nothing else: pin-only, in any line order."""
        removed = [" ".join(w for _, w in line) for line in lines]
        added = [
            " ".join(data.draw(PINS[k]) if k else w for k, w in line) for line in lines
        ]
        patch = "\n".join(
            data.draw(
                st.permutations([f"-{r}" for r in removed] + [f"+{a}" for a in added])
            )
        )
        self.assertTrue(rc.is_pin_only(f"--- a/x\n+++ b/x\n{patch}\n"))

    @given(st.lists(pinned_line(), min_size=1, max_size=5), WORD)
    def test_another_word_is_not(
        self, lines: list[list[tuple[str | None, str]]], extra: str
    ) -> None:
        """A word added to one line: not pin-only."""
        removed = [" ".join(w for _, w in line) for line in lines]
        added = [*removed[:-1], removed[-1] + " " + extra]
        patch = "\n".join([*(f"-{r}" for r in removed), *(f"+{a}" for a in added)])
        self.assertFalse(rc.is_pin_only(patch))


class TestPaths(unittest.TestCase):
    """Tests by name, anywhere; anything under a test directory."""

    @given(
        st.lists(SEGMENT, max_size=3),
        st.sampled_from(["test_x.py", "x_test.go", "x.test.ts", "x.spec.js"]),
    )
    def test_named_tests_anywhere(self, dirs: list[str], name: str) -> None:
        """The basename decides."""
        self.assertTrue(rc.is_test_path("/".join([*dirs, name])))

    @given(
        st.lists(SEGMENT, max_size=2),
        st.sampled_from(sorted(rc.TEST_DIRS)),
        st.lists(SEGMENT, max_size=2),
        SEGMENT,
    )
    def test_under_a_test_dir(
        self, above: list[str], tests: str, below: list[str], name: str
    ) -> None:
        """Any file below a test directory."""
        self.assertTrue(rc.is_test_path("/".join([*above, tests, *below, name])))


if __name__ == "__main__":
    unittest.main()
