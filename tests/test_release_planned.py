"""Table tests for lint:release-planned (#1472).

Canon v1.69.3's Release PR sat in the merge queue behind four pull
requests. The queue squashed it on top of them, and its changelog,
written before they landed, names none of them. open-release-pr.sh now
writes `Planned-At: <sha>` into the Release PR body, which the squash
carries into the release commit; the lint reds a release commit whose
parent is not that sha, so the queue removes it before it lands.

The body under test is the task's own `run` string, read out of
mise/config.toml with tomllib and run by bash in a real git history.
Every row asserts the exact output (#650).
"""

import os
import subprocess
from pathlib import Path

import pytest

from belt import decoded
from belt.typed_pytest import fixture
from tests import support

CANON = support.CANON
BELT = CANON / "mise"
BASH = support.optional_tool("bash")
GIT = support.optional_tool("git")

NAME = "lint:release-planned"

# The release commit's message as the squash writes it: subject, the
# Release PR body, then the trailer the body ends with.
RELEASE = "chore: release v1.0.0 (#9)\n\nMerging this PR is the commitment point.\n"


def body() -> str:
    """Read the lint's run string as mise would render it.

    Returns:
        The task body with any `{% raw %}` / `{% endraw %}` lines removed.

    """
    config = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    task = decoded.as_object(tasks[NAME], NAME)
    run = decoded.as_str(task["run"], "the task's run")
    return "".join(
        line
        for line in run.splitlines(keepends=True)
        if line.strip() not in {"{% raw %}", "{% endraw %}"}
    )


BODY = body()


class History:
    """A scratch repository whose commits a row builds one at a time."""

    def __init__(self, root: Path, *, env: support.Env) -> None:
        """Initialise an empty repository at `root`."""
        self.make_env = env
        self.root = root
        self.env = self.make_env()
        self.env |= {
            "HOME": str(root),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        }
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        """Run git in the repository.

        Returns:
            Its stdout, stripped.

        """
        return subprocess.run(
            [support.tool("git"), *args],
            cwd=self.root,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def commit(self, message: str) -> str:
        """Commit with `message`, no hooks.

        Returns:
            The new commit's sha.

        """
        self.git("commit", "-q", "--allow-empty", "--no-verify", "-m", message)
        return self.git("rev-parse", "HEAD")

    def lint(self) -> subprocess.CompletedProcess[str]:
        """Run the lint at HEAD.

        Returns:
            The finished process, stdout and stderr captured as text.

        """
        as_mise = support.task_run("lint:release-planned", BODY)
        return subprocess.run(
            as_mise.argv,
            cwd=self.root,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


class ReleasePlanned:
    """Green where planned, red with both commits and the count, else skip."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Build a history with one commit on the default branch."""
        self.make_env = env
        if GIT is None or BASH is None:
            pytest.skip("git (or bash) not on PATH")
        self.repo = History(Path(str(tmp_path)).resolve(), env=self.make_env)
        self.planned = self.repo.commit("fix: the commit the release was planned at")

    def assert_out(self, line: str) -> None:
        """Assert exit 0 with exactly one stdout line and no stderr."""
        done = self.repo.lint()
        assert done.returncode == 0, done.stderr
        assert done.stdout == f"{NAME}: {line}\n"
        assert not done.stderr

    def assert_red(self, message: str) -> None:
        """Assert exit 1 with exactly `message` on stderr."""
        done = self.repo.lint()
        assert done.returncode == 1, done.stdout + done.stderr
        assert done.stderr == message
        assert not done.stdout


@fixture
def release_planned(tmp_path: Path, env: support.Env) -> ReleasePlanned:
    """Give each row its own ReleasePlanned.

    Returns:
        The row's ReleasePlanned.

    """
    return ReleasePlanned(tmp_path, env=env)


def test_a_release_on_its_planned_commit_is_green(
    release_planned: ReleasePlanned,
) -> None:
    """Planned-At equals HEAD^: nothing landed in between."""
    release_planned.repo.commit(f"{RELEASE}\nPlanned-At: {release_planned.planned}\n")
    release_planned.assert_out(
        f"the release lands on {release_planned.planned}, where it was planned"
    )


def test_a_release_three_commits_past_its_plan_is_red(
    release_planned: ReleasePlanned,
) -> None:
    """Three pull requests landed ahead of it in the queue: red, by count."""
    parent = [release_planned.repo.commit(f"fix: queued ahead {n}") for n in range(3)][
        -1
    ]
    release_planned.repo.commit(f"{RELEASE}\nPlanned-At: {release_planned.planned}\n")
    release_planned.assert_red(
        f"{NAME}: the release was planned at {release_planned.planned}"
        f" but lands on {parent}:\n"
        "  3 commits would ship without release notes;\n"
        "  the Release PR is regenerated on the next push (#1472)\n"
    )


def test_one_commit_past_its_plan_is_named_in_the_singular(
    release_planned: ReleasePlanned,
) -> None:
    """One pull request ahead of it: "1 commit", seen live on #1476."""
    parent = release_planned.repo.commit("fix: queued ahead")
    release_planned.repo.commit(f"{RELEASE}\nPlanned-At: {release_planned.planned}\n")
    release_planned.assert_red(
        f"{NAME}: the release was planned at {release_planned.planned}"
        f" but lands on {parent}:\n"
        "  1 commit would ship without release notes;\n"
        "  the Release PR is regenerated on the next push (#1472)\n"
    )


def test_the_trailer_amid_other_trailers_is_read(
    release_planned: ReleasePlanned,
) -> None:
    """The squash appends its own trailers after the body's."""
    release_planned.repo.commit(
        f"{RELEASE}\nPlanned-At: {release_planned.planned}\n"
        "Signed-off-by: bot <1+bot@users.noreply.github.com>\n"
    )
    release_planned.assert_out(
        f"the release lands on {release_planned.planned}, where it was planned"
    )


def test_a_commit_without_the_trailer_skips(release_planned: ReleasePlanned) -> None:
    """Every commit that is not a release commit skips clean, by name."""
    release_planned.repo.commit("fix: an ordinary pull request")
    release_planned.assert_out(
        "HEAD carries no Planned-At trailer, not a release commit, skipped"
    )


def test_a_trailer_that_is_not_a_full_sha_is_red(
    release_planned: ReleasePlanned,
) -> None:
    """A short or garbled trailer is never read as a match or a skip."""
    release_planned.repo.commit(
        f"{RELEASE}\nPlanned-At: {release_planned.planned[:12]}\n"
    )
    release_planned.assert_red(
        f"{NAME}: HEAD's Planned-At trailer is not one full commit sha:\n"
        f"  Planned-At: {release_planned.planned[:12]}\n"
    )


def test_a_plan_missing_from_the_checkout_is_red_by_name(
    release_planned: ReleasePlanned,
) -> None:
    """An unknown planned commit still reds, saying it is not here."""
    absent = "f" * 40
    release_planned.repo.commit(f"{RELEASE}\nPlanned-At: {absent}\n")
    release_planned.assert_red(
        f"{NAME}: the release was planned at {absent}"
        f" but lands on {release_planned.planned}:\n"
        f"  commits ({absent} is not in this checkout) would ship"
        " without release notes;\n"
        "  the Release PR is regenerated on the next push (#1472)\n"
    )
