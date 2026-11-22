#!/usr/bin/env python3
"""Refuse a REUSE.toml that names a licence whose text is not tracked.

`lint:licence`'s half that a machine can hold (#1589). Every SPDX
identifier a tracked `REUSE.toml` uses, in any annotation, must have
its text tracked at `LICENSES/<id>.txt`. A missing one is red by name,
with the file that named it. An `OR` or `AND` expression needs every
text it names, and the identifier after `WITH` is an exception whose
text REUSE keeps in `LICENSES/` too.

Which licence a file is under is review's question: nothing in a
fixture's bytes says where it came from, so the rule that a copied
fixture carries its source's declaration is CONTRIBUTING.md's. What
this holds is that the declaration the tree makes can be read in full.

It also refuses the scaffold's `<SPDX expression>` placeholder, the
check `lint:licence` made before this helper existed (#214), and a
REUSE.toml that does not parse.

Reads the tracked paths NUL-separated on stdin (`git ls-files -z`), so
an untracked licence text never satisfies it, and reads each tracked
`REUSE.toml` from the working directory. Stdlib and offline. Exits 1
with every finding on stderr, 0 with nothing to say.
"""

import pathlib
import re
import sys

import findings as writer
from decoded import UndecodableError, as_array, as_object, is_strings, load_toml
from typed_std import group, stdin_text

PLACEHOLDER = "<SPDX expression>"

# Operators, matched in either case as SPDX 2.3 allows; parentheses and
# whitespace separate the rest. What remains is an identifier.
OPERATORS = frozenset({"AND", "OR", "WITH"})
TOKEN = re.compile(r"[^\s()]+")


def identifiers(expression: str) -> list[str]:
    """Split an SPDX expression into the identifiers it names.

    A trailing `+` (or-later) is the operator on the identifier before
    it, so `GPL-2.0+` needs `GPL-2.0`'s text.

    Returns:
        Each identifier once, in order of first appearance.

    """
    found: list[str] = []
    for found_token in TOKEN.finditer(expression):
        token = group(found_token) or ""
        if token.upper() in OPERATORS:
            continue
        name = token.removesuffix("+")
        if name and name not in found:
            found.append(name)
    return found


def expressions(config: dict[str, object]) -> list[str]:
    """Collect every licence expression a REUSE.toml declares.

    `SPDX-License-Identifier` may be one string or a list of them.

    Returns:
        The expressions, in annotation order.

    Raises:
        UndecodableError: an annotation is not that shape, naming where.

    """
    found: list[str] = []
    annotations = as_array(config.get("annotations", []), "annotations")
    for index, item in enumerate(annotations):
        where = f"annotations[{index}]"
        value = as_object(item, where).get("SPDX-License-Identifier", [])
        if isinstance(value, str):
            found.append(value)
        elif is_strings(value):
            found.extend(value)
        else:
            msg = (
                f"{where}.SPDX-License-Identifier is not a string or a list of strings"
            )
            raise UndecodableError(msg)
    return found


def findings(root: pathlib.Path, tracked: list[str]) -> list[str]:
    """Check every tracked REUSE.toml under root against tracked texts.

    Returns:
        One message per defect; empty when the tree is whole.

    """
    texts = {
        path.removeprefix("LICENSES/").removesuffix(".txt")
        for path in tracked
        if path.startswith("LICENSES/") and path.endswith(".txt")
    }
    out: list[str] = []
    for path in tracked:
        if pathlib.PurePosixPath(path).name != "REUSE.toml":
            continue
        raw = (root / path).read_text(encoding="utf-8")
        if PLACEHOLDER in raw:
            out.append(
                f"{path} still carries the scaffold's {PLACEHOLDER} "
                "placeholder — pick the licence (#214)"
            )
            continue
        try:
            config = load_toml(raw)
        except UndecodableError as error:
            out.append(f"{path} does not parse: {error}")
            continue
        try:
            declared = expressions(config)
        except UndecodableError as error:
            out.append(f"{path}: {error}")
            continue
        named: list[str] = []
        for expression in declared:
            named.extend(i for i in identifiers(expression) if i not in named)
        out.extend(
            f"{path} names {name}, and LICENSES/{name}.txt is not tracked"
            for name in named
            if name not in texts
        )
    return out


def main(argv: list[str]) -> int:
    """Read `git ls-files -z` on stdin and report.

    Returns:
        A process exit status.

    """
    if len(argv) != 1:
        writer.finding("", "usage: git ls-files -z | licence_texts.py")
        return 2
    tracked = [path for path in stdin_text().split("\0") if path]
    out = findings(pathlib.Path.cwd(), tracked)
    for message in out:
        writer.finding("", message)
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
