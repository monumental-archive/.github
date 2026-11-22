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
new document there, counted, never folded into an `R` the filter drops.
The refusal prints `vex-one-per-merge.txt`, the one sentence the
workflow prints too, then the files.

Skips clean, by name: where no decision is tracked, where there is no
`origin/main`, where nothing is ahead of it (the default branch
itself), and in a merge group. A group's range holds every entry queued
ahead of it too, so two pull requests with one decision each would red
the second for obeying the rule. Nothing is lost: the pull-request run
judged the same head, and the gate's queue-head step (#1422) refuses a
group not built from that head. Network-free, so deterministic.

One reader for both sides (#1615): `vex-attest.yml`'s push branch
asks `--list BEFORE..HEAD` for the decisions a merge changed, through
the same function this count uses, so the gate's count and the
workflow's signing list cannot differ. With renames on, a decision a
merge renamed and edited was an `R`, dropped, and never signed.

Usage:
  vex_one_per_merge.py              (in the repository, from its root)
  vex_one_per_merge.py --list RANGE (print RANGE's changed decisions)

The count exits 0 at most one changed, 1 more than one. The listing
prints one decision per line and exits 0.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import findings
import tracked

GIT = shutil.which("git") or "git"
DECISIONS = "security/vex/*.openvex.json"
BASE = "origin/main"
NAME = "lint:vex-one-per-merge"
# A merge commit's parents line: the commit, then its two parents.
MERGE_WORDS = 3


def git(*args: str) -> str:
    """Run git in the working directory.

    Returns:
        git's standard output.

    """
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


def changed(revisions: str) -> list[str]:
    """Name the decisions a revision range adds or modifies.

    Renames are off, so a decision at a new name is listed there; a
    deletion is not listed, since it leaves nothing to sign.

    Returns:
        The decision paths, in git's order.

    """
    return git(
        "diff",
        "--name-only",
        "--no-renames",
        "--diff-filter=AM",
        revisions,
        "--",
        DECISIONS,
    ).split()


def tip() -> str:
    """Name the pull request's head: HEAD, or a merge commit's second parent.

    Returns:
        A revision naming the branch's own commits.

    """
    parents = git("rev-list", "--parents", "-n", "1", "HEAD").split()
    return "HEAD^2" if len(parents) >= MERGE_WORDS else "HEAD"


def count() -> int:
    """Count the changed decisions and judge them.

    Returns:
        0: at most one, or skipped. 1: more than one.

    """
    if not tracked.tracked(DECISIONS):
        print(f"{NAME}: no VEX decisions tracked, skipped")
        return 0
    if os.environ.get("GITHUB_EVENT_NAME") == "merge_group":
        print(
            f"{NAME}: judged on the pull request: a merge group's range also"
            " holds the entries queued ahead of it, skipped"
        )
        return 0
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
    names = changed(f"{BASE}...{head}")
    if len(names) > 1:
        findings.finding("", f"{message()}:", names)
        return 1
    print(f"{NAME}: {len(names)} VEX decision(s) changed ahead of {BASE}")
    return 0


class Args(argparse.Namespace):
    """The arguments, typed as the parser declares them."""

    revisions: str | None


def main(argv: list[str]) -> int:
    """Count for the gate, or list a range's decisions for the workflow.

    Returns:
        The count's status, or 0 after a listing.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", metavar="RANGE", dest="revisions")
    args = parser.parse_args(argv, namespace=Args())
    if args.revisions is None:
        return count()
    for name in changed(args.revisions):
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
