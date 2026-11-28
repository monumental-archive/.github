"""Table tests for lint:belt-link (#1686).

The org hooks put a clone's belt link and its lock under `.config/mise/`,
and mise reads one lock per config directory, so the belt claims that
directory: a tracked file there is red by name.

The body under test is the task's own `run` string, read out of
mise/config.toml through decoded.py and run by bash in a planted git tree, not
a copy of it. Red rows assert the whole message.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from belt import decoded
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from pathlib import Path

CANON = support.CANON
NAME = "lint:belt-link"
CLAIM = (
    "lint:belt-link: the belt's link and lock live in .config/mise/; a repository's\n"
    "  own config is mise.toml at its root. Tracked there:\n"
)


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


def body() -> str:
    """Read the lint's run string from the belt.

    Returns:
        The task body.

    """
    belt = decoded.load_toml((CANON / "mise" / "config.toml").read_text("utf-8"))
    tasks = decoded.as_object(belt["tasks"], "the belt's [tasks]")
    task = decoded.as_object(tasks[NAME], NAME)
    return decoded.as_str(task["run"], f"{NAME}'s run")


class TheBeltClaimsItsDirectory:
    """Each row: what the repository tracks under .config/mise/."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant an empty repository."""
        self.make_env = env
        self.tmp = tmp_path
        self.env = self.make_env(self.tmp)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q", ".")
        (self.repo / "mise.toml").write_text("[tools]\n", encoding="utf-8")

    def git(self, *args: str) -> None:
        """Run git in the repository, checked."""
        run([support.tool("git"), *args], self.repo, self.env, check=True)

    def track(self, rel: str) -> None:
        """Write and stage a file at `rel`."""
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[tools]\n", encoding="utf-8")
        self.git("add", "--", rel)

    def lint(self) -> subprocess.CompletedProcess[str]:
        """Run the lint's body in the repository.

        Returns:
            The run.

        """
        as_mise = support.task_run("lint:belt-link", body())
        return run(as_mise.argv, self.repo, self.env | as_mise.env)


@fixture
def the_belt_claims_its_directory(
    tmp_path: Path, env: support.Env
) -> TheBeltClaimsItsDirectory:
    """Give each row its own TheBeltClaimsItsDirectory.

    Returns:
        The row's TheBeltClaimsItsDirectory.

    """
    return TheBeltClaimsItsDirectory(tmp_path, env=env)


def test_a_tracked_config_toml_is_red(
    the_belt_claims_its_directory: TheBeltClaimsItsDirectory,
) -> None:
    """A repository's own `.config/mise/config.toml`: red by name."""
    the_belt_claims_its_directory.track(".config/mise/config.toml")
    done = the_belt_claims_its_directory.lint()
    assert done.returncode == 1, done.stdout
    assert done.stderr == CLAIM + "  .config/mise/config.toml\n"


def test_a_tracked_conf_d_file_is_red(
    the_belt_claims_its_directory: TheBeltClaimsItsDirectory,
) -> None:
    """A tracked conf.d file, beside where the belt link goes: red."""
    the_belt_claims_its_directory.track(".config/mise/conf.d/x.toml")
    the_belt_claims_its_directory.track("mise.toml")
    done = the_belt_claims_its_directory.lint()
    assert done.returncode == 1, done.stdout
    assert done.stderr == CLAIM + "  .config/mise/conf.d/x.toml\n"


def test_nothing_tracked_there_is_green(
    the_belt_claims_its_directory: TheBeltClaimsItsDirectory,
) -> None:
    """The untracked links the hooks write are not the repository's."""
    the_belt_claims_its_directory.track("mise.toml")
    conf = the_belt_claims_its_directory.repo / ".config" / "mise" / "conf.d"
    conf.mkdir(parents=True)
    (conf / "belt.toml").symlink_to(the_belt_claims_its_directory.tmp / "belt.toml")
    (conf.parent / "mise.lock").symlink_to(
        the_belt_claims_its_directory.tmp / "mise.lock"
    )
    done = the_belt_claims_its_directory.lint()
    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout == "lint:belt-link: nothing tracked under .config/mise/\n"
