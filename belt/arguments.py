"""The argument types the belt's commands parse with argparse (#2177).

A command names its arguments with `argparse` and nothing else; a value
whose shape is the command's to check is checked by a `type=` here, so
argparse refuses it as it refuses any other usage error: exit 2, the
usage line, and `belt <command>: error: argument <NAME>: <why>`.
"""

import argparse


def repository(text: str) -> str:
    """Read an `OWNER/REPO` repository name.

    Returns:
        The name, as given.

    Raises:
        ArgumentTypeError: either half is empty, or there is no slash.

    """
    owner, _, repo = text.partition("/")
    if not owner or not repo:
        msg = f"{text!r} is not OWNER/REPO"
        raise argparse.ArgumentTypeError(msg)
    return text


def count(text: str) -> int:
    """Read a positive whole number.

    Returns:
        The number.

    Raises:
        ArgumentTypeError: the text is not one.

    """
    if not text.isdecimal() or int(text) < 1:
        msg = f"{text!r} is not a positive whole number"
        raise argparse.ArgumentTypeError(msg)
    return int(text)
