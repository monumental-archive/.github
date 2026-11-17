#!/usr/bin/env python3
"""The canon action's link script, every branch (#1386).

`.github/actions/canon/link.sh` is the canon action's one step: it links
the tree `$/.github/actions/canon` resolved from to `.org-canon` and
names the resolved ref. It finds that tree from its own location, so
each row plants a tree, copies the shipped script into the tree's
`.github/actions/canon/`, and runs it there under bash.

stdlib `unittest`, matching the other tests here.
"""

from __future__ import annotations

import os
import pathlib
import shutil

# ruff: ignore[suspicious-subprocess-import]
import subprocess
import tempfile
import unittest

SCRIPT = (
    pathlib.Path(__file__).resolve().parent.parent
    / ".github"
    / "actions"
    / "canon"
    / "link.sh"
)
CANON = "example-org/.github"
SHA = "0123456789abcdef0123456789abcdef01234567"


def plant(root: pathlib.Path, *, config: bool = True) -> pathlib.Path:
    """Plant a tree with the shipped script where the action keeps it.

    Returns:
        The script's path inside the planted tree.

    """
    action = root / ".github" / "actions" / "canon"
    action.mkdir(parents=True)
    script = action / "link.sh"
    shutil.copy2(SCRIPT, script)
    if config:
        (root / "mise").mkdir()
        (root / "mise" / "config.toml").write_text("[tools]\n", encoding="utf-8")
    return script


class LinkTheCanonTree(unittest.TestCase):
    """Each row: where the tree resolved, and what the step did."""

    def setUp(self) -> None:
        """Give each row a scratch root and a workspace."""
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        self.output = self.tmp / "github-output"
        self.output.write_text("", encoding="utf-8")

    def link(
        self, script: pathlib.Path, repository: str, canon: str | None = CANON
    ) -> subprocess.CompletedProcess[str]:
        """Run the planted script as the action does.

        Returns:
            The run.

        """
        bash = shutil.which("bash")
        if bash is None:
            self.fail("bash is not on PATH")
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [bash, str(script)],
            env={
                "PATH": os.environ["PATH"],
                **({"CANON_REPOSITORY": canon} if canon is not None else {}),
                "GITHUB_REPOSITORY": repository,
                "GITHUB_SHA": SHA,
                "GITHUB_WORKSPACE": str(self.workspace),
                "GITHUB_OUTPUT": str(self.output),
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def linked(self) -> pathlib.Path | None:
        """Read where `.org-canon` points, if it was made.

        Returns:
            The link's target, or None.

        """
        link = self.workspace / ".org-canon"
        return link.readlink() if link.is_symlink() else None

    def test_an_actions_path_names_the_pinned_ref(self) -> None:
        """A caller's pin: the ref is the path segment under _actions."""
        root = self.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
        done = self.link(plant(root), "someone/consumer")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, f"canon resolved: v1.69.0 at {root}\n")
        self.assertEqual(self.output.read_text(encoding="utf-8"), "ref=v1.69.0\n")
        self.assertEqual(self.linked(), root)

    def test_the_canon_calling_itself_names_the_running_commit(self) -> None:
        """A local `$/` resolution in the canon is the running commit."""
        root = self.tmp / "work" / ".github"
        done = self.link(plant(root), CANON)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, f"canon resolved: {SHA} at {root}\n")
        self.assertEqual(self.output.read_text(encoding="utf-8"), f"ref={SHA}\n")
        self.assertEqual(self.linked(), root)

    def test_another_repository_off_an_actions_path_is_refused(self) -> None:
        """A shape the action does not understand is refused, not guessed."""
        root = self.tmp / "work" / "consumer"
        done = self.link(plant(root), "someone/consumer")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(
            done.stdout,
            f"::error::canon action resolved to '{root}' in someone/consumer — not"
            " an _actions path and not a canon self-call\n",
        )
        self.assertEqual(self.output.read_text(encoding="utf-8"), "")
        self.assertIsNone(self.linked())

    def test_an_actions_path_of_another_repository_is_refused(self) -> None:
        """Only the canon's own _actions segment names a pinned ref."""
        root = self.tmp / "_actions" / "someone" / "fork" / "v1.0.0"
        done = self.link(plant(root), "someone/consumer")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("not an _actions path and not a canon self-call", done.stdout)
        self.assertIsNone(self.linked())

    def test_a_tree_with_no_belt_config_is_refused(self) -> None:
        """A tree with no mise/config.toml is not a canon tree."""
        root = self.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
        done = self.link(plant(root, config=False), "someone/consumer")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(
            done.stdout,
            f"::error::resolved canon tree at '{root}' has no mise/config.toml"
            " — not a canon tree\n",
        )
        self.assertEqual(self.output.read_text(encoding="utf-8"), "")
        self.assertIsNone(self.linked())

    def test_no_canon_repository_is_refused(self) -> None:
        """The canon's name is an input: absent, nothing is guessed."""
        root = self.tmp / "_actions" / "example-org" / ".github" / "v1.69.0"
        done = self.link(plant(root), "someone/consumer", canon=None)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(
            "CANON_REPOSITORY must be set (action.yml passes the canon repository)",
            done.stderr,
        )
        self.assertIsNone(self.linked())


if __name__ == "__main__":
    unittest.main()
