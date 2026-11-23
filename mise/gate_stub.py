#!/usr/bin/env python3
"""lint:gate-stub: a gate stub's triggers and concurrency fit the gate (#427).

The task's comment in mise/config.toml has the history; this is its
logic (#1911). A gate caller is a workflow with a line calling the
canon's `ci.yml` (`uses: <canon>/.github/workflows/ci.yml`, or `./`
from the canon itself) and a line starting `pull_request`. Each one is
held to three requirements, each red with its own message:

  the `edited` type (#427): a `- edited` item or a `types:` line naming
  it, so a title or base edit re-runs the gate;
  a `merge_group:` trigger (#1117), indented, so a queued entry's own
  run starts;
  the concurrency lines (#1195, #1617): every `group:` line carries the
  wanted key and every `cancel-in-progress:` line, its indent and a
  trailing comment dropped, is exactly the wanted one; no line of
  either is red too.

The wanted text is fix:gate-stub's, imported from mise/gate_stub_fix.py,
so the lint and its write-mode sibling hold one text.

Usage:
  gate_stub.py OWNER/REPO WORKFLOW...

Each workflow is read as UTF-8, strictly; lines split on the newline
alone, as grep's. Exit 0 with one line, 1 with each unmet requirement's
message, 2 on a usage or read error. Stdlib only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import findings
import gate_stub_fix

EXIT_RED = 1
EXIT_ERROR = 2
# The canon and at least one workflow.
MIN_ARGS = 2
# grep's [[:space:]] within one line.
SPACE = r"[ \t\r\f\v]"
PULL_REQUEST = re.compile(rf"^{SPACE}*pull_request")
EDITED = re.compile(rf"^{SPACE}*-{SPACE}*edited|types:[^#]*\bedited\b")
MERGE_GROUP = re.compile(rf"^{SPACE}+merge_group:")
GROUP = re.compile(rf"^{SPACE}*group:")
CANCEL = re.compile(rf"^{SPACE}*cancel-in-progress:")
WANT_CANCEL = f"cancel-in-progress: {gate_stub_fix.CANCEL}"


def read(path: Path) -> list[str]:
    """Read a workflow as UTF-8, strictly.

    Returns:
        Its lines, split on newline alone.

    Raises:
        ValueError: the file cannot be read or is not UTF-8.

    """
    try:
        return path.read_bytes().decode("utf-8").split("\n")
    except (OSError, UnicodeDecodeError) as err:
        msg = f"{path}: cannot be read as UTF-8: {err}"
        raise ValueError(msg) from err


def bare(line: str) -> str:
    """Drop a line's indent, a trailing comment and trailing blanks, as sed did.

    Returns:
        The line as compared.

    """
    line = re.sub(rf"^{SPACE}+", "", line, count=1)
    line = re.sub(rf"{SPACE}+#.*$", "", line, count=1)
    return re.sub(rf"{SPACE}+$", "", line, count=1)


def grouped(lines: list[str]) -> bool:
    """Judge the concurrency lines of one caller.

    An absent line is red: the shell fed an empty capture to `grep -v`
    as one empty line, which differs from the wanted one.

    Returns:
        True when every group line carries the key and every cancel line
        is the wanted one, and there is at least one of each.

    """
    groups = [line for line in lines if GROUP.search(line)] or [""]
    cancels = [bare(line) for line in lines if CANCEL.search(line)] or [""]
    return all(gate_stub_fix.KEY in line for line in groups) and all(
        line == WANT_CANCEL for line in cancels
    )


def caller(lines: list[str], canon: str) -> bool:
    """Say whether a workflow is a gate caller.

    Returns:
        True when it calls the canon's ci.yml and runs on pull_request.

    """
    uses = re.compile(
        rf"uses:{SPACE}*({re.escape(canon)}/|\./)\.github/workflows/ci\.ya?ml"
    )
    return any(uses.search(line) for line in lines) and any(
        PULL_REQUEST.search(line) for line in lines
    )


def report(bad: str, unqueued: str, ungrouped: str) -> int:
    """Print each unmet requirement's message.

    Returns:
        0 when none is unmet, EXIT_RED otherwise.

    """
    if bad:
        findings.finding(
            "",
            "gate caller's pull_request trigger lacks the edited type,",
            [
                "so title/base edits keep a stale verdict (#427). Set",
                f"types: [opened, synchronize, reopened, edited] in:{bad}",
            ],
        )
    if unqueued:
        findings.finding(
            "",
            "gate caller has no merge_group trigger, so the merge",
            [
                "queue's own run never starts and a queued pull request waits for a",
                'check that cannot report (#1117). Under "on": add',
                "  merge_group:",
                "    types:",
                "      - checks_requested",
                f"in:{unqueued}",
            ],
        )
    if ungrouped:
        findings.finding(
            "",
            "gate caller's concurrency group is shared: a merged pull",
            [
                (
                    "request's event cancels the push gate (#1195), a push"
                    " replaces the one"
                ),
                (
                    "pending (#1617). Run mise run fix:gate-stub, or set under"
                    " concurrency:"
                ),
                f"  group: {gate_stub_fix.GROUP}",
                f"  cancel-in-progress: {gate_stub_fix.CANCEL}",
                f"in:{ungrouped}",
            ],
        )
    if bad or unqueued or ungrouped:
        return EXIT_RED
    print("lint:gate-stub: ok")
    return 0


def main(argv: list[str]) -> int:
    """Judge every gate caller named.

    Returns:
        0 when every caller meets the three requirements, EXIT_RED when
        one does not, EXIT_ERROR on a usage or read error.

    """
    owner, _, repo = argv[0].partition("/") if argv else ("", "", "")
    if not owner or not repo or len(argv) < MIN_ARGS:
        findings.finding("", "usage: gate_stub.py OWNER/REPO WORKFLOW...")
        return EXIT_ERROR
    bad = unqueued = ungrouped = ""
    for name in argv[1:]:
        try:
            lines = read(Path(name))
        except ValueError as err:
            findings.finding("", str(err))
            return EXIT_ERROR
        if not caller(lines, argv[0]):
            continue
        if not any(EDITED.search(line) for line in lines):
            bad += f" {name}"
        if not any(MERGE_GROUP.search(line) for line in lines):
            unqueued += f" {name}"
        if not grouped(lines):
            ungrouped += f" {name}"
    return report(bad, unqueued, ungrouped)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
