#!/usr/bin/env python3
"""coverage_figures.py's decisions, on any listing, run and file (#1622).

The properties of `test_coverage_figures.py`'s fixed rows, over
generated forge answers:

- C: `choose` never picks an artifact a run of another commit kept, or
  one named for another commit, or an expired one: whatever it picks is
  in the listing, named for the commit, unexpired, kept by a run of
  that commit, and the newest such.
- F: `figures` answers only leg lines: every line it returns is one,
  and it returns every non-blank line it was given.
- R: on any JSON document, any run and any bytes, each decision answers
  or refuses by name (`RefusedError`); none dies with anything else.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import unittest
from typing import TYPE_CHECKING

import coverage_figures as figures
import decoded
from hypothesis import strategies as st
from typed_hypothesis import given

if TYPE_CHECKING:
    from collections.abc import Sequence

SHA = "977d99f0ee27ca9d8a3c9c4cf5911105c88241bf"
SHAS = st.sampled_from([SHA, "2980f36dba6e88b75ecd97536850263c140a4103"])
SCALAR = st.none() | st.booleans() | st.integers() | st.text(max_size=8)
# Two levels of JSON, enough to put a wrong shape at every key the
# decisions read; recursion would type its strategies as Any.
FLAT = (
    SCALAR
    | st.lists(SCALAR, max_size=3)
    | st.dictionaries(st.text(max_size=6), SCALAR, max_size=3)
)
JSON = (
    FLAT
    | st.lists(FLAT, max_size=3)
    | st.dictionaries(st.text(max_size=6), FLAT, max_size=3)
)
ENTRY = st.fixed_dictionaries(
    {},
    optional={
        "id": st.integers(min_value=1, max_value=50) | SCALAR,
        "name": SHAS.map(figures.name) | SCALAR,
        "expired": st.booleans() | SCALAR,
        "workflow_run": st.fixed_dictionaries(
            {}, optional={"id": st.integers(min_value=1) | SCALAR, "head_sha": SHAS}
        )
        | JSON,
    },
)
LEG = st.from_regex(r"[a-z][a-z-]{0,12} [0-9]{1,3}(\.[0-9]{1,2})?", fullmatch=True)


def ours(entry: object) -> bool:
    """Say whether an entry is one `choose` may pick for SHA.

    Returns:
        True when it is named for SHA, unexpired, with integer ids, and
        kept by a run of SHA.

    """
    if not decoded.is_object(entry):
        return False
    run = entry.get("workflow_run")
    return (
        decoded.is_object(run)
        and entry.get("name") == figures.name(SHA)
        and entry.get("expired") is False
        and figures.ident(entry.get("id")) is not None
        and figures.ident(run.get("id")) is not None
        and run.get("head_sha") == SHA
    )


class TheChoiceIsThisCommits(unittest.TestCase):
    """C: only this commit's unexpired artifact, the newest of them."""

    @given(st.lists(ENTRY, max_size=6))
    def test_the_choice(self, entries: Sequence[object]) -> None:
        """The pick is the newest eligible entry, or a refusal when none is."""
        ids: list[int] = []
        for entry in entries:
            got = figures.ident(entry.get("id")) if decoded.is_object(entry) else None
            if ours(entry) and got is not None:
                ids.append(got)
        try:
            choice = figures.choose({"artifacts": list(entries)}, SHA)
        except figures.RefusedError:
            self.assertFalse(ids, "an eligible artifact was refused")
            return
        self.assertEqual(max(ids), choice.artifact)


class OnlyLegLines(unittest.TestCase):
    """F: what comes back is leg lines, all of them."""

    @given(st.lists(LEG, min_size=1, max_size=5), st.lists(st.just(""), max_size=2))
    def test_leg_lines_come_back(self, legs: list[str], blanks: list[str]) -> None:
        """Leg lines with blanks among them: the legs, in order."""
        text = "\n".join([*blanks, *legs, *blanks])
        self.assertEqual("".join(f"{leg}\n" for leg in legs), figures.figures(text))


class AnyInput(unittest.TestCase):
    """R: answered or refused by name on anything."""

    @given(JSON)
    def test_any_listing_and_run(self, doc: object) -> None:
        """Any JSON as a listing and as a run: an answer or a refusal."""
        for decide in (figures.choose, figures.judge):
            try:
                got = decide(doc, SHA)
            except figures.RefusedError as refused:
                self.assertTrue(str(refused), "a refusal says why")
            else:
                self.assertIsNotNone(got)

    @given(st.binary(max_size=64) | st.text(max_size=64).map(str.encode))
    def test_any_bytes(self, data: bytes) -> None:
        """Any download and any answer: an answer or a refusal."""
        for command in ("choose", "judge", "figures"):
            try:
                said = figures.answer(command, SHA, data)
            except figures.RefusedError as refused:
                self.assertTrue(str(refused), "a refusal says why")
            else:
                self.assertTrue(said.endswith("\n"), said)

    @given(st.text(max_size=80))
    def test_any_figures_text(self, text: str) -> None:
        """Any text as the figures file: leg lines, or a refusal."""
        try:
            got = figures.figures(text)
        except figures.RefusedError:
            return
        for line in got.splitlines():
            self.assertRegex(line, figures.LEG)


if __name__ == "__main__":
    unittest.main()
