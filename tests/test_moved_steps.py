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
"""

import os
import subprocess
from pathlib import Path

from belt.typed_pytest import fixture
from tests import support

HERE = support.MISE
CANON = HERE.parent
BASH = support.tool("bash")
GIT = support.tool("git")
ONE_PER_MERGE = (HERE / "vex-one-per-merge.txt").read_text("utf-8").strip()


def run(
    script: Path, cwd: Path, *args: str, make_env: support.Env, **env: str
) -> subprocess.CompletedProcess[str]:
    """Run a script by its path, with its arguments.

    Returns:
        The finished process.

    """
    return subprocess.run(
        [BASH, str(script), *args],
        cwd=cwd,
        env=make_env(**env),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


SCRIPT = HERE / "vex-decisions.sh"


class VexDecisions:
    """mise/vex-decisions.sh, in a planted git tree."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Set up the rows' state."""
        self.make_env = env
        self.set_up_tmp(tmp_path)
        self.set_up_test_vex_decisions(tmp_path)

    def set_up_tmp(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path

    def set_up_test_vex_decisions(self, tmp_path: Path) -> None:
        """Make a repository with no decisions yet."""
        self.tree = Path(str(tmp_path))
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
            env=self.make_env(),
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
        return run(
            SCRIPT,
            self.tree,
            str(self.out),
            REPO="o/r",
            DECISIONS="security/vex/*.openvex.json",
            ONE_PER_MERGE=str(HERE / "vex-one-per-merge.txt"),
            **env,
            make_env=self.make_env,
        )

    def fake_list(self, *paths: str) -> str:
        """Write a stand-in for ORG_PYTHON that lists the changed decisions.

        Returns:
            Its path.

        """
        path = self.tree.parent / f"{self.tree.name}-python"
        lines = "".join(f"echo {p}\n" for p in paths)
        path.write_text(f"#!/usr/bin/env bash\n{lines}", "utf-8")
        path.chmod(0o755)
        return str(path)


@fixture
def vex_decisions(tmp_path: Path, env: support.Env) -> VexDecisions:
    """Give each row its own VexDecisions.

    Returns:
        The row's VexDecisions.

    """
    return VexDecisions(tmp_path, env=env)


def test_a_dispatch_signs_every_tracked_decision_unsigned_only(
    vex_decisions: VexDecisions,
) -> None:
    """The state mode, then one --decision= per tracked file."""
    vex_decisions.decide("a", "b")
    done = vex_decisions.decisions()
    assert (done.returncode, done.stdout) == (0, ""), done.stderr
    argv: list[str] = [
        "--unsigned-only",
        "--store",
        "o/r",
        "--decision=security/vex/a.openvex.json",
        "--decision=security/vex/b.openvex.json",
        "",
    ]
    assert vex_decisions.out.read_text("utf-8").split("\0") == argv


def test_nothing_to_sign_is_a_notice_and_no_arguments(
    vex_decisions: VexDecisions,
) -> None:
    """No decisions: the notice, no arguments written, exit 0."""
    vex_decisions.git("commit", "-q", "--allow-empty", "-m", "empty")
    done = vex_decisions.decisions()
    assert (done.returncode, done.stdout) == (
        0,
        "::notice::security/vex/ holds no decisions\n",
    )
    assert not vex_decisions.out.read_text("utf-8")


def test_a_push_of_one_decision_signs_it(vex_decisions: VexDecisions) -> None:
    """The range mode: no mode flags, the one changed decision."""
    vex_decisions.decide("a")
    before = vex_decisions.git("rev-parse", "HEAD").strip()
    done = vex_decisions.decisions(
        BEFORE=before, ORG_PYTHON=vex_decisions.fake_list("security/vex/a.openvex.json")
    )
    assert (done.returncode, done.stdout) == (0, ""), done.stderr
    assert (
        vex_decisions.out.read_text("utf-8")
        == "--decision=security/vex/a.openvex.json\0"
    )


def test_a_push_of_two_decisions_is_refused_by_name(
    vex_decisions: VexDecisions,
) -> None:
    """The one sentence lint:vex-one-per-merge prints, as an error."""
    vex_decisions.decide("a")
    before = vex_decisions.git("rev-parse", "HEAD").strip()
    done = vex_decisions.decisions(
        BEFORE=before,
        ORG_PYTHON=vex_decisions.fake_list(
            "security/vex/a.openvex.json", "security/vex/b.openvex.json"
        ),
    )
    assert done.returncode == 1, done.stderr
    assert done.stdout.startswith("::error::"), done.stdout
    assert ONE_PER_MERGE.split("\n", 1)[0][:40] in done.stdout
    assert not vex_decisions.out.read_text("utf-8")


def plant(root: Path, *, policy: bool = True, canon: support.Canon) -> Path:
    """Plant a canon tree with the action, the writer and the policy.

    Returns:
        The script's path in the tree.

    """
    canon(root, ".github/actions/source-attest", "slsa/verify-policy.json")
    if not policy:
        (root / "slsa" / "verify-policy.json").unlink()
    return root / ".github" / "actions" / "source-attest" / "resolve.sh"


class SourceAttestResolve:
    """.github/actions/source-attest/resolve.sh, in a planted canon tree."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Make a scratch directory, a runner temp and a GITHUB_ENV."""
        self.make_env = env
        self.tmp = Path(os.path.realpath(str(tmp_path)))
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
            make_env=self.make_env,
        )


