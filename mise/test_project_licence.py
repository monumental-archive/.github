#!/usr/bin/env python3
"""Tests for project_licence.py, the reader both derivations call (#1589).

The script rows live beside their scripts (`test_citation.py`,
`test_derive_badges.py`), which run each derivation whole. These run
the reader in process for the branches neither script row reaches.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import project_licence as pl

HERE = Path(__file__).resolve().parent


def run(files: dict[str, str], argv: list[str] | None = None) -> tuple[int, str, str]:
    """Plant files and run the reader's main in that directory.

    Returns:
        The exit status, stdout and stderr.

    """
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        for name, text in files.items():
            (root / name).write_text(text, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.chdir(root),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            status = pl.main(argv or ["project_licence.py", "fix:test"])
        return status, out.getvalue(), err.getvalue()


class Reader(unittest.TestCase):
    """The reader's own branches."""

    def test_one_expression_is_printed(self) -> None:
        """No declaration needed where the tree says one thing."""
        reuse = '[[annotations]]\nSPDX-License-Identifier = "0BSD"\n'
        self.assertEqual((0, "0BSD\n", ""), run({"REUSE.toml": reuse}))

    def test_a_list_value_is_read_whole(self) -> None:
        """SPDX-License-Identifier may be a list; each entry counts."""
        reuse = '[[annotations]]\nSPDX-License-Identifier = ["0BSD", "MIT"]\n'
        status, out, err = run({"REUSE.toml": reuse})
        self.assertEqual((1, ""), (status, out))
        self.assertEqual(
            "fix:test: REUSE.toml names several licence expressions:\n"
            "  0BSD\n  MIT\n" + pl.DECLARE + "\n",
            err,
        )

    def test_no_expression_prints_nothing(self) -> None:
        """The caller words its own refusal; the reader stays silent."""
        self.assertEqual((0, "", ""), run({"REUSE.toml": "version = 1\n"}))

    def test_comments_and_other_keys_are_not_the_licence_line(self) -> None:
        """Only a line whose first field is `licence` declares."""
        states = "# licence MIT\nlicences MIT\n\nlicence  0BSD \n"
        self.assertEqual("0BSD", pl.declared(states))
        self.assertEqual("", pl.declared("reuse registered\n"))

    def test_an_unparsable_file_is_red_by_name(self) -> None:
        """A REUSE.toml that is not TOML is named, never read as empty."""
        status, out, err = run({"REUSE.toml": "[[annotations]\n"})
        self.assertEqual((1, ""), (status, out))
        self.assertTrue(err.startswith("fix:test: REUSE.toml does not parse: "), err)

    def test_a_wrong_shape_is_red_by_where(self) -> None:
        """An annotation of the wrong shape is refused, naming where."""
        for reuse, where in (
            ("annotations = 1\n", "annotations is not a list"),
            ("annotations = [1]\n", "annotations[0] is not an object"),
            (
                "[[annotations]]\nSPDX-License-Identifier = [1]\n",
                (
                    "annotations[0].SPDX-License-Identifier is not a string or"
                    " a list of strings"
                ),
            ),
        ):
            with self.subTest(where=where):
                status, out, err = run({"REUSE.toml": reuse})
                self.assertEqual(
                    (1, "", f"fix:test: REUSE.toml: {where}\n"), (status, out, err)
                )

    def test_usage(self) -> None:
        """No caller is a usage error, not a pass."""
        status, out, err = run({}, ["project_licence.py"])
        usage = "usage: project_licence.py <caller>\n"
        self.assertEqual((2, "", usage), (status, out, err))


if __name__ == "__main__":
    unittest.main()
