#!/usr/bin/env python3
"""Name every mutation pragma that is a real comment (#1354).

audit:mutants' Python leg holds each `# pragma: no mutate` to a reason
written at its site. It used to find them with `git grep`, which also
matched the pragma TEXT inside string literals — the canon's own first
run went red on its test file's fixtures, which plant pragmas in strings
to test the rule. The leg's pragma is a comment, never string text,
so this asks Python's own tokenizer for COMMENT tokens and nothing
else.

Usage: mutation_pragmas.py FILE... Prints one `file<TAB>line<TAB>comment`
record per pragma comment, in file order. A file the tokenizer cannot
read is named on stderr and the exit status is 1: an unreadable file is
a site nobody checked, never a skip.
"""

import sys
import tokenize
from pathlib import Path

NAME = "mutation-pragmas"


def pragmas(path: Path) -> list[tuple[int, str]]:
    """Find the mutation pragma comments in one file.

    Returns:
        (line, comment text) for every COMMENT token naming the pragma.

    """
    found = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            text = token.string
            if (
                token.type == tokenize.COMMENT
                and "pragma:" in text
                and "no mutate" in text
            ):
                found.append((token.start[0], text))
    return found


def main(argv: list[str] | None = None) -> int:
    """Print every pragma comment in the named files.

    Returns:
        0, or 1 when a file could not be tokenized.

    """
    status = 0
    for name in sys.argv[1:] if argv is None else argv:
        try:
            for line, text in pragmas(Path(name)):
                print(f"{name}\t{line}\t{text}")
        except (OSError, SyntaxError, UnicodeDecodeError, tokenize.TokenError) as bad:
            print(f"{NAME}: {name}: could not tokenize: {bad}", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
