#!/usr/bin/env python3
"""Run a command under an exclusive lock on a file (#1481).

    with_lock.py [--timeout SECONDS] [--message TEXT] <lockfile> -- <command…>

The lock is the kernel's: `fcntl.flock` on `<lockfile>`, created if it
is absent and never removed. The kernel releases it when this process
exits, however it exits — killed included — so there is no pid file,
no guess at whether a holder is alive, and nobody but the holder ever
lets the lock go. That is the whole reason this replaces #1140's
`mkdir` lock, whose waiters removed a lock they judged stale (#1481).
`flock(1)` would do the same, and macOS does not ship it.

A caller waits for the lock up to `--timeout` seconds (default 600),
then prints `--message` and exits 75 (EX_TEMPFAIL) without running the
command. Holding the lock, it runs the command, which does not inherit
the lock's descriptor, and exits with the command's own status.
"""

import argparse
import fcntl
import os

# ruff: ignore[suspicious-subprocess-import]
import subprocess
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

TIMED_OUT = 75  # EX_TEMPFAIL
POLL = 0.1


def acquire(path: str, timeout: float) -> int | None:
    """Take an exclusive lock on `path`, waiting up to `timeout` seconds.

    Returns:
        The open descriptor holding the lock, or None on timeout.

    """
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(POLL)
        else:
            return fd


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command under the lock.

    Returns:
        The command's exit status, 75 on a timeout, 2 on bad usage.

    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        print(
            "with-lock: usage: with_lock.py <lockfile> -- <command…>", file=sys.stderr
        )
        return 2
    split = argv.index("--")
    parser = argparse.ArgumentParser(prog="with_lock.py")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--message", default="")
    parser.add_argument("lockfile")
    args = parser.parse_args(argv[:split])
    command = argv[split + 1 :]
    if not command:
        print("with-lock: no command after --", file=sys.stderr)
        return 2
    fd = acquire(args.lockfile, args.timeout)
    if fd is None:
        message = args.message or (
            f"with-lock: waited {args.timeout:g}s for {args.lockfile}"
        )
        print(message, file=sys.stderr)
        return TIMED_OUT
    try:
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(command, check=False).returncode
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
