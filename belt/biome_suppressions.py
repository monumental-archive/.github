"""Find the biome ignores biome never re-checks (#2137).

The org's rule is that a suppression must be able to go stale loudly:
for Rust every suppression is an `#[expect]` (#1857,
rust_suppressions.py). biome keeps the rule for its one-line form
only. A `biome-ignore` that suppresses nothing is reported
(`suppressions/unused`); a file-wide `biome-ignore-all` and a range's
`biome-ignore-start` / `biome-ignore-end` are not, so they outlive
their reason silently and widen to hide new findings. Measured with
the belt's biome 2.5.7 on a file with nothing to suppress.

`lint:biome` pipes this helper the tracked files it hands to biome,
NUL-separated on stdin (`org_tracked`, #1986): no second tree walk.
Prints each wide ignore as `path:line: form` to stderr, then the
remedy, and exits 1; prints nothing and exits 0 when clean. A tracked
path missing from the working tree is passed over; a file that is not
UTF-8 is refused by name.

Only a comment counts, and the form must open the comment's text (or
one of its lines, past a JSDoc `*`), which is where biome reads it:
measured, `/** biome-ignore-all … */` and a block comment's second
line are honoured, and the form mid-sentence is not. The text inside a
string, a template literal or a regex is not a suppression, so each
language biome parses gets a small lexer rather than a line grep:

- JavaScript and TypeScript: `//` and `/* */` comments; quoted strings,
  which end at a newline the way the language ends them; template
  literals, whose `${…}` holds code again; regex literals, told from
  division by the token before them.
- JSON and JSONC: `//` and `/* */`, and double-quoted strings.
- CSS: `/* */` only, and quoted strings.
- GraphQL: `#` comments, and quoted and block strings.
"""

import argparse
import re
from pathlib import Path

from belt import findings as writer
from belt.tracked import stdin_paths

FORM = re.compile(r"biome-ignore-(?:all|start|end)(?![\w-])")
WORD = re.compile(r"[\w$]+")
SCRIPT = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"})
JSON = frozenset({".json", ".jsonc"})
CSS = frozenset({".css"})
GRAPHQL = frozenset({".graphql", ".gql"})
# After one of these words a `/` opens a regex; after any other word,
# a number, `)`, `]` or `}` it divides.
OPERAND_BEFORE = frozenset({
    "await",
    "case",
    "delete",
    "do",
    "else",
    "in",
    "instanceof",
    "new",
    "of",
    "return",
    "throw",
    "typeof",
    "void",
    "yield",
})
# The top of a template stack while a template literal's text is read;
# any other entry is the brace depth inside one of its `${…}`.
TEMPLATE = -1
HEAD = "a biome ignore wider than one line, which biome never re-checks:"
REMEDY = (
    "a file-wide or range ignore suppresses silently once its reason is gone;",
    "biome reports only the one-line form when it suppresses nothing. Instead:",
    "  // biome-ignore <rule>: <why> above the one line the finding lands on,",
    "  or a config change where the rule is wrong for a whole class of file",
)

Comment = tuple[int, str]


def _line_at(text: str, i: int) -> int:
    """Count the lines up to and including the one `i` sits on.

    Returns:
        The line, from 1.

    """
    return text.count("\n", 0, i) + 1


def _to_newline(text: str, i: int) -> int:
    """Find the end of the line `i` sits on.

    Returns:
        The index of its newline, or the end of the text.

    """
    close = text.find("\n", i)
    return len(text) if close < 0 else close


def _block(text: str, i: int, found: list[Comment]) -> int:
    """Record the `/* */` comment opening at `i`.

    Returns:
        The index after its close, or the end of the text.

    """
    close = text.find("*/", i + 2)
    end = len(text) if close < 0 else close + 2
    found.append((_line_at(text, i), text[i + 2 : end - 2 if close >= 0 else end]))
    return end


def _line_comment(text: str, i: int, width: int, found: list[Comment]) -> int:
    """Record the line comment whose marker, `width` long, is at `i`.

    Returns:
        The index of the newline that ends it.

    """
    end = _to_newline(text, i)
    found.append((_line_at(text, i), text[i + width : end]))
    return end


def _quoted(text: str, i: int) -> int:
    """Skip the quoted string opening at `i`, escapes honoured.

    Returns:
        The index after its closing quote, or of the newline that ends
        an unterminated one: no language here lets a quote span lines.

    """
    quote = text[i]
    i += 1
    while i < len(text) and text[i] != "\n":
        if text[i] == "\\":
            i += 2
        elif text[i] == quote:
            return i + 1
        else:
            i += 1
    return min(i, len(text))


def _regex(text: str, i: int) -> int:
    """Skip the regex literal opening at `i`, classes and escapes honoured.

    Returns:
        The index after its closing slash, or of the newline that ends
        an unterminated one.

    """
    i += 1
    in_class = False
    while i < len(text) and text[i] != "\n":
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            return i + 1
        i += 1
    return min(i, len(text))


