#!/usr/bin/env python3
"""msrv_plan.py's versions and promises, on inputs nobody wrote down.

The properties of `test_msrv_plan.py`'s fixed rows (#1548), over
generated versions, workspaces and toolchain listings:

- Round trip (T): a version written out by `show` reads back as
  itself, and a two-part version reads as its zero-padded three.
- Refusal (R): `parse_version` reads any string and answers with a
  version or None, never a traceback (#1555: a non-ASCII digit such
  as `²` passed `isdigit` and `int()` raised).
- Invariance (V): the order of the members never changes which
  members are grouped under which promise; only active, installed
  toolchains are pins; the `--pgrx` pairs read the same in any order.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import unittest

import msrv_plan as mp
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

PART = st.integers(0, 999)
VERSION = st.tuples(PART, PART, PART)
NAME = st.from_regex(r"[a-z][a-z0-9_-]{0,8}", fullmatch=True)


class Versions(unittest.TestCase):
    """A bare version, written and read."""

    @given(VERSION)
    def test_show_then_parse(self, version: tuple[int, int, int]) -> None:
        """Three parts back as written; two parts padded."""
        self.assertEqual(mp.parse_version(mp.show(version)), version)
        self.assertEqual(
            mp.parse_version(f"{version[0]}.{version[1]}"), (version[0], version[1], 0)
        )


class Refusal(unittest.TestCase):
    """Any string: a version, or None."""

    @given(st.text(max_size=20))
    @example("1.2.²")
    @example("nightly-2026-07-20")
    def test_any_string_is_read_or_refused(self, raw: str) -> None:
        """A version or None."""
        got = mp.parse_version(raw)
        self.assertTrue(got is None or len(got) == 3)  # ruff: ignore[magic-value-comparison] - three parts


@st.composite
def workspace(draw: st.DrawFn) -> list[dict[str, str | None]]:
    """Draw members, each with or without a rust-version promise.

    Returns:
        The members as cargo reports them.

    """
    names = draw(st.lists(NAME, unique=True, max_size=6))
    out = []
    for name in names:
        promise = draw(st.one_of(st.none(), VERSION.map(mp.show)))
        out.append({"name": name, "version": "0.1.0", "rust_version": promise})
    return out


class Promises(unittest.TestCase):
    """Grouping does not depend on order."""

    @given(st.data(), workspace(), st.sets(NAME, max_size=2))
    def test_member_order_does_not_matter(
        self, data: st.DataObject, members: list[dict], exclude: set[str]
    ) -> None:
        """The same members per promise, in any order."""
        shuffled = data.draw(st.permutations(members))
        one, unreachable_one, _ = mp.promises(members, exclude)
        two, unreachable_two, _ = mp.promises(shuffled, exclude)
        self.assertEqual(
            {k: sorted(v) for k, v in one.items()},
            {k: sorted(v) for k, v in two.items()},
        )
        self.assertEqual(sorted(unreachable_one), sorted(unreachable_two))
        grouped = {s.split("@")[0] for specs in one.values() for s in specs}
        self.assertEqual(
            grouped,
            {
                m["name"]
                for m in members
                if m["rust_version"] and m["name"] not in exclude
            },
        )

    @given(
        st.lists(
            st.fixed_dictionaries({
                "version": VERSION.map(mp.show),
                "active": st.booleans(),
                "installed": st.booleans(),
            }),
            max_size=6,
        )
    )
    def test_only_active_installed_toolchains_pin(self, listing: list[dict]) -> None:
        """Pins are the active and installed; absent the active, uninstalled."""
        pinned, absent = mp.pins(listing)
        self.assertEqual(
            set(pinned),
            {
                mp.parse_version(e["version"])
                for e in listing
                if e["active"] and e["installed"]
            },
        )
        self.assertEqual(
            sorted(absent),
            sorted(e["version"] for e in listing if e["active"] and not e["installed"]),
        )

    @given(st.data(), st.dictionaries(NAME, st.integers(10, 30), max_size=4))
    def test_pgrx_pairs_in_any_order(
        self, data: st.DataObject, pairs: dict[str, int]
    ) -> None:
        """Each member's flags name its major."""
        items = data.draw(st.permutations(list(pairs.items())))
        declared = ",".join(f" {n}:{m} " for n, m in items)
        self.assertEqual(
            mp.features(declared),
            {n: f"--no-default-features --features pg{m}" for n, m in pairs.items()},
        )


if __name__ == "__main__":
    unittest.main()
