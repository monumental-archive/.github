#!/usr/bin/env python3
"""Attach a release's assets and publish it, or converge on a resume.

`attach-release.yml`'s "Attach and publish" step calls this as its one
command (#1386); before that it was a 49-line `run:` block. The
behaviour is the block's, ported, and every message keeps its wording.

INPUTS. From the environment, as the step sets them: `TAG`, `DRY_RUN`,
`GITHUB_REPOSITORY`, `GITHUB_SERVER_URL`, `GITHUB_RUN_ID`, and the
`GH_TOKEN` the `gh` CLI reads. The assets are `dist/` under the working
directory, and `dist/checksums.txt` is their digest list.

A PUBLISHED RELEASE CONVERGES, NEVER CLOBBERS (#316, found on v1.20.0's
first mint). The DOI job runs after publish, so a mint-only failure is
re-dispatched into a release that is already published and immutable,
where `--clobber`'s delete is refused with a 422 and the resume dies
before reaching the one job that failed. So a published release proves
the rebuilt bytes equal the published bytes and passes through; a
mismatch is a real failure, not a resume.

What must be identical is the BUILD SUBJECTS, and which assets those
are is the published evidence manifest's own typing to say (stele#156).
Comparing every asset made a resume impossible whenever an evidence
document legitimately differed: a Sigstore signature embeds a fresh
timestamp and certificate on every signing, and an SBOM exported live
from the forge's dependency graph can change too (measured on v1.44.1's
duplicate tag push, where the resume could never converge). A release
published before entries were typed (schema 1 carries no `entries`)
has no answer to give, so the whole-set comparison stands for it.

A REHEARSAL STAYS A DRAFT and says so (#597): to anyone reading the
releases page a kept draft is indistinguishable from a burn, which is
how the org accumulated thirty-two drafts nobody could classify. The
reason is known here and nowhere later, so `record-draft.sh` writes it
down here.

stdlib only.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import subprocess
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "mise"))

import decoded
import findings
import workflow_command

HERE = pathlib.Path(__file__).resolve().parent
DIST = pathlib.Path("dist")
PUBLISHED_CHECKSUMS = "published-checksums.txt"
PUBLISHED_MANIFEST = "published-manifest.json"


class StepError(Exception):
    """A refusal: the message is printed as the step's error."""


def gh(*args: str, capture: bool = False, quiet: bool = False) -> str:
    """Run `gh`, exiting with its status when it fails.

    Args:
        *args: The `gh` arguments.
        capture: Return stdout rather than letting it through.
        quiet: Discard stderr (a best-effort download).

    Returns:
        Stdout, when captured; otherwise empty.

    Raises:
        SystemExit: `gh` failed, with its exit status.

    """
    done = subprocess.run(
        # ruff: ignore[start-process-with-partial-path]
        ["gh", *args],
        check=False,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.DEVNULL if quiet else None,
        text=True,
    )
    if done.returncode != 0:
        raise SystemExit(done.returncode)
    return done.stdout or ""


def raw(value: object) -> str:
    """Render a JSON value the way `jq -r` prints it.

    Returns:
        A string as itself; anything else as its JSON.

    """
    return value if isinstance(value, str) else json.dumps(value)


def build_subjects(path: pathlib.Path) -> list[str]:
    """Name the build subjects a published evidence manifest types.

    Returns:
        Each `build-subject` entry's name; none for an absent, empty or
        untyped manifest.

    Raises:
        StepError: The manifest is not a JSON object, or its entries
            are not a list.

    """
    if not path.is_file() or path.stat().st_size == 0:
        return []
    try:
        doc = decoded.load_json(path.read_bytes())
    except decoded.UndecodableError as err:
        msg = f"{path} is not JSON: {err}"
        raise StepError(msg) from err
    if not decoded.is_object(doc):
        msg = f"{path} is not a JSON object"
        raise StepError(msg)
    entries = doc.get("entries") or []
    # A manifest whose entries are not a list types nothing it can be
    # read for; `{"entries": true}` died here as a TypeError (#1556).
    if not decoded.is_array(entries):
        msg = f"{path} is not an evidence manifest: its entries are not a list"
        raise StepError(msg)
    return [
        raw(entry.get("name"))
        for entry in entries
        if decoded.is_object(entry) and entry.get("type") == "build-subject"
    ]


