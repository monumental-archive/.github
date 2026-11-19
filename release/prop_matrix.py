#!/usr/bin/env python3
"""matrix.py, the release jobs' matrix derivation, on any request.

The properties of `test_matrix.py`'s fixed rows (#1556), over generated
comma-separated requests, split as the shell split them (spaces removed,
the first line only, a trailing comma dropped):

- V: spaces anywhere in a request, a trailing comma, a second line and
  the order of the fields do not change the set of legs planned, and
  each leg is planned in the order requested — for both per-target
  classes and for the pgrx majors.
- V: a field the table does not know is refused by name — the first
  such field, spaces removed — whatever known fields surround it; for
  the majors, a field that is not a number is refused by name the same
  way.
- R: on any request text at all, `main` plans (exit 0, outputs on
  stdout) or refuses (exit 1, one `::error::` line, nothing on stdout).

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

import matrix
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

CLASS = st.sampled_from(sorted(matrix.TARGETS))
MAJOR = st.integers(10, 99).map(str)
# A field nobody tabled: no comma, newline or space (those are the
# splitter's), and not a known target.
KNOWN = {t for runners, _ in matrix.TARGETS.values() for t in runners}
STRANGER = st.text(
    st.characters(exclude_characters=", \n"), min_size=1, max_size=12
).filter(lambda f: f not in KNOWN)


@st.composite
def spaced(draw: st.DrawFn, fields: list[str]) -> str:
    """Write fields as a caller might: spaces anywhere, maybe a tail.

    Returns:
        The request text.

    """
    out = []
    for field in fields:
        text = field
        cut = draw(st.lists(st.integers(0, len(field)), max_size=3))
        for i in sorted(cut, reverse=True):
            text = f"{text[:i]}{' ' * draw(st.integers(1, 2))}{text[i:]}"
        out.append(text)
    text = ",".join(out)
    if draw(st.booleans()):
        text += ","
    if draw(st.booleans()):
        text += "\n" + draw(st.text(max_size=10))
    return text


def run(argv: list[str]) -> tuple[int, str, str]:
    """Run the derivation's command line.

    Returns:
        The exit status, stdout and stderr.

    """
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = matrix.main(argv)
    return code, out.getvalue(), err.getvalue()


def expected(stdout: str) -> list[str]:
    """Read the `expected=` output.

    Returns:
        The planned legs, in order.

    """
    line = next(x for x in stdout.splitlines() if x.startswith("expected="))
    return json.loads(line.removeprefix("expected="))


class LegsFollowTheFields(unittest.TestCase):
    """V: spaces, a tail and order move nothing but the order of legs."""

    @given(CLASS, st.data())
    def test_targets(self, cls: str, data: st.DataObject) -> None:
        """Every requested target is a leg, in the order requested."""
        runners = sorted(matrix.TARGETS[cls][0])
        fields = data.draw(st.permutations(runners)).copy()
        fields = fields[: data.draw(st.integers(1, len(fields)))]
        request = data.draw(spaced(fields))
        code, out, err = run(["targets", cls, request])
        self.assertEqual(code, 0, err)
        self.assertEqual(expected(out), fields)

    @given(st.lists(MAJOR, min_size=1, max_size=5, unique=True), st.data())
    def test_majors(self, majors: list[str], data: st.DataObject) -> None:
        """Every requested major is a cell, in the order requested."""
        code, out, err = run(["pgrx-images", data.draw(spaced(majors))])
        self.assertEqual(code, 0, err)
        self.assertEqual(expected(out), majors)


class StrangerRefusedByName(unittest.TestCase):
    """V: the first field nobody tabled is the one the refusal names."""

    @given(CLASS, STRANGER, st.data())
    @example("go-binary", "linux-386", None)
    def test_unknown_target(
        self, cls: str, stranger: str, data: st.DataObject | None
    ) -> None:
        """Known fields around it do not hide it."""
        runners, hint = matrix.TARGETS[cls]
        known = sorted(runners)
        before = data.draw(st.lists(st.sampled_from(known), max_size=2)) if data else []
        after = data.draw(st.lists(st.sampled_from(known), max_size=2)) if data else []
        request = ",".join([*before, stranger, *after])
        code, out, err = run(["targets", cls, request])
        self.assertEqual(
            (code, out, err),
            (1, "", f"::error::unknown target {stranger}; {hint}\n"),
        )

    @given(STRANGER.filter(lambda f: not (f.isascii() and f.isdigit())), st.data())
    def test_a_major_that_is_not_a_number(
        self, stranger: str, data: st.DataObject
    ) -> None:
        """A field that is not ASCII digits is refused by name."""
        before = data.draw(st.lists(MAJOR, max_size=2, unique=True))
        request = ",".join([*before, stranger])
        code, out, err = run(["pgrx-images", request])
        self.assertEqual(
            (code, out, err), (1, "", f"::error::{stranger} is not a PG major\n")
        )


class AnyRequest(unittest.TestCase):
    """R: any request text is planned or refused by name."""

    @given(st.sampled_from(["go-binary", "rust-binary", None]), st.text())
    def test_plans_or_refuses(self, cls: str | None, request: str) -> None:
        """Exit 0 with outputs, or 1 with one error line and no output."""
        argv = ["targets", cls, request] if cls else ["pgrx-images", request]
        code, out, err = run(argv)
        if code == 0:
            self.assertTrue(out.startswith("matrix="), out)
        else:
            self.assertEqual((code, out), (1, ""))
            self.assertTrue(err.startswith("::error::"), err)
            self.assertEqual(err.count("\n"), 1, err)


if __name__ == "__main__":
    unittest.main()
