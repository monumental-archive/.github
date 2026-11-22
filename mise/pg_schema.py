#!/usr/bin/env python3
"""Compare or write a pgrx extension's SQL surface snapshot (#896).

The judging half of `lint:pg-schema` and the writing half of
`fix:pg-schema`; `pg-schema.sh` derives the extension, provisions
Postgres and runs `cargo pgrx schema`, then hands the raw output here.
Extracted so every branch has table tests (`test_pg_schema.py`).

NORMALISED, never raw. pgrx annotates every item with its source
location (`-- src/lib.rs:33`), so an edit that only shifts a line number
would diff, and its own header states that item ordering is not stable —
it is driven by a dependency graph. A check that fires on unrelated
refactors is a check people learn to regenerate without reading. So
comments are stripped, whitespace is collapsed, and statements are
sorted one per line, which leaves exactly what is guarded: the set of
SQL objects and their signatures.

Ported from edtf's `.github/scripts/schema-snapshot.sh` unchanged in
effect, so edtf's tracked snapshot passes as it stands. The split on
`;` and the comment strip are lexical and know nothing of string
literals or dollar quoting; pgrx's generated surface carries neither in
a form that splits differently, and a snapshot that ever did would show
it as a diff, not hide it.

AN EXTENSION WITH NO SNAPSHOT IS RED, never a skip (ruled on #896). A
skip would let any extension leave its surface unguarded by tracking
nothing, which is the defect the check exists to end. TRACKED is what
counts, asked of git: a file written by `fix:pg-schema` and never
committed guards nothing in CI.

Usage:
    pg_schema.py check <raw.sql> <snapshot>   (lint:pg-schema)
    pg_schema.py write <raw.sql> <snapshot>   (fix:pg-schema)
"""

import difflib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import findings

FIX = "fix:pg-schema"
GIT = shutil.which("git") or "git"
ARGUMENTS = 3  # mode, raw, snapshot


def normalise(text: str) -> str:
    """Reduce generated SQL to its sorted statements, comments stripped.

    Returns:
        One statement per line, sorted, each ending in `;`, with a
        trailing newline; the empty string when nothing survives.

    """
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", " ", text)
    statements = []
    for raw in text.split(";"):
        collapsed = " ".join(raw.split())
        if collapsed:
            statements.append(collapsed + ";")
    if not statements:
        return ""
    return "\n".join(sorted(statements)) + "\n"


def tracked(path: str) -> bool:
    """Ask git whether the snapshot is tracked in the current repository.

    Returns:
        True when `git ls-files --error-unmatch` finds it.

    """
    result = subprocess.run(
        [GIT, "ls-files", "--error-unmatch", "--", path],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def surface(raw: str) -> str | None:
    """Read and normalise the generated schema, or say why it cannot be.

    Returns:
        The normalised surface, or None after printing the reason.

    """
    try:
        text = Path(raw).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        findings.finding("", f"cannot read the generated schema: {err}")
        return None
    generated = normalise(text)
    if not generated:
        findings.finding(
            "",
            "the generated schema normalised to nothing",
            ["an extension with no SQL surface cannot be right"],
        )
        return None
    return generated


def check(raw: str, snapshot: str) -> int:
    """Hold the generated surface to the tracked snapshot.

    Returns:
        0 when they match; 1 when the snapshot is untracked, unreadable
        or different, or the generation is unusable.

    """
    if not tracked(snapshot):
        findings.finding(
            "",
            f"no snapshot tracked at {snapshot}",
            [f"run `mise run {FIX}` to write it, read it, and commit it"],
        )
        return 1
    generated = surface(raw)
    if generated is None:
        return 1
    try:
        recorded = Path(snapshot).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        findings.finding("", f"cannot read {snapshot}: {err}")
        return 1
    if recorded == generated:
        return 0
    sys.stdout.writelines(
        difflib.unified_diff(
            recorded.splitlines(keepends=True),
            generated.splitlines(keepends=True),
            fromfile=snapshot,
            tofile="generated",
        ),
    )
    findings.finding(
        "",
        f"the SQL surface differs from {snapshot}",
        [
            "read the diff above first: it is the surface change this commit makes",
            f"if it is intended, run `mise run {FIX}` and commit the snapshot with",
            "the upgrade script it implies (sql/<ext>--<previous>--<this>.sql)",
        ],
    )
    return 1


def write(raw: str, snapshot: str) -> int:
    """Write the normalised generated surface to the snapshot path.

    Returns:
        0 once written; 1 when the generation is unusable or the path
        cannot be written.

    """
    generated = surface(raw)
    if generated is None:
        return 1
    try:
        Path(snapshot).write_text(generated, encoding="utf-8")
    except OSError as err:
        findings.finding("", f"cannot write {snapshot}: {err}")
        return 1
    print(f"{FIX}: wrote {snapshot}")
    return 0


def main(argv: list[str]) -> int:
    """Dispatch `check` or `write`.

    Returns:
        The mode's status; 1 on a usage error.

    """
    modes = {"check": check, "write": write}
    if len(argv) != ARGUMENTS or argv[0] not in modes:
        findings.finding("", "usage: pg_schema.py check|write <raw.sql> <snapshot>")
        return 1
    return modes[argv[0]](argv[1], argv[2])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
