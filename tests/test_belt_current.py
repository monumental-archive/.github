"""Table tests for lint:belt-current: this machine's mise against CI's.

The script reads the mise version CI runs from the belt's own ci.yml
and compares this machine's: older is red, newer is a note, equal is
one line. Whether the belt answering is the tree's pin is not this
script's (#1988): every belt task refuses another pin's belt, and
mise/test_belt_refused.py holds that.

Each row plants a canon tree pinning CI's mise and a stub `mise` that
answers the row's version, then runs the shipped script with
ORG_CANON_DIR at the planted tree. The red row asserts the whole
message.
"""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from pathlib import Path

CANON = support.CANON
SCRIPT = CANON / "mise" / "belt-current.sh"
MISE_VERSION = "2026.11.7"


def run(
    argv: list[str], cwd: Path, env: dict[str, str], *, check: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run one command, its output captured as text, within a minute.

    Returns:
        The run.

    """
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=check,
        timeout=60,
    )


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


class TheMiseIsCurrent:
    """Each row: this machine's mise version, and the verdict."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant a canon tree pinning CI's mise and an empty repository."""
        self.make_env = env
        self.tmp = tmp_path
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.canon = plant_canon(self.tmp / "canon", "1.2.0")
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        run([support.tool("git"), "init", "-q", "."], self.repo, self.env(), check=True)

    def env(self, **extra: str) -> dict[str, str]:
        """Build the row's environment.

        Returns:
            The environment, with its own HOME.

        """
        return self.make_env(self.home, **extra)

    def check(self, version: str) -> subprocess.CompletedProcess[str]:
        """Run the shipped script with a stub mise answering `version`.

        Returns:
            The run.

        """
        stubs = self.tmp / "stubs"
        stubs.mkdir(exist_ok=True)
        (stubs / "mise").write_text(
            f'#!/bin/sh\necho "{version} macos-arm64 (2026-11-11)"\n',
            encoding="utf-8",
        )
        (stubs / "mise").chmod(0o755)
        path = f"{stubs}{os.pathsep}{os.environ['PATH']}"
        return run(
            [support.tool("bash"), str(SCRIPT)],
            self.repo,
            self.env(ORG_CANON_DIR=str(self.canon), PATH=path),
        )


@fixture
def the_mise_is_current(tmp_path: Path, env: support.Env) -> TheMiseIsCurrent:
    """Give each row its own TheMiseIsCurrent.

    Returns:
        The row's TheMiseIsCurrent.

    """
    return TheMiseIsCurrent(tmp_path, env=env)


def test_the_same_mise_is_one_line(the_mise_is_current: TheMiseIsCurrent) -> None:
    """CI's own version: green, in one line naming both."""
    done = the_mise_is_current.check(MISE_VERSION)
    assert (done.returncode, done.stderr) == (0, "")
    assert (
        done.stdout
        == f"lint:belt-current: mise {MISE_VERSION}; CI runs {MISE_VERSION}\n"
    )


def test_an_older_mise_is_red(the_mise_is_current: TheMiseIsCurrent) -> None:
    """Older than CI's: red, naming the self-update."""
    done = the_mise_is_current.check("2026.8.3")
    assert done.returncode == 1, done.stdout
    assert done.stderr == (
        f"lint:belt-current: this machine runs mise 2026.8.3; CI runs"
        f" {MISE_VERSION}.\n"
        "  mise keeps the tools current and never updates itself, and an older\n"
        "  mise reads the lockfile and the registry differently. Run:\n"
        f"    mise self-update {MISE_VERSION}\n"
    )


def test_a_newer_mise_is_a_note(the_mise_is_current: TheMiseIsCurrent) -> None:
    """Newer than CI's: a note, then green."""
    done = the_mise_is_current.check("2026.12.1")
    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout == (
        f"belt-current: note — mise 2026.12.1 here is newer than CI's"
        f" {MISE_VERSION}\n"
        f"lint:belt-current: mise 2026.12.1; CI runs {MISE_VERSION}\n"
    )


def test_a_belt_with_another_pin_is_not_this_lints(
    the_mise_is_current: TheMiseIsCurrent,
) -> None:
    """A gate stub pinning another tree: this lint says nothing of it (#1988)."""
    wf = the_mise_is_current.repo / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "gate.yml").write_text(
        "jobs:\n  ci:\n    uses: example-org/.github/.github/workflows/"
        f"ci.yml@{'b' * 40} # v1.3.0\n",
        encoding="utf-8",
    )
    done = the_mise_is_current.check(MISE_VERSION)
    assert (done.returncode, done.stderr) == (0, "")
