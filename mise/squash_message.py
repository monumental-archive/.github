#!/usr/bin/env python3
"""Hold a pull request's squash message to the commit canon.

The gate's "The squash message is the PR title and body" step calls this
as its one command (#1386); before that it was a 23-line `run:` block.
The behaviour is the block's, ported, and every message keeps its
wording.

WHY. The squash message exists only at merge, so `lint:commits`, which
walks the branch's commits, cannot see it. That is how an 84-character
subject reached main past a 72-column ceiling (v1.26.0's belt merge):
every branch commit was clean and the title was not. So the message the
merge will mint is composed here and held to the same tool and config
the branch commits answer to: `mise run commits:check`, reading it from
`ORG_COMMIT_MSG_FILE`.

INPUTS. From the environment, as the step sets them: `PR_TITLE`,
`PR_BODY` and `PR_NUMBER` (values via env, never interpolated: the body
is attacker-controlled on a fork pull request), and `RUNNER_TEMP`.

stdlib only.
"""

from __future__ import annotations

import os
import pathlib
import re
import string
import subprocess
import sys

import findings

# What bash's [[:space:]] matches: a body of only these is empty.
SPACE = frozenset(string.whitespace)

# stele's footer line (`internal/convcommit`, Conventional Commits §8):
# a token, or the one spaced token, then ": " or " #". Leftmost-first
# alternation, as Go's regexp reads it.
FOOTER = re.compile(r"(BREAKING CHANGE|[A-Za-z0-9-]+)(?:: | #)")
# The tokens whose value is the break's description (§12, §16); upper
# case only, as stele's §15 reading has it.
BREAKING_TOKENS = ("BREAKING CHANGE", "BREAKING-CHANGE")


class StepError(Exception):
    """A refusal: the message is printed as the step's error."""


def header_line(body: str) -> None:
    """Refuse a body line GitHub's squash signer reads as a header.

    A body line that begins with `committer` makes the merge queue's
    squash commit unverifiable (#1529). GitHub signs the commit, then
    writes its `gpgsig` header after a line that begins with
    `committer` — and when the body holds one, it lands there, inside
    the message. The signature is good over the commit without the splice
    and bad over the commit as stored, so `required_signatures` reads
    it `invalid`, and the queue removes the green pull request with
    `branch_protection_failure` and no other word. Measured on #1512,
    whose body had `committer) and the row's own overrides.` at a line
    start: three queue commits, all three `invalid`.

    Raises:
        StepError: A body line begins with `committer`.

    """
    for number, line in enumerate(body.split("\n"), start=1):
        if line.startswith("committer"):
            msg = (
                f"PR body line {number} begins with 'committer': {line!r} — GitHub"
                " splices the queue squash commit's signature after that line, the"
                " commit is unverifiable and the queue drops the pull request"
                " (#1529) — reword the BODY so no line starts with 'committer'"
            )
            raise StepError(msg)


def _breaking_span(lines: list[str]) -> tuple[int, int] | None:
    """Find the breaking-change footer as the release engine reads it.

    Returns:
        The footer's first and one-past-last line index, or None.

    """
    footers = [(i, m[1]) for i, line in enumerate(lines) if (m := FOOTER.match(line))]
    for at, (start, token) in enumerate(footers):
        if token in BREAKING_TOKENS:
            end = footers[at + 1][0] if at + 1 < len(footers) else len(lines)
            return start, end
    return None


def breaking(body: str) -> list[str]:
    """Read a body's breaking-change footer as stele reads it (#1924).

    stele's `internal/convcommit` reads line by line (Conventional
    Commits §10): the footer section starts at the first line that is a
    token and a separator, and each footer's value runs to the next such
    line, or to the end — blank lines and paragraphs included. Of all
    footer values, only the break's is ever rendered: it is the
    "Breaking" entry of the release notes.

    Returns:
        The lines of the first `BREAKING CHANGE` or `BREAKING-CHANGE`
        footer, from its token line up to the next footer line or the
        end; empty when the body has none.

    """
    lines = body.split("\n")
    span = _breaking_span(lines)
    if span is None:
        return []
    start, end = span
    return lines[start:end]


