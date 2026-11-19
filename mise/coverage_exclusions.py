#!/usr/bin/env python3
"""Refuse coverage-exclusion markers in tracked source (#1522).

The org's testing standard (CONTRIBUTING.md, "No number can be
bought") forbids excluding code from coverage: an excluded line is a
number bought, not earned. This is the rule's enforcement, for the
languages the belt measures. There is no allow-list and no per-site
escape, because the standard has none.

Reads newline-separated paths on stdin and reports, per finding, the
file, the line and the rule. Exits 1 on any finding.

Where a marker counts, by language:

- Python: a comment token only, read with `tokenize`, so a marker
  quoted in a string literal (a test fixture's text) is not a finding.
  A file that does not tokenize is read line by line instead, with
  quoted text removed first.
- Rust: an attribute, `#[...]` or `#![...]`, at the start of a line.
- TypeScript, JavaScript and Go: a comment, after quoted text on the
  line is removed.
"""

import io
import re
import sys
import tokenize
from pathlib import Path
from typing import NamedTuple

PYTHON = frozenset({".py", ".pyi"})
RUST = frozenset({".rs"})
SCRIPT = frozenset({".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"})
GO = frozenset({".go"})


class Rule(NamedTuple):
    """One kind of marker: its name and the pattern that finds it."""

    name: str
    pattern: re.Pattern[str]


# coverage.py's own default exclusion patterns, for lines and branches.
PYTHON_RULES = (
    Rule(
        "python: pragma no cover",
        re.compile(r"#\s*pragma[:\s]?\s*no\s*cover", re.IGNORECASE),
    ),
    Rule(
        "python: pragma no branch",
        re.compile(r"#\s*pragma[:\s]?\s*no\s*branch", re.IGNORECASE),
    ),
)
RUST_RULES = (
    Rule("rust: coverage(off)", re.compile(r"^\s*#!?\[.*\bcoverage\s*\(\s*off\s*\)")),
    Rule(
        "rust: cfg(coverage)",
        re.compile(r"^\s*#!?\[\s*cfg(?:_attr)?\s*\(.*\bcoverage\b"),
    ),
)
SCRIPT_RULES = (
    Rule("script: istanbul ignore", re.compile(r"(?://|/\*)\s*istanbul\s+ignore\b")),
    Rule("script: c8 ignore", re.compile(r"(?://|/\*)\s*c8\s+ignore\b")),
    Rule("script: v8 ignore", re.compile(r"(?://|/\*)\s*v8\s+ignore\b")),
)
GO_RULES = (
    Rule(
        "go: build constraint on coverage",
        re.compile(r"^\s*//\s*(?:go:build|\+build)\b.*\bcoverage\b"),
    ),
    Rule("go: coverage:ignore", re.compile(r"//\s*coverage:ignore\b")),
)

# Quoted text on one line: double, single and backtick quotes, with
# backslash escapes. Removed before a line is matched, so a marker in a
# string is not a marker.
QUOTED = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`')


class Finding(NamedTuple):
    """One marker, where it is and what it is."""

    path: str
    line: int
    rule: str

    def __str__(self) -> str:
        """Render as `path:line: rule`.

        Returns:
            The finding's one line.

        """
        return f"{self.path}:{self.line}: {self.rule}"


def match(rules: tuple[Rule, ...], text: str) -> str | None:
    """Name the first rule `text` breaks.

    Returns:
        The rule's name, or None.

    """
    for rule in rules:
        if rule.pattern.search(text):
            return rule.name
    return None


def by_line(path: str, text: str, rules: tuple[Rule, ...]) -> list[Finding]:
    """Match every line, quoted text removed.

    Returns:
        The findings, in line order.

    """
    out: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        name = match(rules, QUOTED.sub('""', line))
        if name is not None:
            out.append(Finding(path, number, name))
    return out


def python(path: str, text: str) -> list[Finding]:
    """Match Python comment tokens only.

    Returns:
        The findings, in line order.

    """
    out: list[Finding] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                name = match(PYTHON_RULES, token.string)
                if name is not None:
                    out.append(Finding(path, token.start[0], name))
    except tokenize.TokenError, SyntaxError:
        return by_line(path, text, PYTHON_RULES)
    return out


def scan(path: str) -> list[Finding]:
    """Read one file by its language.

    Returns:
        The findings, or none for a language this does not read.

    """
    suffix = Path(path).suffix
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError, IsADirectoryError:
        return []
    if suffix in PYTHON:
        return python(path, text)
    if suffix in RUST:
        return by_line(path, text, RUST_RULES)
    if suffix in SCRIPT:
        return by_line(path, text, SCRIPT_RULES)
    if suffix in GO:
        return by_line(path, text, GO_RULES)
    return []


def main() -> None:
    """Scan the paths on stdin and report."""
    paths = [p for p in sys.stdin.read().splitlines() if p]
    found = [f for p in paths for f in scan(p)]
    for finding in found:
        print(finding, file=sys.stderr)
    if found:
        print(
            f"lint:coverage-exclusions: {len(found)} coverage exclusion(s).\n"
            '  The testing standard forbids them (CONTRIBUTING.md, "No number\n'
            '  can be bought"): an excluded line is a bought number. Test the\n'
            "  code, or delete code no test can reach. There is no allow-list.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"lint:coverage-exclusions: {len(paths)} file(s), no coverage exclusions")


if __name__ == "__main__":
    main()
