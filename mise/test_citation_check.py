#!/usr/bin/env python3
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

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom. stdlib
`unittest`, run through the gate as `mise run test`.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import citation_check
import decoded
import hermetic

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:citation"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


BELT = Path(__file__).resolve().parent
CANON = BELT.parent
GIT = hermetic.tool("git")
MINT = "jobs:\n  r:\n    with:\n      mint-doi: true\n"
SKIPPED = ["lint:citation: no CITATION.cff and no mint-doi request, skipped"]


def same[T](case: unittest.TestCase, got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    case.assertEqual(got, want, msg or None)


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


class Citation(unittest.TestCase):
    """Planted trees; the citation's verdict on them."""

    @override
    def setUp(self) -> None:
        """Plant an empty tree."""
        self.tree = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tree)
        self.env = hermetic.env(
            MISE_TASK_NAME=TASK,
            ORG_BELT_DIR=str(BELT),
            ORG_PYTHON=sys.executable,
        )

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

    def helper(self, *files: str) -> hermetic.Called:
        """Call the helper's main in the planted tree.

        Returns:
            The status and what it printed.

        """
        argv = list(files)
        return hermetic.call_main(
            lambda: citation_check.main(argv), env=self.env, cwd=self.tree
        )

    def task(self, *tracked: str) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree, git tracking some files.

        Returns:
            The finished process.

        """
        subprocess.run([GIT, "init", "-q"], cwd=self.tree, env=self.env, check=True)
        if tracked:
            add = [GIT, "add", "--", *tracked]
            subprocess.run(add, cwd=self.tree, env=self.env, check=True)
        as_mise = hermetic.task_run(TASK, body())
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_no_tracked_workflow_is_skipped_by_name(self) -> None:
        """No workflow tracked, a minting one untracked: the task skips."""
        self.plant(".github/workflows/loose.yml", MINT)
        done = self.task()
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), SKIPPED)

    def test_drift_is_red_through_the_task(self) -> None:
        """A derived field edited by hand: the diff, then the finding."""
        drifted = source("CITATION.cff").replace("license: 0BSD", "license: MIT")
        self.sources(drifted)
        done = self.task()
        same(self, done.returncode, citation_check.EXIT_RED)
        diff = done.stdout.splitlines()
        same(self, diff[0].split("\t")[0], "--- CITATION.cff")
        self.assertIn("-license: MIT", diff)
        self.assertIn("+license: 0BSD", diff)
        want = [
            (
                "lint:citation: CITATION.cff drifted from its sources — edit"
                " REUSE.toml (or the human fields) and run fix:citation"
            )
        ]
        same(self, done.stderr.splitlines(), want)

    def test_a_failed_render_is_its_own_status(self) -> None:
        """The render refuses: its message, its status, no verdict."""
        self.plant("REUSE.toml", "x = 1\n")
        self.plant("CITATION.cff", source("CITATION.cff"))
        done = self.task()
        same(self, done.returncode, 1)
        want = [
            (
                "lint:citation: REUSE.toml declares no licence expression — fill"
                " it first (lint:licence)"
            )
        ]
        same(self, done.stderr.splitlines(), want)
        same(self, done.stdout, "")

    def test_agreeing_citation_is_ok(self) -> None:
        """The canon's own citation against its own sources: ok."""
        self.sources(source("CITATION.cff"))
        done = self.helper()
        same(self, done.returncode, 0, done.stderr)
        want = ["lint:citation: CITATION.cff agrees with REUSE.toml"]
        same(self, done.stdout.splitlines(), want)

    def test_no_reuse_is_red(self) -> None:
        """A citation with no source: red, by name."""
        self.plant("CITATION.cff", source("CITATION.cff"))
        done = self.helper()
        same(self, done.returncode, citation_check.EXIT_RED)
        want = [
            (
                "lint:citation: CITATION.cff exists but REUSE.toml (its source of"
                " truth) does not"
            )
        ]
        same(self, done.stderr.splitlines(), want)

    def test_no_minting_request_is_skipped(self) -> None:
        """No request, or one only in a comment: skipped by name."""
        rows = {
            "none": "on: push\n",
            "a comment": "# mint-doi: true\n",
            "after a hash": "x: 1 # mint-doi: true\n",
        }
        for row, text in rows.items():
            with self.subTest(row=row):
                rel = self.plant(".github/workflows/wf.yml", text)
                done = self.helper(rel)
                same(self, done.returncode, 0, done.stderr)
                same(self, done.stdout.splitlines(), SKIPPED)
                same(self, done.stderr, "")

    def test_minting_without_a_citation_is_red(self) -> None:
        """Each workflow requesting a DOI named, in order, then the remedy."""
        one = self.plant(".github/workflows/a.yml", MINT)
        clean = self.plant(".github/workflows/b.yml", "on: push\n")
        two = self.plant(".github/workflows/c.yaml", "mint-doi:\ttrue")
        done = self.helper(one, clean, two)
        same(self, done.returncode, citation_check.EXIT_RED)
        want = [
            (
                "lint:citation: mint-doi: true with no CITATION.cff — a citable repo"
                " carries its citation (#316 finding 6):"
            ),
            f"  {one}",
            f"  {two}",
            "  run fix:citation (REUSE.toml is the source), commit the render",
        ]
        same(self, done.stderr.splitlines(), want)
        same(self, done.stdout, "")

    def test_not_utf8_is_an_error(self) -> None:
        """A workflow that is not UTF-8 is refused by name, exit 2."""
        rel = self.plant(".github/workflows/bad.yml", b"on: \xff\n")
        done = self.helper(rel)
        same(self, done.returncode, citation_check.EXIT_ERROR)
        self.assertTrue(
            done.stderr.startswith(f"{TASK}: {rel}: cannot be read as UTF-8:"),
            done.stderr,
        )


if __name__ == "__main__":
    unittest.main()
