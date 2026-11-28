"""Planted rows for the decoy the canon's `test` task runs under (#1463).

The first rows run the `test` task's own body, decoded from the canon's
`mise.toml`, over a planted tree: one test file per discovery root, and
in `mise/` one that runs `git add` with whatever environment it was
given. The body runs with `GIT_DIR` at a throwaway repository, the
environment git hands a pre-push hook in a linked worktree (#857). The
throwaway stands in for the real repository: its index must come out
byte-identical, and the leak must be red by name.

The later rows drive the helper directly, one per kind of change a leak
can make, each asserting the MESSAGE as well as the status.

Every git call in this file passes a scrubbed `env=`: the rows run under
the very hook they are about.
"""

import contextlib
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from belt import decoded, typed_std
from belt import git_decoy as gd
from tests import support

_HERE = support.MISE

GIT = support.tool("git")
BASH = support.tool("bash")
CLEAN = support.env()
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
# The roots the body names: `unittest_guarded.py roots <dir>…` (#2056).
ROOTS_RE = re.compile(r"unittest_guarded\.py roots((?: [^\s\\]+)+)")

PASSING = """\
import unittest


class T(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)
"""

# The planted fault: `git add` in a fresh directory, with the environment
# the test was handed. `env=` is the one thing a careful harness adds.
LEAKY = """\
import os
import pathlib
import subprocess
import tempfile
import unittest

ENV = {ENV}


class T(unittest.TestCase):
    def test_stage_a_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            if ENV is not None:
                subprocess.run(["git", "init", "-q"], cwd=d, env=ENV, check=True)
            pathlib.Path(d, "leak.txt").write_text("x\\n")
            subprocess.run(["git", "add", "leak.txt"], cwd=d, env=ENV, check=True)
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


def roots() -> list[str]:
    """Read the discovery roots the `test` task names.

    Returns:
        Each root the `unittest_guarded.py roots` call names, in order.

    Raises:
        AssertionError: the body names none, so a row would plant no
            root and prove nothing.

    """
    found: list[str] = []
    for match in ROOTS_RE.finditer(body()):
        found += (typed_std.group(match, 1) or "").split()
    if not found:
        msg = "the test task names no `unittest_guarded.py roots` call"
        raise AssertionError(msg)
    return found


def throwaway(root: pathlib.Path) -> pathlib.Path:
    """Make a repository standing in for the real one.

    Returns:
        Its path.

    """
    root.mkdir()
    env = {**CLEAN, **IDENTITY}
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "real"]):
        subprocess.run([GIT, "-C", str(root), *args], env=env, check=True)
    return root


def snapshot(repo: pathlib.Path) -> tuple[bytes, str]:
    """Read what a leak would change in a repository.

    Returns:
        The index file's bytes and the refs.

    """
    refs = subprocess.run(
        [GIT, "-C", str(repo), "for-each-ref"],
        env={**CLEAN, **IDENTITY},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return (repo / ".git" / "index").read_bytes(), refs


@pytest.mark.usefixtures("_stack")
class TestTask:
    """The `test` task's body, run over a planted tree."""

    @pytest.fixture
    def _stack(self, request: pytest.FixtureRequest) -> None:
        """Hold the row's cleanups, undone last first after it ends."""
        self.stack = contextlib.ExitStack()
        request.addfinalizer(self.stack.close)

    def plant(self, *, scrubbed: bool) -> tuple[pathlib.Path, pathlib.Path]:
        """Build a tree holding one test per root and the git-running one.

        Returns:
            The tree, and the throwaway repository GIT_DIR names.

        """
        tmp = tempfile.TemporaryDirectory()
        self.stack.callback(tmp.cleanup)
        base = pathlib.Path(tmp.name)
        tree = base / "tree"
        # The runner and all it reads, as the tree's mise/: the whole
        # belt, its own tests taken out, since the tree's tests are the
        # ones planted here.
        for test in support.belt(tree / "mise").glob("test_*.py"):
            test.unlink()
        for root in roots():
            (tree / root).mkdir(parents=True, exist_ok=True)
            (tree / root / "test_ok.py").write_text(PASSING, encoding="utf-8")
        leaky = LEAKY.replace("{ENV}", SCRUB if scrubbed else "None")
        (tree / "mise" / "test_leaky.py").write_text(leaky, encoding="utf-8")
        return tree, throwaway(base / "real")

    @staticmethod
    def run_body(
        tree: pathlib.Path, real: pathlib.Path
    ) -> subprocess.CompletedProcess[str]:
        """Run the body as a pre-push hook in a linked worktree would.

        Returns:
            The finished process.

        """
        return subprocess.run(
            [BASH, "-euo", "pipefail", "-c", body()],
            cwd=tree,
            env={**CLEAN, "GIT_DIR": str(real / ".git")},
            capture_output=True,
            text=True,
            check=False,
        )

    def test_a_leaking_test_is_red_and_never_reaches_the_repository(self) -> None:
        """A bare `git add` reds the task by path, and the real index holds.

        The row that fails before #1463: the body exited 0 and the
        throwaway repository's index gained `leak.txt`.
        """
        tree, real = self.plant(scrubbed=False)
        before = snapshot(real)
        r = self.run_body(tree, real)
        assert snapshot(real) == before, "the leak reached the repository"
        assert r.returncode == 1, r.stderr
        assert gd.MESSAGE in r.stderr
        assert "staged: A\tleak.txt" in r.stderr

    def test_a_scrubbed_test_is_green(self) -> None:
        """The same tree, with the scrub its neighbours carry, passes."""
        tree, real = self.plant(scrubbed=True)
        before = snapshot(real)
        r = self.run_body(tree, real)
        assert r.returncode == 0, r.stderr
        assert gd.MESSAGE not in r.stderr
        assert snapshot(real) == before


