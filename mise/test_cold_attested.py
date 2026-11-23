#!/usr/bin/env python3
"""Table tests for lint:cold-attested and its helper (#117, #1934, #1911).

Each row of CASES plants workflows and asserts the exit status and
every line the lint prints, one row per branch of the marker rules: a
marked cache, an unmarked one, a marker on a step with no cache, a
marker a blank line or another line ends, a marker at another indent,
prose naming the marker below a block's first line, the two
cache-by-default shapes with and without `cache: false`, a step closed
by a dedent or by the next file, a nested list item that is not a step,
a commented-out cache, and an empty file. Two rows run the task's own
body, read from mise/config.toml, as mise runs it
(`hermetic.task_run`): the skip when there are no workflows, a clean
tree, an unmarked cache, and two findings in one file, each its own
line in the order found. CASES call the helper's `main` in this
process, in the planted tree. Two more call it with no workflow and
with a missing one. Every row asserts the whole of each stream: a red
row scored on exit alone can pass for the wrong reason.

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

import cold_attested
import decoded
import hermetic

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:cold-attested"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


BELT = Path(__file__).resolve().parent
HEAD = "on: push\njobs:\n  a:\n    runs-on: ubuntu-26.04\n    steps:\n"
MARK = "      # unattested-path: a dev cache, nothing here is signed\n"
CACHE = "      - uses: actions/cache@v5\n        with:\n          path: x\n"
CHECKOUT = "      - uses: actions/checkout@v6\n"
SETUP = "      - uses: actions/setup-go@v7\n"
MISE = "      - uses: jdx/mise-action@v4\n"
OFF = "        with:\n          cache: false\n"
WF = ".github/workflows"
CLEAN = [cold_attested.CLEAN]


def red(*found: tuple[str, str]) -> list[str]:
    """Build the lines some findings print.

    Returns:
        One line per finding, `<task>: <file>:<line>: <message>`.

    """
    return [f"{TASK}: {where}: {message}" for where, message in found]


def default(kind: str) -> str:
    """Name the cache-by-default message for one shape.

    Returns:
        The message.

    """
    return cold_attested.DEFAULT.format(kind)


# name -> (files in order, status, stdout lines, stderr lines)
CASES: dict[str, tuple[dict[str, str], int, list[str], list[str]]] = {
    "a marked cache is clean": (
        {"a.yml": HEAD + CHECKOUT + MARK + CACHE},
        0,
        CLEAN,
        [],
    ),
    "an unmarked cache is red at its uses line": (
        {"a.yml": HEAD + CHECKOUT + CACHE},
        1,
        [],
        red((f"{WF}/a.yml:7", cold_attested.NO_MARKER)),
    ),
    "a marker on a step with no cache is red at the step": (
        {"a.yml": HEAD + MARK + CHECKOUT},
        1,
        [],
        red((f"{WF}/a.yml:7", cold_attested.NO_CACHE)),
    ),
    "a marker a blank line ends introduces no step": (
        {"a.yml": HEAD + MARK + "\n" + CACHE},
        1,
        [],
        red(
            (f"{WF}/a.yml:6", cold_attested.NO_STEP),
            (f"{WF}/a.yml:8", cold_attested.NO_MARKER),
        ),
    ),
    "a marker another line ends introduces no step": (
        {"a.yml": HEAD + CHECKOUT + "        with:\n" + MARK + "          x: y\n"},
        1,
        [],
        red((f"{WF}/a.yml:8", cold_attested.NO_STEP)),
    ),
    "a marker at another indent introduces no step": (
        {"a.yml": HEAD + "    " + MARK + CACHE},
        1,
        [],
        red(
            (f"{WF}/a.yml:6", cold_attested.NO_STEP),
            (f"{WF}/a.yml:7", cold_attested.NO_MARKER),
        ),
    ),
    "prose below a block's first line is no marker": (
        {"a.yml": HEAD + "      # Why:\n" + MARK + CACHE},
        1,
        [],
        red((f"{WF}/a.yml:8", cold_attested.NO_MARKER)),
    ),
    "a setup step that says cache: false is clean": (
        {"a.yml": HEAD + SETUP + OFF + MISE + OFF},
        0,
        CLEAN,
        [],
    ),
    "a setup step left at its default is red": (
        {"a.yml": HEAD + SETUP + CHECKOUT},
        1,
        [],
        red((f"{WF}/a.yml:6", default("actions/setup-*"))),
    ),
    "a mise-action step left at its default is red": (
        {"a.yml": HEAD + MISE},
        1,
        [],
        red((f"{WF}/a.yml:6", default("mise-action"))),
    ),
    "a marked setup step is clean": (
        {"a.yml": HEAD + MARK + SETUP},
        0,
        CLEAN,
        [],
    ),
    "a dedent closes the step": (
        {"a.yml": HEAD + SETUP + "  b:\n    runs-on: x\n"},
        1,
        [],
        red((f"{WF}/a.yml:6", default("actions/setup-*"))),
    ),
    "a nested list item is not a step": (
        {"a.yml": HEAD + MARK + CACHE + "          - name: x\n"},
        0,
        CLEAN,
        [],
    ),
    "a commented-out cache declares nothing": (
        {"a.yml": HEAD + "      # - uses: actions/cache@v5\n\n" + CHECKOUT},
        0,
        CLEAN,
        [],
    ),
    "the next file closes the last one's step, named in its file": (
        {"a.yml": HEAD + MISE, "b.yaml": HEAD + CHECKOUT},
        1,
        [],
        red((f"{WF}/a.yml:6", default("mise-action"))),
    ),
    "an empty file is read as no lines": (
        {"a.yml": HEAD + MARK + CACHE, "b.yml": ""},
        0,
        CLEAN,
        [],
    ),
    "a marker at a file's end is dropped with its file": (
        {"a.yml": HEAD + CHECKOUT + MARK, "b.yml": HEAD + CHECKOUT},
        0,
        CLEAN,
        [],
    ),
    "a carriage return is a blank": (
        {"a.yml": HEAD + MARK + "\r\n" + CACHE},
        1,
        [],
        red(
            (f"{WF}/a.yml:6", cold_attested.NO_STEP),
            (f"{WF}/a.yml:8", cold_attested.NO_MARKER),
        ),
    ),
}


def same[T](case: unittest.TestCase, got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    case.assertEqual(got, want, msg or None)


def body() -> str:
    """Read lint:cold-attested's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


