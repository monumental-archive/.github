#!/usr/bin/env python3
"""A repository that ships a measured language tracks its adoption files (#1910).

Mutation testing and the coverage floor are adopted by a tracked file,
and a leg with no file skips clean, by name: `audit:mutants` reads
`.cargo/mutants.toml`, `.gomuignore`, `cosmic-ray.toml` or
`stryker.config.json`, and `coverage:check` reads `.coverage-floor`.
Opt-in by file made absence green, so a repository could ship a
language with no mutation run and no floor. This closes that door:
for each language the belt measures, a repository that ships it must
track that language's mutation file and a `.coverage-floor`.

What a tracked adoption file must contain stays each leg's business;
this rule only asks that the file is there.

The tree says what a repository ships, by file name alone (no file is
opened), from the names the belt already keys on:

- Rust: a `Cargo.toml` at any depth, outside `vendor/` and `fuzz/`
  (the fuzz workspace is the fuzz leg's, not a shipped crate).
- Go: `go.mod` at the root.
- Python: a `*.py` outside `vendor/` whose name is not `test_*.py`,
  `prop_*.py` or `conftest.py`.
- TypeScript: a `*.ts`, `*.tsx`, `*.mts` or `*.cts` outside `vendor/`
  that is not a declaration (`*.d.ts`, and its `.d.mts`/`.d.cts`
  siblings), `*.test.*` or `*.spec.*`.

Each row of `LANGUAGES` names the mutation leg whose guard it mirrors,
and mise/test_testing_adoption.py reads that guard out of the leg, so the
rule and the leg cannot name different files.

Usage: `git ls-files | testing_adoption.py`, from the repository's root.
Exits 0 when every shipped language is adopted (or none is shipped),
1 with one line per missing file otherwise.
"""

import sys
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import findings as writer
import typed_std

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

NAME = "lint:test-adoption"
FLOOR = ".coverage-floor"
MUTATION_REMEDY = (
    "tracking it adopts the leg; docs/tooling-verdicts.md says what it holds"
)
FLOOR_REMEDY = "mise run coverage:adopt writes it"
VENDOR = "vendor/"
FUZZ = "fuzz/"
TS_SUFFIXES = (".ts", ".tsx", ".mts", ".cts")
TS_DECLARATIONS = (".d.ts", ".d.mts", ".d.cts")


def _rust(path: str) -> bool:
    """Say whether a tracked path makes the repository ship Rust.

    Returns:
        True for a `Cargo.toml` outside `vendor/` and `fuzz/`.

    """
    return (
        PurePosixPath(path).name == "Cargo.toml"
        and not path.startswith(VENDOR)
        and not path.startswith(FUZZ)
    )


def _go(path: str) -> bool:
    """Say whether a tracked path makes the repository ship Go.

    Returns:
        True for `go.mod` at the root.

    """
    return path == "go.mod"


def _python(path: str) -> bool:
    """Say whether a tracked path makes the repository ship Python.

    Returns:
        True for a `*.py` outside `vendor/` that is not a test, a
        property or a `conftest.py`.

    """
    name = PurePosixPath(path).name
    return (
        name.endswith(".py")
        and not path.startswith(VENDOR)
        and not name.startswith(("test_", "prop_"))
        and name != "conftest.py"
    )


def _typescript(path: str) -> bool:
    """Say whether a tracked path makes the repository ship TypeScript.

    Returns:
        True for a TypeScript source outside `vendor/` that is neither a
        declaration nor a test or spec file.

    """
    name = PurePosixPath(path).name
    return (
        name.endswith(TS_SUFFIXES)
        and not name.endswith(TS_DECLARATIONS)
        and ".test." not in name
        and ".spec." not in name
        and not path.startswith(VENDOR)
    )


def _shallowest(path: str) -> tuple[int, str]:
    """Order paths by depth, then by name.

    Returns:
        The sort key: the path's `/` count, then the path.

    """
    return path.count("/"), path


@dataclass(frozen=True)
class Language:
    """One measured language: what ships it, and what adopts its mutation leg."""

    name: str
    ships: Callable[[str], bool]
    mutation: str
    leg: str


LANGUAGES = (
    Language("Rust", _rust, ".cargo/mutants.toml", "mutants-rust.sh"),
    Language("Go", _go, ".gomuignore", "mutants-go.sh"),
    Language("Python", _python, "cosmic-ray.toml", "mutants-python.sh"),
    Language("TypeScript", _typescript, "stryker.config.json", "mutants-typescript.sh"),
)


def shipped(paths: Iterable[str]) -> list[tuple[Language, str]]:
    """Name each measured language the tree ships, with one file that ships it.

    The file shown is the shallowest, then the first by name, so a root
    `Cargo.toml` is named over a member crate's.

    Returns:
        (language, file) pairs, in `LANGUAGES` order.

    """
    tracked = list(paths)
    found: list[tuple[Language, str]] = []
    for language in LANGUAGES:
        hits = [path for path in tracked if language.ships(path)]
        if hits:
            found.append((language, min(hits, key=_shallowest)))
    return found


def findings(paths: Iterable[str]) -> list[str]:
    """List every adoption file a shipped language is missing.

    Returns:
        One line per missing file: each language's mutation file, then
        the floor once, naming every language that owes it.

    """
    tracked = set(paths)
    ships = shipped(tracked)
    lines = [
        f"{NAME}: ships {language.name} ({where}) but tracks no "
        f"{language.mutation} — {MUTATION_REMEDY}"
        for language, where in ships
        if language.mutation not in tracked
    ]
    if ships and FLOOR not in tracked:
        owing = ", ".join(f"{language.name} ({where})" for language, where in ships)
        lines.append(f"{NAME}: ships {owing} but tracks no {FLOOR} — {FLOOR_REMEDY}")
    return lines


def main() -> int:
    """Judge the tracked tree read from standard input.

    Returns:
        0 when every shipped language is adopted, 1 when a file is missing.

    """
    paths = [line for line in typed_std.stdin_text().split("\n") if line]
    ships = shipped(paths)
    if not ships:
        print(f"{NAME}: ships none of Rust, Go, Python, TypeScript, skipped")
        return 0
    missing = findings(paths)
    if missing:
        for line in missing:
            writer.finding("", line.removeprefix(f"{NAME}: "))
        return 1
    names = ", ".join(language.name for language, _ in ships)
    print(f"{NAME}: ok ({names} adopted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