@fixture
def source_attest_resolve(tmp_path: Path, env: support.Env) -> SourceAttestResolve:
    """Give each row its own SourceAttestResolve.

    Returns:
        The row's SourceAttestResolve.

    """
    return SourceAttestResolve(tmp_path, env=env)


def test_an_actions_path_names_the_pinned_ref(
    source_attest_resolve: SourceAttestResolve, canon: support.Canon
) -> None:
    """The ref is the path segment the caller pinned; the env is written."""
    root = (
        source_attest_resolve.tmp
        / "_actions"
        / "monumental-archive"
        / ".github"
        / "v1.2.3"
    )
    done = source_attest_resolve.resolve(plant(root, canon=canon), "claims")
    assert done.returncode == 0, done.stdout + done.stderr
    assert source_attest_resolve.github_env.read_text("utf-8") == (
        f"SA_WORK={source_attest_resolve.runner}/source-attest\n"
        f"SA_CANON_ROOT={root}\n"
        "SA_CANON_REF=v1.2.3\n"
        "SA_ISSUER=https://token.actions.githubusercontent.com\n"
    )


def test_another_stage_is_refused_by_name(
    source_attest_resolve: SourceAttestResolve, canon: support.Canon
) -> None:
    """Only claims and attest are stages."""
    root = (
        source_attest_resolve.tmp
        / "_actions"
        / "monumental-archive"
        / ".github"
        / "v1.2.3"
    )
    done = source_attest_resolve.resolve(plant(root, canon=canon), "sign")
    assert (done.returncode, done.stderr) == (
        1,
        (
            "::error::resolve.sh: source-attest stage must be 'claims' or"
            " 'attest', got 'sign' (#240)\n"
        ),
    )


def test_off_an_actions_path_in_another_repository_is_refused(
    source_attest_resolve: SourceAttestResolve, canon: support.Canon
) -> None:
    """Not an _actions path and not the canon calling itself."""
    root = source_attest_resolve.tmp / "elsewhere"
    done = source_attest_resolve.resolve(plant(root, canon=canon), "attest")
    assert done.returncode == 1
    assert done.stderr == (
        f"::error::resolve.sh: source-attest resolved to '{root}' in"
        " someone/consumer — not an _actions path and not a canon self-call\n"
    )


