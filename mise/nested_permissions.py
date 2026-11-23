#!/usr/bin/env python3
"""lint:nested-permissions: every nested `uses:` job states its permissions.

A `uses:` job with no `permissions:` block inherits the WORKFLOW-level
default of the file it is written in, never its caller's grant, so the
called workflow asks for more than its calling job holds and the run
dies as `startup_failure` with no jobs and no log. The task's comment
in mise/config.toml has the history; this is its logic (#1911).

A file is checked when any line starts `    uses:` (a job-level
`uses:` at four spaces), in reusable workflows and caller stubs alike.
In a checked file, from the line `jobs:` on, each line `  <name>:`
opens a job; a job with a `    uses:` line and no `    permissions:`
line is an offender, named `<file>:<job>`. The reading is by
indentation, as the canon's two-space style writes every workflow;
actionlint, earlier in the same gate, holds the YAML well-formed.

Usage:
  nested_permissions.py WORKFLOW...

Each workflow is read as UTF-8, strictly. Exit 0 with one summary line,
1 with every offender named, 2 on a usage or read error. Stdlib only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import findings

EXIT_RED = 1
EXIT_ERROR = 2
# awk's [[:space:]] within one line: lines split on "\n" alone, as awk's.
SPACE = r"[ \t\r\f\v]"
JOBS = re.compile(rf"jobs:{SPACE}*")
JOB = re.compile(rf"  [A-Za-z0-9_-]+:{SPACE}*")
# awk's first field: the run of characters up to a blank.
FIELD = re.compile(r"[^ \t]*")
USES = "    uses:"
PERMISSIONS = "    permissions:"
TAIL = [
    "it inherits the workflow-level default, not the caller grant —",
    "state exactly what the called workflow needs, or the run dies as",
    "startup_failure with no jobs and no log.",
]


def read(path: Path) -> str:
    """Read a workflow as UTF-8, strictly.

    Returns:
        Its text.

    Raises:
        ValueError: the file cannot be read or is not UTF-8.

    """
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as err:
        msg = f"{path}: cannot be read as UTF-8: {err}"
        raise ValueError(msg) from err


def job_name(line: str) -> str:
    """Name the job a `  <name>:` line opens.

    Returns:
        The line's first blank-separated field, its trailing `:` dropped.

    """
    field = FIELD.match(line.lstrip(" \t"))
    word = field.group() if field else ""
    return word.removesuffix(":")


def offenders(lines: list[str]) -> list[str]:
    """Name the `uses:` jobs of one workflow that state no permissions.

    Returns:
        Each offending job's name, in file order.

    """
    found: list[str] = []
    injobs = False
    job = ""
    uses = perms = False

    def close() -> None:
        if job and uses and not perms:
            found.append(job)

    for line in lines:
        if JOBS.fullmatch(line):
            injobs = True
            continue
        if not injobs:
            continue
        if JOB.fullmatch(line):
            close()
            job, uses, perms = job_name(line), False, False
            continue
        uses = uses or line.startswith(USES)
        perms = perms or line.startswith(PERMISSIONS)
    close()
    return found


def main(argv: list[str]) -> int:
    """Check every workflow named.

    Returns:
        0 when every nested `uses:` job states its permissions,
        EXIT_RED when one does not, EXIT_ERROR on a usage or read error.

    """
    if not argv:
        findings.finding("", "usage: nested_permissions.py WORKFLOW...")
        return EXIT_ERROR
    bad: list[str] = []
    checked = 0
    for name in argv:
        try:
            lines = read(Path(name)).split("\n")
        except ValueError as err:
            findings.finding("", str(err))
            return EXIT_ERROR
        if not any(line.startswith(USES) for line in lines):
            continue
        checked += 1
        bad += [f"{name}:{job}" for job in offenders(lines)]
    if bad:
        findings.finding(
            "", "nested uses: job with no permissions block:", [*bad, *TAIL]
        )
        return EXIT_RED
    print(f"lint:nested-permissions: {checked} workflow(s) with uses: jobs clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
