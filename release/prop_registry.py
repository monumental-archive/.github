#!/usr/bin/env python3
"""registry.py's readers of image facts and metadata, on any input.

The properties of `test_registry.py`'s fixed rows (#1556):

- T: `parse_images` reads back exactly the (image, tag, digest)
  triples `assemble` renders into its `images` output, in order,
  whatever extra keys ride along.
- T: `index_bytes` takes back exactly the manifest `imagetools
  create --dry-run` printed, its one trailing newline dropped, so the
  digest is a function of the manifest's bytes alone.
- V: `read_arch_digests` answers in the order it is asked, whichever
  order the per-architecture files were written in.
- R: on any text, `parse_images` and `facts_pairs` answer or refuse by
  name (`RefusedError`); on any bytes, so does `index_bytes`. None of
  them dies with anything else — including on JSON nested deeper than
  the parser recurses, the shape an untrusted input can take.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

import registry
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

if TYPE_CHECKING:
    import random
    from collections.abc import Callable

IMAGE = st.from_regex(r"ghcr\.io/[a-z0-9._/-]{1,24}", fullmatch=True)
TAG = st.from_regex(r"[A-Za-z0-9._-]{1,16}", fullmatch=True)
DIGEST = st.from_regex(r"sha256:[0-9a-f]{64}", fullmatch=True)
# A JSON scalar: JSON has no NaN or Infinity, and these documents are
# read strictly (#1676), so a float that is not finite is not one.
SCALAR: st.SearchStrategy[object] = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(),
)


def nested(inner: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Wrap values in an array or an object.

    Returns:
        The strategy one level deeper.

    """
    arrays: st.SearchStrategy[object] = st.lists(inner, max_size=3)
    objects: st.SearchStrategy[object] = st.dictionaries(
        st.text(max_size=6), inner, max_size=3
    )
    return arrays | objects


def unnamed(key: str) -> bool:
    """Say whether a key is none of the three an entry is read for.

    Returns:
        True for a key the reader steps over.

    """
    return key not in {"image", "tag", "digest"}


def dumped(value: object) -> str:
    """Write a value as JSON text.

    Returns:
        The text.

    """
    return json.dumps(value)


def dumped_line(value: object) -> bytes:
    """Write a value as a dry run prints it: JSON and a newline.

    Returns:
        The bytes.

    """
    return json.dumps(value).encode() + b"\n"


JSON: st.SearchStrategy[object] = st.recursive(SCALAR, nested, max_leaves=12)
EXTRA: st.SearchStrategy[dict[str, object]] = st.dictionaries(
    st.text(max_size=8).filter(unnamed),
    JSON,
    max_size=2,
)
# Deeper than the parser recurses: an untrusted input may say this.
DEEP = "[" * 100_000


def refused_or(call: Callable[[], object]) -> object:
    """Call a reader, turning its own refusal into None.

    Returns:
        Its answer, or None when it refused by name.

    """
    try:
        return call()
    except registry.RefusedError:
        return None


class ImagesReadBack(unittest.TestCase):
    """T: the images output, read back, is the images written."""

    @given(st.lists(st.tuples(IMAGE, TAG, DIGEST, EXTRA), min_size=1, max_size=4))
    def test_parse_images_reads_back(
        self, entries: list[tuple[str, str, str, dict[str, object]]]
    ) -> None:
        """Rendered as assemble renders it, read back whole."""
        docs: list[dict[str, object]] = [
            {**x, "image": i, "tag": t, "digest": d} for i, t, d, x in entries
        ]
        raw = json.dumps(docs, separators=(",", ":"), ensure_ascii=False)
        want: list[tuple[str, str, str]] = [(i, t, d) for i, t, d, _ in entries]
        self.assertEqual(registry.parse_images(raw), want)


class IndexBytes(unittest.TestCase):
    """T: the manifest bytes are the dry run's, less its newline."""

    @given(st.dictionaries(st.text(max_size=6), JSON, max_size=3))
    def test_index_bytes_takes_the_manifest_back(self, rest: dict[str, object]) -> None:
        """Body and digest follow the printed bytes, nothing else."""
        doc: dict[str, object] = {**rest, "mediaType": registry.OCI_INDEX}
        manifest = json.dumps(doc).encode()
        body, ctype = registry.index_bytes(manifest + b"\n")
        self.assertEqual((body, ctype), (manifest, registry.OCI_INDEX))
        self.assertEqual(
            hashlib.sha256(body).hexdigest(), hashlib.sha256(manifest).hexdigest()
        )


class ArchOrder(unittest.TestCase):
    """V: the digests come back in the order asked, not the order written."""

    @given(
        st.dictionaries(
            st.from_regex(r"[a-z0-9]{1,8}", fullmatch=True),
            DIGEST,
            min_size=1,
            max_size=4,
        ),
        st.randoms(),
    )
    def test_asked_order(self, digests: dict[str, str], shuffle: random.Random) -> None:
        """Files written in any order read back in the asked order."""
        written = list(digests)
        shuffle.shuffle(written)
        asked = list(digests)
        shuffle.shuffle(asked)
        with tempfile.TemporaryDirectory() as tmp:
            for arch in written:
                (Path(tmp) / arch).write_text(digests[arch] + "\n", "utf-8")
            got = registry.read_arch_digests(Path(tmp), asked)
        want: list[str] = [digests[a] for a in asked]
        self.assertEqual(got, want)


class AnyInput(unittest.TestCase):
    """R: the readers answer, or refuse by name, on anything."""

    @given(JSON.map(dumped) | st.text())
    @example(DEEP)
    def test_parse_images(self, raw: str) -> None:
        """Triples, or the step's own refusal."""
        got = refused_or(lambda: registry.parse_images(raw))
        self.assertTrue(got is None or isinstance(got, list))

    @given(JSON.map(dumped) | st.text())
    @example(DEEP)
    def test_facts_pairs(self, facts: str) -> None:
        """Pairs, or the step's own refusal."""
        got = refused_or(lambda: registry.facts_pairs(facts))
        self.assertTrue(got is None or isinstance(got, list))

    @given(st.binary() | JSON.map(dumped_line))
    @example(DEEP.encode() + b"\n")
    def test_index_bytes(self, dry_run: bytes) -> None:
        """A manifest, or the step's own refusal."""
        got = refused_or(lambda: registry.index_bytes(dry_run))
        self.assertTrue(got is None or isinstance(got, tuple))


if __name__ == "__main__":
    unittest.main()
