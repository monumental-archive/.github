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

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "adoption_population",
    Path(__file__).with_name("adoption-population.py"),
)
ap = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ap)

EVERY = ["build", "source", "dependency"]


def member(
    repo: str = "canon",
    *,
    tracks: list[str] | None = None,
    visibility: str = "public",
    tagged: bool = False,
) -> dict:
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


def document(*members: dict, schema: object = 9) -> dict:
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
            code = ap.main(list(argv))
    finally:
        sys.stdin = saved
    return code, out.getvalue(), err.getvalue()


class TestObligations(unittest.TestCase):
    """What a declared membership makes applicable, and what it does not."""

    @staticmethod
    def names(m: dict) -> list[str]:
        """Return just the artifact names a member owes.

        Returns:
            The owed artifact names, in derivation order.

        """
        return [name for name, _ in ap.obligations(m)]

    def test_public_full_member_owes_everything(self) -> None:
        """A public, tagged member on every track owes the whole set."""
        self.assertEqual(
            self.names(member(tagged=True)),
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

    def test_every_track_owes_source_attest_and_an_exclusion_does_not(self) -> None:
        """Resolved tracks decide it: every track holds source, [] does not."""
        self.assertIn("source-attest", self.names(member()))
        self.assertNotIn("source-attest", self.names(member(tracks=[])))

    def test_private_drops_only_the_impossible(self) -> None:
        """Privacy removes what cannot run, and nothing else."""
        owed = self.names(member(visibility="private"))
        self.assertIn("ci", owed)
        self.assertIn("audit", owed)
        self.assertNotIn("scorecard", owed)
        self.assertNotIn("dependency-review", owed)

    def test_exclusion_still_owes_the_hygiene_that_asks_no_track_question(self) -> None:
        """An exclusion narrows EVIDENCE, and evidence only (stele#181)."""
        self.assertEqual(
            self.names(member(tracks=[])),
            ["ci", "audit", "scorecard", "dependency-review"],
        )

    def test_the_private_exclusion_owes_exactly_two(self) -> None:
        """monumental-archive's live shape: four opt-out rows before, none after."""
        self.assertEqual(
            self.names(member(tracks=[], visibility="private")), ["ci", "audit"]
        )

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
        owed = dict(ap.obligations(member(tagged=True)))
        self.assertIn("gate.yml", owed["ci"])
        self.assertIn("self-release.yml", owed["release"])
        self.assertIn("self-dependency-review.yml", owed["dependency-review"])


# Documents this reader refuses, each with the message it must print.
REFUSALS = (
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
        self.assertEqual(["app", "canon", "signer"], list(owed))
        self.assertEqual(owed["app"], ["ci", "audit"])
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

    def test_arguments_are_refused(self) -> None:
        """The roster is no input any more: a caller passing one is told."""
        code, out, err = run_main(document(member()), "--roster", "x.json")
        self.assertEqual((2, ""), (code, out))
        self.assertEqual(
            "adoption-population: takes no arguments, got ['--roster', 'x.json']\n",
            err,
        )


if __name__ == "__main__":
    sys.exit(not unittest.main(exit=False).result.wasSuccessful())
