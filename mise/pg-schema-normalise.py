#!/usr/bin/env python3
"""Normalise `cargo pgrx schema` output to the surface it declares (#896).

The body of `lint:pg-schema`'s comparison, extracted so its guard
branches have table tests (`test_pg_schema_normalise.py`).

pgrx's output is unusable as a snapshot verbatim. It annotates every
item with its source location (`-- src/lib.rs:33`), so an edit that only
shifts a line number would diff, and its own header states that item
ordering is not stable — it is driven by a dependency graph. A check
that fires on unrelated refactors is a check people learn to regenerate
without reading. So comments are stripped, whitespace is collapsed, and
statements are sorted one per line, which leaves exactly what is
guarded: the set of SQL objects and their signatures.

Ported from edtf's `.github/scripts/schema-snapshot.sh` unchanged in
effect, so edtf's tracked snapshot passes as it stands. The split on
`;` and the comment strip are lexical and know nothing of string
literals or dollar quoting; pgrx's generated surface carries neither in
a form that splits differently, and a snapshot that ever did would show
it as a diff, not hide it.

Usage: pg-schema-normalise.py <raw.sql>  (normalised surface on stdout)
"""

import re
import sys
from pathlib import Path

NAME = "lint:pg-schema"


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


def main(argv: list[str]) -> int:
    """Normalise the file named by the one argument onto stdout.

    Returns:
        0 on a non-empty surface; 1 on a usage error, an unreadable file,
        or a surface that normalised to nothing — an extension that
        declares no SQL at all is a broken generation, never a match.

    """
    if len(argv) != 1:
        print(f"{NAME}: usage: pg-schema-normalise.py <raw.sql>", file=sys.stderr)
        return 1
    try:
        text = Path(argv[0]).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        print(f"{NAME}: cannot read the generated schema: {err}", file=sys.stderr)
        return 1
    surface = normalise(text)
    if not surface:
        print(f"{NAME}: the generated schema normalised to nothing", file=sys.stderr)
        print("  an extension with no SQL surface cannot be right", file=sys.stderr)
        return 1
    sys.stdout.write(surface)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
