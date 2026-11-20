#!/usr/bin/env python3
"""deny_skips.py's variants, on deny.toml files nobody wrote down.

The properties of `test_deny_skips.py`'s fixed rows (#1555), over
generated `[bans]` tables:

- Invariance (V): `entries` lists every `skip` and `skip-tree` entry
  in declaration order, version kept, whichever spelling (bare string
  or table) the entry uses.
- Round trip (T): each skip's variant is still TOML, and reads back
  as the same config with exactly that one entry gone: every other
  skip, the same crate's skip at another version, and every `deny`
  entry naming the crate, kept. Names that share a prefix (`foo`,
  `foo-bar`) never take each other's line. This found that every line
  naming the crate was dropped, deny entries and other versions too.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import unittest

import decoded
import deny_skips as ds
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

NAME = st.from_regex(r"[a-z][a-z0-9_-]{0,8}", fullmatch=True)
VERSION = st.one_of(st.just(""), st.from_regex(r"@[0-9]\.[0-9]{1,2}", fullmatch=True))
Drawn = tuple[str, list[tuple[str, str]], list[str]]


def draw_config(draw: st.DrawFn) -> Drawn:
    """Draw a deny.toml with one skip entry per line.

    Returns:
        The text, the (key, spec) skips in declaration order, and the
        denied crates.

    """
    names = draw(st.lists(NAME, min_size=1, max_size=4, unique=True))
    specs = draw(
        st.lists(
            st.tuples(st.sampled_from(names), VERSION).map("".join),
            min_size=1,
            max_size=6,
            unique=True,
        )
    )
    lines: dict[str, list[str]] = {"skip": [], "skip-tree": []}
    skips: dict[str, list[str]] = {"skip": [], "skip-tree": []}
    for spec in specs:
        key = draw(st.sampled_from(ds.SKIP_KEYS))
        if draw(st.booleans()):
            lines[key].append(f'  {{ crate = "{spec}", reason = "lag" }},')
        else:
            lines[key].append(f'  "{spec}",')
        skips[key].append(spec)
    denied = draw(st.lists(st.sampled_from([*names, "zz"]), max_size=3, unique=True))
    text = ["[bans]", "deny = ["]
    text += [f'  {{ crate = "{d}" }},' for d in denied]
    text.append("]")
    order = draw(st.permutations(ds.SKIP_KEYS))
    for key in order:
        text += [f"{key} = [", *lines[key], "]"]
    ordered = [(key, spec) for key in ds.SKIP_KEYS for spec in skips[key]]
    return "\n".join(text) + "\n", ordered, denied


config = st.composite(draw_config)


def crate(item: object) -> str:
    """Name the crate an entry declares, as written.

    Returns:
        The spec.

    """
    if decoded.is_object(item):
        return decoded.as_str(item.get("crate"), "crate")
    return decoded.as_str(item, "entry")


class Variants(unittest.TestCase):
    """One variant per skip, each missing exactly that skip."""

    @given(config())
    def test_entries_in_order(self, drawn: Drawn) -> None:
        """Every skip and skip-tree entry, in declaration order."""
        text, skips, _ = drawn
        self.assertEqual(ds.entries(decoded.load_toml(text)), skips)

    @given(config())
    @example((
        '[bans]\ndeny = [\n  { crate = "foo" },\n]\nskip = [\n  "foo@1.0",\n]\n',
        [("skip", "foo@1.0")],
        ["foo"],
    ))
    @example((
        '[bans]\nskip = [\n  "w@0.48",\n  "w@0.52",\n]\n',
        [("skip", "w@0.48"), ("skip", "w@0.52")],
        [],
    ))
    def test_each_variant_drops_exactly_its_entry(self, drawn: Drawn) -> None:
        """The variant reads back with one skip gone and the rest kept."""
        text, skips, denied = drawn
        lines = text.splitlines(keepends=True)
        for key, spec in skips:
            kept = ds.variant_without(lines, key, spec)
            if kept is None:
                self.fail(f"no variant without {key} {spec}")
            bans = decoded.load_toml("".join(kept))["bans"]
            back = decoded.as_object(bans, "bans")
            for other in ds.SKIP_KEYS:
                expected = [s for k, s in skips if k == other and (k, s) != (key, spec)]
                read = decoded.as_array(back.get(other, []), other)
                crates = [crate(i) for i in read]
                self.assertEqual(crates, expected)
            deny = decoded.as_array(back.get("deny", []), "deny")
            denied_back = [crate(i) for i in deny]
            self.assertEqual(denied_back, denied)


if __name__ == "__main__":
    unittest.main()
