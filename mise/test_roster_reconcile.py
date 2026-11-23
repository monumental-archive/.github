#!/usr/bin/env python3
"""Table tests for roster-reconcile.sh, the population guard's comparison.

#680: repo-baseline.sh and audit:org-pins each asserted the org's size
as a hand-written count beside the roster that already names every
member. Both now reconcile the token's listing against the roster BY
NAME through this one helper, so a row here covers both guards.

Every row asserts the exact message, not only the exit status (a red
scored on status alone can pass for the wrong reason), and no message
may tell the reader to edit a number.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import hermetic

CANON = Path(__file__).resolve().parent.parent
SCRIPT = CANON / "mise" / "roster-reconcile.sh"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")

ROSTER = ["alpha", "beta", "gamma"]
CALLER = "probe"
UNLISTED = (
    "probe: {}: in the org, not in the roster — add it to the roster"
    " or remove the repository"
)
UNSEEN = (
    "probe: {}: in the roster, not visible to this token — an unseen"
    " repository is unchecked, not clean"
)


def run(roster: list[str] | None, listing: str) -> tuple[int, str]:
    """Reconcile `listing` against a planted roster.

    Args:
        roster: repository names for the planted assert-policy.json, or
            None to plant no file at all.
        listing: the token's listing, newline-separated, on stdin.

    Returns:
        The helper's exit status and its stderr.

    """
    with TemporaryDirectory() as tmp:
        # A tracked roster, named from the tree's top as the caller names
        # slsa/assert-policy.json (#2013).
        tree = Path(tmp)
        env = hermetic.env(tree / ".home")
        subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
        path = "assert-policy.json"
        if roster is not None:
            body = {"population": {"repositories": [{"repo": r} for r in roster]}}
            (tree / path).write_text(json.dumps(body), encoding="utf-8")
            subprocess.run([GIT, "add", path], cwd=tree, env=env, check=True)
        done = subprocess.run(
            [BASH, str(SCRIPT), CALLER, path],
            cwd=tree,
            input=listing,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    return done.returncode, done.stderr


class RosterReconcile(unittest.TestCase):
    """Each row: listing in, exit status and exact stderr out."""

    def test_equal_sets_pass_silently(self) -> None:
        """The listing names exactly the roster, in any order."""
        status, err = run(ROSTER, "gamma\nalpha\nbeta\n")
        self.assertEqual((status, err), (0, ""))

    def test_extra_in_listing_is_named(self) -> None:
        """A repository the org holds and the roster omits is red by name."""
        status, err = run(ROSTER, "alpha\nbeta\ngamma\ndelta\n")
        self.assertEqual((status, err), (1, UNLISTED.format("delta") + "\n"))

    def test_missing_from_listing_is_named(self) -> None:
        """A roster member the token cannot see is red by name."""
        status, err = run(ROSTER, "alpha\ngamma\n")
        self.assertEqual((status, err), (1, UNSEEN.format("beta") + "\n"))

    def test_offsetting_errors_both_named(self) -> None:
        """One extra and one missing: a count would pass, names do not."""
        status, err = run(ROSTER, "alpha\nbeta\ndelta\n")
        want = UNLISTED.format("delta") + "\n" + UNSEEN.format("gamma") + "\n"
        self.assertEqual((status, err), (1, want))

    def test_empty_listing_names_every_member(self) -> None:
        """A blind token sees nothing; every member is unseen, by name."""
        status, err = run(ROSTER, "")
        want = "".join(UNSEEN.format(r) + "\n" for r in ROSTER)
        self.assertEqual((status, err), (1, want))

    def test_no_roster_refuses(self) -> None:
        """A tree with no roster refuses by name, never trusts the listing."""
        status, err = run(None, "alpha\n")
        self.assertEqual(status, 2)
        self.assertIn("probe: no roster at ", err)

    def test_no_message_asks_for_a_number(self) -> None:
        """No red tells the reader to edit a count."""
        _, err = run(ROSTER, "alpha\ndelta\n")
        for word in ("count", "number", "constant"):
            self.assertNotIn(word, err)


if __name__ == "__main__":
    unittest.main()
