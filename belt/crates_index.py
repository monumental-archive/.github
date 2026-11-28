"""The crates.io sparse index: where a crate's record lives, once (#1383).

Two readers ask the index whether a crate is published: the crates
publisher (release/publish_crates.py), resuming a release crate by crate,
and audit:semver (mise/semver-audit.sh), asking whether a crate has a
baseline to judge against. Each used to carry its own copy of the path
rule, and a copy that drifted would read a published crate as absent —
for the publisher, a crate uploaded twice; for the audit, a crate never
judged.

The rule is cargo's index format: the name lowercased, then by length
`1/<n>`, `2/<n>`, `3/<first char>/<n>`, else `<n[0:2]>/<n[2:4]>/<n>`. The
file at that path is one JSON object per published version.

As a command, `belt crates-index url <crate>` prints the record's URL, for
a shell caller that keeps its own request.
"""

import argparse

from belt import findings

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
    match len(lower):
        case 0:
            msg = "a crate with no name has no index record"
            raise ValueError(msg)
        case 1 | 2:
            return f"{len(lower)}/{lower}"
        case 3:
            return f"3/{lower[0]}/{lower}"
        case _:
            return f"{lower[0:2]}/{lower[2:4]}/{lower}"


def index_url(name: str) -> str:
    """Name the full URL of a crate's record.

    Returns:
        The index host's URL for the record.

    """
    return f"{INDEX}/{index_path(name)}"


class Args(argparse.Namespace):
    """The command's arguments, typed as parsed."""

    mode: str
    crate: str


def main(argv: list[str]) -> int:
    """Print one crate's record URL.

    Returns:
        0 on success, 2 on a usage error.

    """
    parser = argparse.ArgumentParser(
        prog="belt crates-index", description="Answer a crate's sparse-index URL"
    )
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("url", help="print the crate's index URL").add_argument("crate")
    args = parser.parse_args(argv, namespace=Args())
    try:
        print(index_url(args.crate))
    except ValueError as e:
        findings.finding("", str(e))
        return 2
    return 0
