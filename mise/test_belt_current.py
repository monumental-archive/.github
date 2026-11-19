#!/usr/bin/env python3
"""Table tests for lint:belt-current's belt half (#1686).

A clone loads the belt of its own pin: the org hooks link
`.config/mise/conf.d/belt.toml` to the machine's cache tree of exactly
the gate stub's sha. So the belt answering (ORG_CANON_DIR) is that tree
or it is red, naming both and `lefthook run post-checkout`. Before
#1686 the script compared versions and a belt NEWER than the pin
passed with no note (the fail-before row: green on 977d99f).

Each row plants a consumer with a gate stub, the belt trees it names,
and a stub `mise` that answers CI's own version, then runs the shipped
script with ORG_CANON_DIR at the planted belt. Every red row asserts the
whole message.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from pathlib import Path

import hermetic

CANON = Path(__file__).resolve().parent.parent
SCRIPT = CANON / "mise" / "belt-current.sh"
BASH = shutil.which("bash")
GIT = shutil.which("git")
MISE_VERSION = "2026.11.7"
PIN = "a" * 40
OTHER = "b" * 40


def run(
    argv: list[str], cwd: Path, env: dict[str, str], *, check: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run one command, its output captured as text, within a minute.

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
        timeout=60,
    )


def tool(found: str | None, name: str) -> str:
    """Return a tool's path, or fail by name.

    Returns:
        The path.

    Raises:
        RuntimeError: the tool is not on PATH.

    """
    if found is None:
        msg = f"{name} is not on PATH"
        raise RuntimeError(msg)
    return found


def plant_canon(tree: Path, version: str) -> Path:
    """Make `tree` a canon tree at `version`, its ci.yml pinning mise.

    Returns:
        The tree.

    """
    workflows = tree / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        "jobs:\n  gate:\n    steps:\n"
        "      - uses: jdx/mise-action@" + "c" * 40 + " # v5\n"
        f"        with:\n          version: {MISE_VERSION}\n",
        encoding="utf-8",
    )
    (tree / "CITATION.cff").write_text(f"version: {version}\n", encoding="utf-8")
    return tree


class TheBeltIsThePinned(unittest.TestCase):
    """Each row: a belt planted where ORG_CANON_DIR points, and the verdict."""

    def setUp(self) -> None:
        """Plant a consumer pinning PIN at v1.2.0, a cache and a stub mise."""
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.cache = self.tmp / "cache"
        stubs = self.tmp / "stubs"
        stubs.mkdir()
        (stubs / "mise").write_text(
            f'#!/bin/sh\necho "{MISE_VERSION} macos-arm64 (2026-11-11)"\n',
            encoding="utf-8",
        )
        (stubs / "mise").chmod(0o755)
        self.path = f"{stubs}{os.pathsep}{os.environ['PATH']}"
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        run([tool(GIT, "git"), "init", "-q", "."], self.repo, self.env(), check=True)

    def env(self, **extra: str) -> dict[str, str]:
        """Build the row's environment.

        Returns:
            The environment: its own HOME and cache, the stub mise first.

        """
        return hermetic.env(self.home, XDG_CACHE_HOME=str(self.cache), **extra)

    def pin(self) -> None:
        """Give the consumer its gate stub, pinning PIN at v1.2.0."""
        workflows = self.repo / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "gate.yml").write_text(
            "jobs:\n  ci:\n"
            f"    uses: example-org/.github/.github/workflows/ci.yml@{PIN} # v1.2.0\n",
            encoding="utf-8",
        )

    def cached(self, sha: str, version: str) -> Path:
        """Plant the cache tree of `sha` at `version`.

        Returns:
            The tree.

        """
        return plant_canon(self.cache / "org-belt" / sha, version)

    def link(self, tree: Path) -> None:
        """Point the consumer's belt link into `tree`."""
        conf = self.repo / ".config" / "mise" / "conf.d"
        conf.mkdir(parents=True)
        (conf / "belt.toml").symlink_to(tree / "mise" / "config.toml")

    def check(self, belt: Path, **extra: str) -> subprocess.CompletedProcess[str]:
        """Run the shipped script with `belt` answering.

        Returns:
            The run.

        """
        env = self.env(ORG_CANON_DIR=str(belt), PATH=self.path, **extra)
        return run([tool(BASH, "bash"), str(SCRIPT)], self.repo, env)

    def assert_red(self, belt: Path, why: str) -> None:
        """Assert the run is red naming `belt`, the pinned tree and `why`."""
        done = self.check(belt)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(
            done.stderr,
            f"belt-current: the belt answering is {belt}; this repository's gate\n"
            f"  pins {PIN} (v1.2.0), whose belt is {self.cache / 'org-belt' / PIN}.\n"
            f"  {why} Run:\n"
            "    lefthook run post-checkout\n",
        )

    def test_a_belt_newer_than_the_pin_is_red(self) -> None:
        """The fail-before row: on 977d99f a newer belt passed silently."""
        self.pin()
        newer = self.cached(OTHER, "1.3.0")
        self.link(newer)
        self.assert_red(newer, "This clone's link names another tree.")

    def test_a_belt_older_than_the_pin_is_red(self) -> None:
        """Older is the same red: the tree is the pinned one or it is wrong."""
        self.pin()
        older = self.cached(OTHER, "1.1.0")
        self.link(older)
        self.assert_red(older, "This clone's link names another tree.")

    def test_a_clone_with_no_link_is_red(self) -> None:
        """The machine's checkout answers, at the pinned version even: red."""
        self.pin()
        self.cached(PIN, "1.2.0")
        checkout = plant_canon(self.tmp / "checkout", "1.2.0")
        self.assert_red(
            checkout, "This clone has no .config/mise/conf.d/belt.toml link."
        )

    def test_the_pinned_tree_is_green(self) -> None:
        """The link's tree is the cache tree of the pin."""
        self.pin()
        tree = self.cached(PIN, "1.2.0")
        self.link(tree)
        done = self.check(tree)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(
            done.stdout,
            f"lint:belt-current: mise {MISE_VERSION}; the belt is the pinned"
            f" {PIN} (v1.2.0)\n",
        )

    def test_the_canons_own_tree_is_green(self) -> None:
        """The canon has no pin: its belt is the tree itself."""
        plant_canon(self.repo, "1.2.0")
        (self.repo / "security").mkdir()
        (self.repo / "security" / "identity.toml").write_text("", encoding="utf-8")
        run([tool(GIT, "git"), "add", "-A"], self.repo, self.env(), check=True)
        done = self.check(self.repo)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(
            done.stdout,
            f"lint:belt-current: mise {MISE_VERSION}; the belt is this tree,"
            " the canon\n",
        )

    def test_the_canon_answered_by_another_belt_is_red(self) -> None:
        """In the canon a belt that is not the tree is red by name."""
        plant_canon(self.repo, "1.2.0")
        (self.repo / "security").mkdir()
        (self.repo / "security" / "identity.toml").write_text("", encoding="utf-8")
        run([tool(GIT, "git"), "add", "-A"], self.repo, self.env(), check=True)
        other = plant_canon(self.tmp / "checkout", "1.2.0")
        done = self.check(other)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(
            done.stderr,
            f"belt-current: this is the canon, and the belt answering is {other},\n"
            f"  not this tree ({self.repo}). The tree is its own belt: unset\n"
            "  MISE_GLOBAL_CONFIG_FILE, which is the one carrier ranked above it.\n",
        )

    def test_ci_is_silent_on_the_belt(self) -> None:
        """In CI the canon action delivers the pin's belt: no local check."""
        self.pin()
        tree = plant_canon(self.tmp / "org-canon", "1.2.0")
        done = self.check(tree, GITHUB_ACTIONS="true")
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(
            done.stdout,
            f"lint:belt-current: mise {MISE_VERSION}; in CI the canon action"
            " delivers the belt at the pin\n",
        )


if __name__ == "__main__":
    unittest.main()
