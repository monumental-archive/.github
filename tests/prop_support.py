"""support.py's fixture environment, under environments nobody wrote down.

The properties of `test_support.py`'s fixed rows (#1548), over
generated suite environments and overrides:

- Invariance (V): whatever the suite's environment holds, the fixture
  gets only the allow-list's names, HOME and the harness identity;
  no `GIT_*` value of the suite's reaches it (#857); an allow-listed
  name keeps the suite's value; and an override always wins.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import os

import pytest
from hypothesis import example
from hypothesis import strategies as st

from belt.typed_hypothesis import given
from tests import support as he

IDENTITY = {
    "GIT_EDITOR": "true",
    "GIT_AUTHOR_NAME": he.IDENTITY_NAME,
    "GIT_AUTHOR_EMAIL": he.IDENTITY_EMAIL,
    "GIT_COMMITTER_NAME": he.IDENTITY_NAME,
    "GIT_COMMITTER_EMAIL": he.IDENTITY_EMAIL,
}
NAME = st.one_of(
    st.sampled_from(sorted(he.INHERITED)),
    st.sampled_from(["HOME", "VERSION", "GIT_DIR", "GIT_INDEX_FILE", *IDENTITY]),
    st.from_regex(r"[A-Z][A-Z0-9_]{0,10}", fullmatch=True),
)
VALUE = st.text(
    st.characters(blacklist_characters="\x00", blacklist_categories=["Cs"]),
    max_size=10,
)
ENVIRON = st.dictionaries(NAME, VALUE, max_size=10)
OVERRIDES = st.dictionaries(
    st.from_regex(r"[A-Z][A-Z0-9_]{0,10}", fullmatch=True), VALUE, max_size=3
)
# A repository's git variables in the caller's environment, no override.
LEAKING: dict[str, str] = {"GIT_DIR": "/real/.git", "VERSION": "1.0"}
NO_OVERRIDE: dict[str, str] = {}


class TestEnv:
    """Nothing reaches a fixture that the allow-list does not name."""

    @given(ENVIRON, OVERRIDES, st.one_of(st.none(), st.just("/fixture/home")))
    @example(LEAKING, NO_OVERRIDE, None)
    def test_only_the_allow_list_reaches_it(
        self, environ: dict[str, str], overrides: dict[str, str], home: str | None
    ) -> None:
        """Allow-list, HOME and identity; overrides last."""
        with pytest.MonkeyPatch.context() as patched:
            for name in list(os.environ):
                patched.delenv(name)
            for name, value in environ.items():
                patched.setenv(name, value)
            got = he.env(home, **overrides)
        # ORG_CANON_DIR is the package's canon, set for every fixture.
        allowed = (
            set(he.INHERITED) | {"HOME", "ORG_CANON_DIR"} | set(IDENTITY) | set(overrides)
        )
        assert set(got) <= allowed
        for name, value in overrides.items():
            assert got[name] == value
        for name, value in IDENTITY.items():
            if name not in overrides:
                assert got[name] == value
        for name in he.INHERITED:
            if name not in overrides:
                assert got.get(name) == environ.get(name)
        if "HOME" not in overrides:
            assert got.get("HOME") == (
                home if home is not None else environ.get("HOME")
            )
        for name in got:
            if name.startswith("GIT_") and name not in overrides:
                assert name in IDENTITY
