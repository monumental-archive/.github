#!/usr/bin/env python3
"""Every belt task fails on error, by default (#401).

The belt's tasks ran under `bash -c`, so a body whose middle command
failed went on to its last line and reported that line's status: a
check could fail and the task still pass. #401 pinned the task shell to
`bash -euo pipefail -c`. The pin is stated twice in mise/config.toml,
as `[task_config] shell` for the tasks in that file and as
`unix_default_inline_shell_args` for every repository's own, and both
are read here, from the file, and run.

Each row runs a body through the declared shell, split as mise splits
it: a failing command, a failing pipeline and an unset name must each
stop the body before its last line.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import shlex
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tomllib
import unittest
from pathlib import Path

import hermetic

BELT = Path(__file__).resolve().parent
CONFIG = tomllib.loads((BELT / "config.toml").read_text(encoding="utf-8"))
SHELLS = {
    "[task_config] shell": CONFIG["task_config"]["shell"],
    "unix_default_inline_shell_args": CONFIG["settings"][
        "unix_default_inline_shell_args"
    ],
}
# Each failure, then the line that must never run.
BODIES = {
    "a failing command": "false\necho reached\n",
    "a failing pipeline": "false | cat\necho reached\n",
    "an unset name": 'echo "${ORG_NEVER_SET}"\necho reached\n',
}


class TasksFailOnError(unittest.TestCase):
    """The declared shell stops a body at its first failure (#401)."""

    def test_each_declared_shell_stops_at_the_first_failure(self) -> None:
        """No body reaches its last line, and each run is red."""
        for where, shell in SHELLS.items():
            for what, body in BODIES.items():
                with self.subTest(shell=where, body=what):
                    # ruff: ignore[subprocess-without-shell-equals-true]
                    done = subprocess.run(
                        [*shlex.split(shell), body],
                        env=hermetic.env(),
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertNotIn("reached", done.stdout)
                    self.assertNotEqual(0, done.returncode)


if __name__ == "__main__":
    unittest.main()
