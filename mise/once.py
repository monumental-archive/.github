#!/usr/bin/env python3
"""Run a command at most once per marker, the callers waiting (#2061).

    once.py <marker> -- <command> [<argument>…]

Takes an exclusive `fcntl.flock` on `<marker>.lock`, blocking. With
`<marker>` present it exits 0 at once; otherwise it runs the command
and creates `<marker>`, a directory (the form coverage-measure.sh
reads), only when the command exits 0. A non-zero exit
is this helper's exit status and writes no marker, so the next caller
runs the command again. Every caller proceeds only after the one that
ran it has finished: the lock is held while the command runs.

The kernel releases the lock when its holder dies, so a caller killed
mid-command leaves no marker and no held lock: no polling, no timeout.

`measured` calls it for every Rust call, its marker in the run's
directory, so the workspace clean runs once per run and a second call
starting beside the first waits for it rather than building into a
directory the clean is emptying.

Stdlib only.
"""

import fcntl
import subprocess
import sys
from pathlib import Path

import findings

USAGE = "usage: once.py <marker> -- <command> [<argument>…]"
# The arguments before the command: the marker and the separator.
HEAD = 2


def once(marker: Path, command: list[str]) -> int:
    """Run the command unless the marker exists, under the marker's lock.

    Returns:
        0 when the marker exists or the command succeeded, else the
        command's exit status.

    """
    with Path(f"{marker}.lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if marker.exists():
            return 0
        status = subprocess.run(command, check=False).returncode
        if status == 0:
            marker.mkdir()
        return status


def main(argv: list[str] | None = None) -> int:
    """Read the marker and the command, then run it at most once.

    Returns:
        The exit status `once` gives, or 2 on a usage error.

    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) <= HEAD or args[1] != "--":
        findings.finding("", USAGE)
        return 2
    return once(Path(args[0]), args[HEAD:])


if __name__ == "__main__":
    sys.exit(main())