class ColdAttested(unittest.TestCase):
    """Planted workflows; the lint's verdict on them."""

    @override
    def setUp(self) -> None:
        """Plant an empty workflows directory."""
        self.tree = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tree)
        (self.tree / WF).mkdir(parents=True)
        self.env = hermetic.env(
            MISE_TASK_NAME=TASK,
            ORG_BELT_DIR=str(BELT),
            ORG_PYTHON=sys.executable,
        )

    def plant(self, files: dict[str, str]) -> list[str]:
        """Write workflows.

        Returns:
            Their paths from the tree's top, in order.

        """
        names = []
        for name, text in files.items():
            rel = f"{WF}/{name}"
            (self.tree / rel).write_text(text, encoding="utf-8")
            names.append(rel)
        return names

    def helper(self, *files: str) -> hermetic.Called:
        """Call the helper's main in the planted tree.

        Returns:
            The status and what it printed.

        """
        argv = list(files)
        return hermetic.call_main(
            lambda: cold_attested.main(argv), env=self.env, cwd=self.tree
        )

    def task(self) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree.

        Returns:
            The finished process.

        """
        hermetic.track(self.tree)
        as_mise = hermetic.task_run(TASK, body())
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_no_workflows_is_skipped_by_name(self) -> None:
        """No workflow file: the task says it skipped."""
        done = self.task()
        same(self, done.returncode, 0, done.stderr)
        skipped = ["lint:cold-attested: no workflows, skipped"]
        same(self, done.stdout.splitlines(), skipped)

    def test_a_clean_tree_passes_through_the_task(self) -> None:
        """No cache anywhere: green, the summary on stdout."""
        self.plant({"a.yml": HEAD + CHECKOUT})
        done = self.task()
        same(self, (done.returncode, done.stderr), (0, ""))
        same(self, done.stdout.splitlines(), CLEAN)

    def test_each_finding_is_its_own_line_through_the_task(self) -> None:
        """A default-cache step and a stray marker: two findings, in order."""
        self.plant({"a.yml": HEAD + MISE + MARK + "\n"})
        done = self.task()
        same(self, (done.returncode, done.stdout), (1, ""))
        same(
            self,
            done.stderr.splitlines(),
            red(
                (f"{WF}/a.yml:7", cold_attested.NO_STEP),
                (f"{WF}/a.yml:6", default("mise-action")),
            ),
        )

    def test_an_unmarked_cache_is_red_through_the_task(self) -> None:
        """The task body hands the helper every workflow, in glob order."""
        self.plant({"a.yaml": HEAD + CACHE, "b.yml": HEAD + MARK + CACHE})
        done = self.task()
        same(self, done.returncode, cold_attested.EXIT_RED)
        same(self, done.stdout, "")
        same(
            self,
            done.stderr.splitlines(),
            red((f"{WF}/a.yaml:6", cold_attested.NO_MARKER)),
        )

    def test_cases(self) -> None:
        """Every row of CASES, through the helper's main."""
        for name, (files, status, out, err) in CASES.items():
            with self.subTest(name):
                for old in (self.tree / WF).iterdir():
                    old.unlink()
                done = self.helper(*self.plant(files))
                same(self, done.returncode, status, done.stderr)
                same(self, done.stdout.splitlines(), out)
                same(self, done.stderr.splitlines(), err)

    def test_no_workflow_is_a_usage_error(self) -> None:
        """Called with nothing to read: exit 2, saying how to call it."""
        done = self.helper()
        same(self, done.returncode, cold_attested.EXIT_ERROR)
        same(
            self,
            done.stderr.splitlines(),
            [f"{TASK}: usage: cold_attested.py WORKFLOW..."],
        )

    def test_a_missing_workflow_is_a_read_error(self) -> None:
        """A named file that is not there: exit 2, naming it."""
        done = self.helper(f"{WF}/gone.yml")
        same(self, done.returncode, cold_attested.EXIT_ERROR)
        same(self, done.stdout, "")
        self.assertTrue(
            done.stderr.startswith(f"{TASK}: {WF}/gone.yml: cannot be read as UTF-8: "),
            done.stderr,
        )

    def test_a_workflow_not_utf8_is_a_read_error(self) -> None:
        """Bytes that are not UTF-8: exit 2, naming the file."""
        (self.tree / WF / "a.yml").write_bytes(b"on: push\n# \xe9\n")
        done = self.helper(f"{WF}/a.yml")
        same(self, done.returncode, cold_attested.EXIT_ERROR)
        same(self, done.stdout, "")
        self.assertTrue(
            done.stderr.startswith(f"{TASK}: {WF}/a.yml: cannot be read as UTF-8: "),
            done.stderr,
        )


if __name__ == "__main__":
    unittest.main()
