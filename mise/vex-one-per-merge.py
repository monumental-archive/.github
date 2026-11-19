#!/usr/bin/env python3
"""Refuse a pull request that changes more than one VEX decision (#1603).

`security/vex/README.md`: one decision per document, one document per
merge. `vex-attest.yml` refuses a push to main that changed two, but by
then the merge has happened: main is red and nothing is signed
(#1551). This is the same refusal on the pull request, from the range
the commit lints read: the commits ahead of `origin/main`, with the
real commits of a pull request's synthetic merge (its second parent).

Counted is what the range adds or modifies under
`security/vex/*.openvex.json`, the workflow's own pathspec and filter,
diffed from the merge base so a main that moved on is not counted. A
deletion signs nothing, and a non-decision file there (the README) is
not a decision. Renames are off: a decision moved to a new name is a
new document there, counted, never folded into an `R` the filter drops. The refusal prints `vex-one-per-merge.txt`, the one
sentence the workflow prints too, then the files.

Skips clean, by name: where no decision is tracked, where there is no
`origin/main`, and where nothing is ahead of it (the default branch
itself). Network-free, so deterministic.

Usage:
  vex-one-per-merge.py      (in the repository, from its root)

Exits 0 at most one changed, 1 more than one.
"""

import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path

GIT = shutil.which("git") or "git"
DECISIONS = "security/vex/*.openvex.json"
BASE = "origin/main"
NAME = "lint:vex-one-per-merge"


def git(*args: str) -> str:
    """Run git in the working directory.

    Returns:
        git's standard output.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    return subprocess.run(
        [GIT, *args], capture_output=True, text=True, check=True
    ).stdout


def message() -> str:
    """Read the refusal's one home, shared with vex-attest.yml.

    Returns:
        The sentence, without its newline.

    """
    home = Path(__file__).resolve().with_name("vex-one-per-merge.txt")
    return home.read_text(encoding="utf-8").strip()


def tip() -> str:
    """Name the pull request's head: HEAD, or a merge commit's second parent.

    Returns:
        A revision naming the branch's own commits.

    """
    parents = git("rev-list", "--parents", "-n", "1", "HEAD").split()
    return "HEAD^2" if len(parents) > 2 else "HEAD"


def main() -> int:
    """Count the changed decisions and judge them.

    Returns:
        0: at most one, or skipped. 1: more than one.

    """
    if not git("ls-files", "--", DECISIONS).strip():
        print(f"{NAME}: no VEX decisions tracked, skipped")
        return 0
    # ruff: ignore[subprocess-without-shell-equals-true]
    known = subprocess.run(
        [GIT, "rev-parse", "--verify", "-q", f"{BASE}^{{commit}}"],
        capture_output=True,
        check=False,
    )
    if known.returncode != 0:
        print(f"{NAME}: no {BASE}, skipped")
        return 0
    head = tip()
    if not git("rev-list", f"{BASE}..{head}").strip():
        print(f"{NAME}: nothing ahead of {BASE} (the default branch itself), skipped")
        return 0
    changed = git(
        "diff",
        "--name-only",
        "--no-renames",
        "--diff-filter=AM",
        f"{BASE}...{head}",
        "--",
        DECISIONS,
    ).split()
    if len(changed) > 1:
        print(f"{NAME}: {message()}:", file=sys.stderr)
        for name in changed:
            print(f"  {name}", file=sys.stderr)
        return 1
    print(f"{NAME}: {len(changed)} VEX decision(s) changed ahead of {BASE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
