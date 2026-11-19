#!/usr/bin/env python3
"""attach_publish.py's two readers, on any checksums list and manifest.

The properties of `test_attach_publish.py`'s fixed rows (#1556):

- T: `digests` reads back, for every asset name, exactly the digests a
  checksums list written as attach-release.yml writes it
  (`sha256sum ./*` with each `./` cut: `<digest>  <name>`) records for
  that name, in order, whatever other records surround them. The
  names are drawn from the alphabet a release asset name keeps (GitHub
  renames the rest on upload), so every name the converge step can ask
  about is one of them.
- R: `digests` answers on any text at all; `build_subjects` answers or
  refuses by name (`StepError`) on any JSON document and any text.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import attach_publish as attach
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

DIGEST = st.from_regex(r"[0-9a-f]{64}", fullmatch=True)
NAME = st.from_regex(r"[A-Za-z0-9._+-]{1,24}", fullmatch=True)
SCALAR = (
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text()
)
JSON = st.recursive(
    SCALAR,
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.text(max_size=6), inner, max_size=3)
    ),
    max_leaves=12,
)
# A manifest shaped like one, with any value where a field goes.
ENTRY = st.fixed_dictionaries(
    {},
    optional={"type": st.sampled_from(["build-subject", "sbom"]) | JSON, "name": JSON},
)
MANIFEST = st.fixed_dictionaries(
    {}, optional={"entries": st.lists(ENTRY | JSON, max_size=4) | SCALAR | JSON}
)


def written(text: str) -> Path:
    """Write text to a fresh scratch file; the caller unlinks it.

    Returns:
        Its path.

    """
    fd, name = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    path = Path(name)
    path.write_text(text, encoding="utf-8")
    return path


class DigestsReadBack(unittest.TestCase):
    """T: what the list records for a name is what `digests` reads."""

    @given(st.lists(st.tuples(DIGEST, NAME), max_size=8), NAME)
    @example([("a" * 64, "tool"), ("b" * 64, "tool.sig")], "tool")
    def test_every_name_reads_back(
        self, records: list[tuple[str, str]], probe: str
    ) -> None:
        """Each name, and one maybe absent, reads back its digests."""
        path = written("".join(f"{d}  {n}\n" for d, n in records))
        try:
            for name in {n for _, n in records} | {probe}:
                want = "\n".join(d for d, n in records if n == name)
                self.assertEqual(attach.digests(path, name), want, name)
        finally:
            path.unlink()


class AnyInput(unittest.TestCase):
    """R: the readers answer, or refuse by name, on anything."""

    @given(st.text(), st.text(max_size=12))
    def test_digests_answers_on_any_text(self, text: str, name: str) -> None:
        """Any list, any name: an answer, never a raise."""
        path = written(text)
        try:
            self.assertIsInstance(attach.digests(path, name), str)
        finally:
            path.unlink()

    @given(MANIFEST | JSON)
    @example({"entries": True})
    @example({"entries": 1})
    def test_build_subjects_answers_or_refuses(self, doc: object) -> None:
        """Any JSON: a list of names, or the step's own refusal."""
        self.read_manifest(json.dumps(doc))

    @given(st.text())
    def test_build_subjects_on_any_text(self, text: str) -> None:
        """Any text: a list of names, or the step's own refusal."""
        self.read_manifest(text)

    def read_manifest(self, text: str) -> None:
        """Hold one manifest's reading to an answer or a StepError."""
        path = written(text)
        try:
            names = attach.build_subjects(path)
        except attach.StepError as refused:
            self.assertTrue(str(refused).startswith(f"{path} is not "), refused)
        else:
            self.assertIsInstance(names, list)
            for name in names:
                self.assertIsInstance(name, str)
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
