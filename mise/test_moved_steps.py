#!/usr/bin/env python3
"""Rows for the steps #1660 moved out of vex-attest and source-attest.

Routing each step's workflow commands through the canon's writer grew
its `run:` block past the ceiling (#1352), and the one permitted
response moved the block into a canon script the step runs:

- mise/vex-decisions.sh: the decisions vex-attest signs, as stele's
  arguments; a notice and nothing when there is nothing to sign; a
  refusal by name of a push that changed more than one decision.
- .github/actions/source-attest/resolve.sh: the stage guard, the canon
  tree and its ref, and the environment the later steps read.

Each row runs the script itself, by its path, in a scratch tree.

stdlib `unittest`, matching the rest of mise/; run through the gate as
`mise run test`.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override

import hermetic

HERE = Path(__file__).resolve().parent
CANON = HERE.parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
ONE_PER_MERGE = (HERE / "vex-one-per-merge.txt").read_text("utf-8").strip()


def run(
    script: Path, cwd: Path, *args: str, **env: str
) -> subprocess.CompletedProcess[str]:
    """Run a script by its path, with its arguments.

    Returns:
        The finished process.

    """
    return subprocess.run(
        [BASH, str(script), *args],
        cwd=cwd,
        env=hermetic.env(**env),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


class VexDecisions(unittest.TestCase):
    """mise/vex-decisions.sh, in a planted git tree."""

    SCRIPT = HERE / "vex-decisions.sh"

    @override
    def setUp(self) -> None:
        """Make a repository with no decisions yet."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tree = Path(tmp.name)
        self.git("init", "-q")
        (self.tree / "security" / "vex").mkdir(parents=True)

    def git(self, *args: str) -> str:
        """Run git in the tree.

        Returns:
            Its standard output.

        """
        return subprocess.run(
            [GIT, *args],
            cwd=self.tree,
            env=hermetic.env(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def decide(self, *names: str) -> None:
        """Track one decision document per name, and commit them."""
        for name in names:
            (self.tree / "security" / "vex" / f"{name}.openvex.json").write_text(
                "{}", "utf-8"
            )
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "decide")

    def decisions(self, **env: str) -> subprocess.CompletedProcess[str]:
        """Ask the script for this run's decisions, written to self.out.

        Returns:
            The finished process.

        """
        self.out = self.tree.parent / f"{self.tree.name}-args"
        self.addCleanup(self.out.unlink, missing_ok=True)
        return run(
            self.SCRIPT,
            self.tree,
            str(self.out),
            REPO="o/r",
            DECISIONS="security/vex/*.openvex.json",
            ONE_PER_MERGE=str(HERE / "vex-one-per-merge.txt"),
            **env,
        )

    def test_a_dispatch_signs_every_tracked_decision_unsigned_only(self) -> None:
        """The state mode, then one --decision= per tracked file."""
        self.decide("a", "b")
        done = self.decisions()
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)
        argv: list[str] = [
            "--unsigned-only",
            "--store",
            "o/r",
            "--decision=security/vex/a.openvex.json",
            "--decision=security/vex/b.openvex.json",
            "",
        ]
        self.assertEqual(self.out.read_text("utf-8").split("\0"), argv)

    def test_nothing_to_sign_is_a_notice_and_no_arguments(self) -> None:
        """No decisions: the notice, no arguments written, exit 0."""
        self.git("commit", "-q", "--allow-empty", "-m", "empty")
        done = self.decisions()
        self.assertEqual(
            (done.returncode, done.stdout),
            (0, "::notice::security/vex/ holds no decisions\n"),
        )
        self.assertEqual(self.out.read_text("utf-8"), "")

    def fake_list(self, *paths: str) -> str:
        """Write a stand-in for ORG_PYTHON that lists the changed decisions.

        Returns:
            Its path.

        """
        path = self.tree.parent / f"{self.tree.name}-python"
        lines = "".join(f"echo {p}\n" for p in paths)
        path.write_text(f"#!/usr/bin/env bash\n{lines}", "utf-8")
        path.chmod(0o755)
        self.addCleanup(path.unlink)
        return str(path)

    def test_a_push_of_one_decision_signs_it(self) -> None:
        """The range mode: no mode flags, the one changed decision."""
        self.decide("a")
        before = self.git("rev-parse", "HEAD").strip()
        done = self.decisions(
            BEFORE=before, ORG_PYTHON=self.fake_list("security/vex/a.openvex.json")
        )
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)
        self.assertEqual(
            self.out.read_text("utf-8"), "--decision=security/vex/a.openvex.json\0"
        )

    def test_a_push_of_two_decisions_is_refused_by_name(self) -> None:
        """The one sentence lint:vex-one-per-merge prints, as an error."""
        self.decide("a")
        before = self.git("rev-parse", "HEAD").strip()
        done = self.decisions(
            BEFORE=before,
            ORG_PYTHON=self.fake_list(
                "security/vex/a.openvex.json", "security/vex/b.openvex.json"
            ),
        )
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertTrue(done.stdout.startswith("::error::"), done.stdout)
        self.assertIn(ONE_PER_MERGE.split("\n", 1)[0][:40], done.stdout)
        self.assertEqual(self.out.read_text("utf-8"), "")


