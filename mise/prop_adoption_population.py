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
import operator
import sys
import unittest

import adoption_population as ap
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

MEMBER = st.fixed_dictionaries({
    "repo": st.from_regex(r"[a-z][a-z0-9-]{0,10}", fullmatch=True),
    "tracks": st.lists(
        st.sampled_from(["source", "build", "dependency", "x"]), max_size=3
    ),
    "visibility": st.sampled_from(["public", "private", "internal"]),
    "releaseTags": st.booleans(),
})
MEMBERS = st.lists(
    MEMBER, min_size=1, max_size=6, unique_by=operator.itemgetter("repo")
)
JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=10),
    lambda inner: (
        st.lists(inner, max_size=4)
        | st.dictionaries(st.text(max_size=10), inner, max_size=4)
    ),
    max_leaves=15,
)


def printed(members: list[dict]) -> str:
    """Run main over a document holding the members.

    Returns:
        What it printed.

    Raises:
        AssertionError: main refused the document.

    """
    doc = json.dumps({"schema": ap.SCHEMA, "members": members})
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
    def test_what_a_member_owes(self, member: dict) -> None:
        """Each artifact owed exactly when its condition holds."""
        owed = {artifact for artifact, _ in ap.obligations(member)}
        self.assertLessEqual({"ci", "audit"}, owed)
        public = member["visibility"] != "private"
        self.assertEqual("scorecard" in owed, public)
        self.assertEqual("dependency-review" in owed, public)
        self.assertEqual("source-attest" in owed, "source" in member["tracks"])
        self.assertEqual("release" in owed, member["releaseTags"])
        self.assertEqual("publish" in owed, member["releaseTags"])

    @given(st.data(), MEMBERS)
    def test_member_order_does_not_matter(
        self, data: st.DataObject, members: list[dict]
    ) -> None:
        """The same members in any order print the same lines."""
        shuffled = data.draw(st.permutations(members))
        self.assertEqual(printed(members), printed(shuffled))


class Refusal(unittest.TestCase):
    """Any document is read or refused by name."""

    @given(
        st.one_of(
            JSON.map(json.dumps),
            st.lists(st.one_of(MEMBER, JSON), max_size=3).map(
                lambda ms: json.dumps({"schema": ap.SCHEMA, "members": ms})
            ),
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
