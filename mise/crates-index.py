#!/usr/bin/env python3
"""The crates.io sparse index: where a crate's record lives, once (#1383).

Two readers ask the index whether a crate is published: the crates
publisher (release/publish-crates.py), resuming a release crate by crate,
and audit:semver (mise/semver-audit.sh), asking whether a crate has a
baseline to judge against. Each used to carry its own copy of the path
rule, and a copy that drifted would read a published crate as absent —
for the publisher, a crate uploaded twice; for the audit, a crate never
judged.

The rule is cargo's index format: the name lowercased, then by length
`1/<n>`, `2/<n>`, `3/<first char>/<n>`, else `<n[0:2]>/<n[2:4]>/<n>`. The
file at that path is one JSON object per published version.

As a command, `crates-index.py url <crate>` prints the record's URL, for
a shell caller that keeps its own request.
"""

import sys

INDEX = "https://index.crates.io"


def index_path(name: str) -> str:
    """Place a crate's record in the sparse index.

    Args:
        name: the crate name, as cargo reports it.

    Returns:
        The record's path under the index root.

    Raises:
        ValueError: for an empty name, which has no record.

    """
    lower = name.lower()
    if not lower:
        msg = "a crate with no name has no index record"
        raise ValueError(msg)
    if len(lower) <= 2:  # noqa: PLR2004 - cargo's own buckets
        return f"{len(lower)}/{lower}"
    if len(lower) == 3:  # noqa: PLR2004 - cargo's own buckets
        return f"3/{lower[0]}/{lower}"
    return f"{lower[0:2]}/{lower[2:4]}/{lower}"


def index_url(name: str) -> str:
    """Return the full URL of a crate's record."""
    return f"{INDEX}/{index_path(name)}"


def main(argv: list[str]) -> int:
    """Print one crate's record URL.

    Returns:
        0 on success, 2 on a usage error.

    """
    if len(argv) != 2 or argv[0] != "url":  # noqa: PLR2004 - `url <crate>`
        print("usage: crates-index.py url <crate>", file=sys.stderr)
        return 2
    try:
        print(index_url(argv[1]))
    except ValueError as e:
        print(f"crates-index.py: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
