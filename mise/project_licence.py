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

import findings
import tracked
from decoded import UndecodableError, as_array, as_object, is_strings, load_toml

EXPECTED_ARGC = 2
DECLARE = "  say which is the project's in .badge-states: 'licence <SPDX expression>'"


def expressions(config: dict[str, object]) -> list[str]:
    """Collect each distinct licence expression the annotations name.

    `SPDX-License-Identifier` may be one string or a list of them.

    Returns:
        The expressions, sorted, each once.

    Raises:
        UndecodableError: an annotation is not that shape, naming where.

    """
    found: set[str] = set()
    annotations = as_array(config.get("annotations", []), "annotations")
    for index, item in enumerate(annotations):
        where = f"annotations[{index}]"
        value = as_object(item, where).get("SPDX-License-Identifier", [])
        if isinstance(value, str):
            found.add(value)
        elif is_strings(value):
            found.update(value)
        else:
            msg = (
                f"{where}.SPDX-License-Identifier is not a string or a list of strings"
            )
            raise UndecodableError(msg)
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
        findings.finding("", "usage: project_licence.py <caller>")
        return 2
    caller = argv[1]
    try:
        config = load_toml(pathlib.Path("REUSE.toml").read_text(encoding="utf-8"))
    except UndecodableError as error:
        findings.finding("", f"REUSE.toml does not parse: {error}")
        return 1
    try:
        names = expressions(config)
    except UndecodableError as error:
        findings.finding("REUSE.toml", str(error))
        return 1
    # A declaration counts when the repository tracks it (#2013); the
    # tree is the working directory's, whatever GIT_DIR a hook carries.
    states = pathlib.Path(".badge-states")
    tracked_states = tracked.has(str(states), root=pathlib.Path.cwd())
    text = states.read_text(encoding="utf-8") if tracked_states else ""
    choice, errors = decide(caller, names, declared(text))
    if errors:
        head, *rest = errors
        findings.finding(
            "",
            head.removeprefix(f"{caller}: "),
            [line.removeprefix("  ") for line in rest],
        )
        return 1
    if choice:
        print(choice)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
