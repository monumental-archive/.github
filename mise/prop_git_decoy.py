#!/usr/bin/env python3
"""git_decoy.py's scrub and state diff, on inputs nobody wrote down.

The properties of `test_git_decoy.py`'s fixed rows (#1548), over
generated environments and decoy states:

- Invariance (V): `scrubbed` removes every `GIT_*` name the caller's
  environment holds and keeps every other name with its value.
- Invariance (V): `changes` between a state and itself is nothing; a
  ref that appears, moves or goes, or a rewritten config, is named
  exactly once, whatever order the refs were listed in.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import os
import unittest
from pathlib import Path
from unittest import mock

import git_decoy as gd
from hypothesis import strategies as st
from typed_hypothesis import given

ENV_NAME = st.one_of(
    st.from_regex(r"GIT_[A-Z_]{1,12}", fullmatch=True),
    st.from_regex(r"[A-Z][A-Z0-9_]{0,10}", fullmatch=True),
)
VALUE = st.text(
    st.characters(blacklist_characters="\x00", blacklist_categories=["Cs"]), max_size=8
)
OID = st.from_regex(r"[0-9a-f]{8}", fullmatch=True)
REF = st.from_regex(r"refs/(heads|tags)/[a-z]{1,6}", fullmatch=True)
REFS = st.dictionaries(REF, OID, max_size=5)
HASH = st.sampled_from(["absent", "aaaa", "bbbb"])


def state(refs: dict[str, str], config: str) -> gd.State:
    """Build a decoy state that differs only in refs and config.

    Returns:
        The state.

    """
    return gd.State(
        head="ref: refs/heads/main",
        commit="c0ffee",
        refs=frozenset(f"{oid} {name}" for name, oid in refs.items()),
        index_hash="1111",
        config=config,
        status="",
    )


class Scrubbed(unittest.TestCase):
    """No repository reaches a scrubbed git."""

    @given(st.dictionaries(ENV_NAME, VALUE, max_size=10))
    def test_every_git_name_and_nothing_else(self, environ: dict[str, str]) -> None:
        """GIT_* gone; the rest kept as they were."""
        with mock.patch.dict(os.environ, environ, clear=True):
            got = gd.scrubbed()
        kept = {k: v for k, v in environ.items() if not k.startswith("GIT_")}
        self.assertEqual(got, kept)


class Changes(unittest.TestCase):
    """Each change named once; no change, no line."""

    @given(REFS, HASH)
    def test_a_state_against_itself_is_nothing(
        self, refs: dict[str, str], config: str
    ) -> None:
        """Equal states, empty list."""
        one = state(refs, config)
        self.assertEqual(gd.changes(Path("/nonexistent"), one, one), list[str]())

    @given(REFS, REFS, HASH, HASH)
    def test_each_ref_and_config_change_named_once(
        self, before: dict[str, str], after: dict[str, str], c1: str, c2: str
    ) -> None:
        """New, moved and lost refs, and the config, each once."""
        got = gd.changes(Path("/nonexistent"), state(before, c1), state(after, c2))
        expected = [
            *(
                f"new or moved ref: {n}"
                for o, n in sorted((o, n) for n, o in after.items())
                if before.get(n) != o
            ),
            *(
                f"lost ref: {n}"
                for o, n in sorted((o, n) for n, o in before.items())
                if after.get(n) != o
            ),
        ]
        if c1 != c2:
            expected.append("config rewritten")
        self.assertEqual(got, expected)


if __name__ == "__main__":
    unittest.main()
