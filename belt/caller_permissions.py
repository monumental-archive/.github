"""lint:caller-permissions: every caller grants what its callee asks for.

Seam 2 of #358: a caller of a `workflow_call` workflow must grant the
union of the callee's job-level permissions, or the run dies as
startup_failure with no jobs and no log. stele computes that join
(`stele assert permissions`) from the canon tree the belt came from,
ORG_CANON_DIR, against the callers this checkout declares. The task's
comment in mise/config.toml has the history; this is its logic (#1911).

  no stele on PATH: red, by name, before anything runs;
  stele's assertion passes: its report, and exit 0;
  otherwise red: stele's findings (belt/stele_findings.py), then one
  more naming the fix.

The workflows are the task's guard: the selection that says this tree
runs workflows at all. A workflow-free tree seals CANNOT_JUDGE rather
than passing vacuously, so the task skips before calling this, and a
call with none is a usage error.

Usage:
  belt caller-permissions CANON_DIR WORKFLOW...

Exit 0 when the grants hold, 1 on a finding, 2 on a usage error. Stdlib
only, and stele on PATH.
"""

from __future__ import annotations

import argparse
import shutil

from belt import findings, stele_findings

EXIT_RED = 1
# The canon and at least one workflow.


def assertion(canon: str) -> list[str]:
    """Build the stele-findings call judging this checkout's callers.

    Returns:
        Its arguments.

    """
    return [
        "assert",
        "permissions",
        "--",
        "--policy",
        f"{canon}/slsa/assert-policy.json",
        "--tree",
        canon,
        "--callers",
        ".",
    ]


class Args(argparse.Namespace):
    """The command's arguments, typed as parsed."""

    canon_dir: str
    workflows: list[str]


def main(argv: list[str]) -> int:
    """Judge every caller's grant against its callee's requirement.

    Returns:
        0 when every grant suffices, EXIT_RED when one does not or stele
        is missing; argparse's 2 on a usage error.

    """
    parser = argparse.ArgumentParser(
        prog="belt caller-permissions",
        description="Judge every caller's grant against its callee's requirement",
    )
    parser.add_argument("canon_dir", metavar="CANON_DIR")
    parser.add_argument("workflows", nargs="+", metavar="WORKFLOW")
    args = parser.parse_args(argv, namespace=Args())
    if not args.canon_dir:
        parser.error("the canon directory is empty")
    if shutil.which("stele") is None:
        findings.finding("", "stele missing from the belt")
        return EXIT_RED
    if stele_findings.main(assertion(args.canon_dir)):
        findings.finding(
            "",
            "see above — grant what the pinned canon's",
            ["callee jobs ask for, at the caller's uses: job (#358 seam 2)."],
        )
        return EXIT_RED
    return 0
