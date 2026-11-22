#!/usr/bin/env python3
"""Find the Rust suppressions clippy cannot see (#1857).

The org's rule is that every suppression is an `#[expect]`: an expect
errors once the lint it excuses stops firing, and an allow never does.
clippy's `allow_attributes` enforces the rule for OUTER attributes
only. Two forms compile past it, measured under the belt's own clippy
command:

- an inner attribute that allows, plain or under `cfg_attr`
  (`#![allow(…)]`, `#![cfg_attr(test, allow(…))]`), for any lint;
- an `allow` level in a manifest's `[lints.*]` or
  `[workspace.lints.*]` tables, string or table form. A `[lints.rust]`
  allow silences rustc's lint today, because `-D warnings` raises
  warn-level lints and leaves an allowed one alone; a `[lints.clippy]`
  allow loses to the belt's later `-D` flags today and would win the
  day the flag order changed. One rule for every tool's table.

Reads tracked paths, one per line, on stdin: each `Cargo.toml` is
decoded with `tomllib` (through `decoded`, never grepped), every other
path is read as Rust source. Prints one finding per line to stderr,
then the remedy, and exits 1; prints nothing and exits 0 when clean.
A tracked path missing from the working tree is not a build input and
is passed over, as the task's other per-file checks pass it over.

A crate-level allow or expect of a whole clippy GROUP is refused before
any of that, line by line, with its own message: the grep `lint:rust`
ran in its body until the body's line debt asked for it here (#1857).

The source scan is a small lexer, not a line grep: rustfmt breaks a
long `#![cfg_attr(…)]` across lines, and a doc comment, a string or a
block comment can hold the text `#![allow(` without being an
attribute. An inner `#![expect(…)]` stays legal everywhere.
"""

import re
import sys
from pathlib import Path

import findings as writer
from decoded import UndecodableError, is_object, load_toml
from typed_std import stdin_text

ALLOW = re.compile(r"\ballow\s*\(")
INNER = re.compile(r"#\s*!\s*\[")
RAW_OPEN = re.compile(r'(#*)"')
IDENT = re.compile(r"[A-Za-z0-9_]+")
MANIFEST = "Cargo.toml"
# A crate-level allow or expect of a whole clippy group: line by line,
# the grep this replaced, and refused first with its own message.
GROUPS = (
    "all|pedantic|nursery|cargo|restriction|style|complexity|perf"
    "|correctness|suspicious"
)
GROUP = re.compile(
    rf"^[^\S\n]*#!\[(allow|expect)\([^]\n]*clippy::({GROUPS})\b", re.MULTILINE
)
GROUP_MESSAGE = (
    "  a group allow silences every level this task sets and the gate still goes green",
    "  suppress one lint at the narrowest scope instead, with a reason:",
    '    #[expect(clippy::the_lint, reason = "why this code is right as written")]',
    "  an expect fails once it stops being needed; an allow rots silently",
)
REMEDY = (
    "lint:rust: a suppression clippy cannot see is not the org's exception mechanism",
    "  suppress one lint at the narrowest scope instead, with a reason:",
    '    #[expect(the_lint, reason = "why this code is right as written")]',
    '    #![expect(the_lint, reason = "…")] where a whole module needs it',
    "  an expect fails once it stops being needed; an allow rots silently",
)


def _skip_block_comment(text: str, i: int) -> int:
    """Skip a block comment, nested ones included, opening at `i`.

    Returns:
        The index after its close, or the end of the text.

    """
    depth = 0
    while i < len(text):
        if text.startswith("/*", i):
            depth += 1
            i += 2
        elif text.startswith("*/", i):
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    return i


def _skip_string(text: str, i: int) -> int:
    """Skip a quoted string literal, escapes honoured, opening at `i`.

    Returns:
        The index after its closing quote, or the end of the text.

    """
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
        elif text[i] == '"':
            return i + 1
        else:
            i += 1
    return len(text)


def _skip_char(text: str, i: int) -> int:
    """Skip a char literal at `i`, or only the quote of a lifetime.

    Returns:
        The index after the literal, or after the quote for a lifetime
        or label, whose name is then read as an identifier.

    """
    if text.startswith("\\", i + 1):
        close = text.find("'", i + 3)
        return len(text) if close < 0 else close + 1
    if text.startswith("'", i + 2):
        return i + 3
    return i + 1


def _skip_ident(text: str, i: int) -> int:
    """Skip an identifier at `i`, and the raw string it may open.

    Returns:
        The index after the identifier, or after the raw string when
        the identifier is `r`, `br` or `cr` followed by `#*"`.

    """
    end = i + len(_ident_at(text, i))
    if text[i:end] in {"r", "br", "cr"}:
        opened = RAW_OPEN.match(text, end)
        if opened:
            close = '"' + opened.group(1)
            found = text.find(close, opened.end())
            return len(text) if found < 0 else found + len(close)
    return end


