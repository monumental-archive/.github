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

Reads zizmor's `--format=json` array on stdin. Exits 0 when nothing is
left to report, 1 when findings remain, 2 when the input is not that
array.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

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
    return [ref for ref in USES.findall(feature) if "@" in ref]


def references(finding: dict) -> list[str]:
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
    refs = []
    # Every level is checked for its type: a field of the wrong one
    # carries no reference, so the finding is kept and reported, never
    # exempted (#1555: one raised a traceback here).
    locations = finding.get("locations")
    for location in locations if isinstance(locations, list) else []:
        if not isinstance(location, dict):
            continue
        symbolic = location.get("symbolic")
        if not isinstance(symbolic, dict) or symbolic.get("kind") != kind:
            continue
        concrete = location.get("concrete")
        feature = concrete.get("feature") if isinstance(concrete, dict) else None
        if isinstance(feature, str):
            refs.extend(location_references(feature))
    return refs


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


def on_signer(finding: dict, signer: str) -> bool:
    """Report whether a finding is an exempt audit on the signer.

    Returns:
        True only when the audit is exempt here and every reference
        the finding names is in the signer's repository.

    """
    refs = references(finding)
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


def main(argv: list[str] | None = None) -> int:
    """Filter zizmor's findings read from stdin.

    Returns:
        0 when nothing remains to report, 1 when findings remain, 2 on
        input that is not zizmor's JSON array.

    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--signer-workflow",
        required=True,
        help="the signer's workflow ref, from security/identity.toml",
    )
    args = parser.parse_args(argv)
    try:
        signer = signer_repository(args.signer_workflow)
        findings = json.load(sys.stdin)
    except (ValueError, json.JSONDecodeError) as err:
        print(f"zizmor-signer: {err}", file=sys.stderr)
        return 2
    if not isinstance(findings, list) or not all(isinstance(f, dict) for f in findings):
        print("zizmor-signer: input is not zizmor's finding array", file=sys.stderr)
        return 2

    live = [f for f in findings if f.get("ignored") is not True]
    dropped = [f for f in live if on_signer(f, signer)]
    kept = [f for f in live if not on_signer(f, signer)]
    for audit, (_, why) in AUDITS.items():
        count = sum(1 for f in dropped if f.get("ident") == audit)
        print(
            f"audit:actions: {count} {audit} finding(s) on the signer dropped"
            f" ({signer} {why})",
        )
    if kept:
        idents = sorted({str(f.get("ident")) for f in kept})
        print(
            f"audit:actions: {len(kept)} finding(s) remain: {', '.join(idents)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
