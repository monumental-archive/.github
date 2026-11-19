#!/usr/bin/env python3
"""Print the project's licence expression, the one both derivations state.

`fix:badges` renders it on the licence shield and `fix:citation` writes
it into CITATION.cff. Both read it here, so the two cannot disagree
(#1589).

`REUSE.toml`'s annotations name every expression the tree's files are
under. Where they all name one, that is the project's licence. Where
they name several (the canon's own 0BSD beside edtf's MIT OR Apache-2.0
on the fixtures copied from it), nothing here can know which is the
project's, so the repository says so once in `.badge-states` as
`licence <SPDX expression>`, and that line must name an expression
`REUSE.toml` declares: a licence no file is under is not the project's.

Usage: `project_licence.py <caller>`, run in the repository root. The
caller (`fix:badges`, `fix:citation`) prefixes each message. Prints the
expression and exits 0; prints nothing and exits 0 when `REUSE.toml`
names none, which each caller reports in its own words; exits 1 with
the reason on stderr otherwise.
"""

import pathlib
import sys
import tomllib

EXPECTED_ARGC = 2
DECLARE = "  say which is the project's in .badge-states: 'licence <SPDX expression>'"


def expressions(config: dict) -> list[str]:
    """Collect each distinct licence expression the annotations name.

    `SPDX-License-Identifier` may be one string or a list of them.

    Returns:
        The expressions, sorted, each once.

    """
    found: set[str] = set()
    for annotation in config.get("annotations", []):
        value = annotation.get("SPDX-License-Identifier", [])
        found.update([value] if isinstance(value, str) else value)
    return sorted(found)


def declared(states: str) -> str:
    """Read the `licence` line of a `.badge-states` file.

    Returns:
        The rest of the first `licence` line, or "" when there is none.

    """
    for line in states.splitlines():
        key, _, rest = line.strip().partition(" ")
        if key == "licence":
            return rest.strip()
    return ""


def decide(caller: str, names: list[str], declaration: str) -> tuple[str, list[str]]:
    """Choose the project's expression.

    Returns:
        The expression ("" for none), and the error lines (empty when
        the choice stands).

    """
    listed = [f"  {name}" for name in names]
    if declaration:
        if declaration in names:
            return declaration, []
        head = (
            f"{caller}: .badge-states declares licence {declaration},"
            " and REUSE.toml names no such expression:"
        )
        return "", [head, *listed]
    if len(names) > 1:
        return "", [
            f"{caller}: REUSE.toml names several licence expressions:",
            *listed,
            DECLARE,
        ]
    return (names[0] if names else ""), []


def main(argv: list[str]) -> int:
    """Print the project's licence expression for the caller.

    Returns:
        A process exit status.

    """
    if len(argv) != EXPECTED_ARGC:
        print("usage: project_licence.py <caller>", file=sys.stderr)
        return 2
    caller = argv[1]
    try:
        config = tomllib.loads(pathlib.Path("REUSE.toml").read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        print(f"{caller}: REUSE.toml does not parse: {error}", file=sys.stderr)
        return 1
    states = pathlib.Path(".badge-states")
    text = states.read_text(encoding="utf-8") if states.is_file() else ""
    choice, errors = decide(caller, expressions(config), declared(text))
    for line in errors:
        print(line, file=sys.stderr)
    if errors:
        return 1
    if choice:
        print(choice)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