def _ident_at(text: str, i: int) -> str:
    """Read the identifier starting at `i`.

    Returns:
        Its text, empty when none starts there.

    """
    found = IDENT.match(text, i)
    return "" if found is None else found.group(0)


def _skip(text: str, i: int) -> int | None:
    """Skip whatever at `i` is not code: a comment, a literal, a word.

    Returns:
        The index after it, or None when `i` starts code punctuation.

    """
    if text.startswith("//", i):
        close = text.find("\n", i)
        return len(text) if close < 0 else close
    if text.startswith("/*", i):
        return _skip_block_comment(text, i)
    char = text[i]
    if char == '"':
        return _skip_string(text, i)
    if char == "'":
        return _skip_char(text, i)
    if char.isalnum() or char == "_":
        return _skip_ident(text, i)
    return None


def _attribute(text: str, i: int) -> tuple[int, str]:
    """Read the inner attribute whose `[` is just before `i`.

    Returns:
        The index after its closing `]`, and its code with every
        comment and literal removed, so `allow(` inside a reason or a
        doc string is never read as the attribute's own.

    """
    depth = 1
    code: list[str] = []
    while i < len(text) and depth:
        skipped = _skip(text, i)
        if skipped is not None:
            word = _ident_at(text, i)
            if word and skipped == i + len(word):
                code.append(word)
            code.append(" ")
            i = skipped
            continue
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
        code.append(text[i])
        i += 1
    return i, "".join(code)


def inner_allows(text: str) -> list[tuple[int, str]]:
    """Find every inner attribute that allows, in source order.

    Returns:
        (line, attribute source with whitespace collapsed) for each
        `#![…]` whose attribute tree holds `allow(`.

    """
    found: list[tuple[int, str]] = []
    i = 0
    while i < len(text):
        opened = INNER.match(text, i) if text[i] == "#" else None
        if opened:
            end, code = _attribute(text, opened.end())
            if ALLOW.search(code):
                line = text.count("\n", 0, i) + 1
                found.append((line, " ".join(text[i:end].split())))
            i = end
            continue
        skipped = _skip(text, i)
        i = i + 1 if skipped is None else skipped
    return found


def manifest_allows(manifest: dict[str, object]) -> list[str]:
    """Find every `allow` level in a manifest's lint tables.

    Returns:
        `[table.tool] lint = "allow"` for each: `[lints.*]` first,
        then `[workspace.lints.*]`, each in document order. A
        tool table's lint is either a level string or a table whose
        `level` is one; `[lints] workspace = true` is no tool table.

    """
    found: list[str] = []
    workspace = manifest.get("workspace")
    tables = (
        ("lints", manifest.get("lints")),
        ("workspace.lints", workspace.get("lints") if is_object(workspace) else None),
    )
    for prefix, lints in tables:
        if not is_object(lints):
            continue
        for tool, entries in lints.items():
            if not is_object(entries):
                continue
            for lint, value in entries.items():
                level = value.get("level") if is_object(value) else value
                if level == "allow":
                    found.append(f'[{prefix}.{tool}] {lint} = "allow"')
    return found


def findings(path: Path) -> list[str]:
    """Check one tracked path.

    Returns:
        One `path:where: what` line per suppression clippy cannot see.

    Raises:
        UndecodableError: the file is not UTF-8, or a manifest is not
            TOML.

    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return []
    if path.name == MANIFEST:
        return [f"{path}: {key}" for key in manifest_allows(load_toml(raw))]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        msg = "not UTF-8"
        raise UndecodableError(msg) from None
    return [f"{path}:{line}: {attr}" for line, attr in inner_allows(text)]


def group_suppressed(path: Path) -> bool:
    """Say whether a source suppresses a whole clippy group crate-wide.

    Returns:
        True when a line opens `#![allow(` or `#![expect(` naming a
        group; False for a manifest or a path not in the tree.

    """
    if path.name == MANIFEST:
        return False
    try:
        text = path.read_bytes().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return False
    return GROUP.search(text) is not None


def main() -> int:
    """Check every path on stdin and report what clippy cannot see.

    Returns:
        A process exit status: 0 clean, 1 on any finding or refusal.

    """
    names = [name for name in stdin_text().splitlines() if name]
    blanket = [name for name in names if group_suppressed(Path(name))]
    if blanket:
        writer.finding(
            "",
            "crate-level suppression of a whole clippy group:"
            + "".join(f" {name}" for name in blanket),
            [line.removeprefix("  ") for line in GROUP_MESSAGE],
        )
        return 1
    found: list[str] = []
    for name in names:
        try:
            found.extend(findings(Path(name)))
        except UndecodableError as error:
            writer.finding(name, str(error))
            return 1
    if not found:
        return 0
    writer.finding("", "suppression clippy's allow_attributes cannot see:", found)
    head, *rest = REMEDY
    writer.finding(
        "",
        head.removeprefix("lint:rust: "),
        [line.removeprefix("  ") for line in rest],
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
