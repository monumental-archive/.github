#!/usr/bin/env python3
"""audit:citations: every org release and run URL docs/ cites resolves (#349).

S2's cheap leg: the docs cited "lab v0.19.1, which it blocked" for a
tag that never existed, and the error survived two audits because
checking a citation meant a human resolving it by hand (finding 2).
This resolves every citation of an org release in docs/ against the
live org, so a fabricated or mistyped one is a red Monday.

The citation form, and why a bare `vX.Y.Z` is not walked, is written
beside the task in the belt's config and in CONTRIBUTING.md. An
org-record citation names its repository beside its version:

  `.github@v1.24.1`, `monumental-archive/stele@v0.2.2`,
  stele v0.16.0, or a `.../releases/tag/vX.Y.Z` URL.

Each resolves against THAT repository's tags. Every Actions run URL
docs/ cites must answer the API.

docs/ is the input, read from git (#2013): git tracks no directory, so
a docs/ with nothing tracked under it is missing, and a docs/ holding no
tracked markdown reds too. Resolving zero citations out of zero
documents is the "read nothing, report clean" shape (#738).

Network: the org listing and each run through `gh`, each repository's
tags through `git ls-remote`. A tag listing that fails reads as no
tags, so its citations red by name.

Usage: citations.py, in the canon's top, with GITHUB_REPOSITORY_OWNER
set. Exit 0 with one summary line, 1 with every finding. Stdlib only.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import findings
import tracked

GIT = shutil.which("git") or "git"
EXIT_RED = 1
VERSION = r"v[0-9]+\.[0-9]+\.[0-9]+"
TAG_REF = re.compile(r"refs/tags/(v[0-9][0-9.]*)$")
OWNER_UNSET = (
    "GITHUB_REPOSITORY_OWNER: audit:citations walks an org the environment"
    " names (#579) — Actions sets it; a local run exports it"
)


class Reads(Protocol):
    """What the audit asks the live org."""

    def org_repos(self, owner: str) -> list[str] | None:
        """List the org's repositories, or None when they cannot be."""
        ...

    def repo_tags(self, owner: str, repo: str) -> set[str]:
        """List one repository's release tags."""
        ...

    def run_resolves(self, owner: str, repo: str, run_id: str) -> bool:
        """Say whether one Actions run answers."""
        ...


