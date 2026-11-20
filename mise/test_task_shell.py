#!/usr/bin/env python3
"""Every task fails on error, by default (#401, #1745).

The belt's tasks ran under `bash -c`, so a body whose middle command
failed went on to its last line and reported that line's status: a
check could fail and the task still pass. #401 pinned the task shell to
`bash -euo pipefail -c` as `[task_config] shell` in mise/config.toml,
for the tasks in that file. A repository's own tasks run under the pin
in its own file, which `lint:belt-shell` reds it for leaving out; the
belt states no default for them (#1745).

Each row of the first table runs a body through the declared shell,
split as mise splits it: a failing command, a failing pipeline and an
unset name must each stop the body before its last line. The second
runs mise for real, in a planted repository that pins its own shell
and loads the belt through a clone's `conf.d` link.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import shlex
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import tomllib
import unittest
from pathlib import Path

import hermetic

BELT = Path(__file__).resolve().parent
CONFIG = tomllib.loads((BELT / "config.toml").read_text(encoding="utf-8"))
SHELL = CONFIG["task_config"]["shell"]
# Each failure, then the line that must never run.
BODIES = {
    "a failing command": "false\necho reached\n",
    "a failing pipeline": "false | cat\necho reached\n",
    "an unset name": 'echo "${ORG_NEVER_SET}"\necho reached\n',
}
# What mise prints for a setting it refuses from a non-global file.
REFUSED = "ignored for security reasons"


class TasksFailOnError(unittest.TestCase):
    """The declared shell stops a body at its first failure (#401)."""

    def test_the_declared_shell_stops_at_the_first_failure(self) -> None:
        """No body reaches its last line, and each run is red."""
        for what, body in BODIES.items():
            with self.subTest(body=what):
                # ruff: ignore[subprocess-without-shell-equals-true]
                done = subprocess.run(
                    [*shlex.split(SHELL), body],
                    env=hermetic.env(),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotIn("reached", done.stdout)
                self.assertNotEqual(0, done.returncode)


class ALinkedCloneThatPinsItsShell(unittest.TestCase):
    """A real mise, the belt as a clone's link, no global config (#1745)."""

    def test_the_pin_holds_and_the_belt_sets_nothing_refused(self) -> None:
        """The failing pipeline reds the task, and mise warns of nothing."""
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp)
        repo = tmp / "repo"
        conf = repo / ".config" / "mise" / "conf.d"
        conf.mkdir(parents=True)
        belt = hermetic.belt(tmp / "belt")
        (conf / "belt.toml").symlink_to(belt / "config.toml")
        (repo / "mise.toml").write_text(
            f'[task_config]\nshell = "{SHELL}"\n\n'
            '[tasks.t]\nrun = "false | cat; echo reached"\n',
            encoding="utf-8",
        )
        (tmp / "config").mkdir()
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run(
            [hermetic.tool("git"), "init", "-q", "."],
            cwd=repo,
            env=hermetic.env(),
            check=True,
        )
        env = hermetic.env(
            MISE_CONFIG_DIR=str(tmp / "config"),
            XDG_CONFIG_HOME=str(tmp / "config"),
            MISE_CEILING_PATHS=str(tmp),
            MISE_TRUSTED_CONFIG_PATHS=str(tmp),
            MISE_OFFLINE="1",
            MISE_YES="1",
            MISE_AUTO_INSTALL="0",
            MISE_TASK_RUN_AUTO_INSTALL="0",
        )
        # ruff: ignore[subprocess-without-shell-equals-true]
        done = subprocess.run(
            [hermetic.tool("mise"), "run", "t"],
            cwd=repo,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        out = done.stdout + done.stderr
        self.assertNotEqual(0, done.returncode, out)
        self.assertNotIn("reached", done.stdout, out)
        self.assertNotIn(REFUSED, out)


if __name__ == "__main__":
    unittest.main()
