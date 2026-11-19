#!/usr/bin/env python3
"""Table tests for the belt's lock in a linked clone (#1686, item 6).

A clone loads the belt of its own pin through two links the org hooks
make: `.config/mise/conf.d/belt.toml` and `.config/mise/mise.lock`. mise
locks a project conf.d file with its config directory's own lock, one
level up, and with nothing else (measured on #1686: a lock beside the
conf.d file, or at the repository root, is not read for it). These rows
hold that, under a real mise, with `MISE_LOCKED=1`: a belt tool resolves
from the linked lock and is refused when that lock lacks it, while the
repository's own tool resolves from its root `mise.lock` beside it and
is refused when the root lock lacks it.

The fixture is the real belt and its real lock, copied into a cache
tree, with uv moved from the belt to the repository's own `mise.toml`
so each lock owns one tool. The runs are `mise install --dry-run`,
offline: the registry is the tree's own aqua-registry.yaml by `file://`,
so the gate stays deterministic. A resolved tool reads
`would install`; a refused one names the lockfile.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from pathlib import Path

import decoded
import hermetic

CANON = Path(__file__).resolve().parent.parent
BELT = CANON / "mise"
UV_KEY = '"aqua:astral-sh/uv"'


def run(
    argv: list[str], cwd: Path, env: dict[str, str], *, check: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run one command, its output captured as text, within two minutes.

    Returns:
        The run.

    """
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=check,
        timeout=120,
    )


def block(key: str) -> re.Pattern[str]:
    """Match one tool's entries in a lock, header to the next tool.

    Returns:
        The pattern.

    """
    return re.compile(
        rf"\[\[tools\.{re.escape(key)}\]\].*?(?=\n\[\[tools\.|\Z)", re.DOTALL
    )


def pin(key: str) -> str:
    """Read the belt's pin for one `[tools]` key.

    Returns:
        The version.

    """
    belt = decoded.load_toml((BELT / "config.toml").read_text("utf-8"))
    tools = decoded.as_object(belt["tools"], "the belt's [tools]")
    return decoded.as_str(tools[key.strip('"')], f"the belt's {key} pin")


class LockedFromTheLinkedLock(unittest.TestCase):
    """Each row: which locks hold which tool, and what a locked install says."""

    def setUp(self) -> None:
        """Plant the cache tree, the linked clone and its root lock.

        Raises:
            AssertionError: the belt's lock holds no uv entry to move.

        """
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        tree = self.tmp / "cache" / "org-belt" / ("a" * 40) / "mise"
        tree.mkdir(parents=True)
        config = (BELT / "config.toml").read_text("utf-8")
        line = f'{UV_KEY} = "{pin(UV_KEY)}"\n'
        self.assertIn(line, config)
        (tree / "config.toml").write_text(config.replace(line, ""), "utf-8")
        for name in ("clippy.toml", "aqua-registry.yaml"):
            shutil.copy2(BELT / name, tree / name)
        lock = (BELT / "mise.lock").read_text("utf-8")
        uv = block(UV_KEY).search(lock)
        if uv is None:
            msg = "the belt's lock holds no uv"
            raise AssertionError(msg)
        self.uv_entry = uv.group(0)
        self.belt_lock = block(UV_KEY).sub("", lock)
        self.tree = tree
        self.repo = self.tmp / "repo"
        conf = self.repo / ".config" / "mise" / "conf.d"
        conf.mkdir(parents=True)
        run(
            [hermetic.tool("git"), "init", "-q", "."], self.repo, self.env(), check=True
        )
        (self.repo / "mise.toml").write_text(f"[tools]\n{line}", "utf-8")
        (conf / "belt.toml").symlink_to(tree / "config.toml")
        (conf.parent / "mise.lock").symlink_to(tree / "mise.lock")
        self.locks(belt=self.belt_lock, root=self.uv_entry)

    def env(self) -> dict[str, str]:
        """Build an environment with every mise directory inside the row's.

        Returns:
            The environment.

        """
        return hermetic.env(
            self.tmp / "home",
            MISE_CONFIG_DIR=str(self.tmp / "config"),
            MISE_DATA_DIR=str(self.tmp / "data"),
            MISE_CACHE_DIR=str(self.tmp / "mise-cache"),
            MISE_STATE_DIR=str(self.tmp / "state"),
            XDG_CACHE_HOME=str(self.tmp / "cache"),
            MISE_TRUSTED_CONFIG_PATHS=str(self.tmp),
            MISE_LOCKED="1",
            MISE_OFFLINE="1",
            MISE_YES="1",
            MISE_AQUA_REGISTRIES=f"file://{self.tree / 'aqua-registry.yaml'}",
        )

    def locks(self, *, belt: str, root: str) -> None:
        """Write the cache tree's lock and the repository's root lock."""
        (self.tree / "mise.lock").write_text(belt, "utf-8")
        (self.repo / "mise.lock").write_text(
            f"lockfile_version = 4\n\n{root}\n", "utf-8"
        )

    def install(self, key: str) -> subprocess.CompletedProcess[str]:
        """Ask a locked mise what installing one tool would do.

        Returns:
            The run.

        """
        argv = [hermetic.tool("mise"), "install", "--dry-run", key.strip('"')]
        return run(argv, self.repo, self.env())

    def assert_resolves(self, key: str, name: str) -> None:
        """Assert the locked dry run would install `name` at the belt's pin."""
        done = self.install(key)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(
            done.stderr, rf"{re.escape(name)}@{re.escape(pin(key))}\s+⇢ would install"
        )

    def assert_refused(self, key: str, name: str) -> None:
        """Assert the locked dry run refuses `name` as missing from its lock."""
        done = self.install(key)
        self.assertNotEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"{name}@{pin(key)} is not in the lockfile", done.stderr)

    def test_a_belt_tool_resolves_from_the_linked_lock(self) -> None:
        """The linked lock holds jq: a locked install would install it."""
        self.assert_resolves("jq", "jq")

    def test_a_belt_tool_the_linked_lock_lacks_is_refused(self) -> None:
        """The linked lock lacks jq: refused, though the root lock is there."""
        self.locks(belt=block("jq").sub("", self.belt_lock), root=self.uv_entry)
        self.assert_refused("jq", "jq")

    def test_the_own_tool_resolves_from_the_root_lock(self) -> None:
        """The repository pins uv, locked at its root, beside the link."""
        self.assert_resolves(UV_KEY, "aqua:astral-sh/uv")

    def test_a_repository_tool_its_root_lock_lacks_is_refused(self) -> None:
        """Only the linked lock holds uv: the root lock is the repository's."""
        self.locks(belt=self.belt_lock + "\n" + self.uv_entry, root="")
        self.assert_refused(UV_KEY, "aqua:astral-sh/uv")


if __name__ == "__main__":
    unittest.main()
