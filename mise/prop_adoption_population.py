#!/usr/bin/env python3
"""adoption_population.py's obligations, on rosters nobody wrote down.

The properties of `test_adoption_population.py`'s fixed rows (#1548),
over generated population documents:

- Invariance (V): every member owes `ci` and `audit`; a public one
  owes `scorecard` and `dependency-review`, a private one never does;
  `source-attest` is owed exactly when the member is on the source
  track, `release` and `publish` exactly when it has released; and the
  order of the members never changes what is printed.
- Refusal (R): whatever JSON arrives on stdin, `load_members` returns
  members or raises its own RefusalError, never anything else.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import contextlib
import io
import json
import sys
import unittest
from typing import TYPE_CHECKING, TypedDict

import adoption_population as ap
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

if TYPE_CHECKING:
    from collections.abc import Sequence


class MemberDoc(TypedDict):
    """One member as stele's population document carries it."""

    repo: str
    tracks: list[str]
    visibility: str
    releaseTags: bool


REPO = st.from_regex(r"[a-z][a-z0-9-]{0,10}", fullmatch=True)
TRACKS: st.SearchStrategy[list[str]] = st.lists(
    st.sampled_from(["source", "build", "dependency", "x"]), max_size=3
)
VISIBILITY: st.SearchStrategy[str] = st.sampled_from(["public", "private", "internal"])
MEMBER: st.SearchStrategy[MemberDoc] = st.builds(
    MemberDoc,
    repo=REPO,
    tracks=TRACKS,
    visibility=VISIBILITY,
    releaseTags=st.booleans(),
)


def repo_of(member: MemberDoc) -> str:
    """Name the member, its key among the members.

    Returns:
        Its repository.

    """
    return member["repo"]


MEMBERS: st.SearchStrategy[list[MemberDoc]] = st.lists(
    MEMBER, min_size=1, max_size=6, unique_by=repo_of
)
SCALAR: st.SearchStrategy[object] = (
    st.none() | st.booleans() | st.integers() | st.text(max_size=10)
)


def nested(inner: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Wrap values in an array or an object.

    Returns:
        The strategy one level deeper.

    """
    arrays: st.SearchStrategy[object] = st.lists(inner, max_size=4)
    objects: st.SearchStrategy[object] = st.dictionaries(
        st.text(max_size=10), inner, max_size=4
    )
    return arrays | objects


JSON: st.SearchStrategy[object] = st.recursive(SCALAR, nested, max_leaves=15)


def dumped(value: object) -> str:
    """Serialise a value as JSON.

    Returns:
        The document.

    """
    return json.dumps(value)


def document(members: Sequence[object]) -> str:
    """Wrap members in a population document.

    Returns:
        The document, serialised.

    """
    doc: dict[str, object] = {"schema": ap.SCHEMA, "members": members}
    return json.dumps(doc)


def printed(members: list[MemberDoc]) -> str:
    """Run main over a document holding the members.

    Returns:
        What it printed.

    Raises:
        AssertionError: main refused the document.

    """
    doc = document(members)
    out = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO(doc)
    try:
        with contextlib.redirect_stdout(out):
            status = ap.main([])
    finally:
        sys.stdin = old
    if status != 0:
        msg = f"main refused a valid document: {status}"
        raise AssertionError(msg)
    return out.getvalue()


class Obligations(unittest.TestCase):
    """Membership derives the obligations."""

    @given(MEMBER)
    def test_what_a_member_owes(self, member: MemberDoc) -> None:
        """Each artifact owed exactly when its condition holds."""
        owed = {artifact for artifact, _ in ap.obligations(ap.Member.of(member, "m"))}
        self.assertLessEqual({"ci", "audit"}, owed)
        public = member["visibility"] != "private"
        self.assertEqual("scorecard" in owed, public)
        self.assertEqual("dependency-review" in owed, public)
        self.assertEqual("source-attest" in owed, "source" in member["tracks"])
        self.assertEqual("release" in owed, member["releaseTags"])
        self.assertEqual("publish" in owed, member["releaseTags"])

    @given(st.data(), MEMBERS)
    def test_member_order_does_not_matter(
        self, data: st.DataObject, members: list[MemberDoc]
    ) -> None:
        """The same members in any order print the same lines."""
        shuffled = data.draw(st.permutations(members))
        self.assertEqual(printed(members), printed(shuffled))


class Refusal(unittest.TestCase):
    """Any document is read or refused by name."""

    @given(
        st.one_of(
            JSON.map(dumped),
            st.lists(st.one_of(MEMBER, JSON), max_size=3).map(document),
            st.text(max_size=40),
        )
    )
    @example('{"schema": 9, "members": [{"repo": "a", "tracks": []}]}')
    def test_members_or_refusal(self, stdin: str) -> None:
        """A list of members, or RefusalError."""
        try:
            members = ap.load_members(io.StringIO(stdin))
        except ap.RefusalError:
            return
        self.assertTrue(members)


if __name__ == "__main__":
    unittest.main()
