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
import subprocess  # ruff: ignore[suspicious-subprocess-import]
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
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
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
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
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
            (done.returncode, done.stdout),
            (
                1,
                (
                    "::error::source-attest stage must be 'claims' or 'attest', got"
                    " 'sign' (#240)\n"
                ),
            ),
        )

    def test_off_an_actions_path_in_another_repository_is_refused(self) -> None:
        """Not an _actions path and not the canon calling itself."""
        root = self.tmp / "elsewhere"
        done = self.resolve(plant(root), "attest")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stdout,
            f"::error::source-attest resolved to '{root}' in someone/consumer —"
            " not an _actions path and not a canon self-call\n",
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
            done.stdout,
            f"::error::resolved canon tree at '{root}' has no"
            " slsa/verify-policy.json\n",
        )


if __name__ == "__main__":
    unittest.main()
