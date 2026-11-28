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
"""

import io
import json
import sys

import pytest

from belt import adoption_population as ap
from tests import support

EVERY = ["build", "source", "dependency"]
# The canon's own repository name, as main() is handed it from
# security/identity.toml. Members here are named for their role, so
# the canon's is `canon`.
CANON = "canon"
# The task that runs the helper, whose name its findings carry (#1934).
TASK = "audit:adoption"
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


def run_main(
    doc: object, *argv: str, capfd: pytest.CaptureFixture[str], env: support.Env
) -> tuple[int, str, str]:
    """Run main() with `doc` on stdin.

    Returns:
        The exit code, stdout and stderr.

    """
    with pytest.MonkeyPatch.context() as patched:
        for name, value in (env(MISE_TASK_NAME=TASK)).items():
            patched.setenv(name, value)
        patched.setattr(
            sys, "stdin", io.StringIO(doc if isinstance(doc, str) else json.dumps(doc))
        )
        status = ap.main(list(argv) or [CANON])
    out, err = capfd.readouterr()
    return status, out, err


# What a declared membership makes applicable, and what it does not.
def names(m: dict[str, object]) -> list[str]:
    """Return just the artifact names a member owes.

    Returns:
        The owed artifact names, in derivation order.

    """
    return [name for name, _ in ap.obligations(ap.Member.of(m, "m"), CANON)]


def unmet(m: dict[str, object], present: set[str]) -> list[str]:
    """Name each owed artifact no present workflow satisfies.

    audit:adoption's own test: an artifact is met when any one of
    its names is a file in the member's .github/workflows.

    Returns:
        The unmet artifact names, in derivation order.

    """
    owed = ap.obligations(ap.Member.of(m, "m"), CANON)
    return [name for name, names in owed if not present & set(names)]


def test_public_full_member_owes_everything() -> None:
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
    assert names(member("app", tagged=True)) == expected


def test_every_track_owes_source_attest_and_an_exclusion_does_not() -> None:
    """Resolved tracks decide it: every track holds source, [] does not."""
    assert "source-attest" in names(member())
    assert "source-attest" not in names(member(tracks=[]))


def test_private_drops_only_the_impossible() -> None:
    """Privacy removes what cannot run, and nothing else."""
    owed = names(member(visibility="private"))
    assert "ci" in owed
    assert "records" in owed
    assert "scorecard" not in owed
    assert "dependency-review" not in owed


def test_exclusion_still_owes_the_hygiene_that_asks_no_track_question() -> None:
    """An exclusion narrows EVIDENCE, and evidence only (stele#181)."""
    expected = ["ci", *SUBJECTS, "scorecard", "dependency-review"]
    assert names(member("app", tracks=[])) == expected


def test_the_private_exclusion_owes_the_gate_and_the_subjects() -> None:
    """monumental-archive's live shape: four opt-out rows before, none after."""
    expected = ["ci", *SUBJECTS]
    private = member("app", tracks=[], visibility="private")
    assert names(private) == expected


def test_source_only_member_owes_source_attest() -> None:
    """A member bearing SOURCE owes the source chain's evidence."""
    assert "source-attest" in names(member(tracks=["source"]))


def test_build_only_member_does_not_owe_source_attest() -> None:
    """A member off the SOURCE track owes no source evidence, and no row."""
    assert "source-attest" not in names(member(tracks=["build"]))


def test_tags_add_the_release_machinery_whatever_the_tracks() -> None:
    """A tag is an act, not a claim: a released repo owes it (#510)."""
    owed = names(member(tracks=[], visibility="private", tagged=True))
    assert "release" in owed
    assert "publish" in owed


def test_untagged_owes_neither() -> None:
    """A repository that never released owes no release machinery."""
    owed = names(member())
    assert "release" not in owed
    assert "publish" not in owed


def test_alternate_names_are_offered() -> None:
    """The canon's self-* variants and gate.yml satisfy the same obligations."""
    owed = dict(ap.obligations(ap.Member.of(member(tagged=True), "m"), CANON))
    assert "gate.yml" in owed["ci"]
    assert "self-release.yml" in owed["release"]
    assert "self-dependency-review.yml" in owed["dependency-review"]
    assert "self-dependencies.yml" in owed["dependencies"]
    assert "self-deep-tests.yml" in owed["deep-tests"]
    assert "self-records.yml" in owed["records"]


def test_the_retired_stub_alone_names_the_five() -> None:
    """A member still on the single audit stub owes all five (#1858)."""
    app = member("app", tracks=[], visibility="private")
    assert unmet(app, {"ci.yml", "audit.yml"}) == SUBJECTS


def test_the_five_stubs_pass() -> None:
    """A member carrying the five subject stubs owes nothing more."""
    app = member("app", tracks=[], visibility="private")
    none: list[str] = []
    assert unmet(app, {"ci.yml", *STUBS}) == none


def test_a_set_missing_records_names_it() -> None:
    """Four of the five: the one missing is named, and only it."""
    app = member("app", tracks=[], visibility="private")
    present = {"ci.yml", *STUBS} - {"records.yml"}
    expected = ["records"]
    assert unmet(app, present) == expected


def test_the_canon_passes_on_its_three_self_callers() -> None:
    """The canon's identity owes three subjects, met by self-* (#1858)."""
    canon = member(CANON, tracks=[], visibility="private")
    present = {
        "gate.yml",
        "self-dependencies.yml",
        "self-deep-tests.yml",
        "self-records.yml",
    }
    none: list[str] = []
    assert unmet(canon, present) == none
    assert "public-api" not in names(canon)
    assert "own-audits" not in names(canon)


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


# The one mode, end to end, over the org's real shape.
def test_obligations_over_the_document(
    capfd: pytest.CaptureFixture[str], env: support.Env
) -> None:
    """The private exclusion owes two artifacts and no written row."""
    doc = document(
        member("canon", tagged=True),
        member("signer", tracks=["source"]),
        member("app", tracks=[], visibility="private"),
    )
    code, out, err = run_main(doc, capfd=capfd, env=env)
    assert (code, err) == (0, "")
    owed: dict[str, list[str]] = {}
    for line in out.splitlines():
        _, repo, artifact, _names = line.split("\t")
        owed.setdefault(repo, []).append(artifact)
    repos = ["app", "canon", "signer"]
    listed = list(owed)
    assert repos == listed
    app = ["ci", *SUBJECTS]
    assert owed["app"] == app
    assert "public-api" not in owed["canon"]
    assert "public-api" in owed["signer"]
    assert "release" in owed["canon"]
    assert "release" not in owed["signer"]
    assert "source-attest" in owed["signer"]
    assert "OWES\tcanon\tci\tci.yml|gate.yml" in out.splitlines()


@pytest.mark.parametrize(("name", "doc", "message"), REFUSALS)
def test_each_refusal_exits_two_and_names_its_field(
    name: object,
    doc: object,
    message: object,
    capfd: pytest.CaptureFixture[str],
    env: support.Env,
) -> None:
    """A refused document exits 2, says so, and prints no obligation."""
    code, out, err = run_main(doc, capfd=capfd, env=env)
    assert code == 2
    assert out == ""
    assert err.startswith(f"{TASK}: {message}"), err


def test_arguments_other_than_the_canon_are_refused(
    capfd: pytest.CaptureFixture[str], env: support.Env
) -> None:
    """The roster is no input any more: a caller passing one is told."""
    code, out, err = run_main(
        document(member()), "--roster", "x.json", capfd=capfd, env=env
    )
    assert (code, out) == (2, "")
    assert (
        f"{TASK}: takes the canon's repository name and nothing"
        " else, got ['--roster', 'x.json']\n"
    ) == err


def test_an_empty_canon_name_is_refused(
    capfd: pytest.CaptureFixture[str], env: support.Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An identity read that failed leaves an empty name: refused."""
    for name, value in (env(MISE_TASK_NAME=TASK)).items():
        monkeypatch.setenv(name, value)
    status = ap.main([""])
    out, err = capfd.readouterr()
    assert (status, out) == (2, "")
    assert (
        f"{TASK}: takes the canon's repository name and nothing else, got ['']\n" == err
    )
