#!/usr/bin/env python3
"""Hold every EXIT trap to the completion guard (#1233).

The defect, measured on macOS's `/bin/bash` 3.2.57: a script that sets
an EXIT trap and then dies on an unset variable exits 0.

    set -eu; trap '<cleanup>' EXIT; echo "$nope"      -> exit 0
    set -eu; trap '<cleanup>' EXIT; : "${nope:?}"     -> exit 0
    set -eu; trap '<cleanup>' EXIT; false             -> exit 1
    set -eu; echo "$nope"                             -> exit 1

Inside the trap `$?` is already 0, so saving it first does not help.
Runners carry bash 5, where all four are non-zero: CI is right and the
local run is wrong, which is the direction that lets a script reach a
release untested. It bit `settings/repo-baseline.sh check`, which
reported success locally while dying half-way through.

The remedy is that a script with an EXIT trap marks its own completion
and the trap fails a run that never reached the mark — `SHAPE` below,
printed with every finding so the message hands over the lines to paste.
An explicit non-zero exit keeps its code; an early `exit 0` sets the
mark first, or it reads as the death it cannot be told apart from.

Two rules, both facts about the text, read here because
`lint:bash-portability` is the lint that holds scripts to bash 3.2:

  - every `trap ... EXIT` carries the guard, verbatim around its
    cleanup;
  - a script or task body with an EXIT trap ends on `org_done=1`.

Shell files are read whole. Task bodies are read out of mise configs by
`belt-shell.py`'s own decoder, the one reader of a body the belt keeps;
each body is its own script, so its mark is its own last statement.
Workflow `run:` steps are not read: they run only on a runner's bash 5,
where the defect does not exist.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

_SPEC = importlib.util.spec_from_file_location(
    "belt_shell",
    Path(__file__).resolve().with_name("belt-shell.py"),
)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - delivered beside it
    msg = "exit-trap: belt-shell.py did not arrive beside this file"
    raise SystemExit(msg)
_BELT_SHELL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BELT_SHELL)

# Any trap that names EXIT among its signals. `trap - EXIT` resets the
# handler rather than setting one, and owes nothing.
TRAP_RE = re.compile(r"^\s*trap\s.*\bEXIT\b")
RESET_RE = re.compile(r"^\s*trap\s+-\s")
# The guard, verbatim around a cleanup of the script's own.
GUARD_RE = re.compile(
    r"^\s*trap 'rc=\$\?; .+; "
    r'\[\[ -n \$\{org_done:-\} \|\| \$\{rc\} -ne 0 \]\] \|\| rc=1; exit "\$\{rc\}"'
    r"' EXIT\s*$",
)
# The mark, at top level, optionally followed by a comment.
MARK_RE = re.compile(r"^org_done=1(?:\s+#.*)?\s*$")
COMMENT_RE = re.compile(r"^\s*#")

# The guard as a script carries it, for the message and the table tests.
GUARD = (
    "trap 'rc=$?; {cleanup}; "
    '[[ -n ${{org_done:-}} || ${{rc}} -ne 0 ]] || rc=1; exit "${{rc}}"'
    "' EXIT"
)
# shellcheck reads the references inside a trap string and not the
# assignment, so the guard reports SC2154 on `rc` without this (measured
# on shellcheck 0.11.0 at the org's `enable=all`).
DIRECTIVE = "# shellcheck disable=SC2154  # rc is assigned inside the trap string"
MARK = "org_done=1"
SHAPE = f"""\
  on bash 3.2 (macOS) a script with an EXIT trap that dies on an unset
  variable exits 0 (#1233). The trap fails a run that never reached
  its last statement, and an explicit non-zero exit keeps its code:

    {DIRECTIVE}
    {GUARD.format(cleanup="<cleanup>")}
    ...
    {MARK}

  An early `exit 0` after the trap sets {MARK} first."""


def findings(path: str, base: int, what: str, source: str) -> Iterator[str]:
    """Judge one script: its EXIT traps, then its last statement.

    `base` is the file line before the script's first, so a finding in a
    task body points at the line of the config that holds it.

    Yields:
        One message per finding, as `path:line: [what]problem`.

    """
    lines = source.splitlines()
    traps = 0
    last = 0
    for lineno, line in enumerate(lines, start=1):
        if not line.strip() or COMMENT_RE.match(line):
            continue
        last = lineno
        if not TRAP_RE.match(line) or RESET_RE.match(line):
            continue
        traps += 1
        if not GUARD_RE.match(line):
            yield (
                f"{path}:{base + lineno}: {what}EXIT trap without the completion guard"
            )
    if traps and not MARK_RE.match(lines[last - 1]):
        yield (
            f"{path}:{base + last}: {what}has an EXIT trap and its last statement"
            f" is not {MARK}"
        )


def scripts(path: Path) -> Iterator[tuple[int, str, str]]:
    """Every script one file contributes, with where to point at it.

    Yields:
        (base line, label, source): the whole file for shell, each task
        body for a config. A `'''` body starts on the line after `run =`.

    """
    if path.suffix == ".toml":
        for body in _BELT_SHELL.read(path).bodies:
            base = body.run_line if body.delim else body.run_line - 1
            yield base, f"task {body.name}: ", body.source
    else:
        yield 0, "", path.read_text(encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Check every shell file and mise config named.

    Returns:
        A process exit status.

    """
    parser = argparse.ArgumentParser(description="hold EXIT traps to the guard")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)

    found: list[str] = []
    checked = 0
    for name in args.files:
        for base, what, source in scripts(Path(name)):
            checked += 1
            found.extend(findings(name, base, what, source))
    if found:
        for line in found:
            print(f"exit-trap: {line}", file=sys.stderr)
        print(SHAPE, file=sys.stderr)
        return 1
    print(f"exit-trap: {checked} script(s) and task body(ies), every EXIT trap guarded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
