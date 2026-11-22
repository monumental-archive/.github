#!/usr/bin/env python3
"""Table tests for lint:cold-attested's findings (#117, #1934).

The lint's awk program prints each finding as one unit-separated line
for `org_finding_lines`: the file and line as the subject, then the
message. These rows run the task's own `run` string, read out of
mise/config.toml, as mise runs it (`hermetic.task_run`), in a planted
tree, and assert the whole of each stream: a red row scored on exit
alone can pass for the wrong reason.

stdlib `unittest`, matching the other mise/test_*.py.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

import decoded
import hermetic

BELT = Path(__file__).resolve().parent
TASK = "lint:cold-attested"
GIT = hermetic.tool("git")

HEAD = "on: push\njobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps:\n"
CLEAN = HEAD + "      - uses: actions/checkout@v5\n"
# The cache step is line 6.
CACHE = HEAD + "      - uses: actions/cache@v4\n        with:\n          path: x\n"
# The mise-action step is line 6; the stray marker is line 7.
TWO = (
    HEAD
    + "      - uses: jdx/mise-action@v3\n"
    + "      # unattested-path: nothing follows\n"
    + "\n"
)
CLEAN_LINE = (
    "lint:cold-attested: every cache declaration carries its own"
    " unattested-path marker\n"
)


def body() -> str:
    """Read the lint's run string as mise would render it.

    Returns:
        The task body with any `{% raw %}` / `{% endraw %}` lines removed.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    run = decoded.as_str(decoded.as_object(tasks[TASK], TASK)["run"], "its run")
    return "".join(
        line
        for line in run.splitlines(keepends=True)
        if line.strip() not in {"{% raw %}", "{% endraw %}"}
    )


def lint(workflow: str) -> subprocess.CompletedProcess[str]:
    """Run the lint over one planted workflow, `.github/workflows/a.yml`.

    Returns:
        The finished process.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / ".github" / "workflows" / "a.yml"
        path.parent.mkdir(parents=True)
        path.write_text(workflow, encoding="utf-8")
        env = hermetic.env()
        subprocess.run([GIT, "init", "-q"], cwd=root, env=env, check=True)
        as_mise = hermetic.task_run(TASK, body())
        return subprocess.run(
            as_mise.argv,
            cwd=root,
            env=env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


class ColdAttested(unittest.TestCase):
    """Each finding names its file and line; a clean tree says so."""

    def test_a_tree_with_no_cache_passes(self) -> None:
        """No cache anywhere: green, the summary on stdout."""
        done = lint(CLEAN)
        self.assertEqual(
            (done.returncode, done.stdout, done.stderr), (0, CLEAN_LINE, "")
        )

    def test_an_unmarked_cache_step_is_red_by_file_and_line(self) -> None:
        """The cache step's file and line are the finding's subject."""
        done = lint(CACHE)
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        self.assertEqual(
            done.stderr,
            "lint:cold-attested: .github/workflows/a.yml:6: actions/cache step"
            " carries no unattested-path marker\n",
        )

    def test_each_finding_is_its_own_line(self) -> None:
        """A default-cache step and a stray marker: two findings, in order."""
        done = lint(TWO)
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        self.assertEqual(
            done.stderr,
            "lint:cold-attested: .github/workflows/a.yml:7: unattested-path"
            " marker introduces no step\n"
            "lint:cold-attested: .github/workflows/a.yml:6: mise-action step"
            " does not say `cache: false` and carries no unattested-path marker\n",
        )


if __name__ == "__main__":
    unittest.main()
