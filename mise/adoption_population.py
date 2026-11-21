#!/usr/bin/env python3
"""Membership derives the obligations: the adoption audit's one table.

`audit:adoption` used to enumerate its population by asking the forge
and then demand the same five artifacts of everything it found. Two
things follow from that, and both are wrong.

A repository the roster declares as an EXCLUSION was invisible to it,
so the only way to stop the walk demanding evidence of a repository
that owes none was a written opt-out row per artifact. For
monumental-archive that is four rows — `audit`, `scorecard`,
`source-attest`, `dependency-review` — measured 2026-08-21 against the
live org. Four standing exception rows for a repository that owes
nothing is precisely the vocabulary violation #672 exists to prevent:
an exclusion produces NOTHING, while an exception is dated and loud.

And a fixed artifact list cannot distinguish "does not apply" from
"has not got". Those are different sentences and only one is debt.

WHO the population is, is stele's (#1379, stele#388): `stele derive
population` prints the members every assert walk enumerates, with
their resolved tracks, the forge's word for their visibility and
whether they carry a release tag, after reconciling the roster against
the listing by name. This file keeps no second reading of the roster —
no track vocabulary, no rule for an absent or empty `tracks`, no
listing — and reads that document on stdin. What it keeps is the one
thing that is the canon's: which house workflows a member owes.

  ci, dependencies,       every member, always. These ask no track
  deep-tests, records     question — they are hygiene a repository
                          owes whatever it publishes, and `ci` is owed
                          by one that publishes nothing. Being listed
                          at all is what puts a repository under them
                          (stele#181). The scheduled checks are one
                          stub per subject, each required (#1858);
                          the single `audit.yml` stub they replaced
                          satisfies none of them.

  public-api, own-audits  every member but the canon itself (the
                          repository named on the command line, read
                          from security/identity.toml). The canon
                          publishes no API with a registry baseline
                          and defines no audit task outside its belt,
                          so it has no subject for either (#1858).

  scorecard               members the forge does not list as private.
                          Scorecard scores a public repository and
                          publishes to a public dashboard; on a private
                          one it is not refused, it is impossible.

  dependency-review       the same members. The action reads the
                          Dependency Graph API, which on a private
                          repository requires GitHub Advanced
                          Security. Measured 2026-08-21: the org is on
                          the `team` plan and the private member
                          reports `code_security: disabled`.

  source-attest           members whose resolved tracks hold `source`.
                          This one IS track evidence, so an exclusion
                          drops it by derivation and never by a row.

  release, publish        members carrying a release tag. Kept as a
                          CONSEQUENCE, deliberately: a tag is an act,
                          not a claim, so a repository that has
                          released owes the release machinery whatever
                          tracks it declares. This can only make the
                          audit louder, never quieter (#505, #354).

A member the document does not say enough about is refused by name,
never defaulted: an absent `visibility` is not public and an absent
`releaseTags` is not untagged (stele prints neither as "could not
see"). The table tests in test_adoption_population.py reach shapes the
live org does not contain.
"""

import sys
from dataclasses import dataclass
from typing import Self, TextIO

import decoded

# The population document's epoch this reader was written against
# (stele docs/population.md). A different one is refused, never read
# as if nothing had changed.
SCHEMA = 9

# artifact -> acceptable workflow filenames. The canon's self-* variants
# and gate.yml satisfy the same obligations under alternate names.
CI = ("ci.yml", "gate.yml")
# The scheduled checks, one stub per subject (#1858). No list offers
# `audit.yml`: the stub they replaced satisfies nothing, so a member
# that has not swapped is named with every subject it owes.
DEPENDENCIES = ("dependencies.yml", "self-dependencies.yml")
DEEP_TESTS = ("deep-tests.yml", "self-deep-tests.yml")
PUBLIC_API = ("public-api.yml",)
RECORDS = ("records.yml", "self-records.yml")
OWN_AUDITS = ("own-audits.yml",)
SCORECARD = ("scorecard.yml",)
SOURCE_ATTEST = ("source-attest.yml",)
DEPENDENCY_REVIEW = ("dependency-review.yml", "self-dependency-review.yml")
RELEASE = ("release.yml", "self-release.yml")
PUBLISH = ("publish.yml", "self-publish.yml")


class RefusalError(Exception):
    """A population document this tool will not derive obligations from."""


