#!/usr/bin/env python3
"""publish-npm.py's version order and log masking, on any input.

The properties of `test_publish_npm.py`'s fixed rows (#1556):

- V: `version_key` orders as semver orders every generated
  `major.minor.patch` triple — part by part, numerically, so `9.10.0`
  sorts after `9.9.0` — and the trusted-publishing gate refuses an npm
  exactly when it sorts below 11.5.1.
- V: `masked` keeps no line naming a credential and no token-shaped
  run of 32 or more characters, whatever the log holds, and keeps at
  most the last LOG_TAIL lines.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import unittest
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

_SPEC = importlib.util.spec_from_file_location(
    "publish_npm",
    Path(__file__).with_name("publish-npm.py"),
)
publish = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(publish)

PART = st.integers(0, 10**6)
TRIPLE = st.tuples(PART, PART, PART)
NEED = (11, 5, 1)
LINE = st.text(st.characters(exclude_characters="\n\r"), max_size=60)
SECRET = st.from_regex(r"[A-Za-z0-9_-]{32,48}", fullmatch=True)
CREDENTIAL = st.sampled_from([
    "Authorization",
    "authorization",
    "Bearer",
    "_authToken",
    "Set-Cookie",
    "npm-otp",
])


def spelled(triple: tuple[int, int, int]) -> str:
    """Write a triple as npm prints its version.

    Returns:
        `major.minor.patch`.

    """
    return ".".join(map(str, triple))


class SemverOrder(unittest.TestCase):
    """V: the key's order is semver's, part by part."""

    @given(TRIPLE, TRIPLE)
    @example((9, 10, 0), (9, 9, 0))
    @example((11, 5, 1), (11, 5, 1))
    def test_orders_as_semver(
        self, a: tuple[int, int, int], b: tuple[int, int, int]
    ) -> None:
        """The keys compare as the triples do."""
        ka, kb = publish.version_key(spelled(a)), publish.version_key(spelled(b))
        self.assertEqual(ka < kb, a < b)
        self.assertEqual(ka == kb, a == b)

    @given(TRIPLE)
    @example((11, 5, 0))
    @example((11, 5, 1))
    @example((11, 4, 99))
    def test_gate_refuses_exactly_below_the_need(
        self, have: tuple[int, int, int]
    ) -> None:
        """The run's first check: refused iff the triple is below 11.5.1."""
        refused = publish.version_key(spelled(have)) < publish.version_key(publish.NEED)
        self.assertEqual(refused, have < NEED)


class MaskedLog(unittest.TestCase):
    """V: nothing credential-shaped survives the mask."""

    @given(
        st.lists(
            st.one_of(
                LINE,
                st.builds(lambda a, c, b: f"{a}{c}{b}", LINE, CREDENTIAL, LINE),
                st.builds(lambda a, t, b: f"{a} {t} {b}", LINE, SECRET, LINE),
            ),
            max_size=8,
        ),
    )
    def test_no_credential_survives(self, lines: list[str]) -> None:
        """No credential line, no 32-char token run, at most the tail."""
        kept = publish.masked("\n".join(lines))
        self.assertLessEqual(len(kept), publish.LOG_TAIL)
        for line in kept:
            self.assertIsNone(publish.CREDENTIAL_LINE.search(line), line)
            self.assertIsNone(publish.TOKEN_SHAPED.search(line), line)


if __name__ == "__main__":
    unittest.main()
