#!/usr/bin/env python3
"""Table tests for the adoption population and its derivation (#693).

These are the REGRESSION net, not the evidence. The evidence for #693
is the live walk recorded on the pull request: before it,
monumental-archive — a declared EXCLUSION — needed four written opt-out
rows; after it, it needs none, and the repositories in the org but
absent from the roster scream by name.

What the table adds is the part a live walk on one organisation cannot
reach. The org contains exactly one private member, no archived
repository, no fork, one partial-track member and no malformed roster
at all — so the branches that matter most have no live exercise, and a
guard that skips when it should run looks exactly like success (#364).

The one that matters most is `tracks` ABSENT versus PRESENT-AND-EMPTY.
stele models the field as a POINTER for this reason: absent means every
track, empty means an exclusion, and a decoder that collapsed them
would silently convert "bears all evidence" into "bears none" on a
missing key. It is one character of difference in the roster and the
whole vocabulary hangs on it, so it is driven in both directions here.

Every refusal is checked for the FIELD NAME in its message, not merely
for a nonzero exit: a refusal nobody can act on sends the reader to the
wrong file.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "adoption_population",
    Path(__file__).with_name("adoption-population.py"),
)
ap = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ap)

# Roster shapes the canon's own tree does not contain, each paired with
# the fragment its refusal must name.
REFUSALS = (
    ("missing population", {"schema": 6}, "population is absent"),
    ("population not an object", {"population": []}, "population is absent"),
    (
        "repositories not a list",
        {"population": {"repositories": {}}},
        "population.repositories is absent or not a list",
    ),
    (
        "empty roster",
        {"population": {"repositories": []}},
        "empty roster is no population",
    ),
    (
        "entry not an object",
        {"population": {"repositories": ["a"]}},
        "population.repositories[0] is not an object",
    ),
    (
        "repo absent",
        {"population": {"repositories": [{"tracks": []}]}},
        "population.repositories[0].repo is absent",
    ),
    (
        "repo empty",
        {"population": {"repositories": [{"repo": ""}]}},
        "population.repositories[0].repo is absent",
    ),
    (
        "duplicate repo",
        {"population": {"repositories": [{"repo": "a"}, {"repo": "a"}]}},
        "names 'a' twice",
    ),
    (
        "tracks not a list",
        {"population": {"repositories": [{"repo": "a", "tracks": "s", "reason": "r"}]}},
        "tracks is not a list of strings",
    ),
    (
        "tracks holds a non-string",
        {"population": {"repositories": [{"repo": "a", "tracks": [1], "reason": "r"}]}},
        "tracks is not a list of strings",
    ),
    (
        "unknown track name",
        {
            "population": {
                "repositories": [{"repo": "a", "tracks": ["Source"], "reason": "r"}],
            },
        },
        "which is no track this org judges",
    ),
    (
        "narrowing with no reason",
        {"population": {"repositories": [{"repo": "a", "tracks": []}]}},
        "reason is absent or empty",
    ),
    (
        "narrowing with a blank reason",
        {"population": {"repositories": [{"repo": "a", "tracks": [], "reason": "  "}]}},
        "reason is absent or empty",
    ),
)

MAIN_ROSTER = {
    "population": {
        "repositories": [
            {"repo": "canon"},
            {"repo": "signer", "tracks": ["source"], "reason": "publishes nothing"},
            {"repo": "app", "tracks": [], "reason": "excluded, not excepted"},
            {"repo": "vanished"},
        ],
    },
}


def listing_of(*entries: tuple[str, bool]) -> list[dict]:
    """Build a forge listing from (name, private) pairs.

    Returns:
        A listing in the shape the forge serves.

    """
    return [
        {"name": name, "private": private, "archived": False, "fork": False}
        for name, private in entries
    ]


def write_roster(directory: str, doc: dict) -> str:
    """Write a roster document and return its path.

    Returns:
        The path the roster was written to.

    """
    path = Path(directory) / "roster.json"
    path.write_text(json.dumps(doc))
    return str(path)


def load(doc: dict) -> dict[str, ap.Member]:
    """Load a roster document through a temporary file.

    Returns:
        The parsed roster.

    """
    with tempfile.TemporaryDirectory() as tmp:
        return ap.load_roster(write_roster(tmp, doc))


class TestTracksAbsentVersusEmpty(unittest.TestCase):
    """The one character the whole vocabulary hangs on."""

    def test_absent_bears_every_track(self) -> None:
        """An absent `tracks` key is the default: every track."""
        roster = load({"population": {"repositories": [{"repo": "a"}]}})
        self.assertEqual(roster["a"], ap.Member(bears_all=True, tracks=frozenset()))

    def test_empty_is_an_exclusion_not_every_track(self) -> None:
        """A present but empty `tracks` list is an exclusion."""
        entry = {"repo": "a", "tracks": [], "reason": "w"}
        roster = load({"population": {"repositories": [entry]}})
        self.assertEqual(roster["a"], ap.Member(bears_all=False, tracks=frozenset()))

    def test_null_tracks_is_absent_not_empty(self) -> None:
        """A literal null must read as absent, never as an exclusion."""
        roster = load({"population": {"repositories": [{"repo": "a", "tracks": None}]}})
        self.assertEqual(roster["a"], ap.Member(bears_all=True, tracks=frozenset()))

    def test_absent_owes_source_attest_and_empty_does_not(self) -> None:
        """The distinction reaches the obligations, not just the parse."""
        every = ap.obligations(
            ap.Member(bears_all=True, tracks=frozenset()),
            private=False,
            tagged=False,
        )
        excluded = ap.obligations(
            ap.Member(bears_all=False, tracks=frozenset()),
            private=False,
            tagged=False,
        )
        self.assertIn("source-attest", [name for name, _ in every])
        self.assertNotIn("source-attest", [name for name, _ in excluded])


class TestRosterRefusals(unittest.TestCase):
    """Every refusal names its field, and only the intended shape trips it."""

    def test_each_refusal_names_its_field(self) -> None:
        """Each malformed roster is refused, naming the offending field."""
        for label, doc, expected in REFUSALS:
            with self.subTest(label), self.assertRaises(ap.RefusalError) as caught:
                load(doc)
            self.assertIn(expected, str(caught.exception))

    def test_the_accepting_direction(self) -> None:
        """The same shapes, made valid, are accepted — the guard is not a wall."""
        roster = load(
            {
                "population": {
                    "repositories": [
                        {"repo": "a"},
                        {"repo": "b", "tracks": ["source"], "reason": "signs only"},
                        {"repo": "c", "tracks": [], "reason": "excluded"},
                        {
                            "repo": "d",
                            "tracks": ["build", "source", "dependency"],
                            "reason": "all three, stated",
                        },
                    ],
                },
            },
        )
        self.assertEqual(
            roster,
            {
                "a": ap.Member(bears_all=True, tracks=frozenset()),
                "b": ap.Member(bears_all=False, tracks=frozenset({"source"})),
                "c": ap.Member(bears_all=False, tracks=frozenset()),
                "d": ap.Member(
                    bears_all=False,
                    tracks=frozenset({"build", "source", "dependency"}),
                ),
            },
        )

    def test_unreadable_roster_is_a_refusal(self) -> None:
        """A roster that cannot be opened is refused, not defaulted."""
        with self.assertRaises(ap.RefusalError) as caught:
            ap.load_roster("/nonexistent/roster.json")
        self.assertIn("cannot be read as JSON", str(caught.exception))


class TestListing(unittest.TestCase):
    """The default predicate, and the reads it refuses to guess at."""

    def test_default_predicate_drops_archived_and_forks(self) -> None:
        """Archived repositories and forks are out of the population."""
        doc = listing_of(("keep", False))
        doc.append({"name": "old", "private": False, "archived": True, "fork": False})
        doc.append({"name": "copy", "private": False, "archived": False, "fork": True})
        self.assertEqual(ap.load_listing(io.StringIO(json.dumps(doc))), {"keep": False})

    def test_private_flag_survives(self) -> None:
        """Visibility reaches the caller, because obligations depend on it."""
        doc = listing_of(("pub", False), ("priv", True))
        self.assertEqual(
            ap.load_listing(io.StringIO(json.dumps(doc))),
            {"pub": False, "priv": True},
        )

    def test_empty_listing_is_refused(self) -> None:
        """An empty listing is the blind-read shape, not a clean walk."""
        with self.assertRaises(ap.RefusalError) as caught:
            ap.load_listing(io.StringIO("[]"))
        self.assertIn("blind-read", str(caught.exception))

    def test_absent_private_is_refused_not_assumed(self) -> None:
        """Visibility is never guessed in either direction."""
        doc = [{"name": "a", "archived": False, "fork": False}]
        with self.assertRaises(ap.RefusalError) as caught:
            ap.load_listing(io.StringIO(json.dumps(doc)))
        self.assertIn("no boolean 'private'", str(caught.exception))

    def test_not_an_array_is_refused(self) -> None:
        """A listing that is not an array is refused."""
        with self.assertRaises(ap.RefusalError):
            ap.load_listing(io.StringIO('{"name": "a"}'))

    def test_unnamed_entry_is_refused(self) -> None:
        """An entry with no name cannot be reconciled, so it is refused."""
        with self.assertRaises(ap.RefusalError) as caught:
            ap.load_listing(io.StringIO('[{"private": false}]'))
        self.assertIn("no name", str(caught.exception))


class TestReconcile(unittest.TestCase):
    """Both directions, by name."""

    def test_both_directions_by_name(self) -> None:
        """An undeclared repository and a vanished one are both reported."""
        roster = {
            "a": ap.Member(bears_all=True, tracks=frozenset()),
            "gone": ap.Member(bears_all=True, tracks=frozenset()),
        }
        unlisted, absent = ap.reconcile(roster, {"a": False, "extra": False})
        self.assertEqual(unlisted, ["extra"])
        self.assertEqual(absent, ["gone"])

    def test_agreement_is_silent(self) -> None:
        """A roster that matches the listing produces nothing."""
        roster = {"a": ap.Member(bears_all=True, tracks=frozenset())}
        self.assertEqual(ap.reconcile(roster, {"a": False}), ([], []))

    def test_a_count_would_not_have_caught_this(self) -> None:
        """Same size, different names — why reconciliation is by name."""
        roster = {"a": ap.Member(bears_all=True, tracks=frozenset())}
        self.assertEqual(ap.reconcile(roster, {"b": False}), (["b"], ["a"]))


class TestObligations(unittest.TestCase):
    """What a declared membership makes applicable, and what it does not."""

    @staticmethod
    def names(member: ap.Member, *, private: bool, tagged: bool) -> list[str]:
        """Return just the artifact names a member owes.

        Returns:
            The owed artifact names, in derivation order.

        """
        owed = ap.obligations(member, private=private, tagged=tagged)
        return [name for name, _ in owed]

    def test_public_full_member_owes_everything(self) -> None:
        """A public, tagged member on every track owes the whole set."""
        self.assertEqual(
            self.names(
                ap.Member(bears_all=True, tracks=frozenset()),
                private=False,
                tagged=True,
            ),
            [
                "ci",
                "audit",
                "scorecard",
                "dependency-review",
                "source-attest",
                "release",
                "publish",
            ],
        )

    def test_private_drops_only_the_impossible(self) -> None:
        """Privacy removes what cannot run, and nothing else."""
        owed = self.names(
            ap.Member(bears_all=True, tracks=frozenset()),
            private=True,
            tagged=False,
        )
        self.assertIn("ci", owed)
        self.assertIn("audit", owed)
        self.assertNotIn("scorecard", owed)
        self.assertNotIn("dependency-review", owed)

    def test_exclusion_still_owes_the_hygiene_that_asks_no_track_question(self) -> None:
        """An exclusion narrows EVIDENCE, and evidence only (stele#181)."""
        owed = self.names(
            ap.Member(bears_all=False, tracks=frozenset()),
            private=False,
            tagged=False,
        )
        self.assertEqual(owed, ["ci", "audit", "scorecard", "dependency-review"])

    def test_the_private_exclusion_owes_exactly_two(self) -> None:
        """monumental-archive's live shape: four opt-out rows before, none after."""
        self.assertEqual(
            self.names(
                ap.Member(bears_all=False, tracks=frozenset()),
                private=True,
                tagged=False,
            ),
            ["ci", "audit"],
        )

    def test_source_only_member_owes_source_attest(self) -> None:
        """A member bearing SOURCE owes the source chain's evidence."""
        owed = self.names(
            ap.Member(bears_all=False, tracks=frozenset({"source"})),
            private=False,
            tagged=False,
        )
        self.assertIn("source-attest", owed)

    def test_build_only_member_does_not_owe_source_attest(self) -> None:
        """A member off the SOURCE track owes no source evidence, and no row."""
        owed = self.names(
            ap.Member(bears_all=False, tracks=frozenset({"build"})),
            private=False,
            tagged=False,
        )
        self.assertNotIn("source-attest", owed)

    def test_tags_add_the_release_machinery_whatever_the_tracks(self) -> None:
        """A tag is an act, not a claim: an excluded repo that released owes it."""
        owed = self.names(
            ap.Member(bears_all=False, tracks=frozenset()),
            private=True,
            tagged=True,
        )
        self.assertIn("release", owed)
        self.assertIn("publish", owed)

    def test_untagged_owes_neither(self) -> None:
        """A repository that never released owes no release machinery."""
        owed = self.names(
            ap.Member(bears_all=True, tracks=frozenset()),
            private=False,
            tagged=False,
        )
        self.assertNotIn("release", owed)
        self.assertNotIn("publish", owed)

    def test_alternate_names_are_offered(self) -> None:
        """The canon's self-* variants and gate.yml satisfy the same obligations."""
        owed = dict(
            ap.obligations(
                ap.Member(bears_all=True, tracks=frozenset()),
                private=False,
                tagged=True,
            ),
        )
        self.assertIn("gate.yml", owed["ci"])
        self.assertIn("self-release.yml", owed["release"])
        self.assertIn("self-dependency-review.yml", owed["dependency-review"])


class TestMain(unittest.TestCase):
    """The two subcommands, end to end, over the org's real shape."""

    LISTING = listing_of(
        ("canon", False),
        ("signer", False),
        ("app", True),
        ("newcomer", False),
    )

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        """Run main() with MAIN_ROSTER and LISTING wired up.

        Returns:
            The exit code, stdout and stderr.

        """
        out, err = io.StringIO(), io.StringIO()
        saved = sys.stdin
        with tempfile.TemporaryDirectory() as tmp:
            path = write_roster(tmp, MAIN_ROSTER)
            sys.stdin = io.StringIO(json.dumps(self.LISTING))
            try:
                with redirect_stdout(out), redirect_stderr(err):
                    code = ap.main([*argv, "--roster", path])
            finally:
                sys.stdin = saved
        return code, out.getvalue(), err.getvalue()

    def test_population_reports_both_reconciliation_directions(self) -> None:
        """An undeclared repository and a vanished one both surface by name."""
        code, out, _ = self.run_main("population")
        self.assertEqual(code, 0)
        self.assertIn("UNLISTED\tnewcomer", out)
        self.assertIn("ABSENT\tvanished", out)
        # A member the listing does not confirm is reported, never walked.
        self.assertNotIn("POP\tvanished", out)
        self.assertIn("POP\tcanon", out)
        self.assertIn("POP\tapp", out)

    def test_obligations_needs_no_row_for_the_exclusion(self) -> None:
        """The private exclusion owes two artifacts and no written row."""
        code, out, _ = self.run_main("obligations", "--tagged", "canon")
        self.assertEqual(code, 0)
        owed: dict[str, list[str]] = {}
        for line in out.splitlines():
            _, repo, artifact, _names = line.split("\t")
            owed.setdefault(repo, []).append(artifact)
        self.assertEqual(owed["app"], ["ci", "audit"])
        self.assertIn("release", owed["canon"])
        self.assertNotIn("release", owed["signer"])
        self.assertIn("source-attest", owed["signer"])
        self.assertNotIn("vanished", owed)

    def test_a_refused_roster_exits_two_and_says_so(self) -> None:
        """A refused roster exits 2 and names the tool that refused."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ap.main(["population", "--roster", "/nonexistent.json"])
        self.assertEqual(code, 2)
        self.assertIn("adoption-population:", err.getvalue())


if __name__ == "__main__":
    sys.exit(not unittest.main(exit=False).result.wasSuccessful())
