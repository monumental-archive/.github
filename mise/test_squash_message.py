#!/usr/bin/env python3
"""Run `mise/squash_message.py` against a stub `mise` (#1386).

The gate's squash-message step is now one call to the helper. These rows
run it as the step does, with a stub `mise` first on PATH that keeps a
copy of the message `commits:check` was handed and answers with the
row's verdict. Every refusal row asserts the step's message.

The breaking-change footer rule (#1924) is held to its reader: one class
runs the belt's pinned `stele` over a scratch repository, one commit per
body, and asserts the rule refuses exactly the bodies whose rendered
"Breaking" entry carries text from after a blank line.

stdlib `unittest`, matching the other tests here.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import override

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
STELE = hermetic.optional_tool("stele")
GIT = hermetic.optional_tool("git")
# The release notes, the breaking changes lifted into their own group.
NOTES = ("derive", "notes", "-git-dir", ".", "-breaking-group", "Breaking")

# edtf#316's body as it was merged (a9035118): the attribution line
# after the footer became part of v2.0.0's "Breaking" entry.
ATTRIBUTION = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
EDTF_316_LEAD = """\
edtf#289 removed the `pg14` Cargo feature from `edtf-postgres` under a
`feat` title that said the change was not breaking. Cargo's semver
rules class a removed feature as a major change, and `audit:semver`
judges it so against crates.io 1.3.5. The release plan takes the
number from commit types, so the open Release PR proposes 1.4.0.

This commit declares the break so the plan cuts 2.0.0 and its notes
list it. The diff is one sentence in the README paragraph that already
records Postgres 14 leaving the matrix. The type is `feat` because
`docs` and `chore` release nothing.

"""
EDTF_316_FOOTERS = """\
Refs #315

BREAKING CHANGE: the `pg14` feature of `edtf-postgres` is removed;
1.3.5 is the last release that has it.
"""
EDTF_316 = f"{EDTF_316_LEAD}{EDTF_316_FOOTERS}\n{ATTRIBUTION}\n"
EDTF_316_FIXED = f"{EDTF_316_LEAD}{ATTRIBUTION}\n\n{EDTF_316_FOOTERS}"

# Every body the footer rule is held to, and whether it is refused.
# The last four are the bodies the issue probed stele 0.27.0 with.
BREAKING_ROWS: dict[str, tuple[str, bool]] = {
    "edtf#316 as it was": (EDTF_316, True),
    "edtf#316, attribution above the footer": (EDTF_316_FIXED, False),
    "hyphened token, then a paragraph": (
        "BREAKING-CHANGE: x is removed.\n\nA paragraph.",
        True,
    ),
    "footer after a footer line, then a paragraph": (
        "Refs #12\nBREAKING CHANGE: x is removed.\n\nA paragraph.",
        True,
    ),
    "a footer after the blank line": (
        "BREAKING CHANGE: x is removed.\n\nRefs #12",
        False,
    ),
    "a paragraph after another footer": ("Refs #12\n\nA paragraph.", False),
    "lower case is not the token": (
        "breaking change: x is removed.\n\nA paragraph.",
        False,
    ),
    "a two-line value": ("BREAKING CHANGE: x is removed;\nsecond line.", False),
    "trailing blank lines": ("BREAKING CHANGE: x is removed.\n\n\n", False),
    "probe A": (
        "BREAKING CHANGE: A is removed;\nsecond line.\n\nGenerated with Tool",
        True,
    ),
    "probe B": (
        "Refs #12\nBREAKING CHANGE: B is removed.\n\nGenerated with Tool",
        True,
    ),
    "probe C": (
        "Generated with Tool\n\nBREAKING CHANGE: C is removed.\nRefs #12",
        False,
    ),
    "probe D": (
        "BREAKING CHANGE: D is removed.\n\nCloses #9\n\ntrailing prose",
        False,
    ),
}


def refusal(number: int, line: str) -> str:
    """Build the step's refusal of a paragraph inside the break's footer.

    Returns:
        The workflow command the step prints.

    """
    return (
        f"::error::PR body line {number} follows a blank line inside the"
        f" BREAKING CHANGE footer: {line!r} — everything from a"
        " `BREAKING CHANGE:` footer to the next footer is the break's"
        " description in the release notes — move that text above the footer,"
        " so the footer and any footers after it end the body\n"
    )


class StepHarness(unittest.TestCase):
    """Run the helper as the step does, with a stub `mise`."""

    @override
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

    def checked(self) -> str:
        """Read the message `commits:check` was handed.

        Returns:
            Its bytes, decoded, with every carriage return kept.

        """
        # Bytes, not read_text: universal newlines would turn a CRLF the
        # helper failed to strip into the LF this test expects (#880).
        return (self.tmp / "checked-message").read_bytes().decode("utf-8")


class SquashMessage(StepHarness):
    """Each row: a title and body, and the commit the merge would mint."""

    def test_the_script_contract(self) -> None:
        """Started as a process, it reads the step's variables and exits 0."""
        done = subprocess.run(
            [sys.executable, str(HELPER)],
            env=self.step_env("fix: a thing (#700)", "", 0),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual((0, PASSES), (done.returncode, done.stdout), done.stderr)

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


class BreakingFooter(StepHarness):
    """#1924: the break's footer ends the body, as stele will read it."""

    def assert_refused(self, row: str, number: int, line: str) -> None:
        """Assert a row is refused, naming its line, before the canon."""
        done = self.squash("feat: x", BREAKING_ROWS[row][0])
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout, refusal(number, line))
        self.assertFalse((self.tmp / "mise-args").exists())

    def assert_passes(self, row: str) -> None:
        """Assert a row reaches the commit canon and passes."""
        body = BREAKING_ROWS[row][0]
        done = self.squash("feat: x", body)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, PASSES)
        self.assertEqual(self.checked(), f"feat: x (#812)\n\n{body}\n")

    def test_edtf_316_as_it_was_names_the_attribution(self) -> None:
        """#1924: the attribution line was published as part of the break."""
        self.assert_refused("edtf#316 as it was", 17, ATTRIBUTION)

    def test_edtf_316_with_the_attribution_above_the_footer(self) -> None:
        """#1924: the footers end the body, so the break is the footer alone."""
        self.assert_passes("edtf#316, attribution above the footer")

    def test_hyphened_token_then_a_paragraph(self) -> None:
        """#1924: BREAKING-CHANGE is the same footer (§16)."""
        self.assert_refused("hyphened token, then a paragraph", 3, "A paragraph.")

    def test_footer_after_a_footer_line_then_a_paragraph(self) -> None:
        """#1924: a footer not first in its paragraph is still read."""
        self.assert_refused(
            "footer after a footer line, then a paragraph", 4, "A paragraph."
        )

    def test_a_footer_after_the_blank_line(self) -> None:
        """#1924: the next footer ends the break's value."""
        self.assert_passes("a footer after the blank line")

    def test_a_paragraph_after_another_footer(self) -> None:
        """#1924: other footers' values are never rendered."""
        self.assert_passes("a paragraph after another footer")

    def test_lower_case_is_not_the_token(self) -> None:
        """#1924: the token is upper case only (§15)."""
        self.assert_passes("lower case is not the token")

    def test_a_two_line_value(self) -> None:
        """#1924: a value may span lines; only a blank line splits it."""
        self.assert_passes("a two-line value")

    def test_trailing_blank_lines(self) -> None:
        """#1924: blank lines that nothing follows are not a finding."""
        self.assert_passes("trailing blank lines")

    def test_breaking_reads_the_footer(self) -> None:
        """#1924: the footer's lines, to the next footer or the end."""
        spaced: list[str] = ["BREAKING CHANGE: a", "b", ""]
        none: list[str] = []
        hyphened: list[str] = ["BREAKING-CHANGE: a", "", "c"]
        self.assertEqual(
            squash_message.breaking("Refs #1\nBREAKING CHANGE: a\nb\n\nCloses #2"),
            spaced,
        )
        self.assertEqual(squash_message.breaking("Refs #1\n\nprose"), none)
        self.assertEqual(squash_message.breaking("BREAKING-CHANGE: a\n\nc"), hyphened)


