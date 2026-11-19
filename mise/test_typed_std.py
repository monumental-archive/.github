#!/usr/bin/env python3
"""typed_std.py's two readers, row by row (#1676).

`group`: a matched group, an optional group that did not take part, a
named group and group 0. `stdin_text`: standard input with text, and a
process started with none, in process and as a real process whose
descriptor 0 is closed.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import io
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import unittest
from pathlib import Path
from typing import override
from unittest import mock

import hermetic
import typed_std

HERE = Path(__file__).resolve().parent
BASH = shutil.which("bash") or "bash"
VERSION = re.compile(r"v(?P<major>\d+)\.(\d+)(-(?P<pre>\w+))?")


class Group(unittest.TestCase):
    """The group's text by its span, or None, never Any."""

    @override
    def setUp(self) -> None:
        """Match one version with no pre-release part."""
        found = VERSION.fullmatch("v1.70")
        if found is None:
            self.fail("the row's pattern did not match its text")
        self.match = found

    def test_a_matched_group(self) -> None:
        """A numbered group that took part: its text."""
        self.assertEqual("70", typed_std.group(self.match, 2))

    def test_an_unmatched_optional_group(self) -> None:
        """An optional group that did not take part: None."""
        self.assertIsNone(typed_std.group(self.match, 3))
        self.assertIsNone(typed_std.group(self.match, "pre"))

    def test_a_named_group(self) -> None:
        """A group by its name."""
        self.assertEqual("1", typed_std.group(self.match, "major"))

    def test_group_zero(self) -> None:
        """The whole match, by default and by number."""
        self.assertEqual("v1.70", typed_std.group(self.match))
        self.assertEqual("v1.70", typed_std.group(self.match, 0))

    def test_an_empty_group_is_text(self) -> None:
        """A group that took part and matched nothing is "", not None."""
        found = re.match(r"(a*)b", "b")
        if found is None:
            self.fail("the row's pattern did not match its text")
        self.assertEqual("", typed_std.group(found, 1))


class StdinText(unittest.TestCase):
    """All of standard input, or the one refusal."""

    def test_text(self) -> None:
        """Standard input's text, read to its end."""
        with mock.patch("sys.stdin", io.StringIO("one\ntwo\n")):
            self.assertEqual("one\ntwo\n", typed_std.stdin_text())

    def test_absent_in_process(self) -> None:
        """No standard input (sys.stdin is None): refused by name."""
        with (
            mock.patch("sys.stdin", None),
            self.assertRaises(typed_std.NoStdinError) as caught,
        ):
            typed_std.stdin_text()
        self.assertEqual(
            "no standard input: the process was started without one",
            str(caught.exception),
        )

    def test_absent_in_a_real_process(self) -> None:
        """A process whose descriptor 0 is closed: the same refusal."""
        program = (
            "import sys\n"
            f"sys.path.append({str(HERE)!r})\n"
            "import typed_std\n"
            "try:\n"
            "    typed_std.stdin_text()\n"
            "except typed_std.NoStdinError as err:\n"
            "    print(err)\n"
        )
        # ruff: ignore[subprocess-without-shell-equals-true]
        done = subprocess.run(
            [BASH, "-c", 'exec 0<&-; exec "$0" -c "$1"', sys.executable, program],
            capture_output=True,
            text=True,
            check=True,
            env=hermetic.env(),
        )
        self.assertEqual(
            "no standard input: the process was started without one\n", done.stdout
        )


if __name__ == "__main__":
    unittest.main()
