#!/usr/bin/env python3
"""Say whether a pull request declares that it breaks something (#1906).

`break:check` (mise/break-check.sh) measures what a pull request breaks
against its base, and a break is red unless the pull request declares it.
This is the declaration's one reading, made the way the release engine
will read the squash commit: the title is the subject, the body follows.

A change is declared breaking when either

- the title's header carries `!` right before its `: ` (`type!:` or
  `type(scope)!:`), as stele's `internal/convcommit` reads §13: the
  prefix before the first `: ` ends in `!`; or
- `squash_message.breaking` (#1924) finds a `BREAKING CHANGE` or
  `BREAKING-CHANGE` footer in the body, read line by line as stele
  reads §10.

Both are held to the reader: `mise/test_break_check.py` runs the belt's
stele over the same messages and asserts it files exactly these under
"Breaking".

INPUTS. `PR_TITLE` (required: unset is red by name) and `PR_BODY` from
the environment, as the gate step sets them. Prints `declared` or
`undeclared`.

stdlib only.
"""

from __future__ import annotations

import os
import sys

import squash_message


def declared(title: str, body: str) -> bool:
    """Read a title and body as the squash commit stele will read.

    Returns:
        True when the header carries `!` or the body a breaking footer.

    """
    prefix, colon, _ = title.partition(": ")
    if colon and prefix.endswith("!"):
        return True
    return bool(squash_message.breaking(body.replace("\r", "")))


def main(argv: list[str]) -> int:
    """Print whether the pull request in the environment declares a break.

    Returns:
        0, or 1 when `PR_TITLE` is unset.

    """
    del argv  # the step reads its environment, not arguments
    title = os.environ.get("PR_TITLE")
    if title is None:
        print(
            "break:check: PR_TITLE is unset — the gate step sets it from the pull"
            " request, and without it there is no declaration to hold",
            file=sys.stderr,
        )
        return 1
    body = os.environ.get("PR_BODY", "")
    print("declared" if declared(title, body) else "undeclared")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
