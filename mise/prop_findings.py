#!/usr/bin/env python3
"""findings.py, the Python writer of findings, on any text (#1984).

The property, over any subject, message and detail lines Hypothesis's
`st.text()` draws (lone surrogates among them, as `os.fsdecode` makes
of a file name that is not UTF-8):

- R: with `ORG_FINDINGS` naming a file, `finding()` returns, the file
  gains exactly one line, and reading it back gives each field equal
  to `_text` of what was given, modelled apart in the test: the text
  the log line shows.

`ORG_FINDINGS` is set by the test to a temporary file, so the case runs
on every machine.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import decoded
import findings
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given


def printed(s: str) -> str:
    """Model the writer's `_text`, apart from it (#1984).

    Returns:
        The text, each character UTF-8 cannot carry as its backslash
        escape.

    """
    return s.encode("utf-8", "backslashreplace").decode("utf-8")


# The examples' detail lists, typed for the examples' decorator.
LONE: list[str] = ["\udfff"]
NONE: list[str] = []


class OneRecordReadsBack(unittest.TestCase):
    """R: one line, read back as the text the log line shows."""

    @given(st.text(), st.text(), st.lists(st.text(), max_size=4))
    @example("bad\udcffname", "x\ud800y", LONE)
    @example("", "", NONE)
    def test_one_record_reads_back(
        self, subject: str, message: str, detail: list[str]
    ) -> None:
        """Exactly one line; each field is `_text` of what was given."""
        with tempfile.TemporaryDirectory() as scratch:
            file = Path(scratch) / "out" / "job.jsonl"
            env = {"ORG_FINDINGS": str(file), "MISE_TASK_NAME": "lint:demo"}
            with (
                mock.patch.dict(os.environ, env),
                redirect_stderr(io.StringIO()),
            ):
                findings.finding(subject, message, detail)
            text = file.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"), text)
        self.assertEqual(text.count("\n"), 1, text)
        record = decoded.as_object(decoded.load_json(text), "the record")
        lines: list[object] = [printed(line) for line in detail]
        expected: dict[str, object] = {
            "v": findings.VERSION,
            "task": "lint:demo",
            "subject": printed(subject),
            "message": printed(message),
            "detail": lines,
        }
        self.assertEqual(record, expected)


if __name__ == "__main__":
    unittest.main()
