"""Table tests for lint:citation and its helper (#1911).

Each row plants a tree and asserts the exit status and every line of
the message: with no CITATION.cff, a workflow requesting DOI minting
(red, each named) or none (a named skip, a request only in a comment
included); a CITATION.cff with no REUSE.toml; one that agrees with its
render; and a workflow that is not UTF-8. Three rows run the task's own
body, read from mise/config.toml, in a git tree: the skip with no
tracked workflow, a drift end to end (its diff on stdout), and a render
that fails (its own message and status); the rest call the helper's
`main` in this process.

The sources are this repository's own REUSE.toml, .badge-states and
CITATION.cff, so the agreeing row is the canon's real render.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import citation_check, decoded
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:citation"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
CANON = BELT.parent
GIT = support.tool("git")
MINT = "jobs:\n  r:\n    with:\n      mint-doi: true\n"
SKIPPED = ["lint:citation: no CITATION.cff and no mint-doi request, skipped"]


def same[T](got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    assert got == want, msg or None


def body() -> str:
    """Read lint:citation's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


def source(name: str) -> str:
    """Read one of this repository's citation sources.

    Returns:
        Its text.

    """
    return (CANON / name).read_text(encoding="utf-8")


class Citation:
    """Planted trees; the citation's verdict on them."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant an empty repository: the helper asks git (#2013)."""
        self.make_env = env
        self.tree = tmp_path
        self.env = self.make_env(
            MISE_TASK_NAME=TASK,
            ORG_BELT_DIR=str(BELT),
            ORG_PYTHON=sys.executable,
        )
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        """Run git in the planted tree."""
        subprocess.run([GIT, *args], cwd=self.tree, env=self.env, check=True)

    def plant(self, name: str, text: str | bytes) -> str:
        """Write one file into the tree.

        Returns:
            Its path from the tree's top.

        """
        path = self.tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        return name

    def sources(self, citation: str) -> None:
        """Plant the canon's sources beside a citation."""
        self.plant("REUSE.toml", source("REUSE.toml"))
        self.plant(".badge-states", source(".badge-states"))
        self.plant("CITATION.cff", citation)

    def helper(
        self,
        *files: str,
        untracked: tuple[str, ...] = (),
        capfd: pytest.CaptureFixture[str],
    ) -> tuple[int, str, str]:
        """Call the helper's main in the planted tree, tracking what is in it.

        `untracked` are planted files git is not to track.

        Returns:
            The status and what it printed.

        """
        self.git("add", "-A")
        if untracked:
            self.git("rm", "-q", "--cached", "--", *untracked)
        argv = list(files)
        with pytest.MonkeyPatch.context() as patched:
            for name, value in self.env.items():
                patched.setenv(name, value)
            patched.chdir(self.tree)
            status = citation_check.main(argv)
        out, err = capfd.readouterr()
        return status, out, err

    def task(self, *tracked: str) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree, git tracking some files.

        Returns:
            The finished process.

        """
        subprocess.run([GIT, "init", "-q"], cwd=self.tree, env=self.env, check=True)
        if tracked:
            add = [GIT, "add", "--", *tracked]
            subprocess.run(add, cwd=self.tree, env=self.env, check=True)
        as_mise = support.task_run(TASK, body())
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


@fixture
def citation_state(tmp_path: Path, env: support.Env) -> Citation:
    """Give each row its own Citation."""
    return Citation(tmp_path, env=env)


def test_no_tracked_workflow_is_skipped_by_name(citation_state: Citation) -> None:
    """No workflow tracked, a minting one untracked: the task skips."""
    citation_state.plant(".github/workflows/loose.yml", MINT)
    done = citation_state.task()
    same(done.returncode, 0, done.stderr)
    same(done.stdout.splitlines(), SKIPPED)


def test_drift_is_red_through_the_task(citation_state: Citation) -> None:
    """A derived field edited by hand: the diff, then the finding."""
    drifted = source("CITATION.cff").replace("license: 0BSD", "license: MIT")
    citation_state.sources(drifted)
    done = citation_state.task("REUSE.toml", ".badge-states", "CITATION.cff")
    same(done.returncode, citation_check.EXIT_RED)
    diff = done.stdout.splitlines()
    same(diff[0].split("\t")[0], "--- CITATION.cff")
    assert "-license: MIT" in diff
    assert "+license: 0BSD" in diff
    want = [
        (
            "lint:citation: CITATION.cff drifted from its sources — edit"
            " REUSE.toml (or the human fields) and run fix:citation"
        )
    ]
    same(done.stderr.splitlines(), want)


def test_a_failed_render_is_its_own_status(citation_state: Citation) -> None:
    """The render refuses: its message, its status, no verdict."""
    citation_state.plant("REUSE.toml", "x = 1\n")
    citation_state.plant("CITATION.cff", source("CITATION.cff"))
    done = citation_state.task("REUSE.toml", "CITATION.cff")
    same(done.returncode, 1)
    want = [
        (
            "lint:citation: REUSE.toml declares no licence expression — fill"
            " it first (lint:licence)"
        )
    ]
    same(done.stderr.splitlines(), want)
    same(done.stdout, "")


def test_agreeing_citation_is_ok(
    citation_state: Citation, capfd: pytest.CaptureFixture[str]
) -> None:
    """The canon's own citation against its own sources: ok."""
    citation_state.sources(source("CITATION.cff"))
    status, out, err = citation_state.helper(capfd=capfd)
    same(status, 0, err)
    want = ["lint:citation: CITATION.cff agrees with REUSE.toml"]
    same(out.splitlines(), want)


