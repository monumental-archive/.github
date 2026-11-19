#!/usr/bin/env python3
"""lint:dco reads every sign-off trailer to the end (#1637).

The task asks whether a commit's sign-off names its author. It once
asked with `git log | grep "^Signed-off-by:" | grep -qF "<email>"`:
the last reader stopped at its first match, the trailer grep was then
killed writing the rest, and pipefail called a signed-off commit
unsigned. The trailers are captured now and the address is matched
against the captured text.

The rows run the task's own `run` body, read from mise/config.toml, in
a planted git tree whose `origin/main` is its first commit.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import tomllib
import unittest
from pathlib import Path

import hermetic

BELT = Path(__file__).resolve().parent
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"
SIGN_OFF = f"Signed-off-by: {hermetic.IDENTITY_NAME} <{hermetic.IDENTITY_EMAIL}>"


def body() -> str:
    """Read lint:dco's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    with (BELT / "config.toml").open("rb") as handle:
        return tomllib.load(handle)["tasks"]["lint:dco"]["run"]


def run(message: str) -> subprocess.CompletedProcess[str]:
    """Run the task over one planted commit carrying this message.

    Returns:
        The finished process.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env = hermetic.env(root / "home")

        def git(*args: str, stdin: str | None = None) -> None:
            subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [GIT, *args], cwd=root, env=env, input=stdin, text=True, check=True
            )

        git("init", "-q")
        git("commit", "-q", "--allow-empty", "-m", "base")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        git("commit", "-q", "--allow-empty", "-F", "-", stdin=message)
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, "-euo", "pipefail", "-c", body()],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


class SignOff(unittest.TestCase):
    """The author's own sign-off is found wherever the trailers run on."""

    def test_a_sign_off_ahead_of_many_trailers_is_found(self) -> None:
        """The author's sign-off, then 4,000 more: the commit is signed.

        Measured on main before the fix: the trailer grep, killed
        writing past the first match, red the commit 50 runs in 50. A
        long message after one sign-off does not fire it (the trailer
        grep writes once), so the row plants trailers.
        """
        others = "".join(
            f"Signed-off-by: Co {n} <co{n}@example.invalid>\n" for n in range(4000)
        )
        done = run(f"fix: planted\n\n{SIGN_OFF}\n{others}")
        self.assertEqual("", done.stderr)
        self.assertEqual(0, done.returncode)

    def test_a_commit_without_the_authors_sign_off_is_red(self) -> None:
        """Another person's sign-off is not the author's."""
        done = run("fix: planted\n\nSigned-off-by: Other <other@example.invalid>\n")
        self.assertEqual(1, done.returncode)
        self.assertRegex(
            done.stderr,
            r"^lint:dco: commits missing a sign-off matching their author:"
            r" [0-9a-f]{8}\n  fix with: git commit -s",
        )


if __name__ == "__main__":
    unittest.main()
