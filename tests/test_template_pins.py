"""Table tests for lint:template-pins and mise/template_pins.py (#1887).

Each row plants workflow templates in a git tree with tagged commits,
and asserts the exit status and every line of the message: a pin that
holds, each way a pin can fail, a clone with no tags, a template that
is not UTF-8, and a call with no canon. Two rows run the task's own
body, read from mise/config.toml: the skip when there are no templates,
and the canon action pin end to end; the rest call the helper's `main`
in this process, in the planted tree.

scorecard.yml is self-contained by design: Scorecard's publish path
refuses a reusable workflow. Its `report` job needs the canon action
for the belt's Python, so that one pin is allowed (owner's ruling,
2026-11-21) and held to the same released SHA as every other.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import decoded, template_pins
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:template-pins"


@pytest.fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
CANON_DIR = BELT.parent
BASH = support.tool("bash")
GIT = support.tool("git")
CANON = "monumental-archive/.github"
ACTION = f"{CANON}/.github/actions/canon"
REUSABLE = f"{CANON}/.github/workflows/ci.yml"
SCORECARD = "workflow-templates/scorecard.yml"
STUB = "workflow-templates/records.yml"
TAIL = [
    "  templates pin one merged, RELEASED canon SHA and move together",
    "  (#111, #233); scorecard.yml pins the canon action, never a reusable (#1887).",
]


def same[T](got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    assert got == want, msg or None


def body() -> str:
    """Read lint:template-pins' run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks["lint:template-pins"], "lint:template-pins")
    return decoded.as_str(task["run"], "lint:template-pins.run")


def template(uses: str, sha: str) -> str:
    """Write a template with one canon pin beside a foreign one.

    Returns:
        The template's text.

    """
    return (
        "jobs:\n  a:\n    steps:\n"
        "      - uses: ossf/scorecard-action@" + "0" * 40 + " # v2\n"
        f"      - uses: {uses}@{sha} # v1.0.0\n"
    )


def red(*violations: str) -> list[str]:
    """Build the red message for some violations.

    Returns:
        Its lines.

    """
    head = f"lint:template-pins: template pin violations: {' '.join(violations)}"
    return [head, *TAIL]


