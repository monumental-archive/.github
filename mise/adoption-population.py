#!/usr/bin/env python3
"""The roster is the population, and membership derives the obligations.

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

So the population is the roster, reconciled against the listing by
NAME in both directions (#266), and the obligation set is a function
of what a repository's declared membership makes applicable:

  ci, audit               every roster member, always. These ask no
                          track question — they are hygiene a
                          repository owes whatever it publishes, and
                          `ci` is owed by one that publishes nothing.
                          Being listed at all is what puts a
                          repository under them (stele#181).

  scorecard               public members only. Scorecard scores a
                          public repository and publishes to a public
                          dashboard; on a private one it is not
                          refused, it is impossible.

  dependency-review       public members only. The action reads the
                          Dependency Graph API, which on a private
                          repository requires GitHub Advanced
                          Security. Measured 2026-08-21: the org is on
                          the `team` plan and the private member
                          reports `code_security: disabled`.

  source-attest           members bearing the SOURCE track. This one
                          IS track evidence, so an exclusion drops it
                          by derivation and never by a row.

  release, publish        members carrying `v*` tags. Kept as a
                          CONSEQUENCE, deliberately: a tag is an act,
                          not a claim, so a repository that has
                          released owes the release machinery whatever
                          tracks it declares. This can only make the
                          audit louder, never quieter (#505, #354).

The `tracks` key is read the way stele reads it, and the distinction is
load-bearing: ABSENT means every track, PRESENT AND EMPTY means an
exclusion. A decoder that collapsed the two would turn "bears all
evidence" into "bears none" on a missing key — absent must never
become zero (stele's `internal/jsonx`), so this refuses rather than
defaults, and names the field when it does.

Two subcommands, because the network sits between them: `population`
reconciles and prints the roster's members, `obligations` prints what
each owes. Both are pure functions of their input, which is what makes
the table tests in test_adoption_population.py able to reach shapes the
live org does not contain.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import NamedTuple, TextIO

# stele's track vocabulary, `level.Track.Key()` — lowercase, exact.
# A name outside it is refused rather than ignored, so a typo cannot
# quietly narrow a population.
TRACKS = ("build", "source", "dependency")

# artifact -> acceptable workflow filenames. The canon's self-* variants
# and gate.yml satisfy the same obligations under alternate names.
CI = ("ci.yml", "gate.yml")
AUDIT = ("audit.yml",)
SCORECARD = ("scorecard.yml",)
SOURCE_ATTEST = ("source-attest.yml",)
DEPENDENCY_REVIEW = ("dependency-review.yml", "self-dependency-review.yml")
RELEASE = ("release.yml", "self-release.yml")
PUBLISH = ("publish.yml", "self-publish.yml")


class RefusalError(Exception):
    """A roster or listing this tool will not derive a population from."""


class Member(NamedTuple):
    """One repository's declared membership.

    `bears_all` is what an ABSENT `tracks` key means, and it is not the
    same statement as a `tracks` list that happens to hold every name:
    the first is the default, the second is a narrowing that named
    everything. Only the first survives a new track being added.
    """

    bears_all: bool
    tracks: frozenset[str]


def _read_tracks(entry: dict, where: str) -> Member:
    """Read one entry's `tracks`, refusing anything it cannot parse.

    Returns:
        The entry's declared membership.

    Raises:
        RefusalError: the key is malformed, names an unknown track, or
            narrows the default without a reason.

    """
    # ABSENT and PRESENT-AND-EMPTY are different statements. `in`
    # rather than .get(), so a literal null is caught too.
    if "tracks" not in entry or entry["tracks"] is None:
        return Member(bears_all=True, tracks=frozenset())

    tracks = entry["tracks"]
    if not isinstance(tracks, list) or not all(isinstance(t, str) for t in tracks):
        msg = f"{where}.tracks is not a list of strings"
        raise RefusalError(msg)
    unknown = [t for t in tracks if t not in TRACKS]
    if unknown:
        msg = (
            f"{where}.tracks names {unknown[0]!r}, which is no track this"
            f" org judges ({', '.join(TRACKS)})"
        )
        raise RefusalError(msg)
    # stele's rule, and the reason an exclusion cannot be silent: a
    # narrowing nobody explained is not a narrowing anyone approved.
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        msg = (
            f"{where}.reason is absent or empty — it bears evidence on fewer"
            " tracks than the default and must say why"
        )
        raise RefusalError(msg)
    return Member(bears_all=False, tracks=frozenset(tracks))


def _entries(path: str) -> list:
    """Pull `population.repositories` out of the roster document.

    Returns:
        The roster's raw entry list.

    Raises:
        RefusalError: the document cannot be read, or holds no roster.

    """
    try:
        with Path(path).open("rb") as handle:
            doc = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"{path}: cannot be read as JSON — {exc}"
        raise RefusalError(msg) from exc

    population = doc.get("population") if isinstance(doc, dict) else None
    if not isinstance(population, dict):
        msg = f"{path}: population is absent or not an object"
        raise RefusalError(msg)
    entries = population.get("repositories")
    if not isinstance(entries, list):
        msg = f"{path}: population.repositories is absent or not a list"
        raise RefusalError(msg)
    if not entries:
        msg = (
            f"{path}: population.repositories is empty — an empty roster is"
            " no population, and would pass every walk by covering nothing"
        )
        raise RefusalError(msg)
    return entries


def load_roster(path: str) -> dict[str, Member]:
    """Parse the roster into declared memberships, keyed by repository.

    Returns:
        Every declared repository and the membership it declares.

    Raises:
        RefusalError: the roster is malformed. Nothing is defaulted; the
            offending field is always named.

    """
    roster: dict[str, Member] = {}
    for index, entry in enumerate(_entries(path)):
        where = f"population.repositories[{index}]"
        if not isinstance(entry, dict):
            msg = f"{where} is not an object"
            raise RefusalError(msg)
        repo = entry.get("repo")
        if not isinstance(repo, str) or not repo:
            msg = f"{where}.repo is absent or not a non-empty string"
            raise RefusalError(msg)
        if repo in roster:
            msg = f"{where}.repo names {repo!r} twice"
            raise RefusalError(msg)
        roster[repo] = _read_tracks(entry, where)
    return roster


def load_listing(stream: TextIO) -> dict[str, bool]:
    """Parse the forge listing, applying the default predicate.

    Archived repositories and forks are out; everything else is in.

    Returns:
        Every admitted repository, mapped to whether it is private.

    Raises:
        RefusalError: the listing is malformed, or empty — a walk over
            no repositories passes by covering nothing.

    """
    try:
        doc = json.load(stream)
    except json.JSONDecodeError as exc:
        msg = f"org listing is not JSON — {exc}"
        raise RefusalError(msg) from exc
    if not isinstance(doc, list):
        msg = "org listing is not a JSON array"
        raise RefusalError(msg)
    if not doc:
        msg = (
            "org listing is empty — a walk over no repositories passes by"
            " covering nothing, which is the blind-read shape (#290 f7)"
        )
        raise RefusalError(msg)

    listing: dict[str, bool] = {}
    for item in doc:
        if not isinstance(item, dict):
            msg = "org listing contains a non-object entry"
            raise RefusalError(msg)
        name = item.get("name")
        if not isinstance(name, str) or not name:
            msg = "org listing contains an entry with no name"
            raise RefusalError(msg)
        if item.get("archived") or item.get("fork"):
            continue
        # Visibility decides what is structurally possible, so an absent
        # `private` is refused rather than assumed either way.
        private = item.get("private")
        if not isinstance(private, bool):
            msg = f"org listing entry {name!r} has no boolean 'private'"
            raise RefusalError(msg)
        listing[name] = private
    return listing


def reconcile(
    roster: dict[str, Member],
    listing: dict[str, bool],
) -> tuple[list[str], list[str]]:
    """Compare roster and listing by NAME, in both directions.

    A count cannot say which repository went missing; this does.

    Returns:
        The repositories the listing shows and the roster does not, and
        the repositories the roster declares and the listing does not.

    """
    unlisted = sorted(set(listing) - set(roster))
    absent = sorted(set(roster) - set(listing))
    return unlisted, absent


def obligations(
    member: Member,
    *,
    private: bool,
    tagged: bool,
) -> list[tuple[str, tuple[str, ...]]]:
    """Derive what one member owes from what its membership makes apply.

    Returns:
        Each owed artifact with the workflow filenames that satisfy it.

    """
    owed: list[tuple[str, tuple[str, ...]]] = [("ci", CI), ("audit", AUDIT)]
    if not private:
        owed.extend(
            (("scorecard", SCORECARD), ("dependency-review", DEPENDENCY_REVIEW)),
        )
    if member.bears_all or "source" in member.tracks:
        owed.append(("source-attest", SOURCE_ATTEST))
    if tagged:
        owed.extend((("release", RELEASE), ("publish", PUBLISH)))
    return owed


def _report_population(
    roster: dict[str, Member],
    listing: dict[str, bool],
) -> None:
    """Print the reconciliation, then the members the listing confirms."""
    unlisted, absent = reconcile(roster, listing)
    for repo in unlisted:
        print(f"UNLISTED\t{repo}")
    for repo in absent:
        print(f"ABSENT\t{repo}")
    for repo in sorted(roster):
        # Only members the listing confirms are walked; one the listing
        # does not show is reported above, never judged.
        if repo in listing:
            print(f"POP\t{repo}")


def _report_obligations(
    roster: dict[str, Member],
    listing: dict[str, bool],
    tagged: set[str],
) -> None:
    """Print every derived obligation, one per line."""
    for repo in sorted(roster):
        if repo not in listing:
            continue
        for artifact, names in obligations(
            roster[repo],
            private=listing[repo],
            tagged=repo in tagged,
        ):
            print(f"OWES\t{repo}\t{artifact}\t{'|'.join(names)}")


def main(argv: list[str] | None = None) -> int:
    """Run one subcommand over the roster and the listing on stdin.

    Returns:
        0 on success, 2 when the roster or listing is refused.

    """
    parser = argparse.ArgumentParser(description="Derive the adoption population.")
    parser.add_argument("mode", choices=("population", "obligations"))
    parser.add_argument("--roster", required=True)
    parser.add_argument(
        "--tagged",
        default="",
        help="whitespace-separated repositories carrying v* tags",
    )
    args = parser.parse_args(argv)

    try:
        roster = load_roster(args.roster)
        listing = load_listing(sys.stdin)
    except RefusalError as exc:
        print(f"adoption-population: {exc}", file=sys.stderr)
        return 2

    if args.mode == "population":
        _report_population(roster, listing)
    else:
        _report_obligations(roster, listing, set(args.tagged.split()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
