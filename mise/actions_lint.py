#!/usr/bin/env python3
r"""lint:actions: actionlint on the workflows named, the canon's tree followed.

The task's comment in mise/config.toml has the reasons; this is the call
it makes, moved out of the body when the body's file selection (#2007)
took it past the 15-line ceiling.

actionlint runs on exactly the files named (the tracked workflows, from
mise/action-files.sh's workflow_files), never on the directory, which it
would walk, untracked files and all. Two `-ignore` flags hold the
documented exceptions: GitHub's `$/` self-repository syntax and the
ubuntu-26.04 runner labels, both newer than actionlint 1.7.12, each
anchored so any other malformed `uses:` or unknown label stays red.

shellcheck, which actionlint runs on every `run:` block, is told where
`.org-canon` is (#1715): a directory of this run's own holds
`.org-canon` as a link to the canon tree (ORG_CANON_DIR) and goes when
the run ends, so nothing is written in the working tree.

Usage:
  actions_lint.py WORKFLOW...

Exit 0 when actionlint passes; 1 with one finding naming the fix when it
does not (its own report is above it); 2 on a usage error, no
ORG_CANON_DIR, or no actionlint on PATH. Stdlib only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import findings

ACTIONLINT = shutil.which("actionlint") or "actionlint"
EXIT_RED = 1
EXIT_ERROR = 2
# GitHub's `$/` self-repository syntax (#165), anchored to its prefix.
SELF_REF = 'specifying action "\\$/'
# The ubuntu-26.04 runner labels, x64 and arm, and no other.
RUNNER = 'label "ubuntu-26\\.04(-arm)?" is unknown'


def main(argv: list[str]) -> int:
    """Run actionlint on the workflows named.

    Returns:
        0 when it passes, EXIT_RED when it reports, EXIT_ERROR on a
        usage error, no canon tree, or no actionlint.

    """
    if not argv:
        findings.finding("", "usage: actions_lint.py WORKFLOW...")
        return EXIT_ERROR
    canon = os.environ.get("ORG_CANON_DIR", "")
    if not canon:
        findings.finding(
            "", "ORG_CANON_DIR is unset — no canon tree for shellcheck to follow"
        )
        return EXIT_ERROR
    with tempfile.TemporaryDirectory() as src:
        (Path(src) / ".org-canon").symlink_to(canon)
        opts = f"{os.environ.get('SHELLCHECK_OPTS', '')} --source-path={src}"
        try:
            done = subprocess.run(
                [ACTIONLINT, "-color", "-ignore", SELF_REF, "-ignore", RUNNER, *argv],
                env=os.environ | {"SHELLCHECK_OPTS": opts},
                check=False,
            )
        except FileNotFoundError:
            findings.finding("", "actionlint is not on PATH")
            return EXIT_ERROR
    if done.returncode != 0:
        findings.finding(
            "",
            "actionlint findings above — fixed by hand; fix:actions only re-pins"
            " uses: (pinact)",
        )
        return EXIT_RED
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
