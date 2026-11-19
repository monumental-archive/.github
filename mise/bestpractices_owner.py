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
import sys
from dataclasses import dataclass
from typing import Self

import decoded
from typed_std import NoStdinError, stdin_text


def normalised(url: str) -> str:
    """Fold the differences the comparison ignores.

    Returns:
        The URL lowercased, with one trailing slash and then one trailing
        `.git` removed.

    """
    url = url.strip().lower()
    url = url.removesuffix("/")
    return url.removesuffix(".git")


@dataclass(frozen=True)
class Entry:
    """The two fields of a project entry the verdict reads."""

    project: str  # the entry's `id` as the finding writes it, or `?`
    repo_url: str | None  # None unless the entry carries a string

    @classmethod
    def of(cls, entry: dict[str, object]) -> Self:
        """Read the two fields from a decoded project object.

        Returns:
            The entry; a missing or non-string `repo_url` is None.

        """
        url = entry.get("repo_url")
        return cls(
            project=str(entry.get("id", "?")),
            repo_url=url if isinstance(url, str) else None,
        )


def finding(entry: Entry, wearer: str) -> str | None:
    """Compare an entry's repository with the wearer's.

    Returns:
        None when the entry belongs to the wearer, else the finding.

    """
    project = entry.project
    repo_url = entry.repo_url
    if repo_url is None or not repo_url.strip():
        return f"project {project} names no repo_url, so nothing binds it to {wearer}"
    if normalised(repo_url) == normalised(wearer):
        return None
    return (
        f"project {project}'s repo_url is {repo_url}, not {wearer}, "
        "the repository wearing it"
    )


class Args(argparse.Namespace):
    """The arguments, typed as the parser declares them."""

    wearer: str


def main(argv: list[str] | None = None) -> int:
    """Read the entry from stdin and judge it against the argument.

    Returns:
        The process exit status.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wearer", help="https URL of the repository wearing the shield")
    args = parser.parse_args(argv, namespace=Args())
    try:
        entry = decoded.load_json(stdin_text())
    except NoStdinError as err:
        print(f"the issuer's answer cannot be read: {err}")
        return 1
    except decoded.UndecodableError as err:
        print(f"the issuer's answer is not JSON: {err}")
        return 1
    if not decoded.is_object(entry):
        print("the issuer's answer is not a project object")
        return 1
    found = finding(Entry.of(entry), args.wearer)
    if found is None:
        return 0
    print(found)
    return 1


if __name__ == "__main__":
    sys.exit(main())