def entry_after_blank(body: str, entry: list[str]) -> bool:
    """Say whether a rendered entry carries body text from after a blank line.

    stele drops the footer's blank lines when it renders, so the entry's
    lines are found in the body in order and the blank lines between
    them are counted.

    Returns:
        True when a blank body line separates two of the entry's lines.

    Raises:
        AssertionError: The entry is not in the body.

    """
    lines = body.split("\n")
    for start, line in enumerate(lines):
        if not line.endswith(entry[0]):
            continue
        at, blank = start + 1, False
        for want in entry[1:]:
            while at < len(lines) and not lines[at].strip():
                at, blank = at + 1, True
            if at == len(lines) or lines[at] != want:
                break
            at += 1
        else:
            return blank
    msg = f"the entry {entry!r} is not in the body"
    raise AssertionError(msg)


class TheReaderAgrees(unittest.TestCase):
    """#1924: the rule refuses exactly what stele would publish in the break."""

    @override
    def setUp(self) -> None:
        """Skip unless the belt's stele is installed; make the scratch repo."""
        if not STELE or not GIT:
            self.skipTest("stele is the belt's release engine and renders the notes")
        self.stele, self.git_tool = STELE, GIT
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.env = hermetic.env(self.tmp)
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "chore: start")
        self.git("tag", "v1.0.0")

    def git(self, *args: str) -> str:
        """Run git in the scratch repository.

        Returns:
            Its standard output.

        """
        return subprocess.run(
            [
                self.git_tool,
                *("-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"),
                *args,
            ],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout

    def breaking_entry(self, body: str) -> list[str]:
        """Commit one body as the squash would and render its notes.

        Returns:
            The "Breaking" entry's lines, empty when there is none.

        """
        message = self.tmp / "message"
        message.write_text(f"feat: a row\n\n{body}\n", encoding="utf-8")
        self.git(
            "commit", "-q", "--allow-empty", "--cleanup=verbatim", "-F", str(message)
        )
        done = subprocess.run(
            [self.stele, *NOTES],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        tags = self.git("tag", "--list", "v*").split()
        self.git("tag", f"v{len(tags) + 1}.0.0")
        section = done.stdout.split("### Breaking\n", 1)
        if len(section) == 1:
            return []
        entry = section[1].split("\n### ", 1)[0].strip("\n").split("\n")
        self.assertTrue(entry[0].startswith("- "), done.stdout)
        self.assertEqual(sum(line.startswith("- ") for line in entry), 1, done.stdout)
        return [entry[0].removeprefix("- "), *entry[1:]]

    def test_every_row(self) -> None:
        """Refused exactly when the rendered break carries a later paragraph."""
        for row, (body, refused) in BREAKING_ROWS.items():
            with self.subTest(row=row):
                entry = self.breaking_entry(body)
                published = bool(entry) and entry_after_blank(body, entry)
                try:
                    squash_message.breaking_footer(body)
                except squash_message.StepError:
                    found = True
                else:
                    found = False
                self.assertEqual((found, published), (refused, refused), entry)


if __name__ == "__main__":
    unittest.main()
