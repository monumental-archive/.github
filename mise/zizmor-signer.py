#!/usr/bin/env python3
"""Drop zizmor's stale-action-refs findings on the org signer (#1152).

`audit:actions` runs zizmor online, and since zizmor 1.32 its
`stale-action-refs` audit reports every `uses:` pinned to a commit that
no tag points at. The org signer is never tagged, by design: the SHA is
its whole identity (`mise/pinact.yaml`). So the ten signer references
red the audit for a fact that is true and is not a defect.

The exemption lives here, keyed on the signer's identity, because every
other place was measured or ruled out on #1152: a second comment on the
`uses:` line hides the reference from Renovate, a `zizmor.yml` ignore
for the rule hides a stale third-party pin, and `file:line` rots.

A finding is dropped only when BOTH hold: its audit is
`stale-action-refs`, and every reference it names belongs to the
signer's repository. The reference is read from the finding's Primary
location (`concrete.feature`, `owner/repo[/path]@ref`), the shape
zizmor 1.32.0 emitted on the canon. A finding whose reference cannot be
read is kept, so a change in zizmor's output reds rather than hides.

Reads zizmor's `--format=json` array on stdin. Exits 0 when nothing is
left to report, 1 when findings remain, 2 when the input is not that
array.
"""

from __future__ import annotations

import argparse
import json
import sys

AUDIT = "stale-action-refs"
# `owner/repo` is the shortest reference that names a repository.
OWNER_AND_REPO = 2


def references(finding: dict) -> list[str]:
    """Return the references a finding's Primary locations name.

    Returns:
        Each Primary location's `owner/repo[/path]@ref` text; empty when
        the finding carries none in that shape.

    """
    refs = []
    for location in finding.get("locations") or []:
        if not isinstance(location, dict):
            continue
        if (location.get("symbolic") or {}).get("kind") != "Primary":
            continue
        feature = (location.get("concrete") or {}).get("feature")
        if isinstance(feature, str) and "@" in feature:
            refs.append(feature.strip())
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
    """Report whether a finding is a stale-action-refs on the signer.

    Returns:
        True only when the audit matches and every reference the
        finding names is in the signer's repository.

    """
    if finding.get("ident") != AUDIT:
        return False
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
    print(
        f"audit:actions: {len(dropped)} {AUDIT} finding(s) on the signer dropped"
        f" ({signer} is never tagged by design, mise/pinact.yaml)",
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
