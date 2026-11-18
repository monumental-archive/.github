#!/usr/bin/env python3
"""Run `unittest discover`, naming any test that moves the process (#1491).

A test that changes the process's working directory and does not put it
back leaves every later test in the wrong place, or in a directory that
no longer exists, and a subprocess started from there can fail on it
(a shell's "getcwd() failed"). The failure then lands on whichever test
runs next, by discovery order and by platform, never on the test that
caused it.

This is `python3 -m unittest` with one change: a result class that,
after every test, checks the working directory still exists and is the
one the run started in. A test that leaves it elsewhere is an ERROR on
that test, by name, and the directory is put back so the next test is
judged on its own. Discovery, arguments, output and exit status are
otherwise unittest's own.
"""

import os
import sys
import unittest
from pathlib import Path

START = str(Path.cwd())

# Where the process was last sent. A deleted directory has no name to
# ask for, so the last successful `os.chdir` is the only way to say
# which one a test left behind.
_chdir = os.chdir
_last = {"path": START}


def _recording_chdir(path: os.PathLike[str] | str | int) -> None:
    """Change directory and remember where to, for the report."""
    _chdir(path)
    _last["path"] = str(Path.cwd())


os.chdir = _recording_chdir


class LeftTheProcessError(Exception):
    """A test returned with the process somewhere other than it started."""


def where() -> str | None:
    """Say where the process is, unless that is where the run started.

    Returns:
        The message naming the directory, or None when it is unchanged.

    """
    try:
        here = str(Path.cwd())
    except FileNotFoundError:
        return f"left the process in {_last['path']} (deleted)"
    if here != START:
        return f"left the process in {here} (moved from {START})"
    return None


class GuardedResult(unittest.TextTestResult):
    """unittest's text result, plus the working-directory check."""

    # ruff: ignore[invalid-function-name]
    def stopTest(self, test: unittest.TestCase) -> None:
        """After every test, judge the directory and put it back."""
        message = where()
        if message is not None:
            _chdir(START)
            _last["path"] = START
            # Reported, never raised: the test has already returned, and
            # there is no frame of its to point at.
            self.addError(
                test, (LeftTheProcessError, LeftTheProcessError(message), None)
            )
        super().stopTest(test)


class GuardedRunner(unittest.TextTestRunner):
    """unittest's text runner, reporting through GuardedResult.

    A class rather than an instance, so unittest still builds it with the
    command line's verbosity, failfast and buffer settings.
    """

    resultclass = GuardedResult


def main() -> None:
    """Hand the command line to unittest, with the guarded result."""
    # `python3 -m unittest` puts the working directory first on the
    # import path; a script puts its own directory there. Match -m.
    sys.path[0] = START
    unittest.main(
        module=None,
        argv=["python3 -m unittest", *sys.argv[1:]],
        testRunner=GuardedRunner,
    )


if __name__ == "__main__":
    main()
