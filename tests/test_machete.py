"""Planted rows for lint:machete's file list (#2008).

lint:machete ran cargo-machete with no arguments, so the tool walked
the directory itself and read every Cargo.toml git does not ignore,
tracked or not: an untracked scratch crate with an unused dependency
reddened the gate for a file that is not in the commit. The task now
passes the tracked manifests, from the one tracked-file producer
(#1986), as the tool's arguments.

The task's own body, read from mise/config.toml, runs through
`support.task_run` in a planted git tree with the belt's real
cargo-machete. Red rows assert the manifest the tool names, not just
the exit status.

Mutation-checked: run against `main`'s mise/config.toml, the
untracked-crate row fails (cargo-machete exits 1 naming
scratch/Cargo.toml).

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

import subprocess
from typing import TYPE_CHECKING

import pytest

from belt import decoded
from tests import support

if TYPE_CHECKING:
    from pathlib import Path

BELT = support.MISE
GIT = support.tool("git")
TASK = "lint:machete"

CLEAN = '[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n'
UNUSED = CLEAN + '\n[dependencies]\nserde = "1"\n'


def body() -> str:
    """Read lint:machete's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    table = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(table["run"], f"{TASK}.run")


@pytest.mark.usefixtures("_set_up_test_machete")
class TestMachete:
    """A planted tree; lint:machete's verdict on it."""

    @pytest.fixture
    def _set_up_test_machete(self, tmp_path: Path) -> None:
        """Make an empty git repository and a scrubbed environment."""
        support.tool("cargo-machete")
        tmp = tmp_path
        self.tree = tmp / "tree"
        self.tree.mkdir()
        self.env = support.env()
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        """Run git in the planted tree."""
        subprocess.run([GIT, *args], cwd=self.tree, env=self.env, check=True)

    def crate(self, where: str, manifest: str, *, track: bool) -> None:
        """Write a crate at `where`, tracked or not."""
        root = self.tree / where
        (root / "src").mkdir(parents=True)
        name = where.replace("/", "-") if where != "." else "root"
        (root / "Cargo.toml").write_text(manifest.format(name=name), encoding="utf-8")
        (root / "src" / "lib.rs").write_text("", encoding="utf-8")
        if track:
            self.git("add", "--", where)

    def run_task(self) -> subprocess.CompletedProcess[str]:
        """Run the task's body in the planted tree.

        Returns:
            The finished process.

        """
        as_mise = support.task_run(TASK, body())
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_an_untracked_crate_is_not_read(self) -> None:
        """An unused dependency in an untracked crate: green."""
        self.crate(".", CLEAN, track=True)
        self.crate("scratch", UNUSED, track=False)
        done = self.run_task()
        assert done.returncode == 0, done.stdout + done.stderr
        assert "scratch" not in done.stdout
        assert "lint:machete: no unused dependencies" in done.stdout

    def test_a_tracked_unused_dependency_reds(self) -> None:
        """An unused dependency in a tracked member: red, naming it."""
        self.crate(".", CLEAN, track=True)
        self.crate("fuzz", UNUSED, track=True)
        done = self.run_task()
        assert done.returncode != 0, done.stdout + done.stderr
        assert "fuzz/Cargo.toml" in done.stdout
        assert "serde" in done.stdout
        assert "lint:machete: no unused dependencies" not in done.stdout

    def test_no_tracked_manifest_skips(self) -> None:
        """No tracked Cargo.toml, an untracked one beside: skipped."""
        self.crate("scratch", UNUSED, track=False)
        done = self.run_task()
        assert done.returncode == 0, done.stdout + done.stderr
        assert done.stdout == "lint:machete: no Cargo.toml tracked, skipped\n"