def test_the_canon_calling_itself_names_the_running_commit(
    source_attest_resolve: SourceAttestResolve, canon: support.Canon
) -> None:
    """Off an _actions path in the canon: the ref is GITHUB_SHA."""
    root = source_attest_resolve.tmp / "checkout"
    done = source_attest_resolve.resolve(
        plant(root, canon=canon), "claims", "monumental-archive/.github"
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"SA_CANON_REF={'c' * 40}\n" in source_attest_resolve.github_env.read_text(
        "utf-8"
    )


def test_a_tree_without_the_policy_is_refused(
    source_attest_resolve: SourceAttestResolve, canon: support.Canon
) -> None:
    """No slsa/verify-policy.json: not a canon tree to attest with."""
    root = (
        source_attest_resolve.tmp
        / "_actions"
        / "monumental-archive"
        / ".github"
        / "v1.2.3"
    )
    done = source_attest_resolve.resolve(
        plant(root, policy=False, canon=canon), "claims"
    )
    assert done.returncode == 1
    assert done.stderr == (
        f"::error::resolve.sh: resolved canon tree at '{root}' has no"
        " slsa/verify-policy.json\n"
    )


# A stub that appends its argv, one argument per line and a blank line
# after, to $STUB_LOG, and exits 0.
RECORDER = """#!/usr/bin/env bash
{ printf '%s\\n' "$(basename "$0")" "$@"; echo; } >> "${STUB_LOG}"
"""
NO_STELE = "::error::{}: stele is not on the runner — it is a belt tool\n"


class Stubbed:
    """A scratch work directory, a stub bin, and a script run in it."""

    def __init__(
        self, tmp_path: Path, *, canon: support.Canon, env: support.Env
    ) -> None:
        """Make the work directory, the stub bin and the log."""
        self.plant_canon = canon
        self.make_env = env
        self.tmp = Path(os.path.realpath(str(tmp_path)))
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
            env=self.make_env(
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


@fixture
def stubbed(tmp_path: Path, canon: support.Canon, env: support.Env) -> Stubbed:
    """Give each row its own Stubbed.

    Returns:
        The row's Stubbed.

    """
    return Stubbed(tmp_path, canon=canon, env=env)


LEVELS_PUBLISH_SCRIPT = HERE / "levels-publish.sh"


class LevelsPublish(Stubbed):
    """mise/levels-publish.sh: level.yml's publish step (#1934)."""

    def publish(self, investigate: str) -> subprocess.CompletedProcess[str]:
        """Publish the planted cells with a stub git.

        Returns:
            The finished process.

        """
        self.stub("git")
        (self.work / "levels").mkdir()
        return self.run_in(
            LEVELS_PUBLISH_SCRIPT,
            os.environ["PATH"],
            GH_TOKEN=support.FAKE_CREDENTIAL,
            GITHUB_REPOSITORY="org/repo",
            investigate=investigate,
        )

    def pushed(self) -> list[str]:
        """Name the git subcommands run, in order.

        Returns:
            Each call's first argument.

        """
        return [argv[1] for argv in self.calls()]


@fixture
def levels_publish(
    tmp_path: Path, canon: support.Canon, env: support.Env
) -> LevelsPublish:
    """Give each row its own LevelsPublish.

    Returns:
        The row's LevelsPublish.

    """
    return LevelsPublish(tmp_path, canon=canon, env=env)


def test_the_cells_are_pushed_to_the_levels_branch(
    levels_publish: LevelsPublish,
) -> None:
    """Nothing to investigate: committed, force-pushed, green."""
    done = levels_publish.publish("0")
    assert (done.returncode, done.stderr) == (0, ""), done.stdout
    expected: list[str] = ["init", "config", "config", "add", "commit", "push"]
    assert levels_publish.pushed() == expected
    push = levels_publish.calls()[-1]
    assert "https://x-access-token:planted@github.com/org/repo.git" in push
    assert push[-1] == "levels:levels"


def test_a_regression_is_refused_after_the_push(levels_publish: LevelsPublish) -> None:
    """Exit 4 before: the good cells still publish, then one finding."""
    done = levels_publish.publish("4")
    assert (done.returncode, done.stdout) == (1, ""), done.stderr
    assert levels_publish.pushed()[-1] == "push"
    assert done.stderr == (
        "::error::levels-publish.sh: a cell that was judged before cannot be"
        " judged now — investigate; good reports were not overwritten\n"
    )


class SourceAttestChain(Stubbed):
    """.github/actions/source-attest/chain.sh: the attest step (#1934)."""

    def chain(self, genesis: str, path: str) -> subprocess.CompletedProcess[str]:
        """Emit the chain from a planted canon tree.

        Returns:
            The finished process.

        """
        root = self.tmp / "canon"
        self.plant_canon(root, ".github/actions/source-attest")
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


@fixture
def source_attest_chain(
    tmp_path: Path, canon: support.Canon, env: support.Env
) -> SourceAttestChain:
    """Give each row its own SourceAttestChain.

    Returns:
        The row's SourceAttestChain.

    """
    return SourceAttestChain(tmp_path, canon=canon, env=env)


def test_the_chain_is_emitted_against_the_canon_policy(
    source_attest_chain: SourceAttestChain,
) -> None:
    """Stele emit chain, with the tree's policy and the seam's remote."""
    source_attest_chain.stub("stele")
    done = source_attest_chain.chain("false", "/usr/bin:/bin")
    assert done.returncode == 0, done.stderr
    argv = source_attest_chain.calls()[0]
    expected: list[str] = ["stele", "emit", "chain"]
    assert argv[:3] == expected
    assert f"{source_attest_chain.tmp}/canon/slsa/verify-policy.json" in argv
    assert "file:///remote" in argv
    assert f"{source_attest_chain.tmp}/sa/claims.json" in argv
    assert "--genesis" not in argv


def test_genesis_founds_a_chain(source_attest_chain: SourceAttestChain) -> None:
    """SA_GENESIS=true is the one flag that founds one."""
    source_attest_chain.stub("stele")
    done = source_attest_chain.chain("true", "/usr/bin:/bin")
    assert done.returncode == 0, done.stderr
    assert source_attest_chain.calls()[0][-1] == "--genesis"


def test_no_stele_is_refused_by_name(source_attest_chain: SourceAttestChain) -> None:
    """No stele on the runner: one finding, nothing emitted."""
    done = source_attest_chain.chain("false", "/usr/bin:/bin")
    assert (done.returncode, done.stdout) == (1, ""), done.stderr
    assert done.stderr == NO_STELE.format("chain.sh")
    assert not source_attest_chain.calls()
