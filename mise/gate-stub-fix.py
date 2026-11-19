#!/usr/bin/env python3
"""Rewrite a gate stub's concurrency lines to the ones lint:gate-stub wants.

Org canon — the write-mode sibling of lint:gate-stub (#1617). The stub's
concurrency block lives in each repository's own workflow, where neither
the shared workflow nor Renovate reaches it, so when the canon changes
the wanted group every repository's pin bump turns the lint red. This
makes that bump `mise run fix` instead of a hand edit.

It judges the files the lint judges — each workflow that calls the
canon's `ci.yml` and runs on `pull_request` — and in each one touches
only the top-level `concurrency:` block's `group` and
`cancel-in-progress` lines: a wrong line is rewritten in place, its
indent and any trailing comment kept; a missing one is written at the
end of the block; a right one is left byte for byte. A caller with no
concurrency block is refused by name and left as it is: where to put a
block in someone's workflow is a decision, not a rewrite.

The wanted text is the lint's, restated; the rows in
mise/test_gate_stub.py run this helper and then the lint on its output,
so the two cannot drift apart.
"""

import re
import sys
from pathlib import Path

NAME = "fix:gate-stub"

KEY = "github.event.pull_request.number || format('{0}-{1}', github.ref, github.sha)"
GROUP = "${{ github.workflow }}-${{ " + KEY + " }}"
CANCEL = "${{ github.event_name == 'pull_request' }}"

BLOCK = re.compile(r"^concurrency:[ \t]*(#.*)?$")
LINE = re.compile(
    r"^(?P<indent>[ \t]+)(?P<key>group|cancel-in-progress):"
    r"(?P<value>[^#\n]*?)(?P<comment>[ \t]+#[^\n]*)?(?P<end>\n?)$",
)
PULL_REQUEST = re.compile(r"^[ \t]*pull_request", re.MULTILINE)


def is_caller(text: str, canon: str) -> bool:
    """Say whether a workflow is a gate caller the lint judges.

    Returns:
        True when it calls the canon's ci.yml and runs on pull_request.

    """
    uses = rf"uses:[ \t]*({re.escape(canon)}/|\./)\.github/workflows/ci\.ya?ml"
    return bool(re.search(uses, text)) and bool(PULL_REQUEST.search(text))


def right(key: str, value: str) -> bool:
    """Say whether one line's value is already the wanted one.

    Returns:
        True for a group carrying the key, or the exact cancel expression.

    """
    if key == "group":
        return KEY in value
    return value.strip() == CANCEL


def wanted(key: str) -> str:
    """Return the wanted value for a key.

    Returns:
        The group or the cancel expression.

    """
    return GROUP if key == "group" else CANCEL


def fix(text: str) -> str | None:
    """Rewrite the top-level concurrency block of one caller.

    Returns:
        The new text, or None when the file has no top-level block.

    """
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if BLOCK.match(line)), None)
    if start is None:
        return None
    end = start + 1
    while end < len(lines) and (
        lines[end][:1] in {" ", "\t"} or not lines[end].strip()
    ):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    seen = set()
    indent = "  "
    for i in range(start + 1, end):
        match = LINE.match(lines[i])
        if match is None:
            continue
        key = match["key"]
        seen.add(key)
        indent = match["indent"]
        if not right(key, match["value"]):
            comment = match["comment"] or ""
            lines[i] = f"{indent}{key}: {wanted(key)}{comment}{match['end'] or chr(10)}"
    missing = [
        f"{indent}{key}: {wanted(key)}\n"
        for key in ("group", "cancel-in-progress")
        if key not in seen
    ]
    if missing and not lines[end - 1].endswith("\n"):
        lines[end - 1] += "\n"
    lines[end:end] = missing
    return "".join(lines)


def main(argv: list[str]) -> int:
    """Fix every gate caller named on the command line.

    Returns:
        0 when every caller now carries the wanted lines, 1 when one has
        no concurrency block to rewrite.

    """
    canon, *paths = argv
    refused = []
    for path in map(Path, paths):
        text = path.read_text(encoding="utf-8")
        if not is_caller(text, canon):
            continue
        new = fix(text)
        if new is None:
            refused.append(str(path))
        elif new != text:
            path.write_text(new, encoding="utf-8")
            print(f"{NAME}: rewrote the concurrency lines in {path}")
    if refused:
        print(
            f"{NAME}: no top-level concurrency block to rewrite, so none was"
            " written; add one by hand under the triggers:\n"
            "  concurrency:\n"
            f"    group: {GROUP}\n"
            f"    cancel-in-progress: {CANCEL}\n"
            f"  in: {' '.join(refused)}",
            file=sys.stderr,
        )
        return 1
    print(f"{NAME}: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
