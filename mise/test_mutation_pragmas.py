#!/usr/bin/env python3
"""Tests for mutation_pragmas.py's refusal of a file it cannot read (#1555).

The regression row of the defect `prop_mutation_pragmas.py` found: a file
whose bytes are not UTF-8 past its encoding line raised
UnicodeDecodeError out of `main`, a traceback that also left every later
file unread, where the contract is the file named on stderr and exit 1.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import hermetic
import mutation_pragmas as mp

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "audit:mutants"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


HERE = Path(__file__).resolve().parent


class Unreadable(unittest.TestCase):
    """A file that is not UTF-8 is named, and the next file still read."""

    def test_invalid_utf8_is_named_and_the_rest_read(self) -> None:
        """The shrunk example: a CR, then a truncated UTF-8 sequence."""
        with tempfile.TemporaryDirectory() as scratch:
            bad = Path(scratch) / "bad.py"
            bad.write_bytes(b"\r\xc2\x80")
            good = Path(scratch) / "good.py"
            good.write_text("x = 1  # pragma: no mutate\n", encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = mp.main([str(bad), str(good)])
        self.assertEqual(status, 1)
        self.assertIn(f"{TASK}: {bad}: could not tokenize:", err.getvalue())
        self.assertEqual(out.getvalue(), f"{good}\t1\t# pragma: no mutate\n")


if __name__ == "__main__":
    unittest.main()