def test_no_reuse_is_red(
    citation_state: Citation, capfd: pytest.CaptureFixture[str]
) -> None:
    """A citation with no source: red, by name."""
    citation_state.plant("CITATION.cff", source("CITATION.cff"))
    status, _out, err = citation_state.helper(capfd=capfd)
    same(status, citation_check.EXIT_RED)
    want = [
        (
            "lint:citation: CITATION.cff exists but REUSE.toml (its source of"
            " truth) does not"
        )
    ]
    same(err.splitlines(), want)


def test_an_untracked_citation_is_no_citation(
    citation_state: Citation, capfd: pytest.CaptureFixture[str]
) -> None:
    """CITATION.cff on disk and not in git: the tree has none (#2013)."""
    citation_state.sources(source("CITATION.cff"))
    rel = citation_state.plant(".github/workflows/wf.yml", "on: push\n")
    status, out, err = citation_state.helper(
        rel, untracked=("CITATION.cff",), capfd=capfd
    )
    same(status, 0, err)
    same(out.splitlines(), SKIPPED)


def test_an_untracked_reuse_is_no_source(
    citation_state: Citation, capfd: pytest.CaptureFixture[str]
) -> None:
    """REUSE.toml on disk and not in git: the citation has no source."""
    citation_state.sources(source("CITATION.cff"))
    status, _out, err = citation_state.helper(untracked=("REUSE.toml",), capfd=capfd)
    same(status, citation_check.EXIT_RED)
    want = [
        (
            "lint:citation: CITATION.cff exists but REUSE.toml (its source of"
            " truth) does not"
        )
    ]
    same(err.splitlines(), want)


def test_no_minting_request_is_skipped(
    citation_state: Citation, capfd: pytest.CaptureFixture[str]
) -> None:
    """No request, or one only in a comment: skipped by name."""
    rows = {
        "none": "on: push\n",
        "a comment": "# mint-doi: true\n",
        "after a hash": "x: 1 # mint-doi: true\n",
    }
    for text in rows.values():
        rel = citation_state.plant(".github/workflows/wf.yml", text)
        status, out, err = citation_state.helper(rel, capfd=capfd)
        same(status, 0, err)
        same(out.splitlines(), SKIPPED)
        same(err, "")


def test_minting_without_a_citation_is_red(
    citation_state: Citation, capfd: pytest.CaptureFixture[str]
) -> None:
    """Each workflow requesting a DOI named, in order, then the remedy."""
    one = citation_state.plant(".github/workflows/a.yml", MINT)
    clean = citation_state.plant(".github/workflows/b.yml", "on: push\n")
    two = citation_state.plant(".github/workflows/c.yaml", "mint-doi:\ttrue")
    status, out, err = citation_state.helper(one, clean, two, capfd=capfd)
    same(status, citation_check.EXIT_RED)
    want = [
        (
            "lint:citation: mint-doi: true with no CITATION.cff — a citable repo"
            " carries its citation (#316 finding 6):"
        ),
        f"  {one}",
        f"  {two}",
        "  run fix:citation (REUSE.toml is the source), commit the render",
    ]
    same(err.splitlines(), want)
    same(out, "")


def test_not_utf8_is_an_error(
    citation_state: Citation, capfd: pytest.CaptureFixture[str]
) -> None:
    """A workflow that is not UTF-8 is refused by name, exit 2."""
    rel = citation_state.plant(".github/workflows/bad.yml", b"on: \xff\n")
    status, _out, err = citation_state.helper(rel, capfd=capfd)
    same(status, citation_check.EXIT_ERROR)
    assert err.startswith(f"{TASK}: {rel}: cannot be read as UTF-8:"), err
