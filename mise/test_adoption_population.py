#!/usr/bin/env python3
"""Table tests for the adoption obligations (#693, #1379).

These are the REGRESSION net, not the evidence. The evidence for #693
is the live walk recorded on the pull request: before it,
monumental-archive — a declared EXCLUSION — needed four written opt-out
rows; after it, it needs none.

The population itself is stele's (`stele derive population`,
stele#388), and the rows that drove roster shapes through this file —
tracks absent versus empty, null tracks, the roster's refusals, the
listing's default predicate, reconciliation by name — moved with it
and live in stele's own suite (#1379 names each). What stays here is
the canon's half: which house workflows a member owes, read off the
document stele prints, and the refusals of a document this reader
cannot derive from.

Every refusal is checked for the FIELD NAME in its message, not merely
for a nonzero exit: a refusal nobody can act on sends the reader to the
wrong file.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

import adoption_population as ap

EVERY = ["build", "source", "dependency"]
# The canon's own repository name, as main() is handed it from
# security/identity.toml. Members here are named for their role, so
# the canon's is `canon`.
CANON = "canon"
# The five subject stubs every member but the canon owes (#1858).
SUBJECTS = ["dependencies", "deep-tests", "records", "public-api", "own-audits"]
STUBS = {f"{s}.yml" for s in SUBJECTS}


def member(
    repo: str = "canon",
    *,
    tracks: list[str] | None = None,
    visibility: str = "public",
    tagged: bool = False,
) -> dict[str, object]:
    """Build one member in the shape `stele derive population` prints.

    Returns:
        The member object: tracks resolved, as stele prints them.

    """
    return {
        "repo": repo,
        "tracks": EVERY if tracks is None else tracks,
        "tracksDeclared": tracks is not None,
        "exclusion": tracks == [],
        "visibility": visibility,
        "releaseTags": tagged,
    }


def document(*members: object, schema: object = 9) -> dict[str, object]:
    """Wrap members in the population document.

    Returns:
        The document, reconciliation empty.

    """
    return {
        "schema": schema,
        "owner": "example-org",
        "declared": True,
        "members": list(members),
        "reconciliation": {"undeclared": [], "unlisted": [], "unexercised": []},
    }


def run_main(doc: object, *argv: str) -> tuple[int, str, str]:
    """Run main() with `doc` on stdin.

    Returns:
        The exit code, stdout and stderr.

    """
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdin
    sys.stdin = io.StringIO(doc if isinstance(doc, str) else json.dumps(doc))
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = ap.main(list(argv) or [CANON])
    finally:
        sys.stdin = saved
    return code, out.getvalue(), err.getvalue()


class TestObligations(unittest.TestCase):
    """What a declared membership makes applicable, and what it does not."""

    @staticmethod
    def names(m: dict[str, object]) -> list[str]:
        """Return just the artifact names a member owes.

        Returns:
            The owed artifact names, in derivation order.

        """
        return [name for name, _ in ap.obligations(ap.Member.of(m, "m"), CANON)]

    @staticmethod
    def unmet(m: dict[str, object], present: set[str]) -> list[str]:
        """Name each owed artifact no present workflow satisfies.

        audit:adoption's own test: an artifact is met when any one of
        its names is a file in the member's .github/workflows.

        Returns:
            The unmet artifact names, in derivation order.

        """
        owed = ap.obligations(ap.Member.of(m, "m"), CANON)
        return [name for name, names in owed if not present & set(names)]

    def test_public_full_member_owes_everything(self) -> None:
        """A public, tagged member on every track owes the whole set."""
        expected = [
            "ci",
            *SUBJECTS,
            "scorecard",
            "dependency-review",
            "source-attest",
            "release",
            "publish",
        ]
        self.assertEqual(
            self.names(member("app", tagged=True)),
            expected,
        )

    def test_every_track_owes_source_attest_and_an_exclusion_does_not(self) -> None:
        """Resolved tracks decide it: every track holds source, [] does not."""
        self.assertIn("source-attest", self.names(member()))
        self.assertNotIn("source-attest", self.names(member(tracks=[])))

    def test_private_drops_only_the_impossible(self) -> None:
        """Privacy removes what cannot run, and nothing else."""
        owed = self.names(member(visibility="private"))
        self.assertIn("ci", owed)
        self.assertIn("records", owed)
        self.assertNotIn("scorecard", owed)
        self.assertNotIn("dependency-review", owed)

    def test_exclusion_still_owes_the_hygiene_that_asks_no_track_question(self) -> None:
        """An exclusion narrows EVIDENCE, and evidence only (stele#181)."""
        expected = ["ci", *SUBJECTS, "scorecard", "dependency-review"]
        self.assertEqual(
            self.names(member("app", tracks=[])),
            expected,
        )

    def test_the_private_exclusion_owes_the_gate_and_the_subjects(self) -> None:
        """monumental-archive's live shape: four opt-out rows before, none after."""
        expected = ["ci", *SUBJECTS]
        private = member("app", tracks=[], visibility="private")
        self.assertEqual(self.names(private), expected)

    def test_source_only_member_owes_source_attest(self) -> None:
        """A member bearing SOURCE owes the source chain's evidence."""
        self.assertIn("source-attest", self.names(member(tracks=["source"])))

    def test_build_only_member_does_not_owe_source_attest(self) -> None:
        """A member off the SOURCE track owes no source evidence, and no row."""
        self.assertNotIn("source-attest", self.names(member(tracks=["build"])))

    def test_tags_add_the_release_machinery_whatever_the_tracks(self) -> None:
        """A tag is an act, not a claim: a released repo owes it (#510)."""
        owed = self.names(member(tracks=[], visibility="private", tagged=True))
        self.assertIn("release", owed)
        self.assertIn("publish", owed)

    def test_untagged_owes_neither(self) -> None:
        """A repository that never released owes no release machinery."""
        owed = self.names(member())
        self.assertNotIn("release", owed)
        self.assertNotIn("publish", owed)

    def test_alternate_names_are_offered(self) -> None:
        """The canon's self-* variants and gate.yml satisfy the same obligations."""
        owed = dict(ap.obligations(ap.Member.of(member(tagged=True), "m"), CANON))
        self.assertIn("gate.yml", owed["ci"])
        self.assertIn("self-release.yml", owed["release"])
        self.assertIn("self-dependency-review.yml", owed["dependency-review"])
        self.assertIn("self-dependencies.yml", owed["dependencies"])
        self.assertIn("self-deep-tests.yml", owed["deep-tests"])
        self.assertIn("self-records.yml", owed["records"])

    def test_the_retired_stub_alone_names_the_five(self) -> None:
        """A member still on the single audit stub owes all five (#1858)."""
        app = member("app", tracks=[], visibility="private")
        unmet = self.unmet(app, {"ci.yml", "audit.yml"})
        self.assertEqual(unmet, SUBJECTS)

    def test_the_five_stubs_pass(self) -> None:
        """A member carrying the five subject stubs owes nothing more."""
        app = member("app", tracks=[], visibility="private")
        none: list[str] = []
        self.assertEqual(self.unmet(app, {"ci.yml", *STUBS}), none)

    def test_a_set_missing_records_names_it(self) -> None:
        """Four of the five: the one missing is named, and only it."""
        app = member("app", tracks=[], visibility="private")
        present = {"ci.yml", *STUBS} - {"records.yml"}
        expected = ["records"]
        self.assertEqual(self.unmet(app, present), expected)

    def test_the_canon_passes_on_its_three_self_callers(self) -> None:
        """The canon's identity owes three subjects, met by self-* (#1858)."""
        canon = member(CANON, tracks=[], visibility="private")
        present = {
            "gate.yml",
            "self-dependencies.yml",
            "self-deep-tests.yml",
            "self-records.yml",
        }
        none: list[str] = []
        self.assertEqual(self.unmet(canon, present), none)
        self.assertNotIn("public-api", self.names(canon))
        self.assertNotIn("own-audits", self.names(canon))


# Documents this reader refuses, each with the message it must print.
REFUSALS: tuple[tuple[str, object, str], ...] = (
    ("not JSON", "{", "population document is not JSON"),
    ("not an object", [], "population document is not a JSON object"),
    (
        "another epoch",
        document(member(), schema=8),
        (
            "population document schema is 8, this reader reads 9 — move the"
            " reader with the stele pin"
        ),
    ),
    (
        "no schema",
        {"members": [member()]},
        "population document schema is None, this reader reads 9",
    ),
    (
        "members not a list",
        {"schema": 9, "members": {}},
        "population document members is absent or not a list",
    ),
    (
        "no members",
        document(),
        (
            "population document has no members — a walk over nobody passes by"
            " covering nothing (#290 f7)"
        ),
    ),
    ("member not an object", document("a"), "members[0] is not an object"),
    (
        "repo absent",
        document({k: v for k, v in member().items() if k != "repo"}),
        "members[0].repo is absent or not a non-empty string",
    ),
    (
        "tracks not a list of strings",
        document(member(), {**member("b"), "tracks": [1]}),
        "members[1] (b).tracks is not a list of strings",
    ),
    (
        "visibility absent",
        document({k: v for k, v in member().items() if k != "visibility"}),
        (
            "members[0] (canon).visibility is absent — the forge listing was not"
            " read, so whether scorecard can run there is unknown"
        ),
    ),
    (
        "releaseTags absent",
        document({k: v for k, v in member().items() if k != "releaseTags"}),
        (
            "members[0] (canon).releaseTags is absent — the policy declares no"
            " tags section, so whether it has released is unknown"
        ),
    ),
)


class TestMain(unittest.TestCase):
    """The one mode, end to end, over the org's real shape."""

    def test_obligations_over_the_document(self) -> None:
        """The private exclusion owes two artifacts and no written row."""
        doc = document(
            member("canon", tagged=True),
            member("signer", tracks=["source"]),
            member("app", tracks=[], visibility="private"),
        )
        code, out, err = run_main(doc)
        self.assertEqual((code, err), (0, ""))
        owed: dict[str, list[str]] = {}
        for line in out.splitlines():
            _, repo, artifact, _names = line.split("\t")
            owed.setdefault(repo, []).append(artifact)
        repos = ["app", "canon", "signer"]
        listed = list(owed)
        self.assertEqual(repos, listed)
        app = ["ci", *SUBJECTS]
        self.assertEqual(owed["app"], app)
        self.assertNotIn("public-api", owed["canon"])
        self.assertIn("public-api", owed["signer"])
        self.assertIn("release", owed["canon"])
        self.assertNotIn("release", owed["signer"])
        self.assertIn("source-attest", owed["signer"])
        self.assertIn("OWES\tcanon\tci\tci.yml|gate.yml", out.splitlines())

    def test_each_refusal_exits_two_and_names_its_field(self) -> None:
        """A refused document exits 2, says so, and prints no obligation."""
        for name, doc, message in REFUSALS:
            with self.subTest(name):
                code, out, err = run_main(doc)
                self.assertEqual(2, code)
                self.assertEqual("", out)
                self.assertTrue(err.startswith(f"adoption-population: {message}"), err)

    def test_arguments_other_than_the_canon_are_refused(self) -> None:
        """The roster is no input any more: a caller passing one is told."""
        code, out, err = run_main(document(member()), "--roster", "x.json")
        self.assertEqual((2, ""), (code, out))
        self.assertEqual(
            "adoption-population: takes the canon's repository name and nothing"
            " else, got ['--roster', 'x.json']\n",
            err,
        )

    def test_an_empty_canon_name_is_refused(self) -> None:
        """An identity read that failed leaves an empty name: refused."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ap.main([""])
        self.assertEqual((2, ""), (code, out.getvalue()))
        self.assertEqual(
            "adoption-population: takes the canon's repository name and nothing"
            " else, got ['']\n",
            err.getvalue(),
        )


if __name__ == "__main__":
    sys.exit(not unittest.main(exit=False).result.wasSuccessful())
