#!/usr/bin/env python3
"""pgrx_postgres.py's plan, on listings nobody wrote down.

The properties of `test_pgrx_postgres.py`'s fixed rows (#1548), over
generated `mise ls` listings and declared majors:

- Invariance (V): only an active entry is a pin, and only an
  installed one serves; the majors served are the leading components
  of those versions, in whatever order the listing comes.
- Refusal (R): `pinned` reads any listing, whatever its versions say,
  and never raises (#1555: a version `²` passed `isdigit` and `int()`
  raised).
- Invariance (V): a plan is all or nothing: one record per declared
  major when every one is pinned with a `pg_config`, and no record at
  all, with one finding per major missing, when any is not.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import tempfile
import unittest
from pathlib import Path
from typing import TypedDict

import pgrx_postgres as pp
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given


class EntryDoc(TypedDict):
    """One entry of `mise ls --json` for the postgres tool."""

    version: str
    active: bool
    installed: bool


MAJOR = st.integers(13, 19)
VERSION: st.SearchStrategy[str] = st.tuples(
    MAJOR, st.integers(0, 20), st.integers(0, 9)
).map(lambda v: "{}.{}.{}".format(*v))
ENTRY: st.SearchStrategy[EntryDoc] = st.builds(
    EntryDoc, version=VERSION, active=st.booleans(), installed=st.booleans()
)
ANY_VERSION: st.SearchStrategy[str] = st.text(max_size=8)
ANY_ENTRY: st.SearchStrategy[EntryDoc] = st.builds(
    EntryDoc, version=ANY_VERSION, active=st.booleans(), installed=st.booleans()
)
# A version whose major is a digit Python reads but int() refuses.
SUPERSCRIPT: list[EntryDoc] = [{"version": "²", "active": True, "installed": True}]


class Pinned(unittest.TestCase):
    """Active pins, installed or not."""

    @given(st.data(), st.lists(ENTRY, max_size=6))
    def test_active_installed_serve_in_any_order(
        self, data: st.DataObject, listing: list[EntryDoc]
    ) -> None:
        """Served majors and absent versions, whatever the order."""
        shuffled = data.draw(st.permutations(listing))
        found, absent = pp.pinned(pp.entries(listing))
        found_two, absent_two = pp.pinned(pp.entries(shuffled))
        majors = {
            int(e["version"].split(".")[0])
            for e in listing
            if e["active"] and e["installed"]
        }
        served, served_two = set(found), set(found_two)
        self.assertEqual(served, majors)
        self.assertEqual(served_two, majors)
        absent_sorted = sorted(absent)
        uninstalled = sorted(
            e["version"] for e in listing if e["active"] and not e["installed"]
        )
        self.assertEqual(absent_sorted, uninstalled)
        absent_two_sorted = sorted(absent_two)
        self.assertEqual(absent_sorted, absent_two_sorted)


class Refusal(unittest.TestCase):
    """Any listing is read."""

    @given(st.lists(ANY_ENTRY, max_size=4))
    @example(SUPERSCRIPT)
    def test_any_versions(self, listing: list[EntryDoc]) -> None:
        """Majors are ints; nothing raises."""
        found, _ = pp.pinned(pp.entries(listing))
        self.assertTrue(all(isinstance(m, int) for m in found))


class Plan(unittest.TestCase):
    """Every major, or none."""

    @given(st.sets(MAJOR, min_size=1, max_size=4), st.sets(MAJOR, max_size=6))
    def test_all_or_nothing(self, declared: set[int], served: set[int]) -> None:
        """Records for all declared majors, or findings and no record."""
        with tempfile.TemporaryDirectory() as scratch:
            listing: list[dict[str, object]] = []
            for major in served:
                root = Path(scratch) / str(major)
                (root / "bin").mkdir(parents=True)
                (root / "bin" / "pg_config").write_text("", encoding="utf-8")
                listing.append({
                    "version": f"{major}.1.0",
                    "active": True,
                    "installed": True,
                    "install_path": str(root),
                })
            records, findings = pp.plan(sorted(declared), pp.entries(listing))
        missing = declared - served
        if missing:
            self.assertEqual(records, list[str]())
            for major in missing:
                self.assertTrue(any(f.startswith(f"pg{major} ") for f in findings))
        else:
            self.assertEqual(findings, list[str]())
            planned = [r.split("\t")[1] for r in records]
            majors = [str(m) for m in sorted(declared)]
            self.assertEqual(planned, majors)


if __name__ == "__main__":
    unittest.main()
