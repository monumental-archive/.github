#!/usr/bin/env python3
"""gate-cache-key.py's cache names, on inputs nobody wrote down.

The properties of `test_gate_cache_key.py`'s fixed rows (#1548), over
generated install lists, environments and pins:

- Invariance (V): an install list's digest does not depend on the
  order of its tools, the flags among them or the whitespace between
  them, and changes when a tool is added.
- Invariance (V): the key always begins with the fallback prefix, is
  the same at any two moments of one period, and changes when the
  mise version, the belt's lock, the config hash or the install list
  changes.
- Refusal (R): `mise_version` reads any workflow text and answers with
  the one pinned version or its own ValueError.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import unittest
from pathlib import Path

from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

_SPEC = importlib.util.spec_from_file_location(
    "gate_cache_key",
    Path(__file__).with_name("gate-cache-key.py"),
)
gk = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gk)

TOOL = st.from_regex(r"[a-z][a-z0-9:@.-]{0,10}", fullmatch=True)
FLAG = st.sampled_from(["-y", "--locked", "-q"])
SPACE = st.sampled_from([" ", "  ", "\t", "\n"])
HEX = st.from_regex(r"[0-9a-f]{8}", fullmatch=True)
VERSION = st.from_regex(r"20[0-9]{2}\.[0-9]{1,2}\.[0-9]{1,2}", fullmatch=True)
PERIOD = gk.PERIOD_SECONDS


def spelt(draw: st.DrawFn, tools: list[str]) -> str:
    """Write tools in any order with flags and whitespace among them.

    Returns:
        An install_args string.

    """
    words = draw(st.permutations([*tools, *draw(st.lists(FLAG, max_size=3))]))
    return "".join(draw(SPACE) + w for w in words) + draw(SPACE)


class Install(unittest.TestCase):
    """The digest is of the set of tools."""

    @given(st.data(), st.lists(TOOL, unique=True, max_size=6))
    def test_order_flags_and_space_do_not_matter(
        self, data: st.DataObject, tools: list[str]
    ) -> None:
        """Two spellings of the same tools, one digest."""
        one = gk.install_digest(spelt(data.draw, tools))
        two = gk.install_digest(spelt(data.draw, tools))
        self.assertEqual(one, two)
        self.assertEqual(not one, not tools)

    @given(st.data(), st.lists(TOOL, unique=True, max_size=6), TOOL)
    def test_another_tool_changes_it(
        self, data: st.DataObject, tools: list[str], extra: str
    ) -> None:
        """Adding a tool is a different digest."""
        if extra in tools:
            return
        self.assertNotEqual(
            gk.install_digest(spelt(data.draw, tools)),
            gk.install_digest(spelt(data.draw, [*tools, extra])),
        )


ENV = st.fixed_dictionaries(
    {"HOME": st.just("/home/runner")},
    optional={
        "ImageOS": st.sampled_from(["ubuntu24", "macos15"]),
        "MISE_ENV": st.sampled_from(["ci", "a,b"]),
        "ORG_MISE_CONFIG_HASH": HEX,
        "ORG_GATE_BELT_DIGEST": HEX,
    },
)


class Names(unittest.TestCase):
    """One key per input, stable within a period."""

    @given(ENV, VERSION, HEX, st.integers(0, 1000), st.lists(TOOL, max_size=3))
    def test_prefix_and_period(
        self, env: dict, version: str, belt: str, period: int, tools: list[str]
    ) -> None:
        """The prefix leads; the period's first and last second agree."""
        install = " ".join(tools)
        start = gk.Facts(version, "linux-x64", period * PERIOD, belt)
        end = start._replace(now=(period + 1) * PERIOD - 1)
        one = gk.names("tools", env, start, install)
        self.assertTrue(one["key"].startswith(one["prefix"]))
        self.assertEqual(one, gk.names("tools", env, end, install))

    @given(
        ENV,
        VERSION,
        HEX,
        st.sampled_from(["version", "belt", "config", "install", "period"]),
    )
    def test_each_input_moves_the_key(
        self, env: dict, version: str, belt: str, what: str
    ) -> None:
        """Change one input, a different key."""
        facts = gk.Facts(version, "linux-x64", 0.0, belt)
        before = gk.names("tools", env, facts, "a b")["key"]
        install = "a b"
        if what == "version":
            facts = facts._replace(version=version + "1")
        elif what == "belt":
            facts = facts._replace(belt=belt + "0")
        elif what == "config":
            env = {
                **env,
                "ORG_MISE_CONFIG_HASH": env.get("ORG_MISE_CONFIG_HASH", "") + "f",
            }
        elif what == "install":
            install = "a b c"
        else:
            facts = facts._replace(now=float(PERIOD))
        self.assertNotEqual(gk.names("tools", env, facts, install)["key"], before)


class Refusal(unittest.TestCase):
    """Any workflow text: one version, or the helper's refusal."""

    @given(
        st.lists(
            st.one_of(
                VERSION.map(lambda v: f"          version: {v}"),
                st.text(max_size=30),
            ),
            max_size=6,
        ).map("\n".join)
    )
    @example("  version: 2026.11.7\n  version: 2026.11.7\n")
    @example("  version: 2026.11.7\n  version: 2026.11.6\n")
    def test_one_version_or_value_error(self, workflow: str) -> None:
        """A version that the text pins, or ValueError."""
        try:
            got = gk.mise_version(workflow)
        except ValueError:
            return
        self.assertIn(got, workflow)


if __name__ == "__main__":
    unittest.main()
