#!/usr/bin/env python3
"""Drop zizmor's findings on the org signer's pins (#1152, #1340).

`audit:actions` runs zizmor online, and two of its audits report the
org signer's pins for facts that are true and are not defects:

- `stale-action-refs` (since zizmor 1.32) reports every `uses:` pinned
  to a commit that no tag points at. The signer is never tagged, by
  design: the SHA is its whole identity (`mise/pinact.yaml`).
- `ref-version-mismatch` reports a `# main` comment whenever the
  signer's `main` has moved past the pinned commit. `# main` says the
  commit is on `main`, not that it is `main`'s head; how far behind a
  pin may fall is Renovate's and the pin audit's question, not this one.

The exemption lives here, keyed on the signer's identity, because every
other place was measured or ruled out on #1152: a second comment on the
`uses:` line hides the reference from Renovate, a `zizmor.yml` ignore
for either rule hides a real third-party finding, and `file:line` rots.

A finding is dropped only when BOTH hold: its audit is one of those
two, and every reference it names belongs to the signer's repository.
Each audit carries its reference in a different location, as zizmor
1.32.0 emitted them on the canon: `stale-action-refs` in its Primary
location (`concrete.feature` is `owner/repo[/path]@ref`), and
`ref-version-mismatch` in its Hidden location, whose feature is the
whole job or step and whose `uses:` lines carry the reference (its
Primary feature is the comment, `main`). A finding whose reference
cannot be read is kept, so a change in zizmor's output reds rather than
hides.

Usage:
  zizmor_signer.py --signer-workflow <ref> [<zizmor's arguments>...]

runs `zizmor --format=json <arguments>` once (#1955), its stderr passed
through unchanged, and decodes the finding array it writes to stdout.
Each finding that remains after the drop goes through
mise/findings.py: the subject is its first location's file, the
message `<ident>: <the first line of the feature of the first Primary
location, trimmed>` (the owner's ruling on #1955: `desc` is one text
per audit, so it is no identity; Hidden and Related locations are
context, and a finding with no Primary location is refused), the
detail its `desc`, its severity and each location as
`<file>:<line>: <annotation>`. The line is never in the subject or the
message, so an edit above a finding does not make it new. There is
no second, human-mode run: the records print the findings.

FAIL CLOSED ON BLINDNESS (#310 finding 3): without GH_TOKEN zizmor
silently degrades to offline, skipping every audit `audit:actions`
exists to run, and still exits 0. So no GH_TOKEN is a finding and 1
before zizmor runs, and zizmor's own offline-degradation line on stderr
is a finding and 1 after: a present but rejected token degrades the
same way, and only zizmor knows.

The exit status is zizmor's own: 0 when nothing remains (a run whose
findings were all the signer's is clean), its 10-14 by severity when
findings remain, and any other status, zizmor failing, with no report
read. The refusals exit 1, each by name and recording nothing: no
zizmor on PATH, a signer declaration naming no repository, output that
is not zizmor's finding array, and a remaining finding not of the
shape read here. A zizmor pin whose report moves turns the task red
until this moves with it.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Self

import decoded
import findings as writer
from decoded import UndecodableError
from typed_std import group

# The belt's zizmor, as PATH answers it; read as the command by the job
# install law (release/test_job_installs.py).
ZIZMOR = shutil.which("zizmor") or "zizmor"
EXIT_REFUSED = 1
# zizmor's statuses for a run with findings, one per severity.
FINDINGS = range(10, 15)
# What zizmor prints on stderr when it has dropped to offline.
OFFLINE = b"running in offline mode"

# Each exempt audit, the location kind that carries its reference, and
# why the finding is true but not a defect on the signer.
AUDITS = {
    "stale-action-refs": (
        "Primary",
        "is never tagged by design, mise/pinact.yaml",
    ),
    "ref-version-mismatch": (
        "Hidden",
        "is pinned on `# main`, which names the branch, not its head",
    ),
}
# A `uses:` key at the start of a line, optionally a sequence item; a
# `uses:` inside a comment or prose is not a reference.
USES = re.compile(r"""^\s*(?:-\s+)?uses:\s*["']?([^\s"'#]+)""", re.MULTILINE)
# `owner/repo` is the shortest reference that names a repository.
OWNER_AND_REPO = 2


def location_references(feature: str) -> list[str]:
    """Return the references one location's feature carries.

    Returns:
        The feature itself when it is a bare `owner/repo[/path]@ref`;
        otherwise the value of each `uses:` line in it that has an `@`.

    """
    text = feature.strip()
    if "@" in text and not any(c.isspace() for c in text):
        return [text]
    found = (group(m, 1) or "" for m in USES.finditer(feature))
    return [ref for ref in found if "@" in ref]


def references(finding: dict[str, object]) -> list[str]:
    """Return the references a finding names, read where its audit puts them.

    Returns:
        The references in every location of the kind the finding's
        audit carries them in; empty for an audit not exempt here, or
        when the finding carries none in that shape.

    """
    ident = finding.get("ident")
    audit = AUDITS.get(ident) if isinstance(ident, str) else None
    if audit is None:
        return []
    kind = audit[0]
    refs: list[str] = []
    # Every level is checked for its type: a field of the wrong one
    # carries no reference, so the finding is kept and reported, never
    # exempted (#1555: one raised a traceback here).
    locations = finding.get("locations")
    for location in locations if decoded.is_array(locations) else []:
        if not decoded.is_object(location):
            continue
        symbolic = location.get("symbolic")
        if not decoded.is_object(symbolic) or symbolic.get("kind") != kind:
            continue
        concrete = location.get("concrete")
        feature = concrete.get("feature") if decoded.is_object(concrete) else None
        if isinstance(feature, str):
            refs.extend(location_references(feature))
    return refs


@dataclass(frozen=True)
class Finding:
    """What the filter reads of one zizmor finding, decoded once."""

    ident: str  # the audit's name as the report writes it
    ignored: bool  # zizmor's own `ignored: true`
    refs: tuple[str, ...]  # the references its audit carries

    @classmethod
    def of(cls, finding: dict[str, object]) -> Self:
        """Read one decoded finding object.

        Returns:
            The finding.

        """
        return cls(
            ident=str(finding.get("ident")),
            ignored=finding.get("ignored") is True,
            refs=tuple(references(finding)),
        )


def repository(reference: str) -> str | None:
    """Return the `owner/repo` a reference resolves in, lowercased.

    Returns:
        The repository, or None when the reference names no owner and
        repository (a local `./` call, a `docker://` image).

    """
    path = reference.split("@", 1)[0]
    parts = path.split("/")
    if len(parts) < OWNER_AND_REPO or not parts[0] or not parts[1]:
        return None
    if parts[0] in {".", ".."} or ":" in parts[0]:
        return None
    return f"{parts[0]}/{parts[1]}".lower()


def on_signer(finding: Finding, signer: str) -> bool:
    """Report whether a finding is an exempt audit on the signer.

    Returns:
        True only when the audit is exempt here and every reference
        the finding names is in the signer's repository.

    """
    refs = finding.refs
    if not refs:
        return False
    return all(repository(ref) == signer for ref in refs)


def signer_repository(workflow: str) -> str:
    """Return the signer's `owner/repo` from its declared workflow ref.

    Returns:
        The first two path components of `signer-workflow`, lowercased.

    Raises:
        ValueError: the declaration names no owner and repository.

    """
    repo = repository(workflow + "@")
    if repo is None:
        msg = f"signer-workflow {workflow!r} names no owner/repository"
        raise ValueError(msg)
    return repo


def array(value: object) -> list[dict[str, object]] | None:
    """Read zizmor's array: every item a finding object.

    Returns:
        The findings, or None when the value is not that array.

    """
    if not decoded.is_array(value):
        return None
    found: list[dict[str, object]] = []
    for item in value:
        if not decoded.is_object(item):
            return None
        found.append(item)
    return found


@dataclass(frozen=True)
class Record:
    """One remaining finding, as the writer records it."""

    ident: str
    subject: str
    message: str
    detail: tuple[str, ...]


def message(ident: str, feature: str) -> str:
    """Write a remaining finding's message (owner's ruling on #1955).

    zizmor's `desc` is one text per audit, so two findings of an audit
    in one file would read the same; the first line of the feature of
    its first Primary location, the source zizmor marks at fault, tells
    them apart. Hidden and Related locations are context.

    Returns:
        `<ident>: <the feature's first line, trimmed>`.

    """
    first = feature.splitlines()[0] if feature.strip() else ""
    return f"{ident}: {first.strip()}"


@dataclass(frozen=True)
class Place:
    """One location of a finding, as the record reads it."""

    path: str  # the file
    line: str  # `<file>:<line>: <annotation>`
    kind: str  # Primary marks the location at fault; the rest are context
    feature: str  # the source the location covers


def place(item: object, where: str) -> Place:
    """Read one location: its file, line and annotation, kind and source.

    Returns:
        The location; zizmor's rows count from 0, its lines from 1.

    """
    location = decoded.as_object(item, where)
    symbolic = decoded.as_object(location.get("symbolic"), f"{where}: symbolic")
    key = decoded.as_object(symbolic.get("key"), f"{where}: symbolic.key")
    local = decoded.as_object(key.get("Local"), f"{where}: symbolic.key.Local")
    path = decoded.as_str(local.get("verbatim_path"), f"{where}: verbatim_path")
    note = decoded.as_str(symbolic.get("annotation"), f"{where}: annotation")
    concrete = decoded.as_object(location.get("concrete"), f"{where}: concrete")
    span = decoded.as_object(concrete.get("location"), f"{where}: location")
    start = decoded.as_object(span.get("start_point"), f"{where}: start_point")
    row = decoded.as_int(start.get("row"), f"{where}: start_point.row")
    feature = decoded.as_str(concrete.get("feature"), f"{where}: feature")
    kind = decoded.as_str(symbolic.get("kind"), f"{where}: symbolic.kind")
    return Place(path, f"{path}:{row + 1}: {note}", kind, feature)


def record(finding: dict[str, object], where: str) -> Record:
    """Read one remaining finding into its record.

    Returns:
        Its first location's file; its message; and its `desc`, its
        severity and each location with its line.

    Raises:
        UndecodableError: the finding is not of the shape read here.

    """
    ident = decoded.as_str(finding.get("ident"), f"{where}: ident")
    desc = decoded.as_str(finding.get("desc"), f"{where}: desc")
    judged = decoded.as_object(
        finding.get("determinations"), f"{where}: determinations"
    )
    severity = decoded.as_str(judged.get("severity"), f"{where}: severity")
    listed = decoded.as_array(finding.get("locations"), f"{where}: locations")
    if not listed:
        msg = f"{where}: locations is empty"
        raise UndecodableError(msg)
    places = [place(p, f"{where}: locations[{at}]") for at, p in enumerate(listed)]
    primary = next((p for p in places if p.kind == "Primary"), None)
    if primary is None:
        msg = f"{where}: no location is Primary"
        raise UndecodableError(msg)
    detail = (desc, f"severity: {severity}", *(p.line for p in places))
    return Record(ident, places[0].path, message(ident, primary.feature), detail)


@dataclass(frozen=True)
class Ran:
    """What one zizmor run gave back."""

    status: int
    stdout: bytes
    stderr: bytes


def run_zizmor(args: list[str]) -> Ran:
    """Run zizmor once in its JSON mode.

    Returns:
        Its status, stdout and stderr.

    """
    ran = subprocess.run(
        [ZIZMOR, "--format=json", *args], capture_output=True, check=False
    )
    return Ran(ran.returncode, ran.stdout, ran.stderr)


class Args(argparse.Namespace):
    """The arguments, typed as the parser declares them."""

    signer_workflow: str


def read(ran: Ran, signer: str) -> tuple[list[Finding], list[Record]]:
    """Decode zizmor's array, every remaining finding read before any is recorded.

    Returns:
        The signer's findings, dropped, and a record for each other.

    Raises:
        UndecodableError: the output is not zizmor's finding array, or a
            remaining finding is not of the shape read here.

    """
    listed = array(decoded.load_json(ran.stdout))
    if listed is None:
        msg = "it is not zizmor's finding array"
        raise UndecodableError(msg)
    live = [f for f in listed if f.get("ignored") is not True]
    held = [(f, on_signer(Finding.of(f), signer)) for f in live]
    kept = [f for f, on in held if not on]
    records = [record(f, f"findings[{at}]") for at, f in enumerate(kept)]
    return [Finding.of(f) for f, on in held if on], records


def judge(task: str, ran: Ran, signer: str) -> int:
    """Drop the signer's findings, record the rest.

    Returns:
        0 when nothing remains, zizmor's status when findings do, and
        EXIT_REFUSED when the report is not one read here.

    """
    try:
        dropped, records = read(ran, signer)
    except UndecodableError as err:
        print(
            f"{task}: zizmor exited {ran.status}:"
            f" the output is not zizmor's report: {err}",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    for audit, (_, why) in AUDITS.items():
        count = sum(1 for f in dropped if f.ident == audit)
        print(
            f"{task}: {count} {audit} finding(s) on the signer dropped"
            f" ({signer} {why})",
            flush=True,
        )
    for each in records:
        writer.finding(each.subject, each.message, each.detail)
    if not records:
        return 0
    idents = sorted({r.ident for r in records})
    print(
        f"{task}: {len(records)} finding(s) remain: {', '.join(idents)}",
        file=sys.stderr,
    )
    return ran.status if ran.status in FINDINGS else EXIT_REFUSED


def main(argv: list[str] | None = None) -> int:
    """Run zizmor online, drop the signer's findings, record the rest.

    Returns:
        zizmor's status; 0 when nothing remains; EXIT_REFUSED on a blind
        run or a refusal.

    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--signer-workflow",
        required=True,
        help="the signer's workflow ref, from security/identity.toml",
    )
    # Every argument the parser does not name is zizmor's, in order.
    args, rest = parser.parse_known_args(argv, namespace=Args())
    task = writer.task_name()
    try:
        signer = signer_repository(args.signer_workflow)
    except ValueError as err:
        print(f"{task}: {err}", file=sys.stderr)
        return EXIT_REFUSED
    if not os.environ.get("GH_TOKEN"):
        writer.finding(
            "",
            "GH_TOKEN is unset — the offline run skips every",
            ["API-backed audit this task exists for; refusing to pass blind"],
        )
        return EXIT_REFUSED
    try:
        ran = run_zizmor(rest)
    except FileNotFoundError:
        print(f"{task}: zizmor is not on PATH", file=sys.stderr)
        return EXIT_REFUSED
    sys.stderr.write(ran.stderr.decode("utf-8", "replace"))
    sys.stderr.flush()
    if OFFLINE in ran.stderr:
        writer.finding(
            "",
            "zizmor degraded to offline despite GH_TOKEN —",
            ["the online audits did not run; a green result would be blind"],
        )
        return EXIT_REFUSED
    if ran.status != 0 and ran.status not in FINDINGS:
        return ran.status
    return judge(task, ran, signer)


if __name__ == "__main__":
    sys.exit(main())
