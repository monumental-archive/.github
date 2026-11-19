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
import string

# ruff: ignore[suspicious-subprocess-import]
import subprocess
import sys

# What bash's [[:space:]] matches: a body of only these is empty.
SPACE = frozenset(string.whitespace)


class StepError(Exception):
    """A refusal: the message is printed as the step's error."""


def header_line(body: str) -> None:
    """Refuse a body line GitHub's squash signer reads as a header.

    A body line that begins with `committer` makes the merge queue's
    squash commit unverifiable (#1529). GitHub signs the commit, then
    writes its `gpgsig` header after the LAST line that begins with
    `committer` — and when the body holds one, that is inside the
    message. The signature is good over the commit without the splice
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


def main() -> int:
    """Compose the squash message and hold it to the commit canon.

    Returns:
        The step's exit status.

    """
    env = os.environ
    number = env["PR_NUMBER"]
    try:
        message = compose(env["PR_TITLE"], env.get("PR_BODY", ""), number)
        path = pathlib.Path(env["RUNNER_TEMP"]) / "squash-message"
        path.write_text(message, encoding="utf-8")
        check(path, number)
    except StepError as err:
        print(f"::error::{err}", flush=True)
        return 1
    print("squash-message: title and body mint a conforming commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
