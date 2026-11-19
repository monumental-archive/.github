#!/usr/bin/env python3
r"""Rename every hyphenated Python file to its module name (#1634).

A file named `caller-pins.py` is not a module name, so nothing can
import it and a type checker sees whatever loaded it as untyped. This
renames each tracked `*.py` whose file name holds a hyphen to the same
name with underscores, with `git mv`, then rewrites every reference to
it in the tracked tree by exact file name: `caller-pins.py` becomes
`caller_pins.py` wherever it stands as a whole file name, and nothing
else changes. A task name, a job name or a message that shares the stem
(`lint:caller-pins`, `publish-crates:`) is not a file name and is left.
A name spelled as a regex, `caller-pins\.py`, is the same file name and
moves too (release/test_job_installs.py keys its helpers that way).

The list is derived from the tree on every run, so the run is the same
on any base: a rebase is `git reset` to the new base and one run. A
second run finds no hyphenated file and changes nothing.

Run from the root of the checkout:

    python3 mise/rename_helpers.py

Not delivered and not kept: it is deleted in the pull request that ran
it, and that diff is its record.
"""

from __future__ import annotations

import re
import shutil

# ruff: ignore[suspicious-subprocess-import]
import subprocess
import sys
from pathlib import Path

GIT = shutil.which("git") or "git"

# Never rewritten: this script and its row name the old file names as
# data, and the release notes record what each release shipped.
KEEP = frozenset({
    "mise/rename_helpers.py",
    "mise/test_rename_helpers.py",
    "CHANGELOG.md",
})


def tracked(root: Path) -> list[str]:
    """List the tracked files under root.

    Returns:
        Their paths, relative to root.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    done = subprocess.run(
        [GIT, "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return [p for p in done.stdout.decode().split("\0") if p]


def plan(paths: list[str]) -> dict[str, str]:
    """Pair each hyphenated Python file with its module-name path.

    Returns:
        The old path mapped to the new one, the directory unchanged.

    """
    moves: dict[str, str] = {}
    for path in paths:
        old = Path(path)
        if old.suffix == ".py" and "-" in old.name:
            moves[path] = str(old.with_name(old.name.replace("-", "_")))
    return moves


def pattern(names: list[str]) -> re.Pattern[str]:
    """Match any of the old file names standing as a whole file name.

    A name preceded by a word character, a dot or a hyphen is part of a
    longer name (`pgrx-inventory-plan.py` is not `inventory-plan.py`);
    one followed by a word character is another extension (`.pyi`).

    Returns:
        The compiled pattern; longest names first, so none shadows one.

    """
    alternation = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![\w.-])(?:{alternation})(?!\w)")


def rewrite(text: str, renamed: dict[str, str]) -> str:
    """Replace every whole old file name in text with its new one.

    Returns:
        The rewritten text (the same text when nothing matched).

    """
    if not renamed:
        return text
    found = pattern(list(renamed))
    return found.sub(lambda m: renamed[m.group(0)], text)


def run(root: Path) -> tuple[int, int]:
    """Rename the hyphenated files under root and rewrite references.

    Returns:
        How many files were renamed, and how many were rewritten.

    Raises:
        SystemExit: a new name is already taken.

    """
    moves = plan(tracked(root))
    taken = set(tracked(root))
    for old, new in moves.items():
        if new in taken or (root / new).exists():
            msg = f"rename_helpers: {old} cannot become {new}: it exists"
            raise SystemExit(msg)
    for old, new in moves.items():
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run([GIT, "mv", old, new], cwd=root, check=True)
    renamed = {Path(o).name: Path(n).name for o, n in moves.items()}
    # The regex spelling of each name: its dot escaped.
    renamed |= {
        o.replace(".py", r"\.py"): n.replace(".py", r"\.py") for o, n in renamed.items()
    }
    changed = 0
    for path in tracked(root):
        if path in KEEP:
            continue
        target = root / path
        if target.is_symlink() or not target.is_file():
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        new_text = rewrite(text, renamed)
        if new_text != text:
            target.write_text(new_text, encoding="utf-8")
            changed += 1
    return len(moves), changed


def main() -> None:
    """Run against the working directory and say what moved."""
    moved, changed = run(Path.cwd())
    sys.stdout.write(
        f"rename_helpers: renamed {moved} files, rewrote {changed} files\n"
    )


if __name__ == "__main__":
    main()
