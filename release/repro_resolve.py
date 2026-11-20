#!/usr/bin/env python3
"""Resolve the latest release a reproducibility audit rebuilds.

`repro-check.yml`'s "Read the latest release" step calls this as its
one command (#1386); before that it was a 35-line `run:` block. The
behaviour is the block's, ported, and every message keeps its wording.

INPUTS. `GITHUB_REPOSITORY`, `GITHUB_OUTPUT`, and the `GH_TOKEN` the
`gh` CLI reads. OUTPUTS, to `GITHUB_OUTPUT`: `checksums` (the release's
`checksums.txt`), `manifest` (its evidence manifest, or empty), `tag`
and `version`.

WHICH ARTIFACTS A CLASS BUILT is the release's own answer, and it lives
in the evidence manifest's per-entry typing (stele#185). It is fetched
best-effort because not every release has one to give: a release older
than the manifest, or one below the schema that types entries, carries
no answer — schema 1 has no `entries` key at all.

Such a manifest is NOT handed on. Its build-subject population is zero,
which seals CANNOT_JUDGE and exits non-zero, filing a false issue of a
new shape (measured: exit 4). A release that cannot answer keeps the
whole-release comparison it shipped under, and the verdict says so.
Same reasoning, same schema-1 case, as `attach_publish.py`'s resume
guard.

stdlib only.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import re
import subprocess
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "mise"))

import decoded
import workflow_command

CHECKSUMS = pathlib.Path("checksums.txt")
MANIFEST = pathlib.Path("evidence-manifest.json")
RELEASE_TAG = re.compile(r"^v[0-9]")


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


def first_entry_name(path: pathlib.Path) -> str:
    """Read the first manifest entry's name, as `jq -r` would print it.

    Returns:
        The name; empty for an absent or empty manifest, one with no
        entries, or a first entry with no name.

    Raises:
        StepError: The manifest is not a JSON object, or its entries
            are not a list.

    """
    if not path.is_file() or path.stat().st_size == 0:
        return ""
    try:
        doc = decoded.load_json(path.read_bytes())
    except decoded.UndecodableError as err:
        msg = f"{path} is not JSON: {err}"
        raise StepError(msg) from err
    if not decoded.is_object(doc):
        msg = f"{path} is not a JSON object"
        raise StepError(msg)
    entries = doc.get("entries") or []
    # Entries that are not a list type nothing; `{"entries": true}` and
    # `{"entries": {"": null}}` died here as a TypeError and a KeyError
    # (#1556).
    if not decoded.is_array(entries):
        msg = f"{path} is not an evidence manifest: its entries are not a list"
        raise StepError(msg)
    first = entries[0] if entries else None
    name = first.get("name") if decoded.is_object(first) else None
    if name is None or name is False:
        return ""
    return name if isinstance(name, str) else json.dumps(name)


def resolve(repo: str) -> tuple[str, str]:
    """Download the latest release's checksums and typed manifest.

    Returns:
        The tag, and the lines to append to `GITHUB_OUTPUT`.

    Raises:
        StepError: The latest release is not a v* release, or it has no
            checksums to prove against.

    """
    tag = gh(
        "release", "view", "--repo", repo, "--json", "tagName", "--jq", ".tagName",
        capture=True,
    ).strip()  # fmt: skip
    if not RELEASE_TAG.match(tag):
        msg = f"latest release tag {tag} is not a v* release"
        raise StepError(msg)
    gh(
        "release", "download", tag, "--repo", repo,
        "--pattern", "checksums.txt", "--output", str(CHECKSUMS),
    )  # fmt: skip
    if not CHECKSUMS.is_file() or CHECKSUMS.stat().st_size == 0:
        msg = f"release {tag} has no checksums.txt — nothing to prove against"
        raise StepError(msg)
    with contextlib.suppress(SystemExit):
        gh(
            "release", "download", tag, "--repo", repo,
            "--pattern", "evidence-manifest.json", "--output", str(MANIFEST),
            quiet=True,
        )  # fmt: skip
    manifest = ""
    if first_entry_name(MANIFEST):
        manifest = MANIFEST.read_text(encoding="utf-8").rstrip("\n")
        workflow_command.notice(
            f"{tag} types its entries — the rebuilt class scopes the population"
        )
    else:
        workflow_command.notice(
            f"{tag} carries no per-class typing — judging every build subject"
            " it published"
        )
    checksums = CHECKSUMS.read_text(encoding="utf-8")
    return tag, (
        f"checksums<<CHECKSUMS_EOF\n{checksums}CHECKSUMS_EOF\n"
        f"manifest<<MANIFEST_EOF\n{manifest}\nMANIFEST_EOF\n"
        f"tag={tag}\n"
        f"version={tag.removeprefix('v')}\n"
    )


def main() -> int:
    """Resolve the release and hand its facts to the next jobs.

    Returns:
        The step's exit status.

    """
    try:
        tag, outputs = resolve(os.environ["GITHUB_REPOSITORY"])
    except StepError as err:
        workflow_command.error(str(err))
        return 1
    with pathlib.Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as out:
        out.write(outputs)
    workflow_command.notice(f"proving {tag} reproduces")
    return 0


if __name__ == "__main__":
    sys.exit(main())
