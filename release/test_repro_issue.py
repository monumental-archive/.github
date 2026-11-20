#!/usr/bin/env python3
"""The issue repro-check.yml files on failure, against a stub `gh` (#1262).

`File an issue on failure` runs on `if: failure()`, which is true for
ANY failed step in the compare job. On release-lab#305 the mise install
died on curl exit 22, the comparison never ran, and the step filed "did
not rebuild to the published bytes" against a release whose digests
matched. The step now reads the compare step's outcome: `failure` is a
verdict and keeps its title and body exactly; anything else is no
verdict, and says so without the words of one.

The code under test is `release/repro_issue.py`, which the step runs as
its one command (#1386), with a stub `gh` first on PATH that records
its arguments. The three rows below were written against the step's
`run:` block cut out of the YAML; they are carried over row for row and
now call the helper's `main` in this process (#1698). One row starts
the helper as the step does instead: the script contract.

stdlib `unittest`, matching the other tests here. Run through the gate
as `mise run test`, which `ci` collects.
"""

import os
import pathlib
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from pathlib import Path

# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import hermetic
import repro_issue

HELPER = pathlib.Path(__file__).resolve().parent / "repro_issue.py"
RUN_URL = "https://github.com/monumental-archive/lab/actions/runs/42044978939"
NO_VERDICT_BODY = (
    "The reproducibility audit set out to rebuild v0.28.1 with the"
    " pgrx-extension class workflow, and no verdict was reached: the job"
    " failed before the comparison ran (the compare step was {outcome})."
    " Nothing is known about the published bytes from this run."
    f" Read the run to see which step failed, then dispatch it again: {RUN_URL}"
)
NO_VERDICT_TITLE = (
    "repro-check: v0.28.1 pgrx-extension could not be compared (run 42044978939)"
)
VERDICT_TITLE = (
    "repro-check: v0.28.1 pgrx-extension did not rebuild to the published bytes"
    " (run 42044978939)"
)
VERDICT_BODY = (
    "The reproducibility audit rebuilt v0.28.1 with the pgrx-extension class"
    " workflow and the verdict is not a pass. Judged population: the"
    " pgrx-extension artifacts v0.28.1 published. A digest that DIFFERS from"
    " the published one means a build input escaped the pinning. An artifact"
    " reported ABSENT is one in the judged population this rebuild did not"
    " produce; read the scope the run states before treating it as a pinning"
    f" defect. Read the run: {RUN_URL}"
)
BANNED = ("differs", "did not rebuild", "pinning")
STUB_GH = """#!/usr/bin/env bash
for arg in "$@"; do printf '%s\\0' "${arg}"; done > "${GH_ARGS}"
"""


class FileAnIssue(unittest.TestCase):
    """Each row: the compare outcome, and the exact issue filed."""

    def setUp(self) -> None:
        """Give each row its own directory and stub `gh`."""
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        bindir = self.tmp / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(STUB_GH, encoding="utf-8")
        gh.chmod(0o755)
        self.bindir = bindir

    def file(
        self, outcome: str, manifest: str = "{}", *, script: bool = False
    ) -> tuple[str, str]:
        """Run the helper with the compare step's `outcome`.

        In this process unless `script`, which starts it as the step does.

        Returns:
            The title and body the step handed to `gh issue create`.

        """
        args_file = self.tmp / "gh-args"
        env = hermetic.env(
            PATH=f"{self.bindir}{os.pathsep}{os.environ['PATH']}",
            HOME=str(self.tmp),
            GH_ARGS=str(args_file),
            GH_TOKEN="test",  # ruff: ignore[hardcoded-password-func-arg]
            TAG="v0.28.1",
            MANIFEST=manifest,
            CLASS="pgrx-extension",
            COMPARE=outcome,
            GITHUB_SERVER_URL="https://github.com",
            GITHUB_REPOSITORY="monumental-archive/lab",
            GITHUB_RUN_ID="42044978939",
        )
        proc: subprocess.CompletedProcess[str] | hermetic.Called
        if script:
            proc = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [sys.executable, str(HELPER)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        else:

            def call() -> int:
                return repro_issue.main()

            proc = hermetic.call_main(call, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        args = args_file.read_text(encoding="utf-8").split("\0")[:-1]
        self.assertEqual(
            args[:4], ["issue", "create", "--repo", "monumental-archive/lab"]
        )
        self.assertEqual(args[4], "--title")
        self.assertEqual(args[6], "--body")
        self.assertEqual(len(args), 8, args)
        return args[5], args[7]

    def test_failure_is_a_verdict_and_files_as_before(self) -> None:
        """A comparison that ran and failed keeps its title and body.

        The script row: the helper started as the step starts it.
        """
        title, body = self.file("failure", script=True)
        self.assertEqual(title, VERDICT_TITLE)
        self.assertEqual(body, VERDICT_BODY)

    def test_skipped_files_could_not_be_compared(self) -> None:
        """release-lab#305: the install died, the compare never ran."""
        title, body = self.file("skipped")
        self.assertEqual(title, NO_VERDICT_TITLE)
        self.assertEqual(body, NO_VERDICT_BODY.format(outcome="skipped"))
        self.assert_no_verdict_words(title, body)

    def test_cancelled_files_could_not_be_compared(self) -> None:
        """A cancelled comparison reached no verdict either."""
        title, body = self.file("cancelled")
        self.assertEqual(title, NO_VERDICT_TITLE)
        self.assertEqual(body, NO_VERDICT_BODY.format(outcome="cancelled"))
        self.assert_no_verdict_words(title, body)

    def test_untyped_release_names_the_unscoped_population(self) -> None:
        """No typed manifest: the verdict says it judged the whole release."""
        title, body = self.file("failure", manifest=" \n")
        self.assertEqual(title, VERDICT_TITLE)
        self.assertEqual(
            body,
            VERDICT_BODY.replace(
                "the pgrx-extension artifacts v0.28.1 published",
                "every build subject v0.28.1 published — it carries no per-class"
                " typing, so this pgrx-extension rebuild could not be scoped to its"
                " own class",
            ),
        )

    def assert_no_verdict_words(self, title: str, body: str) -> None:
        """No word of a verdict, in any case, in title or body."""
        for text in (title, body):
            for word in BANNED:
                self.assertNotIn(word, text.lower())


if __name__ == "__main__":
    unittest.main()
