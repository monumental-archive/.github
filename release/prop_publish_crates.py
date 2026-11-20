#!/usr/bin/env python3
"""publish_crates.py's index reading, on any sparse-index answer.

The properties of `test_publish_rust_crate.py`'s fixed rows (#1556),
over generated index files. The fetch is replaced in-process by the
row's answer:

- V: a version present in the crate's index file is published, with
  the checksum its record names; a version absent from it is not;
  where the record sits among the others, and what else they hold,
  moves neither.
- R: on any 200 body at all — any bytes — the reader answers or
  refuses by name (`RefusalError`); an index it cannot read is never
  "not published", and it never dies with anything else.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import json
import unittest
from typing import TYPE_CHECKING

import publish_crates as publish
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

if TYPE_CHECKING:
    import random
    from collections.abc import Mapping

NAME = "edtf"
UA = "org-release (+https://example.invalid)"
VERSION = st.from_regex(
    r"[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,2}(-[a-z0-9.]{1,5})?", fullmatch=True
)
CKSUM = st.from_regex(r"[0-9a-f]{64}", fullmatch=True)
# A record as the index writes one: the fields the reader uses, and
# others it must step over.
Extra = dict[str, bool | int | str | None]
EXTRA: st.SearchStrategy[Extra] = st.dictionaries(
    st.sampled_from(["name", "deps", "features", "yanked", "v", "rust_version"]),
    st.none() | st.booleans() | st.integers() | st.text(max_size=8),
    max_size=3,
)


def answer(status: int | None, body: bytes) -> publish.Fetch:
    """Make a fetch that answers every request alike.

    Returns:
        The fetch.

    """

    def fetch(
        method: str, url: str, headers: Mapping[str, str], data: bytes | None = None
    ) -> publish.Response:
        del method, url, headers, data
        return publish.Response(status, body)

    return fetch


def read(body: bytes, version: str) -> str | None:
    """Ask for one version against an index file.

    Returns:
        The checksum, or None when not published.

    """
    return publish.published_checksum(NAME, version, UA, answer(200, body))


class PresentIsPublished(unittest.TestCase):
    """V: present means published with its checksum; absent means not."""

    @given(
        st.dictionaries(VERSION, st.tuples(CKSUM, EXTRA), min_size=1, max_size=6),
        VERSION,
        st.randoms(),
    )
    def test_present_and_absent(
        self,
        records: dict[str, tuple[str, Extra]],
        probe: str,
        shuffle: random.Random,
    ) -> None:
        """Every recorded version reads its checksum; another reads None."""
        lines: list[str] = []
        for v, (c, extra) in records.items():
            record: dict[str, object] = {**extra, "vers": v, "cksum": c}
            lines.append(json.dumps(record))
        shuffle.shuffle(lines)
        body = ("\n".join(lines) + "\n").encode()
        for version, (cksum, _) in records.items():
            self.assertEqual(read(body, version), cksum)
        if probe not in records:
            self.assertIsNone(read(body, probe))


class AnyIndexText(unittest.TestCase):
    """R: any body is answered or refused by name."""

    @given(st.binary() | st.text().map(str.encode), VERSION)
    @example(b"\xff", "1.0.0")
    @example(b"{", "1.0.0")
    @example(b"[]", "1.0.0")
    @example(b'{"vers": "1.0.0"}', "1.0.0")
    def test_answers_or_refuses(self, body: bytes, version: str) -> None:
        """A checksum, None, or the step's own refusal naming the crate."""
        try:
            got = read(body, version)
        except publish.RefusalError as refused:
            self.assertIn(NAME, str(refused))
        else:
            self.assertTrue(got is None or isinstance(got, str))


if __name__ == "__main__":
    unittest.main()
