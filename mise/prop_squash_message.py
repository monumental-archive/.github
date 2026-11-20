#!/usr/bin/env python3
"""squash_message.py's composition, on titles and bodies nobody wrote down.

The properties of `test_squash_message.py`'s fixed rows (#1548), over
generated pull request titles, bodies and numbers:

- Round trip (T): the composed message reads back as its parts: the
  subject is the title plus ` (#N)`, and the body, CRs removed, is
  everything after the one blank line; a body of whitespace composes
  to the subject alone. A body with a line that begins `committer` is
  refused instead (#1529's guard), and only such a body (#1727: both
  properties were written before the guard, and a random seed drew
  `committer`).
- Invariance (V): CRLF and LF bodies compose to one message, or are
  refused alike; a title
  is refused exactly when it already ends in this pull request's own
  reference, and any other reference (the issue's) is accepted.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import string
import unittest

import squash_message as sm
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

TITLE = st.text(st.characters(blacklist_categories=("Cc", "Cs")), max_size=60)
BODY = st.text(max_size=200)
NUMBER = st.integers(1, 99999).map(str)


def guarded(body: str) -> bool:
    """Say whether #1529's guard refuses a body, CRs already removed.

    Returns:
        True when a line of the body begins `committer`.

    """
    return any(line.startswith("committer") for line in body.split("\n"))


class Compose(unittest.TestCase):
    """The message is its parts."""

    @given(TITLE, BODY, NUMBER)
    @example("fix: x", " \r\n\t", "12")
    @example("fix: x (#12)", "", "123")
    @example("", "committer", "1")
    def test_reads_back_as_its_parts(self, title: str, body: str, number: str) -> None:
        """Subject, blank line, body; the subject alone; or the guard's refusal."""
        if title.endswith(f"(#{number})"):
            return
        clean = body.replace("\r", "")
        if guarded(clean):
            with self.assertRaises(sm.StepError):
                sm.compose(title, body, number)
            return
        message = sm.compose(title, body, number)
        subject = f"{title} (#{number})"
        if not clean.strip(string.whitespace):
            self.assertEqual(message, subject + "\n")
        else:
            self.assertEqual(message, f"{subject}\n\n{clean}\n")

    @given(TITLE, BODY, NUMBER)
    @example("", "committer", "1")
    def test_crlf_is_lf(self, title: str, body: str, number: str) -> None:
        """The API's CRLF body composes, or is refused, as the LF one."""
        if title.endswith(f"(#{number})"):
            return
        lf = body.replace("\r", "")
        if guarded(lf):
            for spelt in (lf, lf.replace("\n", "\r\n")):
                with self.assertRaises(sm.StepError):
                    sm.compose(title, spelt, number)
            return
        self.assertEqual(
            sm.compose(title, lf.replace("\n", "\r\n"), number),
            sm.compose(title, lf, number),
        )

    @given(TITLE, NUMBER, NUMBER)
    @example("fix: x", "12", "123")
    def test_refuses_only_its_own_reference(
        self, title: str, own: str, other: str
    ) -> None:
        """Ending in its own ref is refused; ending in another is not."""
        with self.assertRaises(sm.StepError):
            sm.compose(f"{title} (#{own})", "", own)
        if other != own:
            sm.compose(f"{title} (#{other})", "", own)


if __name__ == "__main__":
    unittest.main()
