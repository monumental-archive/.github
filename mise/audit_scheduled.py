#!/usr/bin/env python3
"""lint:audit-scheduled: every audit:* task has a workflow that runs it.

A check nobody runs is a claim, not a check (#358 seam 1). The task's
comment in mise/config.toml has the history and the two halves' reasons
(#704, #1355, #1858); this is its logic (#1911).

Half 1, the BELT's audit tasks, only where the belt's config is
(mise/config.toml in the working directory): each `[tasks."audit:<n>"]`
header names a task, and each must appear as `mise run audit:<n>` in a
workflow. Zero headers is red: the enumeration is broken.

Half 2, this repository's OWN audit tasks (`--own`, one per line, from
own-audits.sh's `own_audit_tasks`), everywhere: none is the ordinary
answer; with no workflow at all they are red; otherwise a workflow that
calls the canon's repo-own-audits.yml collects them all, or each must
appear as `mise run <task>` in a workflow.

The workflows searched are the files named, exactly: GitHub runs a
workflow only from those, so nothing else in the directory schedules a
task. A name matches when no `[a-z-]` follows it, so `audit:fuzz` is not
`audit:fuzz-long`.

Usage:
  audit_scheduled.py --own TASKS [--canon OWNER/REPO] [WORKFLOW...]

`--canon` (security/identity.toml, read by the task) is needed when there
are own tasks and workflows. Each file is read as UTF-8, strictly. Exit
0 with each half's line, 1 on a finding, 2 on a usage or read error.
Stdlib only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import findings
import tracked

EXIT_RED = 1
EXIT_ERROR = 2
CONFIG = Path("mise/config.toml")
AUDIT = re.compile(r'^\[tasks\."audit:([a-z-]+)"\]')
NAME = "lint:audit-scheduled"


class Args(argparse.Namespace):
    """The command line, typed."""

    own: str = ""
    canon: str = ""
    workflows: list[str]


def read(path: Path) -> list[str]:
    """Read a file as UTF-8, strictly.

    Returns:
        Its lines, split on the newline alone.

    Raises:
        ValueError: the file cannot be read or is not UTF-8.

    """
    try:
        return path.read_bytes().decode("utf-8").split("\n")
    except (OSError, UnicodeDecodeError) as err:
        msg = f"{path}: cannot be read as UTF-8: {err}"
        raise ValueError(msg) from err


def runs(lines: list[str], task: str) -> bool:
    """Say whether a workflow line runs a task.

    Returns:
        True when some line holds `mise run <task>` with no `[a-z-]` after.

    """
    run = re.compile(rf"mise run {re.escape(task)}([^a-z-]|$)")
    return any(run.search(line) for line in lines)


def belt_half(lines: list[str]) -> int:
    """Hold the belt's audit tasks to a scheduling workflow.

    Returns:
        0 when every one is run, EXIT_RED otherwise.

    """
    audits = [match.group(1) for line in read(CONFIG) if (match := AUDIT.match(line))]
    if not audits:
        findings.finding("", "zero belt audit tasks enumerated — the grep is broken")
        return EXIT_RED
    bad = "".join(f" audit:{t}" for t in audits if not runs(lines, f"audit:{t}"))
    if bad:
        findings.finding(
            "",
            f"belt audit task(s) no workflow ever runs:{bad}",
            [
                (
                    "schedule each in .github/workflows/ (slsa.yml or a sibling,"
                    " publish.yml, a"
                ),
                (
                    "repo-*.yml subject workflow or repo-mutants.yml) or delete the"
                    " task — a check"
                ),
                "nobody runs is a claim, not a check (#358 seam 1).",
            ],
        )
        return EXIT_RED
    print(f"{NAME}: {len(audits)} belt audit task(s), every one scheduled", flush=True)
    return 0


def own_half(own: list[str], lines: list[str], canon: str, *, any_flow: bool) -> int:
    """Hold this repository's own audit tasks to a workflow.

    Returns:
        0 when every one is run or collected, EXIT_RED otherwise.

    """
    if not any_flow:
        findings.finding(
            "",
            "repo-defined audit task(s) and NO workflows at all:",
            [
                *(f"  {t}" for t in own),
                "wire the own-audits stub (workflow-templates/own-audits.yml) —",
                "it calls repo-own-audits.yml, whose one leg runs every audit:*",
                "this repository defines (#704, #1858).",
            ],
        )
        return EXIT_RED
    collector = re.compile(
        rf"uses:[ \t\r\f\v]*({re.escape(canon)}/|\./)"
        r"\.github/workflows/repo-own-audits\.ya?ml"
    )
    if any(collector.search(line) for line in lines):
        print(
            f"{NAME}: {len(own)} repo-defined audit task(s), collected by"
            " repo-own-audits.yml",
            flush=True,
        )
        return 0
    missing = "".join(f" {t}" for t in own if not runs(lines, t))
    if missing:
        findings.finding(
            "",
            f"repo-defined audit task(s) no workflow runs:{missing}",
            [
                "wire the own-audits stub (workflow-templates/own-audits.yml), which",
                (
                    "calls repo-own-audits.yml and runs every audit:* this repository"
                    " defines,"
                ),
                "or run each from a workflow here — or delete the task. A check",
                "nobody runs is a claim, not a check (#358 seam 1, #704).",
            ],
        )
        return EXIT_RED
    print(
        f"{NAME}: {len(own)} repo-defined audit task(s), each run by a workflow here",
        flush=True,
    )
    return 0


def main(argv: list[str]) -> int:
    """Run both halves over the workflows named.

    Returns:
        0 when both hold, EXIT_RED when one does not, EXIT_ERROR on a
        usage or read error.

    """
    parser = argparse.ArgumentParser(prog="audit_scheduled.py")
    parser.add_argument("--own", required=True)
    parser.add_argument("--canon", default="")
    parser.add_argument("workflows", nargs="*")
    args = parser.parse_args(argv, namespace=Args())
    own = [t for t in args.own.split("\n") if t]
    if own and args.workflows and "/" not in args.canon.strip("/"):
        findings.finding("", "usage: --canon OWNER/REPO is needed to judge own tasks")
        return EXIT_ERROR
    try:
        lines = [line for name in args.workflows for line in read(Path(name))]
        # The belt half applies where git tracks a belt config (#2013).
        belt = tracked.has(str(CONFIG), root=Path.cwd())
        status = belt_half(lines) if belt else 0
    except ValueError as err:
        findings.finding("", str(err))
        return EXIT_ERROR
    if not own:
        print(f"{NAME}: this repository defines no audit:* of its own", flush=True)
        return status
    return own_half(own, lines, args.canon, any_flow=bool(args.workflows)) or status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
