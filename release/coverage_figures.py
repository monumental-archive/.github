#!/usr/bin/env python3
"""Read the gate's coverage figures for the commit being released (#1622).

The release job used to measure coverage itself, minutes after the
merge queue's gate had measured the same commit with the same script:
three minutes on the canon, a whole instrumented build on a Rust
repository, and a Release PR naming the previous commit for that long.
The gate now keeps the `<leg> <percent>` lines coverage:check judged as
an artifact named for its commit, `coverage-figures-<sha>`, on the
merge-group and default-branch push runs.

    gh api .../artifacts?name=...  | coverage_figures.py choose <sha>
    gh api .../runs/<run>          | coverage_figures.py judge <sha>
    gh api .../artifacts/<id>/zip  | coverage_figures.py figures <sha>

`choose` prints `<artifact id> <run id>`, `judge` the run's address,
and `figures` the leg lines, for `coverage_floor.py write`. The forge
is read by the caller, derive-coverage-floor.sh, with its GH_TOKEN:
this file decides and starts nothing. The figure is taken only when everything
about it is this commit's: an unexpired artifact of that name, kept by
a run of THIS commit (`head_sha`), on a merge-group or push event (a
pull request's run judges a commit that never lands), that completed
successfully, holding one well-formed leg line or more. Anything else
is refused by name, exit 1, and the caller measures as it always did:
a slower release, never a floor derived from another commit's figure.
The ratchet law is not here; `coverage_floor.py` judges the figure
whichever way it arrived.

stdlib only.
"""

import io
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from typing import TypeIs

LABEL = "release:coverage-floor"
FILE = "coverage-figures.txt"
# The runs that judge the tree that lands.
EVENTS = frozenset({"merge_group", "push"})
# A subcommand and the commit it answers for.
ARGS = 2
# What the forge answered, piped in by the caller.
STDIN = 0
# One leg line as coverage-measure.sh prints it.
LEG = re.compile(r"[a-z][a-z-]*\s+[0-9]+(\.[0-9]+)?")


class RefusedError(Exception):
    """The figure cannot be used; the message says why."""


@dataclass(frozen=True)
class Choice:
    """The artifact to read, and the run that kept it."""

    artifact: int
    run: int


def name(sha: str) -> str:
    """Name the gate's artifact for one commit.

    Returns:
        `coverage-figures-<sha>`.

    """
    return f"coverage-figures-{sha}"


def field(record: object, key: str) -> object:
    """Read one key of a decoded JSON object.

    Returns:
        The value, or None when the record is not an object or lacks it.

    """
    if not isinstance(record, dict):
        return None
    value: object = record.get(key)
    return value


def number(value: object) -> TypeIs[int]:
    """Say whether a decoded value is an id: an integer, never a boolean.

    Returns:
        True for an int that is not a bool (JSON `true` decodes to one).

    """
    return isinstance(value, int) and not isinstance(value, bool)


def choose(listing: object, sha: str) -> Choice:
    """Pick this commit's artifact from an artifacts listing.

    Returns:
        The newest unexpired artifact of the commit's name kept by a run
        of that commit.

    Raises:
        RefusedError: no such artifact, or only ones for another commit.

    """
    entries = field(listing, "artifacts")
    if not isinstance(entries, list):
        msg = "the artifacts listing is not one GitHub writes"
        raise RefusedError(msg)
    named: list[tuple[int, int, str]] = []
    entry: object
    for entry in entries:
        ident, run = field(entry, "id"), field(field(entry, "workflow_run"), "id")
        head = field(field(entry, "workflow_run"), "head_sha")
        if (
            field(entry, "name") != name(sha)
            or field(entry, "expired") is not False
            or not number(ident)
            or not number(run)
            or not isinstance(head, str)
        ):
            continue
        named.append((ident, run, head))
    if not named:
        msg = f"no unexpired gate artifact {name(sha)}"
        raise RefusedError(msg)
    ours = [(ident, run) for ident, run, head in named if head == sha]
    if not ours:
        others = ", ".join(sorted({head for _, _, head in named}))
        msg = f"the artifact {name(sha)} was kept by a run of {others}, not {sha}"
        raise RefusedError(msg)
    ident, run = max(ours)
    return Choice(ident, run)


def judge(run: object, sha: str) -> str:
    """Hold the run that kept the artifact to this commit and a green end.

    Returns:
        The run's address, for the step's log.

    Raises:
        RefusedError: the run is for another commit or event, or did not
            complete successfully.

    """
    ident = field(run, "id")
    head, event = field(run, "head_sha"), field(run, "event")
    status, conclusion = field(run, "status"), field(run, "conclusion")
    if head != sha:
        msg = f"run {ident} is for {head}, not {sha}"
        raise RefusedError(msg)
    if event not in EVENTS:
        msg = f"run {ident} is a {event} run, not merge_group or push"
        raise RefusedError(msg)
    if status != "completed" or conclusion != "success":
        msg = f"run {ident} is {status}/{conclusion}, not completed/success"
        raise RefusedError(msg)
    url = field(run, "html_url")
    return url if isinstance(url, str) else f"run {ident}"


def figures(text: str) -> str:
    """Check the figures file: one `<leg> <percent>` line or more.

    Returns:
        The lines, newline-terminated.

    Raises:
        RefusedError: an empty file or a line that is not a leg.

    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        msg = f"{FILE} holds no leg"
        raise RefusedError(msg)
    for line in lines:
        if not LEG.fullmatch(line):
            msg = f"{FILE} holds a line that is not a leg: {line!r}"
            raise RefusedError(msg)
    return "".join(f"{line}\n" for line in lines)


def unpack(data: bytes) -> str:
    """Read the figures file out of the artifact's zip.

    Returns:
        Its text.

    Raises:
        RefusedError: not a zip, or no figures file in it.

    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return archive.read(FILE).decode("utf-8")
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as error:
        msg = f"the artifact holds no readable {FILE} ({error})"
        raise RefusedError(msg) from error


def decoded(data: bytes) -> object:
    """Decode a JSON response.

    Returns:
        The document, as an object to be checked.

    Raises:
        RefusedError: the response is not JSON.

    """
    try:
        document: object = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        msg = f"the forge answered with something that is not JSON ({error})"
        raise RefusedError(msg) from error
    return document


def answer(command: str, sha: str, data: bytes) -> str:
    """Answer one subcommand over what the forge returned.

    Returns:
        `choose`: `<artifact id> <run id>`; `judge`: the run's address;
        `figures`: the leg lines. A refusal raises RefusedError from the
        check that made it.

    """
    if command == "choose":
        choice = choose(decoded(data), sha)
        return f"{choice.artifact} {choice.run}\n"
    if command == "judge":
        return judge(decoded(data), sha) + "\n"
    return figures(unpack(data))


def main(argv: list[str]) -> int:
    """Answer one subcommand over stdin, or say why the figure is refused.

    Returns:
        0 with the answer on stdout, 1 when the caller must measure, 2
        on a usage error.

    """
    commands = ("choose", "judge", "figures")
    if (
        len(argv) != ARGS
        or argv[0] not in commands
        or not re.fullmatch(r"[0-9a-f]{40}", argv[1])
    ):
        print(
            f"usage: coverage_figures.py {{{','.join(commands)}}} <40-hex sha>",
            file=sys.stderr,
        )
        return 2
    try:
        with open(STDIN, "rb", closefd=False) as stdin:
            data = stdin.read()
        said = answer(argv[0], argv[1], data)
    except RefusedError as refused:
        print(f"{LABEL}: not reading the gate's figures: {refused}", file=sys.stderr)
        return 1
    sys.stdout.write(said)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