def digests(path: pathlib.Path, name: str) -> str:
    """Every digest a `sha256sum` list records for one asset name.

    Returns:
        The digests, newline-joined; empty when the name is absent.

    """
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        # ruff: ignore[magic-value-comparison]
        if len(fields) >= 2 and fields[1] == name:
            found.append(fields[0])
    return "\n".join(found)


def converge(tag: str, repo: str) -> None:
    """Prove a published release's bytes are the rebuilt ones.

    Raises:
        StepError: The rebuilt assets differ from the published ones.

    """
    gh(
        "release", "download", tag, "--repo", repo,
        "--pattern", "checksums.txt", "--output", PUBLISHED_CHECKSUMS,
    )  # fmt: skip
    # Best-effort: a release older than the manifest has none.
    with contextlib.suppress(SystemExit):
        gh(
            "release", "download", tag, "--repo", repo,
            "--pattern", "evidence-manifest.json", "--output", PUBLISHED_MANIFEST,
            quiet=True,
        )  # fmt: skip
    published = pathlib.Path(PUBLISHED_CHECKSUMS)
    rebuilt = DIST / "checksums.txt"
    typed = build_subjects(pathlib.Path(PUBLISHED_MANIFEST))
    subjects = [name for name in typed if name]
    if not subjects:
        same = subprocess.run(
            # ruff: ignore[start-process-with-partial-path]
            ["diff", "-u", str(published), str(rebuilt)],
            check=False,
        )
        if same.returncode != 0:
            msg = (
                f"{tag} is published and immutable but the rebuilt assets differ"
                " from the published set — this is not a resume"
            )
            raise StepError(msg)
    else:
        differing = ""
        for name in subjects:
            was, now = digests(published, name), digests(rebuilt, name)
            if not was or not now or was != now:
                differing += f" {name}"
        if differing:
            msg = (
                f"{tag} is published and immutable but these build subjects differ"
                f" from the published bytes:{differing} — this is not a resume"
            )
            raise StepError(msg)
    workflow_command.notice(
        f"{tag} already published; build subjects verified identical, converging"
    )


def publish(tag: str, repo: str, *, dry_run: bool) -> None:
    """Upload the assets to the draft, then publish it or keep it.

    Raises:
        SystemExit: `record-draft.sh` failed, with its exit status.

    """
    assets = sorted(str(p) for p in DIST.iterdir() if not p.name.startswith("."))
    gh("release", "upload", tag, *assets, "--clobber", "--repo", repo)
    if dry_run:
        server, run_id = os.environ["GITHUB_SERVER_URL"], os.environ["GITHUB_RUN_ID"]
        env = {
            **os.environ,
            "REPO": repo,
            "TAG": tag,
            "KIND": "rehearsal",
            "RUN_URL": f"{server}/{repo}/actions/runs/{run_id}",
        }
        recorded = subprocess.run([str(HERE / "record-draft.sh")], env=env, check=False)
        if recorded.returncode != 0:
            raise SystemExit(recorded.returncode)
        workflow_command.warning(f"rehearsal: {tag} left as a draft, nothing published")
        return
    # Publishing is the commitment point. Immutable releases apply at
    # publish, and GitHub mints its own release attestation binding tag,
    # commit and asset digests at the same moment.
    gh("release", "edit", tag, "--draft=false", "--repo", repo)
    workflow_command.notice(f"published {tag}")


def main() -> int:
    """Attach and publish, or converge on a published release.

    Returns:
        The step's exit status.

    """
    tag, repo = os.environ["TAG"], os.environ["GITHUB_REPOSITORY"]
    draft = gh(
        "release", "view", tag, "--repo", repo, "--json", "isDraft", "--jq", ".isDraft",
        capture=True,
    ).strip()  # fmt: skip
    try:
        if draft == "false":
            converge(tag, repo)
        else:
            publish(tag, repo, dry_run=os.environ.get("DRY_RUN") == "true")
    except StepError as err:
        findings.finding("", str(err), annotate=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
