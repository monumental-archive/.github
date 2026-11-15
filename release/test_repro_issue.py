#!/usr/bin/env python3
"""The issue repro-check.yml files on failure, against a stub `gh` (#1262).

`File an issue on failure` runs on `if: failure()`, which is true for
ANY failed step in the compare job. On release-lab#305 the mise install
died on curl exit 22, the comparison never ran, and the step filed "did
not rebuild to the published bytes" against a release whose digests
matched. The step now reads the compare step's outcome: `failure` is a
verdict and keeps its title and body exactly; anything else is no
verdict, and says so without the words of one.

The body under test is the step's own `run:` block, lifted out of
`.github/workflows/repro-check.yml` by its step name and run by bash
with a stub `gh` first on PATH that records its arguments. Reading it
out of the YAML is what keeps the test on the code that ships.

stdlib `unittest`, matching the other tests here. Run through the gate
as `mise run test`, which `ci` collects.
"""

import os
import pathlib
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "repro-check.yml"
STEP = "- name: File an issue on failure"
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


def step_script() -> str:
    """Lift the issue step's `run: |` block out of the workflow.

    Returns:
        The block as bash, dedented to column zero.

    Raises:
        AssertionError: No single step of that name, no `run: |` under
            it, or an empty block. Loud, never a skip: a test that found
            nothing to run reads as a passing one.

    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == STEP]
    if len(starts) != 1:
        msg = f"expected one {STEP!r} in {WORKFLOW}, found {len(starts)}"
        raise AssertionError(msg)
    runs = [
        i
        for i in range(starts[0] + 1, len(lines))
        if lines[i].strip() == "run: |"
    ]
    if not runs:
        msg = f"no 'run: |' under {STEP!r}"
        raise AssertionError(msg)
    key = lines[runs[0]]
    key_indent = len(key) - len(key.lstrip())
    block: list[str] = []
    for line in lines[runs[0] + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= key_indent:
            break
        block.append(line)
    while block and not block[-1].strip():
        block.pop()
    if not block:
        msg = f"empty run block under {STEP!r}"
        raise AssertionError(msg)
    indent = len(block[0]) - len(block[0].lstrip())
    return "\n".join(line[indent:] for line in block) + "\n"


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

    def file(self, outcome: str, manifest: str = "{}") -> tuple[str, str]:
        """Run the lifted step with the compare step's `outcome`.

        Returns:
            The title and body the step handed to `gh issue create`.

        """
        args_file = self.tmp / "gh-args"
        env = {
            "PATH": f"{self.bindir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "GH_ARGS": str(args_file),
            "GH_TOKEN": "test",
            "TAG": "v0.28.1",
            "MANIFEST": manifest,
            "CLASS": "pgrx-extension",
            "COMPARE": outcome,
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "monumental-archive/lab",
            "GITHUB_RUN_ID": "42044978939",
        }
        bash = shutil.which("bash")
        if bash is None:
            self.fail("bash is not on PATH")
        proc = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [bash, "-c", step_script()],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
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
        """A comparison that ran and failed keeps its title and body."""
        title, body = self.file("failure")
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

    def assert_no_verdict_words(self, title: str, body: str) -> None:
        """No word of a verdict, in any case, in title or body."""
        for text in (title, body):
            for word in BANNED:
                self.assertNotIn(word, text.lower())


if __name__ == "__main__":
    unittest.main()
