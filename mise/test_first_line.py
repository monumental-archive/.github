#!/usr/bin/env python3
"""A first-line probe survives a listing longer than the pipe (#679).

`git ls-files | head -1` under `set -o pipefail`: head exits after one
line, git is still writing, and git dies of SIGPIPE, so the pipeline,
the command substitution and the task all exit 141. On a tree whose
listing fits the pipe buffer git has finished before head closes it,
which is why the failure looked intermittent; on a large tree it is
total. The belt's probes read the first line with `sed -n 1p`, which
reads its input to the end.

The row runs lint:licence's own `run` body, read from mise/config.toml,
under the belt's task shell, in a planted git tree whose `git ls-files`
listing is several times a pipe buffer.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

import decoded
import hermetic

BELT = Path(__file__).resolve().parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
# Tracked files, each named long enough that the listing passes 600 KB.
FILES = 3000
NAME = "f" * 200


def body() -> str:
    """Read lint:licence's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    task = decoded.as_object(tasks["lint:licence"], "lint:licence")
    return decoded.as_str(task["run"], "lint:licence's run")


class FirstLineOfALongListing(unittest.TestCase):
    """The probe reads the first tracked file, however many follow (#679)."""

    def test_a_listing_past_the_pipe_buffer_is_read_whole(self) -> None:
        """lint:licence passes a licensed tree, never dying at 141."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = root / "repo"
            tree.mkdir()
            env = hermetic.env(root / "home", ORG_BELT_DIR=str(BELT))
            (tree / "LICENSE").write_text("0BSD\n", encoding="utf-8")
            for i in range(FILES):
                (tree / f"{NAME}{i:05}").touch()
            subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
            subprocess.run([GIT, "add", "."], cwd=tree, env=env, check=True)
            listing = "".join(f"{name}\n" for name in hermetic.tracked(tree))
            self.assertGreater(len(listing.encode()), 8 * 65536)
            as_mise = hermetic.task_run("lint:licence", body())
            done = subprocess.run(
                as_mise.argv,
                cwd=tree,
                env=env | as_mise.env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual("lint:licence: ok\n", done.stdout)


if __name__ == "__main__":
    unittest.main()