@dataclass(frozen=True)
class Network:
    """The live reads, one method each, so a row can stand them in."""

    gh: str
    git: str = GIT

    def org_repos(self, owner: str) -> list[str] | None:
        """List the org's repositories through gh.

        Returns:
            Their names, or None when gh could not list them.

        """
        done = subprocess.run(
            [self.gh, "api", f"orgs/{owner}/repos", "--paginate", "--jq", ".[].name"],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            return None
        return [line for line in done.stdout.splitlines() if line]

    def repo_tags(self, owner: str, repo: str) -> set[str]:
        """List one repository's release tags; a failed listing is none.

        Returns:
            The `v*` tag names.

        """
        url = f"https://github.com/{owner}/{repo}"
        done = subprocess.run(
            [self.git, "ls-remote", "--tags", url, "v[0-9]*"],
            capture_output=True,
            text=True,
            check=False,
        )
        found = (TAG_REF.search(line) for line in done.stdout.splitlines())
        return {m.group(1) for m in found if m is not None}

    def run_resolves(self, owner: str, repo: str, run_id: str) -> bool:
        """Ask the API for one Actions run.

        Returns:
            True when it answers.

        """
        endpoint = f"repos/{owner}/{repo}/actions/runs/{run_id}"
        done = subprocess.run(
            [self.gh, "api", endpoint, "--jq", ".id"],
            capture_output=True,
            text=True,
            check=False,
        )
        return done.returncode == 0


def alternation(repos: list[str]) -> str:
    """Join live repository names into one regex alternation.

    Longest first, so a name that prefixes another cannot win the
    shorter match. No literal list of repos in the belt: an imported
    repo is walked the Monday it exists.

    Returns:
        The alternation, each name escaped.

    """
    ordered = sorted(repos, key=len, reverse=True)
    return "|".join(re.escape(name) for name in ordered)


def cited_pairs(texts: list[str], owner: str, names: str) -> list[tuple[str, str]]:
    """Collect every org release the documents cite, as (repo, version).

    Both forms, `repo@vX.Y.Z` (owner-qualified or not, or a space for
    the `@`) and a releases/tag URL, normalise to one pair.

    Returns:
        The distinct pairs, sorted.

    """
    org = re.escape(owner)
    cited = re.compile(rf"(?:{org}/)?({names})[@ ]({VERSION})")
    linked = re.compile(rf"github\.com/{org}/({names})/releases/tag/({VERSION})")
    pairs: set[tuple[str, str]] = set()
    for text in texts:
        for pattern in (cited, linked):
            pairs.update((m.group(1), m.group(2)) for m in pattern.finditer(text))
    return sorted(pairs)


def cited_runs(texts: list[str], owner: str) -> list[str]:
    """Collect every Actions run URL the documents cite.

    Returns:
        The distinct `github.com/<owner>/<repo>/actions/runs/<id>`, sorted.

    """
    org = re.escape(owner)
    run = re.compile(rf"github\.com/{org}/[A-Za-z0-9._-]+/actions/runs/[0-9]+")
    return sorted({m.group(0) for text in texts for m in run.finditer(text)})


def documents(top: Path) -> list[str] | None:
    """Read docs/ from git, saying what is missing when it is.

    Returns:
        The tracked markdown under docs/, or None after the finding.

    """
    if not tracked.tracked("docs", root=top):
        findings.finding(
            "",
            "on the canon, and the input this leg walks is missing: docs/",
            ["a canon-only audit reds on a missing input, never skips (#738)."],
        )
        return None
    docs = tracked.tracked(":!vendor/**", "docs/*.md", root=top)
    if not docs:
        findings.finding(
            "",
            "on the canon, and docs/ holds no tracked markdown —",
            [
                (
                    "there is nothing to resolve, so there is nothing to report"
                    " clean (#738)."
                ),
            ],
        )
        return None
    return docs


def unresolved(
    net: Reads, owner: str, repos: list[str], texts: list[str]
) -> tuple[list[str], int, int]:
    """Resolve every citation against the live org.

    Returns:
        One line per citation that resolves to nothing, and the counts
        of release citations and run URLs walked.

    """
    pairs = cited_pairs(texts, owner, alternation(repos))
    tags = {repo: net.repo_tags(owner, repo) for repo in {r for r, _ in pairs}}
    fail = [
        f"cited in docs/ as an org release, but no such tag in {repo}: {ver}"
        for repo, ver in pairs
        if ver not in tags[repo]
    ]
    runs = cited_runs(texts, owner)
    for url in runs:
        parts = url.split("/")
        if not net.run_resolves(owner, parts[2], parts[5]):
            fail.append(f"run URL cited in docs/ does not resolve: https://{url}")
    return fail, len(pairs), len(runs)


def connect(net: Reads | None) -> tuple[Reads, str] | None:
    """Reach the org the environment names.

    Returns:
        The reads and the owner, or None after the finding.

    """
    if net is None:
        gh = shutil.which("gh")
        if gh is None:
            findings.finding("", "gh missing")
            return None
        net = Network(gh)
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    if not owner:
        findings.finding("", OWNER_UNSET)
        return None
    return net, owner


def main(net: Reads | None = None, top: Path | None = None) -> int:
    """Resolve docs/'s citations.

    Returns:
        0 when every citation resolves, EXIT_RED otherwise.

    """
    top = top or Path.cwd()
    docs = documents(top)
    reached = connect(net) if docs is not None else None
    if docs is None or reached is None:
        return EXIT_RED
    net, owner = reached
    repos = net.org_repos(owner)
    if repos is None:
        findings.finding("", "could not enumerate org repos — nothing was resolved")
        return EXIT_RED
    if not repos:
        findings.finding(
            "",
            "the org listing named zero repositories — the walk is",
            ["broken, not the docs, and nothing was resolved"],
        )
        return EXIT_RED
    texts = [(top / d).read_text(encoding="utf-8", errors="replace") for d in docs]
    fail, releases, runs = unresolved(net, owner, repos, texts)
    if fail:
        findings.finding(
            "",
            "citations that resolve to nothing:",
            [
                *fail,
                "a citation is a claim about the record — correct the document",
                "or the record, never leave them disagreeing (#349 finding 2)",
            ],
        )
        return EXIT_RED
    print(
        f"audit:citations: {len(docs)} doc(s) walked, {releases} org release"
        f" citation(s) and {runs} run URL(s) all resolve"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
