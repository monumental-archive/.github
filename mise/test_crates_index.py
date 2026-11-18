#!/usr/bin/env python3
"""The crates.io sparse-index path rule, once (#1383).

Every bucket of cargo's layout, read against paths the index host
serves: `ed/tf/edtf-core` is release/testdata's captured record, and
`3/s/syn`, `2/cc`, `1/a` are the host's own short-name buckets.
"""

import importlib.util
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "crates_index", Path(__file__).resolve().parent / "crates-index.py"
)
crates_index = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(crates_index)


class IndexPath(unittest.TestCase):
    """cargo's index layout, bucket by bucket."""

    def test_each_bucket(self) -> None:
        """One, two, three, and four or more characters."""
        for name, want in (
            ("a", "1/a"),
            ("cc", "2/cc"),
            ("syn", "3/s/syn"),
            ("serde", "se/rd/serde"),
            ("edtf-core", "ed/tf/edtf-core"),
            ("lab-core", "la/b-/lab-core"),
        ):
            with self.subTest(name=name):
                self.assertEqual(want, crates_index.index_path(name))

    def test_the_name_is_lowercased(self) -> None:
        """The index files a crate under its lowercased name."""
        self.assertEqual("ed/tf/edtf-core", crates_index.index_path("EDTF-Core"))
        self.assertEqual("3/s/syn", crates_index.index_path("Syn"))

    def test_an_empty_name_has_no_record(self) -> None:
        """An empty name is refused, never placed at `0/`."""
        with self.assertRaisesRegex(
            ValueError, "a crate with no name has no index record"
        ):
            crates_index.index_path("")

    def test_the_url_is_the_host_and_the_path(self) -> None:
        """The record's URL, as both readers ask for it."""
        self.assertEqual(
            "https://index.crates.io/ed/tf/edtf-core",
            crates_index.index_url("edtf-core"),
        )


class Command(unittest.TestCase):
    """`crates-index.py url <crate>`, for the shell reader."""

    @staticmethod
    def run_main(*argv: str) -> tuple[int, str, str]:
        """Run main and capture its streams.

        Returns:
            The exit code, stdout and stderr.

        """
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = crates_index.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_url(self) -> None:
        """One line, the record's URL."""
        self.assertEqual(
            (0, "https://index.crates.io/al/ph/alpha\n", ""),
            self.run_main("url", "alpha"),
        )

    def test_usage(self) -> None:
        """Anything but `url <crate>` is a usage error."""
        for argv in ((), ("url",), ("path", "alpha"), ("url", "a", "b")):
            with self.subTest(argv=argv):
                self.assertEqual(
                    (2, "", "usage: crates-index.py url <crate>\n"),
                    self.run_main(*argv),
                )

    def test_an_empty_name(self) -> None:
        """The refusal reaches the caller's stderr by name."""
        self.assertEqual(
            (2, "", "crates-index.py: a crate with no name has no index record\n"),
            self.run_main("url", ""),
        )


if __name__ == "__main__":
    unittest.main()
