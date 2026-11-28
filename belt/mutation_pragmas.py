"""Hold every mutation pragma to the line form and its reason (#1354, #2177).

audit:mutants' Python leg holds each `# pragma: no mutate` to a reason
written at its site, on the line it excludes:

    x = 3  # pragma: no mutate -- the fixture's width, no caller

Only that line form exists. mutmut 3.8.1 also reads a region: `# pragma:
no mutate block` (the statement it trails, with its body), and the
context opened by `start` and closed by `end`
(mutmut/mutation/pragma_handling.py). Each of those is red by name. A
region excludes whole AST nodes, so a line added inside one later is
never mutated and never gets a reason; a line pragma covers its line
and nothing else.
The word after `no mutate` is read as mutmut reads it (the first word,
before any comma, after a `:`).

The pragma is a comment, never string text: the tokenizer's COMMENT
tokens are read and nothing else, so a test file's fixtures, which
plant pragmas in strings to test this rule, are not sites. (The leg
first found them with `git grep`, and went red on its own fixtures.)

Usage: belt mutation-pragmas FILE... Names every refused pragma and
exits 1; a file the tokenizer cannot read is named and the exit status
is 1, since an unreadable file is a site nobody checked, never a skip.
"""

from __future__ import annotations

import tokenize
from pathlib import Path

from belt import findings

# What mutmut reads as a region rather than one line: a region excludes
# whole AST nodes, so a line added inside one later is never mutated.
REGIONS = ("block", "start", "end")
REASON = " -- "


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


def word(comment: str) -> str:
    """Read the word mutmut reads after `no mutate`.

    Returns:
        The first word before any comma, `--` for a reason, or "".

    """
    rest = comment.partition("no mutate")[2].strip()
    words = rest.lstrip(": ").split(",", 1)[0].split()
    return words[0] if words else ""


def refusal(comment: str) -> str | None:
    """Judge one pragma comment.

    Returns:
        Why it is refused, or None for the line form with its reason.

    """
    said = word(comment)
    if said in REGIONS:
        return (
            f"# pragma: no mutate {said} — only the line form exists;"
            " mark each line, with its reason"
        )
    rest = comment.partition("no mutate")[2]
    if REASON not in rest or not rest.split(REASON, 1)[1].strip():
        return "# pragma: no mutate with no reason — write it after ' -- '"
    return None


def main(argv: list[str]) -> int:
    """Judge every pragma comment in the named files.

    Returns:
        0 when every pragma is the line form with its reason, 1 otherwise.

    """
    status = 0
    for name in argv:
        try:
            found = pragmas(Path(name))
        except (OSError, SyntaxError, UnicodeDecodeError, tokenize.TokenError) as bad:
            findings.finding(name, f"could not tokenize: {bad}")
            status = 1
            continue
        for line, text in found:
            why = refusal(text)
            if why is not None:
                findings.finding(
                    f"{name}:{line}",
                    why,
                    [
                        (
                            "an exclusion records a mutant the suite cannot kill;"
                            " say why at the site"
                        )
                    ],
                )
                status = 1
    return status
