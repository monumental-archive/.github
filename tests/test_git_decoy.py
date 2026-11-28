"""Planted rows for the decoy the canon's `test` task runs under (#1463).

The first rows run the `test` task's own body, decoded from the canon's
`mise.toml`, over a planted tree: the belt, and in `tests/` a passing
test and one that runs `git add` with whatever environment it was
given. The body runs with `GIT_DIR` at a throwaway repository, the
environment git hands a pre-push hook in a linked worktree (#857). The
throwaway stands in for the real repository: its index must come out
byte-identical, and the leak must be red by name.

The later rows drive the helper directly, one per kind of change a leak
can make, each asserting the MESSAGE as well as the status.

Every git call in this file passes a scrubbed `env=`: the rows run under
the very hook they are about.
"""

import os
import pathlib
import subprocess
import tempfile
from pathlib import Path

import pytest

from belt import decoded
from belt import git_decoy as gd
from belt.typed_pytest import fixture
from tests import support

_HERE = support.MISE

GIT = support.tool("git")
BASH = support.tool("bash")
IDENTITY = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}
# The helper removes every GIT_*, identity included, so a planted commit
# brings its own; HOME is the scratch directory, so no machine config.
COMMIT = (
    "git -c user.name=t -c user.email=t@example.invalid -c commit.gpgsign=false commit"
)
PASSING = """\
def test_ok():
    pass
"""

# The planted fault: `git add` in a fresh directory, with the environment
# the test was handed. `env=` is the one thing a careful harness adds.
LEAKY = """\
import os
import pathlib
import subprocess

ENV = {ENV}


def test_stage_a_fixture(tmp_path):
    if ENV is not None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, env=ENV, check=True)
    pathlib.Path(tmp_path, "leak.txt").write_text("x\\n")
    subprocess.run(["git", "add", "leak.txt"], cwd=tmp_path, env=ENV, check=True)
"""

# The careful harness's environment, planted as code into a module that
# cannot import support.py: what git needs and nothing of git's own.
SCRUB = '{"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}'


def body() -> str:
    """Decode the `test` task's body from the canon's own mise.toml.

    Returns:
        The bash source mise runs.

    """
    config = decoded.load_toml((_HERE.parent / "mise.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the [tasks] table")
    return decoded.as_str(decoded.as_object(tasks["test"], "test")["run"], "the run")


def throwaway(root: pathlib.Path, *, env: support.Env) -> pathlib.Path:
    """Make a repository standing in for the real one.

    Returns:
        Its path.

    """
    root.mkdir()
    environ = {**env(), **IDENTITY}
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "real"]):
        subprocess.run([GIT, "-C", str(root), *args], env=environ, check=True)
    return root


