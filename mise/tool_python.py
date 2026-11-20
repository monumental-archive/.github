#!/usr/bin/env python3
"""Check a Python tool's venv sits on the belt's Python (#1788).

A `pipx:` tool the belt pins runs in a venv of its own, and a venv
built on another Python is wrong for the suite: coverage's tracer is a
compiled extension that loads in no child, and Hypothesis's venv runs
the properties. The check was written four times (`measured`,
`mutants_python.py`, `python-properties.sh`, `python-types.sh`); it is
asked here, once.

    tool_python.py LABEL COMMAND [--site MODULE]

COMMAND is found on PATH and resolved to the venv's entry point; the
interpreter is the `python` beside it, which holds for every `pipx:`
tool mise installs through uv. That interpreter and `python3` on PATH
are each asked for `sys.version`; a start that fails for any reason,
or exits non-zero, is an empty version.

Equal and non-empty: the interpreter's path is printed, exit 0. With
`--site MODULE` the directory MODULE's package is imported from, asked
of the venv's interpreter, is printed instead; a module the venv cannot
import is an empty version. Otherwise one refusal, labelled, exit 1.

`check(label, command, site)` is the unit a Python caller imports.

Stdlib only.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

VERSION = "import sys; print(sys.version)"

# The site first, on one line, then the version: `sys.version` may span
# lines on some builds, so it is the remainder.
SITE = (
    "import importlib, os, sys\n"
    "module = importlib.import_module(sys.argv[1])\n"
    "print(os.path.dirname(os.path.dirname(module.__file__)))\n"
    "print(sys.version)\n"
)


def ask(python: str, code: str, *args: str) -> str:
    """Run `code` on `python` and read what it printed.

    Returns:
        Its standard output, stripped, or "" when the program cannot
        start or exits non-zero.

    """
    try:
        done = subprocess.run(
            [python, "-c", code, *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def check(label: str, command: str, site: str | None = None) -> str | None:
    """Check COMMAND's venv runs on the suite's `python3`.

    Returns:
        The venv's interpreter, or with `site` the directory that
        module's package is imported from; None with the refusal
        printed to stderr.

    """
    found = shutil.which(command)
    if found is None:
        print(
            f"{label}: {command} missing — the belt pins pipx:{command};"
            " run: mise install",
            file=sys.stderr,
        )
        return None
    python = str(Path(os.path.realpath(found)).parent / "python")
    where = ""
    if site is None:
        have = ask(python, VERSION)
    else:
        where, _, have = ask(python, SITE, site).partition("\n")
    suite = shutil.which("python3")
    want = "" if suite is None else ask(suite, VERSION)
    if not have or have != want:
        print(
            f"{label}: {command} runs on '{have or 'no interpreter'}'"
            f" but the suite's python3 is '{want or 'missing'}' — the tool"
            " venv must be built on the belt's python pin"
            f" (reinstall: mise install -f pipx:{command})",
            file=sys.stderr,
        )
        return None
    return python if site is None else where


class Args(argparse.Namespace):
    """The arguments, typed as the parser declares them."""

    label: str
    command: str
    site: str | None


def main(argv: list[str] | None = None) -> int:
    """Check the named tool's venv and print what the caller needs.

    Returns:
        0 with the interpreter (or the site) printed, 1 with the
        refusal printed.

    """
    parser = argparse.ArgumentParser(prog="tool_python.py")
    parser.add_argument("label")
    parser.add_argument("command")
    parser.add_argument("--site", metavar="MODULE")
    args = parser.parse_args(argv, namespace=Args())
    found = check(args.label, args.command, args.site)
    if found is None:
        return 1
    print(found)
    return 0


if __name__ == "__main__":
    sys.exit(main())
