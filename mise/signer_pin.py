#!/usr/bin/env python3
"""lint:signer-pin: a tree states the trusted signer exactly once.

The sign.yml@<sha> `uses:` lines are the one statement of the signer
identity; everything that needs it derives from them at run time. The
task's comment in mise/config.toml has the history (#296, #314, #316);
this is its logic (#1911). In order, each red on its own:

  a hand-written `--signer-digest <40-hex>` literal on any line (#314
  finding 1): nothing bumps it, so it drifts from the pin;
  security/signer.pin exists: the deleted second copy (#316 finding 2);
  a workflow reads security/signer.pin on a non-comment line;
  the signer is pinned at more than one digest: a partial bump.

No signer reference at all is a skip, said by name.

Usage:
  signer_pin.py OWNER/REPO SIGNER-WORKFLOW WORKFLOW...

OWNER/REPO is the canon and SIGNER-WORKFLOW the signer's workflow path,
both from security/identity.toml, read by the task. Each workflow is
read as UTF-8, strictly. Exit 0 with one line, 1 with the finding, 2 on
a usage or read error. Stdlib only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import findings

EXIT_RED = 1
EXIT_ERROR = 2
PIN_FILE = "security/signer.pin"
# The canon, the signer, and at least one workflow.
MIN_ARGS = 3
# grep's [[:space:]] within one line: lines split on "\n" alone, as grep's.
SPACE = r"[ \t\r\f\v]"
LITERAL = re.compile(rf"--signer-digest{SPACE}+[0-9a-f]{{40}}")
READER = re.compile(r"^[^#]*security/signer\.pin")


def read(path: Path) -> str:
    """Read a workflow as UTF-8, strictly.

    Returns:
        Its text.

    Raises:
        ValueError: the file cannot be read or is not UTF-8.

    """
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as err:
        msg = f"{path}: cannot be read as UTF-8: {err}"
        raise ValueError(msg) from err


def lines_of(text: str) -> list[str]:
    """Split a file into grep's lines: on newline, no empty last line.

    Returns:
        The lines, in order.

    """
    lines = text.split("\n")
    if lines and not lines[-1]:
        lines.pop()
    return lines


def literals(texts: dict[str, list[str]]) -> list[str]:
    """Find every hand-written --signer-digest literal, as grep -n names it.

    The file is named only when more than one is searched, as grep does.

    Returns:
        One `[file:]line:text` entry per matching line, in order.

    """
    named = len(texts) > 1
    found: list[str] = []
    for name, lines in texts.items():
        for number, line in enumerate(lines, 1):
            if LITERAL.search(line):
                where = f"{name}:" if named else ""
                found.append(f"{where}{number}:{line}")
    return found


def readers(texts: dict[str, list[str]]) -> list[str]:
    """Name every workflow that reads the pin file on a non-comment line.

    Returns:
        The names, in order.

    """
    return [
        name
        for name, lines in texts.items()
        if any(READER.search(line) for line in lines)
    ]


def refs(texts: dict[str, list[str]], signer: str) -> list[str]:
    """Collect every distinct signer reference, sorted.

    Returns:
        Each `<signer>@<sha>` once.

    """
    pattern = re.compile(rf"{re.escape(signer)}@[0-9a-f]{{40}}")
    return sorted({
        match.group(0)
        for lines in texts.values()
        for line in lines
        for match in pattern.finditer(line)
    })


def judge(canon: str, signer: str, texts: dict[str, list[str]]) -> int:
    """Judge the workflows' signer references.

    Returns:
        0 when they agree or there are none, EXIT_RED otherwise.

    """
    found = literals(texts)
    if found:
        findings.finding(
            "",
            "hand-written --signer-digest literal — nothing bumps it, so it"
            " drifts from the uses: pin the certificate will carry (#314):",
            [
                *found,
                f"derive it instead: uses: {canon}/.github/actions/verify-signed",
                "($/.github/actions/verify-signed from a canon-owned workflow)",
            ],
        )
        return EXIT_RED
    if Path(PIN_FILE).is_file():
        findings.finding(
            "",
            f"{PIN_FILE} exists — the second copy of the signer identity was"
            " deleted (#316 finding 2)",
            [
                (
                    "the uses: lines are the single statement of the trusted signer;"
                    " verifiers derive from them"
                ),
                (
                    "(a standalone pin file has no Renovate manager the first-party"
                    " group can match, so it"
                ),
                (
                    "bumps on a separate branch that can never pass this lint — the"
                    " two-branch split)"
                ),
            ],
        )
        return EXIT_RED
    reading = readers(texts)
    if reading:
        findings.finding(
            "",
            f"workflows read {PIN_FILE} (on a non-comment line) but the file is"
            " deleted (#316 finding 2) — derive from the sign.yml uses: pin"
            f" instead:{'\n'.join(reading)}",
        )
        return EXIT_RED
    pinned = refs(texts, signer)
    if not pinned:
        print("lint:signer-pin: no signer references, skipped")
        return 0
    if len(pinned) > 1:
        findings.finding(
            "",
            "sign.yml pinned at more than one digest — a tree states the trusted"
            " signer once:",
            [
                *pinned,
                (
                    "a partial bump left the tree disagreeing with itself; move every"
                    " uses: line together"
                ),
            ],
        )
        return EXIT_RED
    print(f"lint:signer-pin: all signer references agree on {pinned[0].split('@')[-1]}")
    return 0


def main(argv: list[str]) -> int:
    """Check the signer references in every workflow named.

    Returns:
        0 when they hold, EXIT_RED on a violation, EXIT_ERROR on a
        usage or read error.

    """
    # identity.sh names a missing field on stderr and prints nothing, so
    # an empty owner or repository arrives here as "/x" or "x/".
    owner, _, repo = argv[0].partition("/") if argv else ("", "", "")
    if not owner or not repo or len(argv) < MIN_ARGS or not argv[1]:
        findings.finding(
            "", "usage: signer_pin.py OWNER/REPO SIGNER-WORKFLOW WORKFLOW..."
        )
        return EXIT_ERROR
    texts: dict[str, list[str]] = {}
    for name in argv[2:]:
        try:
            texts[name] = lines_of(read(Path(name)))
        except ValueError as err:
            findings.finding("", str(err))
            return EXIT_ERROR
    return judge(argv[0], argv[1], texts)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