@pytest.mark.usefixtures("_set_up_test_helper")
class TestHelper:
    """`run` names each kind of change a leak can make."""

    @pytest.fixture
    def _set_up_test_helper(self, tmp_path: Path) -> None:
        """Plant a decoy in a scratch directory."""
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
        return {**CLEAN, "HOME": str(self.work), "XDG_CONFIG_HOME": str(self.work)}

    def leak(
        self, script: str, *, capfd: pytest.CaptureFixture[str]
    ) -> tuple[int, str]:
        """Call the helper in-process to run a script under the decoy (#1697).

        Returns:
            The helper's exit status and its standard error.

        """
        with pytest.MonkeyPatch.context() as patched:
            for name, value in (self.leak_env()).items():
                patched.setenv(name, value)
            patched.chdir(self.work)
            status = gd.main(["run", str(self.decoy), "--", BASH, "-c", script])
        out, err = capfd.readouterr()
        return status, err

    def test_the_script_contract(self) -> None:
        """Started as a process, `run` hands back its command's status."""
        r = subprocess.run(
            [
                *(sys.executable, str(_HERE / "git_decoy.py")),
                *("run", str(self.decoy), "--", BASH, "-c", "exit 3"),
            ],
            cwd=self.work,
            env=self.leak_env(),
            capture_output=True,
            text=True,
            check=False,
        )
        assert (r.returncode, r.stderr) == (3, "")

    def test_plant_makes_the_decoy_and_exits_0(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """`plant DIR` makes a repository there: the task's first call."""
        fresh = self.work / "fresh"
        fresh.mkdir()
        with pytest.MonkeyPatch.context() as patched:
            for name, value in (self.leak_env()).items():
                patched.setenv(name, value)
            patched.chdir(self.work)
            status = gd.main(["plant", str(fresh)])
        out, err = capfd.readouterr()
        assert (status, err) == (0, "")
        assert (fresh / ".git").is_dir()

    def test_run_with_no_command_is_a_usage_error(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """`run DIR` and nothing to run is argparse's exit 2, by name."""
        with pytest.MonkeyPatch.context() as patched:
            for name, value in (self.leak_env()).items():
                patched.setenv(name, value)
            patched.chdir(self.work)
            status = gd.main(["run", str(self.decoy)])
        out, err = capfd.readouterr()
        assert status == 2
        assert "run: no command after the decoy" in err

    def test_each_change_is_named(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Commit, branch, switch and stage each red with what changed."""
        rows = [
            (f"{COMMIT} -q --allow-empty -m leaked", ["new commit: leaked"]),
            ("git branch topic", ["new or moved ref: refs/heads/topic"]),
            (
                "git checkout -q -b topic",
                ["HEAD moved: ref: refs/heads/", "new or moved ref: refs/heads/topic"],
            ),
            ("echo x > a.txt && git add a.txt", ["staged: A\ta.txt"]),
            ("git config user.name leaked", ["config rewritten"]),
        ]
        for script, wanted in rows:
            self.setUp()
            status, err = self.leak(script, capfd=capfd)
            assert status == 1, err
            assert gd.MESSAGE in err
            for line in wanted:
                assert line in err

    def test_no_git_passes_the_status_through(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A run that leaves the decoy alone answers with its own status."""
        assert self.leak("exit 0", capfd=capfd) == (0, "")
        status, err = self.leak("exit 3", capfd=capfd)
        assert status == 3
        assert gd.MESSAGE not in err

    def test_the_child_sees_the_decoy_and_no_other_git(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """GIT_DIR and GIT_INDEX_FILE name the decoy; GIT_WORK_TREE is unset."""
        script = (
            'test "$GIT_DIR" = "$D/.git" '
            '&& test "$GIT_INDEX_FILE" = "$D/.git/index" '
            '&& test -z "${GIT_WORK_TREE+set}" '
            '|| { echo "env: $(env | grep ^GIT_)" >&2; exit 9; }'
        )
        status, err = self.leak(
            script.replace("$D", str(self.decoy.resolve())), capfd=capfd
        )
        assert (status, err) == (0, "")
