#!/usr/bin/env python3
"""Tests for licence_texts.py, lint:licence's licence-text check (#1589).

The canon's REUSE.toml labelled two fixtures copied from edtf as the
canon's own 0BSD work. The relabel names `MIT OR Apache-2.0`, and a
REUSE.toml naming a licence whose text is not tracked is the half of
that defect a machine can hold. Every red row asserts its message, so
a row cannot pass on a different failure.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

import hermetic
import licence_texts as lt

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:licence"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

FIXTURE_ENTRY = """\
version = 1

[[annotations]]
path = ["**"]
precedence = "aggregate"
SPDX-FileCopyrightText = "2026 Carl Allen"
SPDX-License-Identifier = "0BSD"

[[annotations]]
path = ["release/testdata/edtf-core-1.3.3.crate"]
precedence = "override"
SPDX-FileCopyrightText = "Copyright (c) the edtf contributors"
SPDX-License-Identifier = "MIT OR Apache-2.0"
"""


def run(files: dict[str, str]) -> tuple[int, str]:
    """Plant files, list them all as tracked, and run the helper's main.

    Returns:
        The exit status and stderr.

    """
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        err = io.StringIO()
        stdin = sys.stdin
        with (
            contextlib.chdir(root),
            contextlib.redirect_stderr(err),
        ):
            sys.stdin = io.StringIO("\0".join(files) + "\0")
            try:
                status = lt.main(["licence_texts.py"])
            finally:
                sys.stdin = stdin
        return status, err.getvalue()


class Identifiers(unittest.TestCase):
    """An expression splits into the identifiers whose texts it needs."""

    def test_operators_and_parentheses_are_not_identifiers(self) -> None:
        """OR, AND and WITH in either case, and brackets, name nothing."""
        named: list[str] = [
            "MIT",
            "Apache-2.0",
            "GPL-2.0-only",
            "Classpath-exception-2.0",
        ]
        self.assertEqual(
            named,
            lt.identifiers(
                "(MIT or Apache-2.0) AND GPL-2.0-only WITH Classpath-exception-2.0"
            ),
        )

    def test_or_later_needs_the_base_text(self) -> None:
        """`GPL-2.0+` is GPL-2.0's text with the or-later operator."""
        base: list[str] = ["GPL-2.0"]
        self.assertEqual(base, lt.identifiers("GPL-2.0+"))

    def test_a_list_of_expressions_is_read_whole(self) -> None:
        """SPDX-License-Identifier may be a list of strings."""
        config: dict[str, object] = {
            "annotations": [
                {"SPDX-License-Identifier": ["MIT", "CC0-1.0"]},
                {"SPDX-License-Identifier": "0BSD"},
                {"path": "no-licence"},
            ]
        }
        every: list[str] = ["MIT", "CC0-1.0", "0BSD"]
        self.assertEqual(every, lt.expressions(config))


