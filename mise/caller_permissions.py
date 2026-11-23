#!/usr/bin/env python3
"""lint:caller-permissions: every caller grants what its callee asks for.

Seam 2 of #358: a caller of a `workflow_call` workflow must grant the
union of the callee's job-level permissions, or the run dies as
startup_failure with no jobs and no log. stele computes that join
(`stele assert permissions`) from the canon tree the belt came from,
ORG_CANON_DIR, against the callers this checkout declares. The task's
comment in mise/config.toml has the history; this is its logic (#1911).

  no stele on PATH: red, by name, before anything runs;
  stele's assertion passes: its report, and exit 0;
  otherwise red: stele's findings (mise/stele_findings.py), then one
  more naming the fix.

The workflows are the task's guard: the selection that says this tree
runs workflows at all. A workflow-free tree seals CANNOT_JUDGE rather
than passing vacuously, so the task skips before calling this, and a
call with none is a usage error.

Usage:
  caller_permissions.py CANON_DIR WORKFLOW...

Exit 0 when the grants hold, 1 on a finding, 2 on a usage error. Stdlib
only, and stele on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import findings
from belt_scripts import script

EXIT_RED = 1
EXIT_ERROR = 2
# The canon and at least one workflow.
MIN_ARGS = 2
ADAPTER = script(Path(__file__).resolve().parent, "stele_findings.py")
USAGE = "usage: caller_permissions.py CANON_DIR WORKFLOW..."


def assertion(canon: str) -> list[str]:
    """Build the stele_findings.py call judging this checkout's callers.

    Returns:
        Its argument vector, the interpreter first.

    """
    return [
        sys.executable,
        str(ADAPTER),
        "assert",
        "permissions",
        "--policy",
        f"{canon}/slsa/assert-policy.json",
        "--tree",
        canon,
        "--callers",
        ".",
    ]


def main(argv: list[str]) -> int:
    """Judge every caller's grant against its callee's requirement.

    Returns:
        0 when every grant suffices, EXIT_RED when one does not or stele
        is missing, EXIT_ERROR on a usage error.

    """
    if len(argv) < MIN_ARGS or not argv[0]:
        findings.finding("", USAGE)
        return EXIT_ERROR
    if shutil.which("stele") is None:
        findings.finding("", "stele missing from the belt")
        return EXIT_RED
    ran = subprocess.run(
        assertion(argv[0]), capture_output=True, text=True, check=False
    )
    sys.stdout.write(ran.stdout)
    sys.stderr.write(ran.stderr)
    if ran.returncode:
        findings.finding(
            "",
            "see above — grant what the pinned canon's",
            ["callee jobs ask for, at the caller's uses: job (#358 seam 2)."],
        )
        return EXIT_RED
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
