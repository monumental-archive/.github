#!/usr/bin/env python3
"""bestpractices-owner.py's ownership verdict, on inputs nobody wrote down.

The properties of `test_bestpractices_owner.py`'s fixed rows (#1548),
over generated repository URLs and issuer answers:

- Invariance (V): an entry naming the wearer under any of the folds
  the comparison ignores (case, surrounding whitespace, one trailing
  slash, one trailing `.git`) belongs to it; an entry naming another
  owner or another repository does not.
- Refusal (R): whatever text arrives on stdin, `main` answers with
  its own verdict (0 or 1) and never raises.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

_SPEC = importlib.util.spec_from_file_location(
    "bestpractices_owner",
    Path(__file__).with_name("bestpractices-owner.py"),
)
bo = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bo)

# An owner or repository name as GitHub spells them: no dot at the end,
# so a generated name never ends in `.git` itself.
PART = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9_-]{0,12}", fullmatch=True)
FOLD = st.sampled_from(["", "/", ".git", ".git/"])
SPACE = st.sampled_from(["", " ", "\t", "\n"])

JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=20),
    lambda inner: (
        st.lists(inner, max_size=4)
        | st.dictionaries(st.text(max_size=10), inner, max_size=4)
    ),
    max_leaves=12,
)


def url(owner: str, repo: str) -> str:
    """Spell a repository's https URL.

    Returns:
        The URL.

    """
    return f"https://github.com/{owner}/{repo}"


@st.composite
def spelling(draw: st.DrawFn) -> tuple[str, str]:
    """Draw a wearer and the wearer under the folds the comparison ignores.

    Returns:
        The wearer's URL, and the same URL case-flipped, padded with
        whitespace and given one trailing slash or `.git`.

    """
    wearer = url(draw(PART), draw(PART))
    flips = draw(st.lists(st.booleans(), min_size=len(wearer), max_size=len(wearer)))
    flipped = "".join(
        c.swapcase() if flip else c for c, flip in zip(wearer, flips, strict=True)
    )
    return wearer, draw(SPACE) + flipped + draw(FOLD) + draw(SPACE)


@st.composite
def stranger(draw: st.DrawFn) -> tuple[str, str]:
    """Draw a wearer and a URL naming another owner or repository.

    Returns:
        The wearer's URL and the other URL, with a trailing fold.

    """
    owner, repo, other = draw(PART), draw(PART), draw(PART)
    if draw(st.booleans()):
        if other.lower() == owner.lower():
            other += "x"
        named = url(other, repo)
    else:
        if other.lower() == repo.lower():
            other += "x"
        named = url(owner, other)
    return url(owner, repo), named + draw(FOLD)


class Invariance(unittest.TestCase):
    """The verdict follows the repository, not its spelling."""

    @given(spelling())
    @example(("https://github.com/Owner/repo", " https://GITHUB.com/owner/REPO.git/\n"))
    def test_a_fold_of_the_wearer_belongs(self, drawn: tuple[str, str]) -> None:
        """Case, whitespace, one slash and one `.git` change nothing."""
        wearer, named = drawn
        self.assertIsNone(bo.finding({"id": 1, "repo_url": named}, wearer))

    @given(stranger())
    def test_another_repository_does_not(self, drawn: tuple[str, str]) -> None:
        """A different owner or name, however spelt, is a finding."""
        wearer, named = drawn
        found = bo.finding({"id": 7, "repo_url": named}, wearer)
        self.assertIsNotNone(found)
        self.assertIn("project 7", found)


class Refusal(unittest.TestCase):
    """Any answer from the issuer is judged, never a traceback."""

    @given(st.one_of(JSON.map(json.dumps), st.text(max_size=80)), PART)
    @example('{"repo_url": 5}', "x")
    @example("[]", "x")
    def test_main_answers_any_stdin(self, stdin: str, repo: str) -> None:
        """Exit 0 or 1, with its own message when 1."""
        out = io.StringIO()
        old = sys.stdin
        sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out):
                status = bo.main([url("o", repo)])
        finally:
            sys.stdin = old
        self.assertIn(status, (0, 1))
        if status == 1:
            self.assertTrue(out.getvalue().strip())


if __name__ == "__main__":
    unittest.main()
