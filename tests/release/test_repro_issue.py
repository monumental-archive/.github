"""The issue repro-check.yml files on failure, against a stub `gh` (#1262).

`File an issue on failure` runs on `if: failure()`, which is true for
ANY failed step in the compare job. On release-lab#305 the mise install
died on curl exit 22, the comparison never ran, and the step filed "did
not rebuild to the published bytes" against a release whose digests
matched. The step now reads the compare step's outcome: `failure` is a
verdict and keeps its title and body exactly; anything else is no
verdict, and says so without the words of one.

The code under test is `belt/release/repro_issue.py`, which the step runs as
its one command (#1386), with a stub `gh` first on PATH that records
its arguments. The three rows below were written against the step's
`run:` block cut out of the YAML; they are carried over row for row and
now call the helper's `main` in this process (#1698).
"""

import os
import sys
from typing import TYPE_CHECKING

import pytest

from belt.release import repro_issue
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from pathlib import Path

COMMAND = "repro-issue"
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


class FileAnIssue:
    """Each row: the compare outcome, and the exact issue filed."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Give each row its own directory and stub `gh`."""
        self.make_env = env
        self.tmp = tmp_path
        bindir = self.tmp / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(STUB_GH, encoding="utf-8")
        gh.chmod(0o755)
        self.bindir = bindir

    def enter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        outcome: str,
        manifest: str = "{}",
    ) -> None:
        """Set the step's world with the compare step's `outcome`."""
        args_file = self.tmp / "gh-args"
        env = self.make_env(
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
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(sys, "argv", [COMMAND])

    def ran(self, status: int, said: tuple[str, str]) -> tuple[str, str]:
        """Read what the run did.

        Returns:
            The title and body the step handed to `gh issue create`.

        """
        args_file = self.tmp / "gh-args"
        out, err = said
        assert status == 0, out + err
        args = args_file.read_text(encoding="utf-8").split("\0")[:-1]
        create: list[str] = ["issue", "create", "--repo", "monumental-archive/lab"]
        assert args[:4] == create
        assert args[4] == "--title"
        assert args[6] == "--body"
        assert len(args) == 8, args
        return args[5], args[7]


@fixture
def file_an_issue(tmp_path: Path, env: support.Env) -> FileAnIssue:
    """Give each row its own FileAnIssue."""
    return FileAnIssue(tmp_path, env=env)


def test_failure_is_a_verdict_and_files_as_before(
    file_an_issue: FileAnIssue,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A comparison that ran and failed keeps its title and body."""
    file_an_issue.enter(monkeypatch, "failure")
    status = repro_issue.main([])
    title, body = file_an_issue.ran(status, capfd.readouterr())
    assert title == VERDICT_TITLE
    assert body == VERDICT_BODY


def test_skipped_files_could_not_be_compared(
    file_an_issue: FileAnIssue,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """release-lab#305: the install died, the compare never ran."""
    file_an_issue.enter(monkeypatch, "skipped")
    status = repro_issue.main([])
    title, body = file_an_issue.ran(status, capfd.readouterr())
    assert title == NO_VERDICT_TITLE
    assert body == NO_VERDICT_BODY.format(outcome="skipped")
    assert_no_verdict_words(title, body)


def test_cancelled_files_could_not_be_compared(
    file_an_issue: FileAnIssue,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled comparison reached no verdict either."""
    file_an_issue.enter(monkeypatch, "cancelled")
    status = repro_issue.main([])
    title, body = file_an_issue.ran(status, capfd.readouterr())
    assert title == NO_VERDICT_TITLE
    assert body == NO_VERDICT_BODY.format(outcome="cancelled")
    assert_no_verdict_words(title, body)


def test_untyped_release_names_the_unscoped_population(
    file_an_issue: FileAnIssue,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No typed manifest: the verdict says it judged the whole release."""
    file_an_issue.enter(monkeypatch, "failure", manifest=" \n")
    status = repro_issue.main([])
    title, body = file_an_issue.ran(status, capfd.readouterr())
    assert title == VERDICT_TITLE
    assert body == VERDICT_BODY.replace(
        "the pgrx-extension artifacts v0.28.1 published",
        "every build subject v0.28.1 published — it carries no per-class"
        " typing, so this pgrx-extension rebuild could not be scoped to its"
        " own class",
    )


def assert_no_verdict_words(title: str, body: str) -> None:
    """No word of a verdict, in any case, in title or body."""
    for text in (title, body):
        for word in BANNED:
            assert word not in text.lower()
