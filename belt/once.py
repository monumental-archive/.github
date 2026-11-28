"""Run a command at most once per marker, the callers waiting (#2061).

    belt once <marker> -- <command> [<argument>…]

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

import argparse
import fcntl
import subprocess
from pathlib import Path

# The arguments before the command: the marker and the separator.


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


class Args(argparse.Namespace):
    """The command's arguments, typed as parsed."""

    marker: str
    command: list[str]


def main(argv: list[str]) -> int:
    """Read the marker and the command, then run it at most once.

    Returns:
        The exit status `once` gives, or 2 on a usage error.

    """
    parser = argparse.ArgumentParser(
        prog="belt once",
        description="Read the marker and the command, then run it at most once",
    )
    parser.add_argument("marker", metavar="MARKER")
    parser.add_argument(
        "command",
        nargs="+",
        metavar="COMMAND",
        help="after --: the command and its arguments",
    )
    args = parser.parse_args(argv, namespace=Args())
    return once(Path(args.marker), args.command)