def _template_text(text: str, i: int, stack: list[int]) -> int:
    """Read one step of a template literal's text at `i`.

    Returns:
        The index after the step: an escape, the closing backtick
        (popping the template), `${` (pushing its code), or one char.

    """
    if text[i] == "\\":
        return i + 2
    if text[i] == "`":
        stack.pop()
        return i + 1
    if text.startswith("${", i):
        stack.append(0)
        return i + 2
    return i + 1


def _brace(char: str, stack: list[int]) -> None:
    """Count a brace against the innermost `${…}`, if any is open."""
    if not stack:
        return
    if char == "{":
        stack[-1] += 1
    elif stack[-1] == 0:
        stack.pop()
    else:
        stack[-1] -= 1


def _code(text: str, i: int, stack: list[int], *, regex_next: bool) -> tuple[int, bool]:
    """Read the code token at `i`: a literal, a word or punctuation.

    Returns:
        The index after it, and whether a `/` after it opens a regex.

    """
    char = text[i]
    if char in "'\"":
        return _quoted(text, i), False
    if char == "`":
        stack.append(TEMPLATE)
        return i + 1, False
    if char == "/" and regex_next:
        return _regex(text, i), False
    if word := WORD.match(text, i):
        return word.end(), word.group(0) in OPERAND_BEFORE
    if char in "{}":
        _brace(char, stack)
    return i + 1, char not in ")]}"


def script_comments(text: str) -> list[Comment]:
    """Find every comment in JavaScript or TypeScript source.

    Returns:
        (line, text) for each comment, markers removed, in order.

    """
    found: list[Comment] = []
    stack: list[int] = []
    regex_next = True
    i = 0
    while i < len(text):
        if stack and stack[-1] == TEMPLATE:
            i = _template_text(text, i, stack)
        elif text.startswith("//", i):
            i = _line_comment(text, i, 2, found)
        elif text.startswith("/*", i):
            i = _block(text, i, found)
        elif text[i].isspace():
            i += 1
        else:
            i, regex_next = _code(text, i, stack, regex_next=regex_next)
    return found


def plain_comments(text: str, *, line: str, quotes: str) -> list[Comment]:
    """Find every comment in JSON, CSS or GraphQL.

    `line` is the line-comment marker, empty where there is none;
    `quotes` the characters that open a string. A GraphQL block string
    (`\"\"\"`) is skipped whole.

    Returns:
        (line, text) for each comment, markers removed, in order.

    """
    found: list[Comment] = []
    i = 0
    while i < len(text):
        if line and text.startswith(line, i):
            i = _line_comment(text, i, len(line), found)
        elif line != "#" and text.startswith("/*", i):
            i = _block(text, i, found)
        elif line == "#" and text.startswith('"""', i):
            close = text.find('"""', i + 3)
            i = len(text) if close < 0 else close + 3
        elif text[i] in quotes:
            i = _quoted(text, i)
        else:
            i += 1
    return found


def comments(path: Path, text: str) -> list[Comment]:
    """Find every comment in a file biome parses, by its extension.

    Returns:
        (line, text) for each comment; none for an extension biome
        is not handed.

    """
    suffix = path.suffix.lower()
    if suffix in SCRIPT:
        return script_comments(text)
    if suffix in JSON:
        return plain_comments(text, line="//", quotes='"')
    if suffix in CSS:
        return plain_comments(text, line="", quotes="'\"")
    if suffix in GRAPHQL:
        return plain_comments(text, line="#", quotes='"')
    return []


def wide_ignores(path: Path, text: str) -> list[tuple[int, str]]:
    """Find every file-wide or range ignore biome would honour.

    A comment's text, or any line of it past leading space and `*`,
    must open with the form, as biome reads one.

    Returns:
        (line, form) for each, in order.

    """
    found: list[tuple[int, str]] = []
    for first, body in comments(path, text):
        for offset, line in enumerate(body.split("\n")):
            opened = FORM.match(line.lstrip().lstrip("*").lstrip())
            if opened:
                found.append((first + offset, opened.group(0)))
    return found


def check(names: list[str]) -> int:
    """Check the named paths and report every wide ignore.

    Returns:
        A process exit status: 0 clean, 1 on any finding or refusal.

    """
    found: list[str] = []
    for name in names:
        path = Path(name)
        try:
            text = path.read_bytes().decode("utf-8")
        except FileNotFoundError:
            continue
        except UnicodeDecodeError:
            writer.finding(name, "not UTF-8")
            return 1
        ignores = wide_ignores(path, text)
        found.extend(f"{name}:{line}: {form}" for line, form in ignores)
    if not found:
        return 0
    writer.finding("", HEAD, found)
    writer.finding("", "suppress one line, or change the config", REMEDY)
    return 1


def main(argv: list[str]) -> int:
    """Check every path on stdin.

    Returns:
        A process exit status: 0 clean, 1 on any finding or refusal.

    """
    argparse.ArgumentParser(
        prog="belt biome-suppressions", description="Check every path on stdin"
    ).parse_args(argv)
    return check(stdin_paths())