def plant(root: Path, *, policy: bool = True) -> Path:
    """Plant a canon tree with the action, the writer and the policy.

    Returns:
        The script's path in the tree.

    """
    hermetic.canon(root, ".github/actions/source-attest", "slsa/verify-policy.json")
    if not policy:
        (root / "slsa" / "verify-policy.json").unlink()
    return root / ".github" / "actions" / "source-attest" / "resolve.sh"


class SourceAttestResolve(unittest.TestCase):
    """.github/actions/source-attest/resolve.sh, in a planted canon tree."""

    @override
    def setUp(self) -> None:
        """Make a scratch directory, a runner temp and a GITHUB_ENV."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(os.path.realpath(tmp.name))
        self.runner = self.tmp / "runner"
        self.runner.mkdir()
        self.github_env = self.tmp / "github-env"
        self.github_env.write_text("", "utf-8")

    def resolve(
        self, script: Path, stage: str, repo: str = "someone/consumer"
    ) -> subprocess.CompletedProcess[str]:
        """Run the guard.

        Returns:
            The finished process.

        """
        return run(
            script,
            self.tmp,
            SA_STAGE=stage,
            GITHUB_REPOSITORY=repo,
            GITHUB_SHA="c" * 40,
            RUNNER_TEMP=str(self.runner),
            GITHUB_ENV=str(self.github_env),
        )

    def test_an_actions_path_names_the_pinned_ref(self) -> None:
        """The ref is the path segment the caller pinned; the env is written."""
        root = self.tmp / "_actions" / "monumental-archive" / ".github" / "v1.2.3"
        done = self.resolve(plant(root), "claims")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(
            self.github_env.read_text("utf-8"),
            f"SA_WORK={self.runner}/source-attest\n"
            f"SA_CANON_ROOT={root}\n"
            "SA_CANON_REF=v1.2.3\n"
            "SA_ISSUER=https://token.actions.githubusercontent.com\n",
        )

    def test_another_stage_is_refused_by_name(self) -> None:
        """Only claims and attest are stages."""
        root = self.tmp / "_actions" / "monumental-archive" / ".github" / "v1.2.3"
        done = self.resolve(plant(root), "sign")
        self.assertEqual(
            (done.returncode, done.stderr),
            (
                1,
                (
                    "::error::resolve.sh: source-attest stage must be 'claims' or"
                    " 'attest', got 'sign' (#240)\n"
                ),
            ),
        )

    def test_off_an_actions_path_in_another_repository_is_refused(self) -> None:
        """Not an _actions path and not the canon calling itself."""
        root = self.tmp / "elsewhere"
        done = self.resolve(plant(root), "attest")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            f"::error::resolve.sh: source-attest resolved to '{root}' in"
            " someone/consumer — not an _actions path and not a canon self-call\n",
        )

    def test_the_canon_calling_itself_names_the_running_commit(self) -> None:
        """Off an _actions path in the canon: the ref is GITHUB_SHA."""
        root = self.tmp / "checkout"
        done = self.resolve(plant(root), "claims", "monumental-archive/.github")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f"SA_CANON_REF={'c' * 40}\n", self.github_env.read_text("utf-8"))

    def test_a_tree_without_the_policy_is_refused(self) -> None:
        """No slsa/verify-policy.json: not a canon tree to attest with."""
        root = self.tmp / "_actions" / "monumental-archive" / ".github" / "v1.2.3"
        done = self.resolve(plant(root, policy=False), "claims")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            f"::error::resolve.sh: resolved canon tree at '{root}' has no"
            " slsa/verify-policy.json\n",
        )


# A stub that appends its argv, one argument per line and a blank line
# after, to $STUB_LOG, and exits 0.
RECORDER = """#!/usr/bin/env bash
{ printf '%s\\n' "$(basename "$0")" "$@"; echo; } >> "${STUB_LOG}"
"""
NO_STELE = "::error::{}: stele is not on the runner — it is a belt tool\n"


class Stubbed(unittest.TestCase):
    """A scratch work directory, a stub bin, and a script run in it."""

    @override
    def setUp(self) -> None:
        """Make the work directory, the stub bin and the log."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(os.path.realpath(tmp.name))
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "stub.log"
        self.log.write_text("", "utf-8")

    def stub(self, name: str) -> None:
        """Put a recording stub first on PATH."""
        (self.bin / name).write_text(RECORDER, "utf-8")
        (self.bin / name).chmod(0o755)

    def run_in(
        self, script: Path, path: str, **env: str
    ) -> subprocess.CompletedProcess[str]:
        """Run a script in the work directory, the stub bin first on PATH.

        Returns:
            The finished process.

        """
        return subprocess.run(
            [BASH, str(script)],
            cwd=self.work,
            env=hermetic.env(
                self.tmp / "home",
                PATH=f"{self.bin}:{path}",
                STUB_LOG=str(self.log),
                **env,
            ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def calls(self) -> list[list[str]]:
        """Read the stubs' recorded calls.

        Returns:
            One argv per call, the tool's name first.

        """
        text = self.log.read_text("utf-8")
        return [block.split("\n") for block in text.split("\n\n") if block.strip()]


class LevelsPublish(Stubbed):
    """mise/levels-publish.sh: level.yml's publish step (#1934)."""

    SCRIPT = HERE / "levels-publish.sh"

    def publish(self, investigate: str) -> subprocess.CompletedProcess[str]:
        """Publish the planted cells with a stub git.

        Returns:
            The finished process.

        """
        self.stub("git")
        (self.work / "levels").mkdir()
        return self.run_in(
            self.SCRIPT,
            os.environ["PATH"],
            GH_TOKEN="planted",  # ruff: ignore[hardcoded-password-func-arg]
            GITHUB_REPOSITORY="org/repo",
            investigate=investigate,
        )

    def pushed(self) -> list[str]:
        """Name the git subcommands run, in order.

        Returns:
            Each call's first argument.

        """
        return [argv[1] for argv in self.calls()]

    def test_the_cells_are_pushed_to_the_levels_branch(self) -> None:
        """Nothing to investigate: committed, force-pushed, green."""
        done = self.publish("0")
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        expected: list[str] = ["init", "config", "config", "add", "commit", "push"]
        self.assertEqual(self.pushed(), expected)
        push = self.calls()[-1]
        self.assertIn("https://x-access-token:planted@github.com/org/repo.git", push)
        self.assertEqual(push[-1], "levels:levels")

    def test_a_regression_is_refused_after_the_push(self) -> None:
        """Exit 4 before: the good cells still publish, then one finding."""
        done = self.publish("4")
        self.assertEqual((done.returncode, done.stdout), (1, ""), done.stderr)
        self.assertEqual(self.pushed()[-1], "push")
        self.assertEqual(
            done.stderr,
            "::error::levels-publish.sh: a cell that was judged before cannot be"
            " judged now — investigate; good reports were not overwritten\n",
        )


class SourceAttestChain(Stubbed):
    """.github/actions/source-attest/chain.sh: the attest step (#1934)."""

    def chain(self, genesis: str, path: str) -> subprocess.CompletedProcess[str]:
        """Emit the chain from a planted canon tree.

        Returns:
            The finished process.

        """
        root = self.tmp / "canon"
        hermetic.canon(root, ".github/actions/source-attest")
        return self.run_in(
            root / ".github" / "actions" / "source-attest" / "chain.sh",
            path,
            SA_ACTOR="someone",
            SA_ACTOR_ID="42",
            SA_GENESIS=genesis,
            SA_CANON_ROOT=str(root),
            SA_CANON_REF="v1.2.3",
            SA_WORK=str(self.tmp / "sa"),
            SA_REMOTE_URL="file:///remote",
            GITHUB_REPOSITORY="someone/consumer",
            GITHUB_SHA="c" * 40,
        )

    def test_the_chain_is_emitted_against_the_canon_policy(self) -> None:
        """Stele emit chain, with the tree's policy and the seam's remote."""
        self.stub("stele")
        done = self.chain("false", "/usr/bin:/bin")
        self.assertEqual(done.returncode, 0, done.stderr)
        argv = self.calls()[0]
        expected: list[str] = ["stele", "emit", "chain"]
        self.assertEqual(argv[:3], expected)
        self.assertIn(f"{self.tmp}/canon/slsa/verify-policy.json", argv)
        self.assertIn("file:///remote", argv)
        self.assertIn(f"{self.tmp}/sa/claims.json", argv)
        self.assertNotIn("--genesis", argv)

    def test_genesis_founds_a_chain(self) -> None:
        """SA_GENESIS=true is the one flag that founds one."""
        self.stub("stele")
        done = self.chain("true", "/usr/bin:/bin")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.calls()[0][-1], "--genesis")

    def test_no_stele_is_refused_by_name(self) -> None:
        """No stele on the runner: one finding, nothing emitted."""
        done = self.chain("false", "/usr/bin:/bin")
        self.assertEqual((done.returncode, done.stdout), (1, ""), done.stderr)
        self.assertEqual(done.stderr, NO_STELE.format("chain.sh"))
        self.assertFalse(self.calls())


if __name__ == "__main__":
    unittest.main()