class LicenceTexts(unittest.TestCase):
    """Every identifier a tracked REUSE.toml names has its tracked text."""

    def test_an_identifier_with_no_text_is_red_by_name(self) -> None:
        """A missing text names the identifier and the file naming it."""
        status, err = run({
            "REUSE.toml": '[[annotations]]\nSPDX-License-Identifier = "MIT"\n',
            "LICENSES/0BSD.txt": "0BSD\n",
        })
        self.assertEqual(1, status)
        self.assertEqual(
            "lint:licence: REUSE.toml names MIT, and LICENSES/MIT.txt is not tracked\n",
            err,
        )

    def test_an_or_expression_needs_both_texts(self) -> None:
        """One text of a dual licence is not enough: the other is named."""
        status, err = run({
            "REUSE.toml": FIXTURE_ENTRY,
            "LICENSES/0BSD.txt": "0BSD\n",
            "LICENSES/MIT.txt": "MIT\n",
        })
        self.assertEqual(1, status)
        self.assertEqual(
            "lint:licence: REUSE.toml names Apache-2.0,"
            " and LICENSES/Apache-2.0.txt is not tracked\n",
            err,
        )

    def test_main_with_the_relabel_and_no_texts_is_red(self) -> None:
        """Main's tree after item 1 alone: both new texts named (fails before)."""
        status, err = run({
            "REUSE.toml": FIXTURE_ENTRY,
            "LICENSES/0BSD.txt": "0BSD\n",
        })
        self.assertEqual(1, status)
        self.assertEqual(
            "lint:licence: REUSE.toml names MIT,"
            " and LICENSES/MIT.txt is not tracked\n"
            "lint:licence: REUSE.toml names Apache-2.0,"
            " and LICENSES/Apache-2.0.txt is not tracked\n",
            err,
        )

    def test_every_text_present_is_green(self) -> None:
        """The dual-licensed fixture entry with all three texts passes."""
        status, err = run({
            "REUSE.toml": FIXTURE_ENTRY,
            "LICENSES/0BSD.txt": "0BSD\n",
            "LICENSES/MIT.txt": "MIT\n",
            "LICENSES/Apache-2.0.txt": "Apache\n",
        })
        self.assertEqual((0, ""), (status, err))

    def test_a_nested_reuse_toml_is_held_too(self) -> None:
        """Any tracked REUSE.toml, against the root LICENSES/."""
        status, err = run({
            "REUSE.toml": '[[annotations]]\nSPDX-License-Identifier = "0BSD"\n',
            "sub/REUSE.toml": '[[annotations]]\nSPDX-License-Identifier = "MIT"\n',
            "LICENSES/0BSD.txt": "0BSD\n",
        })
        self.assertEqual(1, status)
        self.assertEqual(
            "lint:licence: sub/REUSE.toml names MIT,"
            " and LICENSES/MIT.txt is not tracked\n",
            err,
        )

    def test_a_text_elsewhere_does_not_count(self) -> None:
        """A text outside LICENSES/, or not `.txt`, is not REUSE's place."""
        status, err = run({
            "REUSE.toml": '[[annotations]]\nSPDX-License-Identifier = "MIT"\n',
            "LICENSE-MIT": "MIT\n",
            "LICENSES/MIT.md": "MIT\n",
        })
        self.assertEqual(1, status)
        self.assertEqual(
            "lint:licence: REUSE.toml names MIT, and LICENSES/MIT.txt is not tracked\n",
            err,
        )

    def test_the_placeholder_is_still_refused(self) -> None:
        """The scaffold's placeholder keeps its message from before (#214)."""
        status, err = run({
            "REUSE.toml": (
                '[[annotations]]\nSPDX-License-Identifier = "<SPDX expression>"\n'
            ),
            "LICENSES/0BSD.txt": "0BSD\n",
        })
        self.assertEqual(1, status)
        self.assertEqual(
            "lint:licence: REUSE.toml still carries the scaffold's"
            " <SPDX expression> placeholder — pick the licence (#214)\n",
            err,
        )

    def test_a_malformed_file_is_red_by_name(self) -> None:
        """A REUSE.toml that is not TOML is named, never read as empty."""
        status, err = run({"REUSE.toml": "[[annotations]\n"})
        self.assertEqual(1, status)
        self.assertTrue(
            err.startswith("lint:licence: REUSE.toml does not parse: "), err
        )

    def test_a_wrong_shape_is_red_by_where(self) -> None:
        """An annotation of the wrong shape is refused, naming where."""
        for reuse, where in (
            ("annotations = 1\n", "annotations is not a list"),
            ("annotations = [1]\n", "annotations[0] is not an object"),
            (
                "[[annotations]]\nSPDX-License-Identifier = 1\n",
                (
                    "annotations[0].SPDX-License-Identifier is not a string or"
                    " a list of strings"
                ),
            ),
        ):
            with self.subTest(where=where):
                status, err = run({"REUSE.toml": reuse})
                self.assertEqual(1, status)
                self.assertEqual(f"lint:licence: REUSE.toml: {where}\n", err)

    def test_no_reuse_toml_is_green(self) -> None:
        """A repository that has not declared yet has nothing to check."""
        self.assertEqual((0, ""), run({"LICENSE": "0BSD\n"}))

    def test_the_canons_own_tree_is_green(self) -> None:
        """The canon's REUSE.toml against the canon's LICENSES/."""
        tracked = ["REUSE.toml"] + [
            f"LICENSES/{path.name}" for path in (ROOT / "LICENSES").iterdir()
        ]
        clean: list[str] = []
        self.assertEqual(clean, lt.findings(ROOT, tracked))
        # The relabel is what makes this row mean something: the canon
        # names licences other than its own, and drops either text red.
        for name in ("MIT", "Apache-2.0"):
            without = [p for p in tracked if p != f"LICENSES/{name}.txt"]
            want: list[str] = [
                f"REUSE.toml names {name}, and LICENSES/{name}.txt is not tracked"
            ]
            self.assertEqual(want, lt.findings(ROOT, without))

    def test_usage(self) -> None:
        """An argument is a usage error, not a pass."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(2, lt.main(["licence_texts.py", "extra"]))
        self.assertEqual(
            f"{TASK}: usage: git ls-files -z | licence_texts.py\n", err.getvalue()
        )


if __name__ == "__main__":
    unittest.main()
