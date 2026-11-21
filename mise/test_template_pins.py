#!/usr/bin/env python3
"""Table tests for lint:template-pins' scorecard rule (#1887).

scorecard.yml is self-contained by design: Scorecard's publish path
refuses a reusable workflow. Its `report` job needs the canon action
for the belt's Python, so that one pin is allowed (owner's ruling,
2026-11-21) and held to the same released SHA as every other template
pin. A reusable workflow pinned there is red by message.

The task's own body, read from mise/config.toml, runs in a planted git
tree with one tagged commit. GIT ENVIRONMENT IS SCRUBBED (#857), the
belt's idiom. stdlib `unittest`, run through the gate as `mise run test`.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic

BELT = Path(__file__).resolve().parent
CANON_DIR = BELT.parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
CANON = "monumental-archive/.github"

SCORECARD = """\
jobs:
  analyze:
    steps:
      - uses: ossf/scorecard-action@0000000000000000000000000000000000000000 # v2
  report:
    steps:
      - uses: {uses}@{sha} # v1.0.0
"""


def body() -> str:
    """Read lint:template-pins' run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks["lint:template-pins"], "lint:template-pins")
    return decoded.as_str(task["run"], "lint:template-pins.run")


class Scorecard(unittest.TestCase):
    """A planted scorecard template; the lint's verdict on its pin."""

    @override
    def setUp(self) -> None:
        """Plant a tree with one tagged commit to pin."""
        self.tree = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tree)
        self.env = hermetic.env() | {
            "ORG_BELT_DIR": str(BELT),
            "ORG_CANON_DIR": str(CANON_DIR),
        }
        self.git("init", "-q")
        self.git("commit", "-q", "--allow-empty", "-m", "seed")
        self.git("tag", "v1.0.0")
        self.sha = self.git("rev-parse", "HEAD").strip()
        (self.tree / "workflow-templates").mkdir()

    def git(self, *args: str) -> str:
        """Run git in the planted tree, unsigned.

        Returns:
            Its standard output.

        """
        unsigned = ("-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false")
        return subprocess.run(
            [GIT, *unsigned, *args],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def run_lint(self, uses: str) -> subprocess.CompletedProcess[str]:
        """Plant scorecard.yml pinning `uses` and run the task body.

        Returns:
            The finished process.

        """
        text = SCORECARD.format(uses=uses, sha=self.sha)
        (self.tree / "workflow-templates" / "scorecard.yml").write_text(text, "utf-8")
        return subprocess.run(
            [BASH, "-euo", "pipefail", "-c", body()],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_canon_action_pin_passes(self) -> None:
        """The canon action, at a released SHA, is scorecard's one pin."""
        done = self.run_lint(f"{CANON}/.github/actions/canon")
        self.assertEqual(done.returncode, 0, done.stderr)
        expected = (
            "lint:template-pins: 1 pins at one ancestor SHA, scorecard calls no"
            " reusable, released as v1.0.0"
        )
        self.assertEqual(done.stdout.splitlines(), [expected])

    def test_reusable_workflow_pin_is_red(self) -> None:
        """A reusable workflow pinned in scorecard.yml is red by message."""
        done = self.run_lint(f"{CANON}/.github/workflows/ci.yml")
        self.assertEqual(done.returncode, 1)
        expected = [
            (
                "lint:template-pins: template pin violations:"
                " workflow-templates/scorecard.yml(pins a reusable workflow:"
                " self-contained by design)"
            ),
            "  templates pin one merged, RELEASED canon SHA and move together",
            "  (#111, #233); scorecard.yml pins the canon action alone, never a",
            "  reusable workflow (#1887).",
        ]
        self.assertEqual(done.stderr.splitlines(), expected)


if __name__ == "__main__":
    unittest.main()
