#!/usr/bin/env python3
"""Name every place a Python file lowers mypy's level for itself (#1604).

The belt delivers mypy's config (mise/mypy.ini) and a repository cannot
outrank it from a config file of its own. Three things in a SOURCE file
still lower it, measured on mypy 2.4.1, and mypy reports none of them:

- an inline `# mypy:` comment sets options for its file, so
  `# mypy: ignore-errors`, `allow-any-expr` or `disable-error-code`
  turns the org's checks off one file at a time;
- a bare `# type: ignore` before the first statement ignores the whole
  module, and `ignore-without-code` does not fire on it (it fires on a
  bare one anywhere else, so naming every bare one here costs nothing);
- `typing.no_type_check` skips the body it decorates, silently, even
  under `disallow_any_decorated`.

The per-line `# type: ignore[<code>]` is the one suppression left: it
names its rule, and mypy reds it once it suppresses nothing.

Comments are read with `tokenize` and names with `ast`, so a marker
inside a string is not a finding. A file that cannot be decoded,
tokenized or parsed is named as unreadable; mypy names its syntax.

    python-types-seams.py FILE...
        prints `path:line: reason` per finding; exits 1 if any.
"""

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

INLINE = re.compile(r"#\s*mypy\s*:")
BARE_IGNORE = re.compile(r"#\s*type\s*:\s*ignore(?!\s*\[)")
SKIPPERS = frozenset({"no_type_check", "no_type_check_decorator"})


def comment_findings(text: str) -> list[tuple[int, str]]:
    """Name the inline configs and bare ignores among a file's comments.

    Returns:
        (line, reason) pairs, in file order.

    """
    out: list[tuple[int, str]] = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type != tokenize.COMMENT:
            continue
        line = tok.start[0]
        if INLINE.match(tok.string):
            out.append((line, "an inline `# mypy:` comment sets options for its file"))
        elif BARE_IGNORE.search(tok.string):
            out.append((line, "`# type: ignore` names no error code"))
    return out


def name_findings(text: str) -> list[tuple[int, str]]:
    """Name every use of `no_type_check`, bare or as an attribute.

    Returns:
        (line, reason) pairs, in file order.

    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.alias):
            name = node.name
        else:
            continue
        if name in SKIPPERS:
            out.append((node.lineno, f"`{name}` skips type checking"))
    return sorted(out)


def findings(path: Path) -> list[tuple[int, str]]:
    """Read one file and name its seams.

    Returns:
        (line, reason) pairs; line 0 for a file that cannot be read.

    """
    try:
        text = path.read_bytes().decode("utf-8")
        return sorted(comment_findings(text) + name_findings(text))
    except (OSError, UnicodeDecodeError, SyntaxError, tokenize.TokenError) as e:
        return [(0, f"cannot be read: {type(e).__name__}")]


def main(argv: list[str]) -> int:
    """Name every seam in the files given.

    Returns:
        1 when any file has one, else 0.

    """
    found = False
    for arg in argv:
        for line, reason in findings(Path(arg)):
            print(f"{arg}:{line}: {reason}")
            found = True
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
