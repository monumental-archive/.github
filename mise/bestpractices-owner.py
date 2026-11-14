#!/usr/bin/env python3
"""Say whether a Best Practices entry belongs to the repository wearing it.

Org canon (#1197). `audit:badges` asked bestpractices.dev whether a
project passes and never whether the project is the repository's own.
monumental-archive-db wore project 13998, which passes, and whose
`repo_url` names `github.com/CarlAllenn/monumental-archive-db`: a shield
bound to another repository's entry is a claim about that repository.

The project JSON, as bestpractices.dev serves it, arrives on stdin; the
repository wearing the shield is the one argument. The comparison
ignores case, one trailing slash and one trailing `.git`, and nothing
else: a redirect from an old owner is still a mismatch, because the
entry still names the old owner.

Exit 0 when the entry is the wearer's. Exit 1 with one line naming both
URLs when it is not, or when the entry names no repository at all.
"""

import argparse
import json
import sys


def normalised(url: str) -> str:
    """Fold the differences the comparison ignores.

    Returns:
        The URL lowercased, with one trailing slash and then one trailing
        `.git` removed.

    """
    url = url.strip().lower()
    url = url.removesuffix("/")
    return url.removesuffix(".git")


def finding(entry: dict, wearer: str) -> str | None:
    """Compare an entry's repository with the wearer's.

    Returns:
        None when the entry belongs to the wearer, else the finding.

    """
    project = entry.get("id", "?")
    repo_url = entry.get("repo_url")
    if not isinstance(repo_url, str) or not repo_url.strip():
        return f"project {project} names no repo_url, so nothing binds it to {wearer}"
    if normalised(repo_url) == normalised(wearer):
        return None
    return (
        f"project {project}'s repo_url is {repo_url}, not {wearer}, "
        "the repository wearing it"
    )


def main(argv: list[str] | None = None) -> int:
    """Read the entry from stdin and judge it against the argument.

    Returns:
        The process exit status.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wearer", help="https URL of the repository wearing the shield")
    args = parser.parse_args(argv)
    try:
        entry = json.load(sys.stdin)
    except json.JSONDecodeError as err:
        print(f"the issuer's answer is not JSON: {err}")
        return 1
    if not isinstance(entry, dict):
        print("the issuer's answer is not a project object")
        return 1
    found = finding(entry, args.wearer)
    if found is None:
        return 0
    print(found)
    return 1


if __name__ == "__main__":
    sys.exit(main())
