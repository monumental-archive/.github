#!/usr/bin/env python3
"""lock_arch.py's asset judge, on lock entries nobody wrote down.

The properties of `test_lock_arch.py`'s fixed rows (#1548), over
generated platform keys and release asset names:

- Invariance (V): an asset spelling the key's own architecture, in any
  of its spellings and in either order with the OS, is never a
  candidate; one spelling another architecture, from a GitHub release,
  always is, and its native regex matches the asset the key should
  have named.
- Round trip (T): a release download URL reads back as the repository
  and tag it was built from.
- Refusal (R): `entry_line` judges any value under any key and
  answers with a candidate or None, never a traceback.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import re
import unittest
from pathlib import Path

import lock_arch as la
from hypothesis import strategies as st
from typed_hypothesis import given

LOCK = Path("mise.lock")
# A tool name carrying no architecture or OS token of its own.
TOOL = st.from_regex(r"[bcdefgh]{3,8}", fullmatch=True)
OWNER = st.from_regex(r"[a-z][a-z0-9-]{0,8}", fullmatch=True)
TAG = st.from_regex(r"v[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,2}", fullmatch=True)
KEY_ARCH = {"x64": "x64", "arm64": "arm64"}
OS = st.sampled_from(["linux", "macos", "windows"])
ARCH = st.sampled_from(sorted(KEY_ARCH))
SOURCE = st.tuples(st.tuples(OWNER, TOOL).map("/".join), TAG)


def draw_asset(draw: st.DrawFn, os_family: str, arch_family: str) -> str:
    """Name an asset for an OS and architecture, in either order.

    Returns:
        The asset's file name.

    """
    tool = draw(TOOL)
    arch = draw(st.sampled_from(la.ARCH_SPELLINGS[arch_family]))
    plat = draw(st.sampled_from(la.OS_SPELLINGS[os_family]))
    sep = draw(st.sampled_from(["-", "_", "."]))
    first, second = (arch, plat) if draw(st.booleans()) else (plat, arch)
    return f"{tool}{sep}1.0{sep}{first}{sep}{second}.tar.gz"


asset = st.composite(draw_asset)


def url(repo: str, tag: str, name: str) -> str:
    """Spell a GitHub release download URL.

    Returns:
        The URL.

    """
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


class Judge(unittest.TestCase):
    """The asset agrees with its key, or it is a candidate."""

    @given(st.data(), OS, ARCH, SOURCE)
    def test_own_architecture_is_never_a_candidate(
        self, data: st.DataObject, os_family: str, arch: str, source: tuple[str, str]
    ) -> None:
        """Any spelling, either order: agreement."""
        name = data.draw(asset(os_family, arch))
        value = {"url": url(*source, name)}
        self.assertIsNone(
            la.entry_line(LOCK, "t", f"platforms.{os_family}-{arch}", value)
        )

    @given(st.data(), OS, ARCH, SOURCE)
    def test_another_architecture_always_is(
        self, data: st.DataObject, os_family: str, arch: str, source: tuple[str, str]
    ) -> None:
        """The other family is named, with the regex for the right asset."""
        other = "arm64" if arch == "x64" else "x64"
        name = data.draw(asset(os_family, other))
        value = {"url": url(*source, name)}
        line = la.entry_line(LOCK, "t", f"platforms.{os_family}-{arch}", value)
        if line is None:
            self.fail(name)
        fields = line.split("\t")
        named = [name, *source]
        self.assertEqual(fields[4:7], named)
        native = data.draw(asset(os_family, arch))
        self.assertTrue(re.search(fields[7], native.lower()), (fields[7], native))


class ReleaseSource(unittest.TestCase):
    """A release URL names its repository and tag."""

    @given(OWNER, TOOL, TAG, TOOL)
    def test_round_trip(self, owner: str, repo: str, tag: str, name: str) -> None:
        """Built from (repo, tag), read back as (repo, tag)."""
        self.assertEqual(
            la.release_source(url(f"{owner}/{repo}", tag, name)),
            (f"{owner}/{repo}", tag),
        )


SCALAR: st.SearchStrategy[object] = (
    st.none() | st.booleans() | st.integers() | st.text(max_size=20)
)


def nested(inner: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Wrap values in an array or an object.

    Returns:
        The strategy one level deeper.

    """
    arrays: st.SearchStrategy[object] = st.lists(inner, max_size=3)
    objects: st.SearchStrategy[object] = st.dictionaries(
        st.text(max_size=5), inner, max_size=3
    )
    return arrays | objects


JSON: st.SearchStrategy[object] = st.recursive(SCALAR, nested, max_leaves=8)
ENTRY: st.SearchStrategy[object] = st.one_of(
    JSON, st.fixed_dictionaries({"url": st.text(max_size=80)})
)


class Refusal(unittest.TestCase):
    """Any entry is judged."""

    @given(
        st.one_of(
            st.text(max_size=30), st.text(max_size=20).map("platforms.{}".format)
        ),
        ENTRY,
    )
    def test_entry_line_answers_anything(self, key: str, value: object) -> None:
        """A candidate line or None."""
        line = la.entry_line(LOCK, "t", key, value)
        self.assertTrue(line is None or line.startswith("mise.lock\t"))


if __name__ == "__main__":
    unittest.main()
