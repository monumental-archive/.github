#!/usr/bin/env python3
"""Table tests for the Best Practices ownership check (#1197).

The two fixtures are bestpractices.dev's own answers, fetched on
2026-11-15 from `https://www.bestpractices.dev/projects/<id>.json` and
reduced with jq to the fields `audit:badges` reads; no value in them was
typed by hand. 13998 is the entry monumental-archive-db wears, whose
`repo_url` names another owner; 14058 is the canon's own. A hand-written
fixture would only prove the check agrees with an idea of the issuer's
answer, which is how canon v1.24.0 burned.

The normalisation rows build on 14058's real URL, varying one thing at a
time, so each ignored difference and each kept one is planted alone.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import bestpractices_owner as owner
import decoded

HERE = Path(__file__).resolve().parent

DB = decoded.as_object(
    decoded.load_json((HERE / "testdata" / "bestpractices-13998.json").read_text()),
    "project 13998's entry",
)
CANON = decoded.as_object(
    decoded.load_json((HERE / "testdata" / "bestpractices-14058.json").read_text()),
    "project 14058's entry",
)

DB_WEARER = "https://github.com/monumental-archive/monumental-archive-db"
CANON_WEARER = "https://github.com/monumental-archive/.github"


def run(entry: object, wearer: str) -> tuple[int, str]:
    """Run the CLI on one entry.

    Returns:
        The exit status and what it printed.

    """
    out = io.StringIO()
    stdin = io.StringIO(json.dumps(entry))
    with mock.patch("sys.stdin", stdin), redirect_stdout(out):
        status = owner.main([wearer])
    return status, out.getvalue()


def entry(repo_url: str) -> dict[str, object]:
    """Return the canon's real entry with repo_url replaced.

    Returns:
        A copy of the fixture.

    """
    return {**CANON, "repo_url": repo_url}


class RealEntries(unittest.TestCase):
    """The two entries as the issuer serves them."""

    def test_13998_is_not_monumental_archive_dbs(self) -> None:
        """The defect as measured: red, naming both URLs."""
        status, said = run(DB, DB_WEARER)
        self.assertEqual(status, 1)
        self.assertEqual(
            said,
            "project 13998's repo_url is "
            "https://github.com/CarlAllenn/monumental-archive-db, not "
            "https://github.com/monumental-archive/monumental-archive-db, "
            "the repository wearing it\n",
        )

    def test_14058_is_the_canons(self) -> None:
        """The canon's own entry passes, silently."""
        self.assertEqual(run(CANON, CANON_WEARER), (0, ""))


class Normalisation(unittest.TestCase):
    """Case, one trailing slash and one trailing .git; nothing else."""

    def test_case_is_ignored(self) -> None:
        """GitHub owners are case-insensitive."""
        shouted = entry("https://github.com/Monumental-Archive/.GitHub")
        self.assertEqual(run(shouted, CANON_WEARER)[0], 0)

    def test_a_trailing_slash_is_ignored(self) -> None:
        """A slash on either side."""
        self.assertEqual(run(entry(CANON_WEARER + "/"), CANON_WEARER)[0], 0)
        self.assertEqual(run(CANON, CANON_WEARER + "/")[0], 0)

    def test_a_trailing_git_is_ignored(self) -> None:
        """The clone URL form."""
        self.assertEqual(run(entry(CANON_WEARER + ".git"), CANON_WEARER)[0], 0)
        self.assertEqual(run(entry(CANON_WEARER + ".git/"), CANON_WEARER)[0], 0)

    def test_an_old_owner_is_still_a_mismatch(self) -> None:
        """A redirect from a renamed owner is not followed."""
        moved = entry("https://github.com/CarlAllenn/.github")
        status, said = run(moved, CANON_WEARER)
        self.assertEqual(status, 1)
        self.assertIn("https://github.com/CarlAllenn/.github", said)
        self.assertIn(CANON_WEARER, said)

    def test_another_host_is_a_mismatch(self) -> None:
        """Only the three named differences fold."""
        elsewhere = entry("https://gitlab.com/monumental-archive/.github")
        self.assertEqual(run(elsewhere, CANON_WEARER)[0], 1)

    def test_a_missing_repo_url_is_red(self) -> None:
        """An entry naming no repository binds to none."""
        entry = {k: v for k, v in CANON.items() if k != "repo_url"}
        status, said = run(entry, CANON_WEARER)
        self.assertEqual(status, 1)
        self.assertEqual(
            said,
            "project 14058 names no repo_url, so nothing binds it to "
            "https://github.com/monumental-archive/.github\n",
        )

    def test_a_process_with_no_stdin_is_red_by_name(self) -> None:
        """No standard input is named, never a traceback (#1681)."""
        out = io.StringIO()
        with mock.patch("sys.stdin", None), redirect_stdout(out):
            status = owner.main([CANON_WEARER])
        self.assertEqual(status, 1)
        self.assertEqual(
            out.getvalue(),
            "the issuer's answer cannot be read: no standard input: the"
            " process was started without one\n",
        )

    def test_a_non_object_answer_is_red(self) -> None:
        """A list is not a project."""
        self.assertEqual(run([CANON], CANON_WEARER)[0], 1)


if __name__ == "__main__":
    unittest.main()