@pytest.mark.usefixtures("_set_up_test_pins")
class TestPins:
    """Planted templates in a tree with a released commit."""

    @pytest.fixture
    def _set_up_test_pins(self, tmp_path: Path) -> None:
        """Plant a tree whose first commit is released as v1.0.0."""
        self.tree = tmp_path
        self.env = support.env(
            MISE_TASK_NAME=TASK,
            ORG_BELT_DIR=str(BELT),
            ORG_CANON_DIR=str(CANON_DIR),
            ORG_PYTHON=sys.executable,
        )
        self.git("init", "-q")
        self.sha = self.commit("v1.0.0")
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

    def commit(self, tag: str = "") -> str:
        """Make an empty commit, tagged when a tag is given.

        Returns:
            Its SHA.

        """
        self.git("commit", "-q", "--allow-empty", "-m", tag or "untagged")
        if tag:
            self.git("tag", tag)
        return self.git("rev-parse", "HEAD").strip()

    def plant(self, name: str, text: str | bytes) -> None:
        """Write one template and track it: the task reads tracked files (#1986)."""
        path = self.tree / name
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        self.git("add", "--", name)

    def helper(
        self, *files: str, capfd: pytest.CaptureFixture[str]
    ) -> tuple[int, str, str]:
        """Call the helper's main in the planted tree.

        Returns:
            The status and what it printed.

        """
        argv = [CANON, *files]
        with pytest.MonkeyPatch.context() as patched:
            for name, value in self.env.items():
                patched.setenv(name, value)
            patched.chdir(self.tree)
            status = template_pins.main(argv)
        out, err = capfd.readouterr()
        return status, out, err

    def task(self) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree.

        Returns:
            The finished process.

        """
        as_mise = support.task_run("lint:template-pins", body())
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_no_templates_is_skipped_by_name(self) -> None:
        """No workflow-templates/: the task says it skipped."""
        shutil.rmtree(self.tree / "workflow-templates")
        done = self.task()
        same(done.returncode, 0, done.stderr)
        skipped = ["lint:template-pins: no workflow-templates/, skipped"]
        same(done.stdout.splitlines(), skipped)

    def test_canon_action_pin_passes(self) -> None:
        """The canon action, at a released SHA, is scorecard's one pin."""
        self.plant(SCORECARD, template(ACTION, self.sha))
        done = self.task()
        same(done.returncode, 0, done.stderr)
        expected = (
            "lint:template-pins: 1 pins at one ancestor SHA, scorecard calls no"
            " reusable, released as v1.0.0"
        )
        same(done.stdout.splitlines(), [expected])

    def test_reusable_workflow_pin_is_red(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A reusable workflow pinned in scorecard.yml is red by message."""
        self.plant(SCORECARD, template(REUSABLE, self.sha))
        status, _out, err = self.helper(SCORECARD, capfd=capfd)
        same(status, 1)
        violation = f"{SCORECARD}(pins a reusable workflow: self-contained by design)"
        same(err.splitlines(), red(violation))

    def test_short_sha_is_red(self, capfd: pytest.CaptureFixture[str]) -> None:
        """A pin that is not 40 hex is red by message."""
        self.plant(STUB, template(REUSABLE, self.sha[:12]))
        status, _out, err = self.helper(STUB, capfd=capfd)
        same(status, 1)
        violation = f"{STUB}(pin is not a full 40-hex SHA)"
        same(err.splitlines(), red(violation))

    def test_foreign_history_is_red(self, capfd: pytest.CaptureFixture[str]) -> None:
        """A SHA HEAD does not contain is red, and names no release."""
        home = self.git("branch", "--show-current").strip()
        self.git("checkout", "-q", "--orphan", "other")
        foreign = self.commit("v9.0.0")
        self.git("checkout", "-q", home)
        self.plant(STUB, template(REUSABLE, foreign))
        status, _out, err = self.helper(STUB, capfd=capfd)
        same(status, 1)
        violation = f"{STUB}({foreign[:8]} not an ancestor of HEAD)"
        same(err.splitlines(), red(violation))

    def test_templates_disagree_is_red(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Two templates on two SHAs are red, both named."""
        newer = self.commit("v1.1.0")
        self.plant(STUB, template(REUSABLE, self.sha))
        self.plant(SCORECARD, template(ACTION, newer))
        status, _out, err = self.helper(STUB, SCORECARD, capfd=capfd)
        same(status, 1)
        violation = f"(templates disagree, 2 distinct SHAs: {self.sha} {newer})"
        same(err.splitlines(), red(violation))

    def test_unreleased_sha_is_red(self, capfd: pytest.CaptureFixture[str]) -> None:
        """An ancestor no tag names is red: a pin names a release."""
        untagged = self.commit()
        self.plant(STUB, template(REUSABLE, untagged))
        status, _out, err = self.helper(STUB, capfd=capfd)
        same(status, 1)
        violation = (
            f"({untagged[:8]} names no released tag — pins must name a release,"
            " not any ancestor)"
        )
        same(err.splitlines(), red(violation))

    def test_no_tags_skips_the_release_check_by_name(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A clone with no tags says it skipped the released-tag check."""
        self.git("tag", "-d", "v1.0.0")
        self.plant(STUB, template(REUSABLE, self.sha))
        status, out, err = self.helper(STUB, capfd=capfd)
        same(status, 0, err)
        expected = (
            "lint:template-pins: 1 pins at one ancestor SHA, scorecard calls no"
            " reusable, released as (no tags in this clone, released-tag check"
            " skipped)"
        )
        same(out.splitlines(), [expected])

    def test_not_utf8_is_an_error(self, capfd: pytest.CaptureFixture[str]) -> None:
        """A template that is not UTF-8 is refused by name, exit 2."""
        self.plant(STUB, b"jobs: \xff\n")
        status, _out, err = self.helper(STUB, capfd=capfd)
        same(status, template_pins.EXIT_ERROR)
        assert err.startswith(
            f"lint:template-pins: {STUB}: cannot be read as UTF-8:"
        ), err

    def test_no_canon_is_a_usage_error(self, capfd: pytest.CaptureFixture[str]) -> None:
        """No canon, or one missing its owner or repository: exit 2."""
        usage = "lint:template-pins: usage: belt template-pins OWNER/REPO TEMPLATE..."
        calls: list[list[str]] = [[], ["/repo", STUB], ["owner/", STUB]]
        for argv in calls:
            status, _out, err = self.refused(argv, capfd=capfd)
            same(status, template_pins.EXIT_ERROR)
            same(err.splitlines(), [usage])

    def refused(
        self, argv: list[str], *, capfd: pytest.CaptureFixture[str]
    ) -> tuple[int, str, str]:
        """Call the helper's main with a command line of the row's.

        Returns:
            The status and what it printed.

        """
        with pytest.MonkeyPatch.context() as patched:
            for name, value in self.env.items():
                patched.setenv(name, value)
            status = template_pins.main(argv)
        out, err = capfd.readouterr()
        return status, out, err