def breaking_footer(body: str) -> None:
    """Refuse text the release notes would publish as part of the break.

    Everything from a `BREAKING CHANGE:` footer to the next footer is
    the break's description in the release notes, so a paragraph after
    it — a session's attribution line, most often — is published as
    part of the breaking-change notice, in a changelog the tag keeps
    (edtf#316). A blank line inside the footer followed by more text is
    that shape; trailing blank lines are not. Text after any other
    footer stays allowed: nothing renders those values.

    Raises:
        StepError: The breaking-change footer carries text after a
            blank line.

    """
    lines = body.split("\n")
    span = _breaking_span(lines)
    if span is None:
        return
    start, end = span
    blank = False
    for index in range(start, end):
        line = lines[index]
        if not line.strip():
            blank = True
        elif blank:
            msg = (
                f"PR body line {index + 1} follows a blank line inside the BREAKING"
                f" CHANGE footer: {line!r} — everything from a `BREAKING CHANGE:`"
                " footer to the next footer is the break's description in the"
                " release notes — move that text above the footer, so the footer"
                " and any footers after it end the body"
            )
            raise StepError(msg)


def compose(title: str, body: str, number: str) -> str:
    """Compose the commit the squash merge will mint.

    The title is this composition's INPUT, so it must not already carry
    its OUTPUT: GitHub appends " (#N)" unconditionally, and a title
    ending in this pull request's own number mints the reference twice
    in the permanent subject. It happened on both pull requests where an
    author wrote the number in (#783 and #788), and the 72-column ceiling
    structurally cannot catch it: an appended "(#N)" is an unwrappable
    tail, and committed is wrap-aware by ruling (#800). This pull
    request's OWN number only: a title ending in the ISSUE's reference
    is the convention.

    The body is the other permanent half (#880). The API serves it
    CRLF-terminated, so the CR is stripped before measuring: an author
    must not fail on characters they cannot see. A body that is empty or
    only whitespace composes to the subject alone.

    Returns:
        The message, newline-terminated.

    Raises:
        StepError: The title already ends in this pull request's number.

    """
    if title.endswith(f"(#{number})"):
        msg = (
            "the PR title already ends in this pull request's own reference"
            f" (#{number}), and the merge appends it again — the permanent subject"
            " would carry it twice — fix the TITLE: it carries the ISSUE reference"
            " only, the squash appends the PR's"
        )
        raise StepError(msg)
    message = f"{title} (#{number})\n"
    body = body.replace("\r", "")
    header_line(body)
    breaking_footer(body)
    if any(c not in SPACE for c in body):
        message += f"\n{body}\n"
    return message


def check(path: pathlib.Path, number: str) -> None:
    """Run the commit canon over the composed message.

    Raises:
        StepError: The message fails the commit canon.

    """
    done = subprocess.run(
        # ruff: ignore[start-process-with-partial-path]
        ["mise", "run", "commits:check"],
        env={**os.environ, "ORG_COMMIT_MSG_FILE": str(path)},
        check=False,
    )
    if done.returncode != 0:
        msg = (
            "the PR title and body become the permanent squash subject (plus"
            f" ' (#{number})') and body, and they fail the commit canon — fix the"
            " TITLE or the BODY, not a commit"
        )
        raise StepError(msg)


def main(argv: list[str]) -> int:
    """Compose the squash message and hold it to the commit canon.

    Returns:
        The step's exit status.

    """
    del argv  # the step reads its environment, not arguments
    env = os.environ
    number = env["PR_NUMBER"]
    try:
        message = compose(env["PR_TITLE"], env.get("PR_BODY", ""), number)
        path = pathlib.Path(env["RUNNER_TEMP"]) / "squash-message"
        path.write_text(message, encoding="utf-8")
        check(path, number)
    except StepError as err:
        findings.finding("", str(err), annotate=True)
        return 1
    print("squash-message: title and body mint a conforming commit")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
