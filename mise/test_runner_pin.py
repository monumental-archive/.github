#!/usr/bin/env python3
"""Table tests for lint:runner-pin and mise/runner_pin.py (#1911).

Each row plants workflows and templates and asserts the exit status
and every line of the message: pinned labels (plain, suffixed, quoted,
commented), a matrix indirection, an expression that is not one, an
unpinned literal, an empty value, a floating label reaching runs-on
through a matrix include, a floating label in a comment (not counted),
two floating lines (the first named), a carriage return, the order
across workflows and templates, a file that is not UTF-8, a missing
file, and a call with no separator. Three rows run the task's own
body, read from mise/config.toml: the skip with nothing tracked, an
untracked workflow (still a skip), and offenders in both lists end to
end; the rest call the helper's `main` in this process.

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

import decoded
import hermetic
import runner_pin

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:runner-pin"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


BELT = Path(__file__).resolve().parent
GIT = hermetic.tool("git")
OK = [
    ("lint:runner-pin: every runs-on pinned or matrix-indirected, no floating labels")
]
SKIPPED = ["lint:runner-pin: no workflows, skipped"]
TAIL = [
    "  pin every runs-on to a named image version (ubuntu-24.04, not",
    "  ubuntu-latest) — Renovate rolls named pins as visible diffs;",
    "  a floating label migrates with no diff at all (#290).",
]


def same[T](case: unittest.TestCase, got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    case.assertEqual(got, want, msg or None)


def body() -> str:
    """Read lint:runner-pin's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


def job(runs_on: str) -> str:
    """Write a workflow with one job on a runner.

    Returns:
        Its text.

    """
    return f"jobs:\n  a:\n    runs-on: {runs_on}\n    steps:\n      - run: true\n"


def red(*offences: str) -> list[str]:
    """Build the red message naming some offences.

    Returns:
        Its lines.

    """
    listed = "".join(f" {o}" for o in offences)
    return [f"{TASK}: floating or unpinned runner labels:{listed}", *TAIL]


class Planted(unittest.TestCase):
    """A planted git tree of workflows and templates."""

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
        subprocess.run([GIT, "init", "-q"], cwd=self.tree, env=self.env, check=True)

    def plant(self, rel: str, text: str | bytes, *, track: bool = True) -> str:
        """Write one file, tracked unless asked otherwise.

        Returns:
            Its path from the tree's top.

        """
        path = self.tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        if track:
            subprocess.run(
                [GIT, "add", "--", rel], cwd=self.tree, env=self.env, check=True
            )
        return rel

    def flow(self, name: str, text: str | bytes) -> str:
        """Write one workflow.

        Returns:
            Its path from the tree's top.

        """
        return self.plant(f".github/workflows/{name}", text)

    def helper(self, *argv: str) -> hermetic.Called:
        """Call the helper's main in the planted tree.

        Returns:
            The status and what it printed.

        """
        args = list(argv)
        return hermetic.call_main(
            lambda: runner_pin.main(args), env=self.env, cwd=self.tree
        )

    def check(self, text: str) -> hermetic.Called:
        """Plant one workflow and check it alone.

        Returns:
            The status and what it printed.

        """
        return self.helper(self.flow("ci.yml", text), "--")

    def task(self) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree.

        Returns:
            The finished process.

        """
        as_mise = hermetic.task_run(TASK, body())
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


class Task(Planted):
    """The task's own body: its guard and its two lists."""

    def test_nothing_tracked_is_skipped_by_name(self) -> None:
        """No workflow and no template: the task says it skipped."""
        done = self.task()
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), SKIPPED)

    def test_an_untracked_workflow_is_no_workflow(self) -> None:
        """A workflow git does not track: still skipped, never read."""
        self.plant(".github/workflows/ci.yml", job("ubuntu-latest"), track=False)
        done = self.task()
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), SKIPPED)

    def test_both_lists_through_the_task(self) -> None:
        """Offenders in a workflow and a template: one finding, workflows first."""
        self.plant("workflow-templates/a.yaml", job("macos-latest"))
        self.flow("z.yml", job("self-hosted"))
        self.flow("ok.yaml", job("ubuntu-26.04"))
        done = self.task()
        same(self, done.returncode, runner_pin.EXIT_RED)
        expected = red(
            ".github/workflows/z.yml(runs-on: self-hosted)",
            "workflow-templates/a.yaml(runs-on: macos-latest)",
            "workflow-templates/a.yaml(floating label: 3:    runs-on: macos-latest)",
        )
        same(self, done.stderr.splitlines(), expected)

    def test_a_template_alone_is_checked(self) -> None:
        """Templates and no workflow: checked, not skipped."""
        self.plant("workflow-templates/a.yml", job("ubuntu-24.04-arm"))
        done = self.task()
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), OK)


