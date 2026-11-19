#!/usr/bin/env python3
"""workspace_inherit.py's verdict, on workspaces nobody wrote down.

The properties of `test_workspace_inherit.py`'s fixed rows (#1548),
over generated `[workspace.package]` tables and cargo's per-member
account of them:

- Invariance (V): a declared key is a finding exactly when no member
  reports what the declaration resolves to for it (the value itself,
  or for a path key the same file rebased from the member's
  directory); the order of the members never changes the findings,
  and a key cargo cannot judge is reported unjudged, never a finding.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import os
import unittest
from pathlib import Path

from hypothesis import strategies as st
from typed_hypothesis import given

_SPEC = importlib.util.spec_from_file_location(
    "workspace_inherit",
    Path(__file__).with_name("workspace_inherit.py"),
)
wi = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(wi)

ROOT = Path("/ws")
VALUE_KEYS = sorted(set(wi.REPORTED) - set(wi.PATH_KEYS))
VALUE = st.one_of(st.text(max_size=6), st.lists(st.text(max_size=4), max_size=2))
DIR = st.lists(st.from_regex(r"[a-z]{1,5}", fullmatch=True), max_size=2).map("/".join)


@st.composite
def workspace(
    draw: st.DrawFn,
) -> tuple[dict[str, str | list[str]], list[dict[str, str]], set[str]]:
    """Draw a declaration, members, and which keys some member takes.

    Returns:
        The table, the members as cargo reports them, and the taken keys.

    """
    keys = draw(
        st.lists(st.sampled_from([*VALUE_KEYS, "readme"]), unique=True, max_size=5)
    )
    table = {}
    for key in keys:
        table[key] = "README.md" if key == "readme" else draw(VALUE)
    count = draw(st.integers(1, 4))
    members = []
    taken: set[str] = set()
    for index in range(count):
        where = draw(DIR)
        manifest = ROOT / where / f"m{index}" / "Cargo.toml"
        package = {"name": f"m{index}", "manifest_path": str(manifest)}
        for key, declared in table.items():
            field = wi.REPORTED[key]
            if draw(st.booleans()):
                taken.add(key)
                package[field] = (
                    os.path.relpath(ROOT / declared, manifest.parent)
                    if key == "readme"
                    else declared
                )
            else:
                package[field] = draw(st.sampled_from([None, [], "own"]))
                if package[field] == declared:
                    taken.add(key)
        members.append(package)
    return table, members, taken


class Judge(unittest.TestCase):
    """A key nobody takes, and only that, is a finding."""

    @given(workspace())
    def test_a_finding_exactly_when_nobody_takes_it(
        self, drawn: tuple[dict, list[dict], set[str]]
    ) -> None:
        """Findings are the declared keys no member reports."""
        table, members, taken = drawn
        report: list[str] = []
        found = wi.judge(table, members, ROOT, report)
        self.assertEqual({f.key for f in found}, set(table) - taken)
        self.assertEqual(report, [])

    @given(st.data(), workspace())
    def test_member_order_does_not_matter(
        self, data: st.DataObject, drawn: tuple[dict, list[dict], set[str]]
    ) -> None:
        """The same members in any order, the same keys found."""
        table, members, _ = drawn
        shuffled = data.draw(st.permutations(members))
        one = [f.key for f in wi.judge(table, members, ROOT, [])]
        two = [f.key for f in wi.judge(table, shuffled, ROOT, [])]
        self.assertEqual(one, two)

    @given(st.sampled_from(sorted(wi.UNRESOLVABLE)), workspace())
    def test_unjudgeable_keys_are_reported(
        self, key: str, drawn: tuple[dict, list[dict], set[str]]
    ) -> None:
        """Named in the report, never a finding."""
        table, members, _ = drawn
        report: list[str] = []
        found = wi.judge({**table, key: False}, members, ROOT, report)
        self.assertNotIn(key, {f.key for f in found})
        self.assertIn(f"{key}: {wi.UNRESOLVABLE[key]}", report)


if __name__ == "__main__":
    unittest.main()
