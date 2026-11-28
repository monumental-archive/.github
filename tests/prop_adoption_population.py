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
from typing import TYPE_CHECKING, TypedDict

import pytest
from hypothesis import example
from hypothesis import strategies as st

from belt import adoption_population as ap
from belt.typed_hypothesis import given

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


# Membership derives the obligations.
@given(st.tuples(MEMBER, st.booleans()))
def test_what_a_member_owes(case: tuple[MemberDoc, bool]) -> None:
    """Each artifact owed exactly when its condition holds."""
    member, is_canon = case
    canon = member["repo"] if is_canon else f"{member['repo']}-canon"
    owed = {
        artifact for artifact, _ in ap.obligations(ap.Member.of(member, "m"), canon)
    }
    assert {"ci", "dependencies", "deep-tests", "records"} <= owed
    assert "audit" not in owed
    assert ("public-api" in owed) == (not is_canon)
    assert ("own-audits" in owed) == (not is_canon)
    public = member["visibility"] != "private"
    assert ("scorecard" in owed) == public
    assert ("dependency-review" in owed) == public
    assert ("source-attest" in owed) == ("source" in member["tracks"])
    assert ("release" in owed) == member["releaseTags"]
    assert ("publish" in owed) == member["releaseTags"]


@given(st.data(), MEMBERS)
def test_member_order_does_not_matter(
    data: st.DataObject, members: list[MemberDoc]
) -> None:
    """The same members in any order print the same lines."""
    shuffled = data.draw(st.permutations(members))
    printed = []
    for each in (members, shuffled):
        out = io.StringIO()
        with pytest.MonkeyPatch.context() as patched, contextlib.redirect_stdout(out):
            patched.setattr(sys, "stdin", io.StringIO(document(each)))
            assert ap.main(["canon"]) == 0, "main refused a valid document"
        printed.append(out.getvalue())
    assert printed[0] == printed[1]


# Any document is read or refused by name.
@given(
    st.one_of(
        JSON.map(dumped),
        st.lists(st.one_of(MEMBER, JSON), max_size=3).map(document),
        st.text(max_size=40),
    )
)
@example('{"schema": 9, "members": [{"repo": "a", "tracks": []}]}')
def test_members_or_refusal(stdin: str) -> None:
    """A list of members, or RefusalError."""
    try:
        members = ap.load_members(io.StringIO(stdin))
    except ap.RefusalError:
        return
    assert members
