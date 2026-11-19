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

stdlib and the belt's decoded.py only.
"""

import io
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

# The canon's one JSON loader and its readers, beside this tree in
# mise/ (#1676). A missing sibling is named, never a bare traceback.
# Appended, so this file's own directory and the standard library come
# first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))
try:
    import decoded
except ModuleNotFoundError as missing:
    if missing.name != "decoded":
        raise
    msg = "coverage_figures: mise/decoded.py did not arrive in the canon tree"
    raise SystemExit(msg) from None

LABEL = "release:coverage-floor"
FILE = "coverage-figures.txt"
# The runs that judge the tree that lands.
EVENTS = frozenset({"merge_group", "push"})
# A subcommand and the commit it answers for, then one leg's name or not.
ARGS = (2, 3)
# A leg a pgrx job keeps alone (#1692).
ONE_LEG = re.compile(r"pgrx-pg[0-9]+")
# What the forge answered, piped in by the caller.
STDIN = 0
# One leg line as coverage-measure.sh prints it.
LEG = re.compile(r"[a-z][a-z0-9-]*\s+[0-9]+(\.[0-9]+)?")


class RefusedError(Exception):
    """The figure cannot be used; the message says why."""


@dataclass(frozen=True)
class Choice:
    """The artifact to read, and the run that kept it."""

    artifact: int
    run: int


def name(sha: str, leg: str = "") -> str:
    """Name the gate's artifact for one commit, and one leg's if named.

    Returns:
        `coverage-figures-<sha>` for the gate job's figures, or
        `coverage-figures-<leg>-<sha>` for one pgrx job's (#1692).

    """
    return f"coverage-figures-{leg}-{sha}" if leg else f"coverage-figures-{sha}"


def ident(value: object) -> int | None:
    """Read a decoded id: an integer, never a boolean (`as_int`'s rule).

    Returns:
        The id, or None when the value is not one.

    """
    try:
        return decoded.as_int(value, "id")
    except decoded.UndecodableError:
        return None


def choose(listing: object, sha: str, leg: str = "") -> Choice:
    """Pick this commit's artifact from an artifacts listing.

    Returns:
        The newest unexpired artifact of the commit's name (and the leg's,
        when one is named) kept by a run of that commit.

    Raises:
        RefusedError: no such artifact, or only ones for another commit.

    """
    entries = listing.get("artifacts") if decoded.is_object(listing) else None
    if not decoded.is_array(entries):
        msg = "the artifacts listing is not one GitHub writes"
        raise RefusedError(msg)
    named: list[tuple[int, int, str]] = []
    for entry in entries:
        if not decoded.is_object(entry):
            continue
        kept = entry.get("workflow_run")
        if not decoded.is_object(kept):
            continue
        artifact, run, head = (
            ident(entry.get("id")),
            ident(kept.get("id")),
            kept.get("head_sha"),
        )
        if (
            entry.get("name") != name(sha, leg)
            or entry.get("expired") is not False
            or artifact is None
            or run is None
            or not isinstance(head, str)
        ):
            continue
        named.append((artifact, run, head))
    if not named:
        msg = f"no unexpired gate artifact {name(sha, leg)}"
        raise RefusedError(msg)
    ours = [(artifact, run) for artifact, run, head in named if head == sha]
    if not ours:
        others = ", ".join(sorted({head for _, _, head in named}))
        msg = f"the artifact {name(sha, leg)} was kept by a run of {others}, not {sha}"
        raise RefusedError(msg)
    artifact, run = max(ours)
    return Choice(artifact, run)


def judge(run: object, sha: str) -> str:
    """Hold the run that kept the artifact to this commit and a green end.

    Returns:
        The run's address, for the step's log.

    Raises:
        RefusedError: the run is for another commit or event, or did not
            complete successfully.

    """
    if not decoded.is_object(run):
        msg = "the run is not one GitHub writes"
        raise RefusedError(msg)
    number = run.get("id")
    head, event = run.get("head_sha"), run.get("event")
    status, conclusion = run.get("status"), run.get("conclusion")
    if head != sha:
        msg = f"run {number} is for {head}, not {sha}"
        raise RefusedError(msg)
    if event not in EVENTS:
        msg = f"run {number} is a {event} run, not merge_group or push"
        raise RefusedError(msg)
    if status != "completed" or conclusion != "success":
        msg = f"run {number} is {status}/{conclusion}, not completed/success"
        raise RefusedError(msg)
    url = run.get("html_url")
    return url if isinstance(url, str) else f"run {number}"


def figures(text: str, leg: str = "") -> str:
    """Check the figures file: one `<leg> <percent>` line or more.

    With a leg named, the file is one pgrx job's (#1692), and holds that
    leg's line and nothing else.

    Returns:
        The lines, newline-terminated.

    Raises:
        RefusedError: an empty file, a line that is not a leg, or not
            exactly the named leg.

    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        msg = f"{FILE} holds no leg"
        raise RefusedError(msg)
    for line in lines:
        if not LEG.fullmatch(line):
            msg = f"{FILE} holds a line that is not a leg: {line!r}"
            raise RefusedError(msg)
    held = [line.split()[0] for line in lines]
    if leg and held != [leg]:
        msg = f"{FILE} holds {' '.join(held)}, not exactly {leg}"
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


def parse(data: bytes) -> object:
    """Decode a JSON response through the canon's one loader.

    Returns:
        The document, to be narrowed by the decision that reads it.

    Raises:
        RefusedError: the response is not strict JSON; decoded.py says
            why.

    """
    try:
        return decoded.load_json(data)
    except decoded.UndecodableError as error:
        msg = f"the forge answered with something that is not JSON ({error})"
        raise RefusedError(msg) from None


def answer(command: str, sha: str, data: bytes, leg: str = "") -> str:
    """Answer one subcommand over what the forge returned.

    Returns:
        `choose`: `<artifact id> <run id>`; `judge`: the run's address;
        `figures`: the leg lines. A leg, when named, is one pgrx job's
        (#1692). A refusal raises RefusedError from the check that made
        it.

    """
    if command == "choose":
        choice = choose(parse(data), sha, leg)
        return f"{choice.artifact} {choice.run}\n"
    if command == "judge":
        return judge(parse(data), sha) + "\n"
    return figures(unpack(data), leg)


def main(argv: list[str]) -> int:
    """Answer one subcommand over stdin, or say why the figure is refused.

    Returns:
        0 with the answer on stdout, 1 when the caller must measure, 2
        on a usage error.

    """
    commands = ("choose", "judge", "figures")
    if (
        len(argv) not in ARGS
        or argv[0] not in commands
        or not re.fullmatch(r"[0-9a-f]{40}", argv[1])
        or (len(argv) == max(ARGS) and not ONE_LEG.fullmatch(argv[-1]))
    ):
        print(
            f"usage: coverage_figures.py {{{','.join(commands)}}} <40-hex sha>"
            " [pgrx-pg<major>]",
            file=sys.stderr,
        )
        return 2
    leg = argv[2] if len(argv) == max(ARGS) else ""
    try:
        with open(STDIN, "rb", closefd=False) as stdin:
            data = stdin.read()
        said = answer(argv[0], argv[1], data, leg)
    except RefusedError as refused:
        print(f"{LABEL}: not reading the gate's figures: {refused}", file=sys.stderr)
        return 1
    sys.stdout.write(said)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