def snapshot(repo: pathlib.Path, *, env: support.Env) -> tuple[bytes, str]:
    """Read what a leak would change in a repository.

    Returns:
        The index file's bytes and the refs.

    """
    refs = subprocess.run(
        [GIT, "-C", str(repo), "for-each-ref"],
        env={**env(), **IDENTITY},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return (repo / ".git" / "index").read_bytes(), refs


class Task:
    """The `test` task's body, run over a planted tree."""

    def __init__(self, tmp_path: Path, *, belt: support.Belt, env: support.Env) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.plant_belt = belt
        self.make_env = env
        self.tmp = tmp_path

    def plant(self, *, scrubbed: bool) -> tuple[pathlib.Path, pathlib.Path]:
        """Build a tree holding one test per root and the git-running one.

        Returns:
            The tree, and the throwaway repository GIT_DIR names.

        """
        tmp = tempfile.mkdtemp(dir=self.tmp)
        base = pathlib.Path(tmp)
        tree = base / "tree"
        # The runner and all it reads, as the tree's own: the whole belt,
        # and in `tests/`, the one root the body names, the planted tests.
        self.plant_belt(tree)
        (tree / "tests").mkdir()
        (tree / "tests" / "test_ok.py").write_text(PASSING, encoding="utf-8")
        leaky = LEAKY.replace("{ENV}", SCRUB if scrubbed else "None")
        (tree / "tests" / "test_leaky.py").write_text(leaky, encoding="utf-8")
        return tree, throwaway(base / "real", env=self.make_env)


@fixture
def task(tmp_path: Path, belt: support.Belt, env: support.Env) -> Task:
    """Give each row its own Task."""
    return Task(tmp_path, belt=belt, env=env)


def run_body(
    tree: pathlib.Path, real: pathlib.Path, *, env: support.Env
) -> subprocess.CompletedProcess[str]:
    """Run the body as a pre-push hook in a linked worktree would.

    Returns:
        The finished process.

    """
    return subprocess.run(
        [BASH, "-euo", "pipefail", "-c", body()],
        cwd=tree,
        env={**env(), "GIT_DIR": str(real / ".git")},
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_leaking_test_is_red_and_never_reaches_the_repository(
    task: Task, env: support.Env
) -> None:
    """A bare `git add` reds the task by path, and the real index holds.

    The row that fails before #1463: the body exited 0 and the
    throwaway repository's index gained `leak.txt`.
    """
    tree, real = task.plant(scrubbed=False)
    before = snapshot(real, env=env)
    r = run_body(tree, real, env=env)
    assert snapshot(real, env=env) == before, "the leak reached the repository"
    assert r.returncode == 1, r.stderr
    assert gd.MESSAGE in r.stderr
    assert "staged: A\tleak.txt" in r.stderr


def test_a_scrubbed_test_is_green(task: Task, env: support.Env) -> None:
    """The same tree, with the scrub its neighbours carry, passes."""
    tree, real = task.plant(scrubbed=True)
    before = snapshot(real, env=env)
    r = run_body(tree, real, env=env)
    assert r.returncode == 0, r.stderr
    assert gd.MESSAGE not in r.stderr
    assert snapshot(real, env=env) == before


class Helper:
    """`run` names each kind of change a leak can make."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant a decoy in a scratch directory."""
        self.make_env = env
        self.decoy = pathlib.Path(str(tmp_path)) / "decoy"
        self.decoy.mkdir()
        gd.plant(self.decoy)
        self.work = pathlib.Path(str(tmp_path)) / "work"
        self.work.mkdir()

    def leak_env(self) -> dict[str, str]:
        """Build the environment a root's test run gets under the decoy.

        Returns:
            The environment.

        """
        return {
            **self.make_env(),
            "HOME": str(self.work),
            "XDG_CONFIG_HOME": str(self.work),
        }

    def enter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set the world the helper runs a script under the decoy in (#1697)."""
        for name, value in (self.leak_env()).items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(self.work)


@fixture
def helper(tmp_path: Path, env: support.Env) -> Helper:
    """Give each row its own Helper."""
    return Helper(tmp_path, env=env)


def test_plant_makes_the_decoy_and_exits_0(
    helper: Helper, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`plant DIR` makes a repository there: the task's first call."""
    fresh = helper.work / "fresh"
    fresh.mkdir()
    for name, value in (helper.leak_env()).items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(helper.work)
    status = gd.main(["plant", str(fresh)])
    _out, err = capfd.readouterr()
    assert (status, err) == (0, "")
    assert (fresh / ".git").is_dir()


def test_run_with_no_command_is_a_usage_error(
    helper: Helper, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run DIR` and nothing to run is argparse's exit 2, by name."""
    with pytest.raises(SystemExit) as ended:
        for name, value in (helper.leak_env()).items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(helper.work)
        gd.main(["run", str(helper.decoy)])
    _out, err = capfd.readouterr()
    assert ended.value.code == 2
    assert "run: no command after the decoy" in err


@pytest.mark.parametrize(
    ("script", "wanted"),
    [
        pytest.param(
            f"{COMMIT} -q --allow-empty -m leaked",
            ["new commit: leaked"],
            id="commit",
        ),
        pytest.param(
            "git branch topic", ["new or moved ref: refs/heads/topic"], id="branch"
        ),
        pytest.param(
            "git checkout -q -b topic",
            ["HEAD moved: ref: refs/heads/", "new or moved ref: refs/heads/topic"],
            id="switch",
        ),
        pytest.param(
            "echo x > a.txt && git add a.txt", ["staged: A\ta.txt"], id="stage"
        ),
        pytest.param("git config user.name leaked", ["config rewritten"], id="config"),
    ],
)
def test_each_change_is_named(
    helper: Helper,
    script: str,
    wanted: list[str],
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commit, branch, switch, stage and config each red with what changed."""
    helper.enter(monkeypatch)
    status = gd.main(["run", str(helper.decoy), "--", BASH, "-c", script])
    err = capfd.readouterr().err
    assert status == 1, err
    assert gd.MESSAGE in err
    for line in wanted:
        assert line in err


def test_no_git_passes_the_status_through(
    helper: Helper, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that leaves the decoy alone answers with its own status."""
    helper.enter(monkeypatch)
    status = gd.main(["run", str(helper.decoy), "--", BASH, "-c", "exit 0"])
    out, err = capfd.readouterr()
    assert (status, err) == (0, "")
    helper.enter(monkeypatch)
    status = gd.main(["run", str(helper.decoy), "--", BASH, "-c", "exit 3"])
    err = capfd.readouterr().err
    assert status == 3
    assert gd.MESSAGE not in err


def test_the_child_sees_the_decoy_and_no_other_git(
    helper: Helper, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """GIT_DIR and GIT_INDEX_FILE name the decoy; GIT_WORK_TREE is unset."""
    script = (
        'test "$GIT_DIR" = "$D/.git" '
        '&& test "$GIT_INDEX_FILE" = "$D/.git/index" '
        '&& test -z "${GIT_WORK_TREE+set}" '
        '|| { echo "env: $(env | grep ^GIT_)" >&2; exit 9; }'
    )
    helper.enter(monkeypatch)
    status = gd.main([
        "run",
        str(helper.decoy),
        "--",
        BASH,
        "-c",
        script.replace("$D", str(helper.decoy.resolve())),
    ])
    err = capfd.readouterr().err
    assert (status, err) == (0, "")
