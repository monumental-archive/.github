#!/usr/bin/env python3
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

import os
import pathlib
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from typing import override

import decoded
import git_decoy as gd
import hermetic
import typed_std

_HERE = pathlib.Path(__file__).resolve().parent

GIT = hermetic.tool("git")
BASH = hermetic.tool("bash")
CLEAN = hermetic.env()
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
ROOT_RE = re.compile(r"--start-directory\s+(\S+)")

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
# cannot import hermetic.py: what git needs and nothing of git's own.
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
        Each `--start-directory` argument, in order.

    """
    found: list[str] = []
    for match in ROOT_RE.finditer(body()):
        root = typed_std.group(match, 1)
        if root is not None:
            found.append(root)
    return found


def throwaway(root: pathlib.Path) -> pathlib.Path:
    """Make a repository standing in for the real one.

    Returns:
        Its path.

    """
    root.mkdir()
    env = {**CLEAN, **IDENTITY}
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "real"]):
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run([GIT, "-C", str(root), *args], env=env, check=True)
    return root


def snapshot(repo: pathlib.Path) -> tuple[bytes, str]:
    """Read what a leak would change in a repository.

    Returns:
        The index file's bytes and the refs.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    refs = subprocess.run(
        [GIT, "-C", str(repo), "for-each-ref"],
        env={**CLEAN, **IDENTITY},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return (repo / ".git" / "index").read_bytes(), refs


class Task(unittest.TestCase):
    """The `test` task's body, run over a planted tree."""

    def plant(self, *, scrubbed: bool) -> tuple[pathlib.Path, pathlib.Path]:
        """Build a tree holding one test per root and the git-running one.

        Returns:
            The tree, and the throwaway repository GIT_DIR names.

        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = pathlib.Path(tmp.name)
        tree = base / "tree"
        # The runner and all it reads, as the tree's mise/: the whole
        # belt, its own tests taken out, since the tree's tests are the
        # ones planted here.
        for test in hermetic.belt(tree / "mise").glob("test_*.py"):
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
        # ruff: ignore[subprocess-without-shell-equals-true]
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
        self.assertEqual(snapshot(real), before, "the leak reached the repository")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn(gd.MESSAGE, r.stderr)
        self.assertIn("staged: A\tleak.txt", r.stderr)

    def test_a_scrubbed_test_is_green(self) -> None:
        """The same tree, with the scrub its neighbours carry, passes."""
        tree, real = self.plant(scrubbed=True)
        before = snapshot(real)
        r = self.run_body(tree, real)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(gd.MESSAGE, r.stderr)
        self.assertEqual(snapshot(real), before)


class Helper(unittest.TestCase):
    """`run` names each kind of change a leak can make."""

    @override
    def setUp(self) -> None:
        """Plant a decoy in a scratch directory."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.decoy = pathlib.Path(tmp.name) / "decoy"
        self.decoy.mkdir()
        gd.plant(self.decoy)
        self.work = pathlib.Path(tmp.name) / "work"
        self.work.mkdir()

    def leak_env(self) -> dict[str, str]:
        """Build the environment a root's test run gets under the decoy.

        Returns:
            The environment.

        """
        return {**CLEAN, "HOME": str(self.work), "XDG_CONFIG_HOME": str(self.work)}

    def leak(self, script: str) -> tuple[int, str]:
        """Call the helper in-process to run a script under the decoy (#1697).

        Returns:
            The helper's exit status and its standard error.

        """
        done = hermetic.call_main(
            lambda: gd.main(["run", str(self.decoy), "--", BASH, "-c", script]),
            env=self.leak_env(),
            cwd=self.work,
        )
        return done.returncode, done.stderr

    def test_the_script_contract(self) -> None:
        """Started as a process, `run` hands back its command's status."""
        # ruff: ignore[subprocess-without-shell-equals-true]
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
        self.assertEqual((r.returncode, r.stderr), (3, ""))

    def test_plant_makes_the_decoy_and_exits_0(self) -> None:
        """`plant DIR` makes a repository there: the task's first call."""
        fresh = self.work / "fresh"
        fresh.mkdir()
        done = hermetic.call_main(
            lambda: gd.main(["plant", str(fresh)]), env=self.leak_env(), cwd=self.work
        )
        self.assertEqual((0, ""), (done.returncode, done.stderr))
        self.assertTrue((fresh / ".git").is_dir())

    def test_run_with_no_command_is_a_usage_error(self) -> None:
        """`run DIR` and nothing to run is argparse's exit 2, by name."""
        done = hermetic.call_main(
            lambda: gd.main(["run", str(self.decoy)]),
            env=self.leak_env(),
            cwd=self.work,
        )
        self.assertEqual(2, done.returncode)
        self.assertIn("run: no command after the decoy", done.stderr)

    def test_each_change_is_named(self) -> None:
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
            with self.subTest(script=script):
                self.setUp()
                status, err = self.leak(script)
                self.assertEqual(status, 1, err)
                self.assertIn(gd.MESSAGE, err)
                for line in wanted:
                    self.assertIn(line, err)

    def test_no_git_passes_the_status_through(self) -> None:
        """A run that leaves the decoy alone answers with its own status."""
        self.assertEqual(self.leak("exit 0"), (0, ""))
        status, err = self.leak("exit 3")
        self.assertEqual(status, 3)
        self.assertNotIn(gd.MESSAGE, err)

    def test_the_child_sees_the_decoy_and_no_other_git(self) -> None:
        """GIT_DIR and GIT_INDEX_FILE name the decoy; GIT_WORK_TREE is unset."""
        script = (
            'test "$GIT_DIR" = "$D/.git" '
            '&& test "$GIT_INDEX_FILE" = "$D/.git/index" '
            '&& test -z "${GIT_WORK_TREE+set}" '
            '|| { echo "env: $(env | grep ^GIT_)" >&2; exit 9; }'
        )
        status, err = self.leak(script.replace("$D", str(self.decoy.resolve())))
        self.assertEqual((status, err), (0, ""))


if __name__ == "__main__":
    unittest.main()