class Labels(Planted):
    """The runs-on values the helper accepts and refuses."""

    def test_pinned_values_are_clean(self) -> None:
        """Pinned literals, suffixed, quoted or commented: clean."""
        for value in (
            "ubuntu-24.04",
            "ubuntu-24.04-arm",
            "macos-15-intel",
            '"ubuntu-26.04"',
            "'windows-2025'",
            "ubuntu-24.04 # the named pin",
            "ubuntu-24.04# no blank",
            "${{ matrix.os }}",
            "${{matrix.runner}}",
            "${{ fromJSON(matrix.runner) }}",
        ):
            with self.subTest(value=value):
                done = self.check(job(value))
                same(self, done.returncode, 0, done.stderr)
                same(self, done.stdout.splitlines(), OK)

    def test_unpinned_values_are_red(self) -> None:
        """A literal with no version, or an expression that is no matrix one."""
        rel = ".github/workflows/ci.yml"
        for value, shown in (
            ("self-hosted", "self-hosted"),
            ("Ubuntu-24.04", "Ubuntu-24.04"),
            ("ubuntu-24.04 extra", "ubuntu-24.04 extra"),
            ("${{ inputs.runner }}", "${{ inputs.runner }}"),
            ("${{ matrix }}", "${{ matrix }}"),
            ("[ubuntu-24.04]", "[ubuntu-24.04]"),
            ("", ""),
            ("# just a comment", ""),
        ):
            with self.subTest(value=value):
                done = self.check(job(value))
                same(self, done.returncode, runner_pin.EXIT_RED)
                same(self, done.stderr.splitlines(), red(f"{rel}(runs-on: {shown})"))

    def test_a_carriage_return_is_part_of_the_value(self) -> None:
        """Lines split on newline alone: a trailing CR leaves the value unpinned.

        The finding writer prints the CR as its escape, as the shell's did.
        """
        done = self.check("jobs:\n  a:\n    runs-on: ubuntu-24.04\r\n")
        same(self, done.returncode, runner_pin.EXIT_RED)
        expected = red(".github/workflows/ci.yml(runs-on: ubuntu-24.04\\r)")
        same(self, done.stderr.splitlines(), expected)


class Floating(Planted):
    """A floating label anywhere outside a comment."""

    def test_a_matrix_include_is_red(self) -> None:
        """ubuntu-latest reaching runs-on through a matrix: the first line named."""
        text = (
            "jobs:\n  a:\n    strategy:\n      matrix:\n"
            "        os: [ubuntu-latest, windows-latest]\n"
            "        also: macos-latest\n"
            "    runs-on: ${{ matrix.os }}\n"
        )
        done = self.check(text)
        same(self, done.returncode, runner_pin.EXIT_RED)
        named = "        os: [ubuntu-latest, windows-latest]"
        expected = red(f".github/workflows/ci.yml(floating label: 5:{named})")
        same(self, done.stderr.splitlines(), expected)

    def test_a_comment_is_not_a_label(self) -> None:
        """A comment line naming ubuntu-latest: not counted."""
        text = "# never ubuntu-latest\n   # nor macos-latest\n" + job("ubuntu-24.04")
        done = self.check(text)
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), OK)

    def test_a_trailing_comment_still_counts(self) -> None:
        """A label before a comment on the same line is a float."""
        text = job("ubuntu-24.04") + "      - run: echo ubuntu-latest # no\n"
        done = self.check(text)
        same(self, done.returncode, runner_pin.EXIT_RED)
        line = "      - run: echo ubuntu-latest # no"
        expected = red(f".github/workflows/ci.yml(floating label: 6:{line})")
        same(self, done.stderr.splitlines(), expected)


class Calls(Planted):
    """Reading the files, and the call itself."""

    def test_the_order_is_the_arguments(self) -> None:
        """Workflows then templates, each file's runs-on before its float."""
        one = self.flow("b.yml", job("ubuntu-latest"))
        two = self.flow("a.yml", job("x"))
        three = self.plant("workflow-templates/c.yml", job("y"))
        done = self.helper(one, two, "--", three)
        same(self, done.returncode, runner_pin.EXIT_RED)
        expected = red(
            f"{one}(runs-on: ubuntu-latest)",
            f"{one}(floating label: 3:    runs-on: ubuntu-latest)",
            f"{two}(runs-on: x)",
            f"{three}(runs-on: y)",
        )
        same(self, done.stderr.splitlines(), expected)

    def test_nothing_given_is_clean(self) -> None:
        """A separator and no file: nothing to name."""
        done = self.helper("--")
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), OK)

    def test_not_utf8_is_an_error(self) -> None:
        """A workflow that is not UTF-8 is refused by name, exit 2."""
        rel = self.flow("ci.yml", b"jobs: \xff\n")
        done = self.helper(rel, "--")
        same(self, done.returncode, runner_pin.EXIT_ERROR)
        self.assertTrue(
            done.stderr.startswith(f"{TASK}: {rel}: cannot be read as UTF-8:"),
            done.stderr,
        )

    def test_a_missing_file_is_an_error(self) -> None:
        """A named file that is not there is refused by name, exit 2."""
        done = self.helper("--", "workflow-templates/gone.yml")
        same(self, done.returncode, runner_pin.EXIT_ERROR)
        self.assertTrue(
            done.stderr.startswith(
                f"{TASK}: workflow-templates/gone.yml: cannot be read as UTF-8:"
            ),
            done.stderr,
        )

    def test_no_separator_is_a_usage_error(self) -> None:
        """No `--` between the lists: exit 2 with the usage line."""
        done = self.helper(".github/workflows/ci.yml")
        same(self, done.returncode, runner_pin.EXIT_ERROR)
        same(self, done.stderr.splitlines(), [f"{TASK}: {runner_pin.USAGE}"])


if __name__ == "__main__":
    unittest.main()