@dataclass(frozen=True)
class Member:
    """The four fields of a member every obligation reads."""

    repo: str
    tracks: tuple[str, ...]  # resolved, as stele prints them
    visibility: str  # the forge's word: `public`, `private`, ...
    release_tags: bool  # whether it carries a release tag

    @classmethod
    def of(cls, item: object, where: str) -> Self:
        """Check one member carries every field an obligation reads.

        Returns:
            The member.

        Raises:
            RefusalError: a field is absent or of the wrong type, named.

        """
        if not decoded.is_object(item):
            msg = f"{where} is not an object"
            raise RefusalError(msg)
        repo = item.get("repo")
        if not isinstance(repo, str) or not repo:
            msg = f"{where}.repo is absent or not a non-empty string"
            raise RefusalError(msg)
        tracks = item.get("tracks")
        if not decoded.is_strings(tracks):
            msg = f"{where} ({repo}).tracks is not a list of strings"
            raise RefusalError(msg)
        # Visibility decides what is structurally possible, so an absent
        # one is refused rather than assumed either way.
        visibility = item.get("visibility")
        if not isinstance(visibility, str):
            msg = (
                f"{where} ({repo}).visibility is absent — the forge listing was"
                " not read, so whether scorecard can run there is unknown"
            )
            raise RefusalError(msg)
        tagged = item.get("releaseTags")
        if not isinstance(tagged, bool):
            msg = (
                f"{where} ({repo}).releaseTags is absent — the policy declares no"
                " tags section, so whether it has released is unknown"
            )
            raise RefusalError(msg)
        return cls(repo, tuple(tracks), visibility, tagged)


def load_members(stream: TextIO) -> list[Member]:
    """Read stele's population document and return its members.

    Returns:
        Every member, in the document's order.

    Raises:
        RefusalError: the document cannot be read, is another epoch, or
            a member lacks a field the obligations read.

    """
    try:
        doc = decoded.load_json(stream.read())
    except decoded.UndecodableError as exc:
        msg = f"population document is not JSON — {exc}"
        raise RefusalError(msg) from exc
    if not decoded.is_object(doc):
        msg = "population document is not a JSON object"
        raise RefusalError(msg)
    if doc.get("schema") != SCHEMA:
        msg = (
            f"population document schema is {doc.get('schema')!r}, this reader"
            f" reads {SCHEMA} — move the reader with the stele pin"
        )
        raise RefusalError(msg)
    members = doc.get("members")
    if not decoded.is_array(members):
        msg = "population document members is absent or not a list"
        raise RefusalError(msg)
    if not members:
        msg = (
            "population document has no members — a walk over nobody passes"
            " by covering nothing (#290 f7)"
        )
        raise RefusalError(msg)
    return [Member.of(item, f"members[{i}]") for i, item in enumerate(members)]


def by_repo(member: Member) -> str:
    """Key a member by its repository.

    Returns:
        The repository.

    """
    return member.repo


def obligations(member: Member, canon: str) -> list[tuple[str, tuple[str, ...]]]:
    """Derive what one member owes from what its membership makes apply.

    `canon` is the canon's own repository name: the one member that owes
    no `public-api` or `own-audits` stub, having no subject for either.

    Returns:
        Each owed artifact with the workflow filenames that satisfy it.

    """
    owed: list[tuple[str, tuple[str, ...]]] = [
        ("ci", CI),
        ("dependencies", DEPENDENCIES),
        ("deep-tests", DEEP_TESTS),
        ("records", RECORDS),
    ]
    if member.repo != canon:
        owed.extend((("public-api", PUBLIC_API), ("own-audits", OWN_AUDITS)))
    if member.visibility != "private":
        owed.extend(
            (("scorecard", SCORECARD), ("dependency-review", DEPENDENCY_REVIEW)),
        )
    if "source" in member.tracks:
        owed.append(("source-attest", SOURCE_ATTEST))
    if member.release_tags:
        owed.extend((("release", RELEASE), ("publish", PUBLISH)))
    return owed


def main(argv: list[str] | None = None) -> int:
    """Print every derived obligation, one per line, from stdin.

    The one argument is the canon's own repository name (#1858).

    Returns:
        0 on success, 2 when the arguments or the document are refused.

    """
    if argv is None or len(argv) != 1 or not argv[0]:
        print(
            "adoption-population: takes the canon's repository name and nothing"
            f" else, got {argv}",
            file=sys.stderr,
        )
        return 2
    canon = argv[0]
    try:
        members = load_members(sys.stdin)
    except RefusalError as exc:
        print(f"adoption-population: {exc}", file=sys.stderr)
        return 2
    for member in sorted(members, key=by_repo):
        for artifact, names in obligations(member, canon):
            print(f"OWES\t{member.repo}\t{artifact}\t{'|'.join(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
