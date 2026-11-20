#!/usr/bin/env python3
"""Run `mise/squash_message.py` against a stub `mise` (#1386).

The gate's squash-message step is now one call to the helper. These rows
run it as the step does, with a stub `mise` first on PATH that keeps a
copy of the message `commits:check` was handed and answers with the
row's verdict. Every refusal row asserts the step's message.

stdlib `unittest`, matching the other tests here.
"""

from __future__ import annotations

import os
import pathlib
import shutil

# ruff: ignore[suspicious-subprocess-import]
import subprocess
import sys
import tempfile
import unittest

import hermetic
import squash_message

HELPER = pathlib.Path(__file__).resolve().parent / "squash_message.py"
STUB_MISE = """#!/usr/bin/env bash
printf '%s\\n' "$*" > "${STUB_DIR}/mise-args"
cp "${ORG_COMMIT_MSG_FILE}" "${STUB_DIR}/checked-message"
exit "${STUB_EXIT}"
"""
OWN_REF = (
    "::error::the PR title already ends in this pull request's own reference"
    " (#812), and the merge appends it again — the permanent subject would"
    " carry it twice — fix the TITLE: it carries the ISSUE reference only, the"
    " squash appends the PR's\n"
)
FAILS_CANON = (
    "::error::the PR title and body become the permanent squash subject (plus"
    " ' (#812)') and body, and they fail the commit canon — fix the TITLE or the"
    " BODY, not a commit\n"
)
PASSES = "squash-message: title and body mint a conforming commit\n"


class SquashMessage(unittest.TestCase):
    """Each row: a title and body, and the commit the merge would mint."""

    def setUp(self) -> None:
        """Give each row its own runner temp and stub `mise`."""
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        bindir = self.tmp / "bin"
        bindir.mkdir()
        stub = bindir / "mise"
        stub.write_text(STUB_MISE, encoding="utf-8")
        stub.chmod(0o755)
        self.bindir = bindir

    def step_env(self, title: str, body: str | None, verdict: int) -> dict[str, str]:
        """Build the environment the workflow step hands the helper.

        Returns:
            The environment, the stub `mise` leading PATH.

        """
        env = hermetic.env(
            PATH=f"{self.bindir}{os.pathsep}{os.environ['PATH']}",
            HOME=str(self.tmp),
            STUB_DIR=str(self.tmp),
            STUB_EXIT=str(verdict),
            RUNNER_TEMP=str(self.tmp),
            PR_TITLE=title,
            PR_NUMBER="812",
        )
        if body is not None:
            env["PR_BODY"] = body
        return env

    def squash(
        self, title: str, body: str | None, *, verdict: int = 0
    ) -> hermetic.Called:
        """Call the helper in-process on one pull request (#1697).

        Returns:
            The status and both streams.

        """
        return hermetic.call_main(
            lambda: squash_message.main([]), env=self.step_env(title, body, verdict)
        )

    def test_the_script_contract(self) -> None:
        """Started as a process, it reads the step's variables and exits 0."""
        # ruff: ignore[subprocess-without-shell-equals-true]
        done = subprocess.run(
            [sys.executable, str(HELPER)],
            env=self.step_env("fix: a thing (#700)", "", 0),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual((0, PASSES), (done.returncode, done.stdout), done.stderr)

    def checked(self) -> str:
        """Read the message `commits:check` was handed.

        Returns:
            Its bytes, decoded, with every carriage return kept.

        """
        # Bytes, not read_text: universal newlines would turn a CRLF the
        # helper failed to strip into the LF this test expects (#880).
        return (self.tmp / "checked-message").read_bytes().decode("utf-8")

    def test_title_ending_in_its_own_reference(self) -> None:
        """#800: the merge would append the PR's number a second time."""
        done = self.squash("fix(ci): hold the title (#812)", "Body.")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout, OWN_REF)
        self.assertFalse((self.tmp / "mise-args").exists())

    def test_title_ending_in_the_issue_reference_is_the_convention(self) -> None:
        """Only this PR's own number is refused, never the issue's."""
        done = self.squash("fix(ci): hold the title (#800)", "Closes #800")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, PASSES)
        self.assertEqual(
            self.checked(), "fix(ci): hold the title (#800) (#812)\n\nCloses #800\n"
        )
        self.assertEqual(
            (self.tmp / "mise-args").read_text(encoding="utf-8"),
            "run commits:check\n",
        )

    def test_body_with_carriage_returns(self) -> None:
        """#880: the API's CRLF body is measured without its CRs."""
        done = self.squash("fix: x", "First line.\r\n\r\nSecond.\r\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.checked(), "fix: x (#812)\n\nFirst line.\n\nSecond.\n\n")

    def test_empty_body_composes_the_subject_alone(self) -> None:
        """No body: the subject and nothing after it."""
        done = self.squash("fix: x", "")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.checked(), "fix: x (#812)\n")

    def test_absent_body_composes_the_subject_alone(self) -> None:
        """A pull request with no body at all reads as an empty one."""
        done = self.squash("fix: x", None)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.checked(), "fix: x (#812)\n")

    def test_whitespace_only_body_composes_the_subject_alone(self) -> None:
        """Blank lines and CRs are no body."""
        done = self.squash("fix: x", " \r\n\t\r\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.checked(), "fix: x (#812)\n")

    def test_body_line_beginning_with_committer(self) -> None:
        """#1529: GitHub splices its signature after such a line."""
        body = (
            "Then `HOME`, the git identity (the author and\r\n"
            "committer) and the row's own overrides.\r\n"
        )
        done = self.squash("fix: x", body)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(
            done.stdout,
            "::error::PR body line 2 begins with 'committer': \"committer) and"
            " the row's own overrides.\" — GitHub splices the queue squash"
            " commit's signature after that line, the commit is unverifiable"
            " and the queue drops the pull request (#1529) — reword the BODY so"
            " no line starts with 'committer'\n",
        )
        self.assertFalse((self.tmp / "mise-args").exists())

    def test_committer_as_a_bare_prefix(self) -> None:
        """#1529: no delimiter is needed; `committerx y` was spliced too."""
        done = self.squash("fix: x", "Plain line.\ncommitterx y")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("PR body line 2 begins with 'committer'", done.stdout)

    def test_committer_inside_a_line_is_not_a_header(self) -> None:
        """The measured verified shapes: mid-line, indented, capitalised."""
        body = "the committer) x\n    committer x\nCommitter x\nauthor x\n"
        done = self.squash("fix: x", body)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, PASSES)

    def test_failing_commit_check(self) -> None:
        """#411: the canon's refusal names what to fix, the title or the body."""
        done = self.squash("Fix: Capitalised", "Body.", verdict=1)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout, FAILS_CANON)
        self.assertEqual(self.checked(), "Fix: Capitalised (#812)\n\nBody.\n")


if __name__ == "__main__":
    unittest.main()
