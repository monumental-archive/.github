#!/usr/bin/env python3
"""Belt task bodies start their helpers under the belt's Python (#1582).

Inside `mise run`, `python3` is whatever the REPOSITORY pins, so a belt
task that starts `python3 "${ORG_BELT_DIR}/<helper>.py"` runs the belt's
helper under the repository's interpreter. #1543 closed this for
workflow steps and the belt's shell scripts; these rows hold the task
bodies to the same law: a helper starts as
`"${ORG_PYTHON:?${ORG_PYTHON_UNSET}}" <helper.py>`, the refusal riding
the expansion so no body gains a line.

Then the behaviour, under a real mise: a scratch repository that pins a
different Python (stub interpreters, each printing its own name) runs a
belt task's helper under the belt's and its own task under its own; and
with the belt's interpreter absent, the task refuses with the one
command that installs it.

stdlib `unittest`, matching the other tests here.
"""

from __future__ import annotations

import pathlib
import re
import runpy
import shutil

# ruff: ignore[suspicious-subprocess-import]
import subprocess
import tempfile
import tomllib
import unittest
from types import SimpleNamespace

hermetic = SimpleNamespace(
    **runpy.run_path(str(pathlib.Path(__file__).resolve().with_name("hermetic.py")))
)

CANON = pathlib.Path(__file__).resolve().parent.parent
BELT = CANON / "mise" / "config.toml"

# The one start: the interpreter, refusing an empty one by the belt's
# own message.
GUARDED = '"${ORG_PYTHON:?${ORG_PYTHON_UNSET}}"'
GUARDED_START = re.compile(re.escape(GUARDED) + r"\s+$")
INTERPRETER = re.compile(r"(?<![\w./$-])(python[\d.]*)\s+$")
COMMAND_POSITION = re.compile(r"(?:^|[;&|(]|\$\()\s*$")
# A shell word: a quoted string whole, or a run of anything that does
# not end a word or a command.
WORD = re.compile(r"\"[^\"]*\"|'[^']*'|[^\s;|&()<>]+")
ASSIGNMENT = re.compile(r"^\s*(?:local\s+)?(\w+)=(\S+)")
VARIABLE = re.compile(r"\$\{?(\w+)\}?")


def git() -> str:
    """Find git.

    Returns:
        Its path.

    Raises:
        RuntimeError: git is not on PATH.

    """
    found = shutil.which("git")
    if found is None:
        msg = "git is not on PATH"
        raise RuntimeError(msg)
    return found


def tracked_helpers() -> set[str]:
    """Name every tracked Python file by its file name.

    Returns:
        The names of the canon's tracked `*.py`.

    """
    return {pathlib.Path(p).name for p in hermetic.tracked(CANON, "*.py")}


def bodies(text: str) -> dict[str, str]:
    """Read every task's `run` from a mise config.

    Returns:
        Task name to its body, a list of commands joined by newlines.

    """
    out = {}
    for name, task in tomllib.loads(text).get("tasks", {}).items():
        run = task.get("run") if isinstance(task, dict) else None
        if isinstance(run, str):
            out[name] = run
        elif isinstance(run, list):
            out[name] = "\n".join(r for r in run if isinstance(r, str))
    return out


def resolve(word: str, assigned: dict[str, str]) -> str:
    """Name the file a word points at, through the body's own variables.

    Returns:
        The word's last path component, quotes removed and each variable
        the body assigns replaced by its value.

    """

    def value(m: re.Match[str]) -> str:
        return assigned.get(m.group(1), m.group(0))

    return VARIABLE.sub(value, word.strip("\"'")).rsplit("/", 1)[-1]


def helper_starts(rel: str, task: str, body: str, tracked: set[str]) -> list[str]:
    """Name each start of a tracked helper that is not the guarded one.

    A helper is started where it stands after an interpreter's name, or
    in a command's place (a line's first word, or after `;`, `|`, `&`,
    `(` or `$(`), where its own first line picks the interpreter.
    Anywhere else it is an argument; `python3 -c` starts no file.

    Returns:
        One line per such start, naming the config, task and helper.

    """
    lines = [
        ln
        for ln in body.replace("\\\n", " ").splitlines()
        if not ln.lstrip().startswith("#")
    ]
    assigned = {}
    for ln in lines:
        m = ASSIGNMENT.match(ln)
        if m:
            assigned.setdefault(m.group(1), m.group(2).strip("\"'"))
    out = []
    for line in lines:
        for word in WORD.finditer(line):
            name = resolve(word.group(0), assigned)
            if name not in tracked or re.match(r"\w+=", word.group(0)):
                continue
            before = line[: word.start()]
            if GUARDED_START.search(before):
                continue
            interpreter = INTERPRETER.search(before)
            if interpreter:
                how = f"with the repository's {interpreter.group(1)}"
            elif COMMAND_POSITION.search(before):
                how = "by its own first line"
            else:
                continue
            out.append(
                f"{rel} {task}: starts {name} {how}; run it as {GUARDED} {name} (#1582)"
            )
    return out


def findings(texts: dict[str, str], tracked: set[str]) -> list[str]:
    """Hold every task body of each config to the one way to start a helper.

    Returns:
        Every red line, config by config, task by task.

    """
    out = []
    for rel, text in texts.items():
        for task, body in bodies(text).items():
            out += helper_starts(rel, task, body, tracked)
    return out


class TaskBodiesStartHelpersUnderTheBeltsPython(unittest.TestCase):
    """The law: every belt task body starts a helper the guarded way."""

    def test_every_belt_task_starts_its_helpers_under_org_python(self) -> None:
        """The tree: no belt task starts a tracked helper any other way."""
        self.assertEqual(
            [],
            findings({"mise/config.toml": BELT.read_text("utf-8")}, tracked_helpers()),
        )

    def test_the_scan_reaches_the_helper_starts(self) -> None:
        """Not vacuous: the belt starts helpers the guarded way, many times."""
        text = BELT.read_text("utf-8")
        self.assertLessEqual(
            {"gate-surface.py", "belt-shell.py", "coverage-floor.py"},
            tracked_helpers(),
        )
        guarded = [b for b in bodies(text).values() if GUARDED in b]
        self.assertGreaterEqual(len(guarded), 30)

    def test_a_python3_start_is_named(self) -> None:
        """Planted: the shape every belt task had before #1582."""
        text = (
            "[tasks.\"lint:x\"]\nrun = '''\n"
            'python3 "${ORG_BELT_DIR}/gate-surface.py" --exclude ""\n'
            'plan=$(python3 "${ORG_BELT_DIR}/msrv-plan.py" \\\n  --x y)\n'
            "'''\n"
        )
        self.assertEqual(
            [
                (
                    "mise/config.toml lint:x: starts gate-surface.py with the"
                    f" repository's python3; run it as {GUARDED} gate-surface.py"
                    " (#1582)"
                ),
                (
                    "mise/config.toml lint:x: starts msrv-plan.py with the"
                    f" repository's python3; run it as {GUARDED} msrv-plan.py"
                    " (#1582)"
                ),
            ],
            findings({"mise/config.toml": text}, {"gate-surface.py", "msrv-plan.py"}),
        )

    def test_a_start_through_a_variable_is_named(self) -> None:
        """Planted: the helper's path is in a variable the body assigns."""
        text = (
            "[tasks.\"lint:deny\"]\nrun = '''\n"
            'helper="${ORG_BELT_DIR}/deny-skips.py"\n'
            'variants=$(python3 "${helper}" deny.toml)\n'
            "'''\n"
        )
        self.assertEqual(
            [
                (
                    "mise/config.toml lint:deny: starts deny-skips.py with the"
                    f" repository's python3; run it as {GUARDED} deny-skips.py"
                    " (#1582)"
                )
            ],
            findings({"mise/config.toml": text}, {"deny-skips.py"}),
        )

    def test_a_start_by_first_line_is_named(self) -> None:
        """Planted: a helper executed by its own `#!` line, in a pipe."""
        text = (
            '[tasks."lint:y"]\n'
            "run = ['git ls-files | \"${ORG_BELT_DIR}/subject-budget.py\"']\n"
        )
        self.assertEqual(
            [
                (
                    "mise/config.toml lint:y: starts subject-budget.py by its own"
                    f" first line; run it as {GUARDED} subject-budget.py (#1582)"
                )
            ],
            findings({"mise/config.toml": text}, {"subject-budget.py"}),
        )

    def test_the_guarded_start_inline_code_and_arguments_pass(self) -> None:
        """Only an unguarded start of a tracked helper is red."""
        text = (
            "[tasks.\"lint:z\"]\nrun = '''\n"
            f'{GUARDED} "${{ORG_BELT_DIR}}/gate-surface.py" --helper registry.py\n'
            "actual=$(python3 -c 'import sys; print(sys.version)')\n"
            "python3 caller-build.py\n"
            "# python3 mise/registry.py\n"
            "'''\n"
        )
        self.assertEqual(
            [],
            findings({"mise/config.toml": text}, {"gate-surface.py", "registry.py"}),
        )


def mise() -> str:
    """Find mise.

    Returns:
        Its path.

    Raises:
        RuntimeError: mise is not on PATH.

    """
    found = shutil.which("mise")
    if found is None:
        msg = "mise is not on PATH"
        raise RuntimeError(msg)
    return found


def belt_version() -> str:
    """Read the belt's Python pin from its file.

    Returns:
        The version `[tools]` pins.

    """
    return tomllib.loads(BELT.read_text("utf-8"))["tools"]["python"]


# Each stub drains its stdin first: a helper reads a pipe, and one that
# never does SIGPIPEs the writer under pipefail.
STUB = '#!/bin/sh\ncat > /dev/null\necho "{who} $*"\n'


class AScratchRepositoryPinningItsOwnPython(unittest.TestCase):
    """The real belt under a real mise, in a repository that pins Python."""

    def setUp(self) -> None:
        """Plant the repository, an empty config dir and the two stubs."""
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.own = belt_version().rsplit(".", 1)[0] + ".0"
        self.assertNotEqual(self.own, belt_version())
        (self.repo / "mise.toml").write_text(
            f'[tools]\npython = "{self.own}"\n\n[tasks.own]\nrun = "python3 own.py"\n',
            encoding="utf-8",
        )
        (self.tmp / "config").mkdir()
        self.data = self.tmp / "data"
        for who, version in (("belt", belt_version()), ("repo", self.own)):
            bindir = self.data / "installs" / "python" / version / "bin"
            bindir.mkdir(parents=True)
            (bindir / "python3").write_text(STUB.format(who=who), encoding="utf-8")
            (bindir / "python3").chmod(0o755)
        env = hermetic.env(self.tmp)
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run([git(), "init", "-q", "."], cwd=self.repo, env=env, check=True)

    def run_task(self, task: str) -> subprocess.CompletedProcess[str]:
        """Run one task in the repository, the belt delivered as in CI.

        Returns:
            The run.

        """
        env = hermetic.env(
            self.tmp,
            ORG_PYTHON="",
            MISE_GLOBAL_CONFIG_FILE=str(BELT),
            MISE_CONFIG_DIR=str(self.tmp / "config"),
            MISE_DATA_DIR=str(self.data),
            MISE_CACHE_DIR=str(self.tmp / "cache"),
            MISE_STATE_DIR=str(self.tmp / "state"),
            MISE_TRUSTED_CONFIG_PATHS=f"{self.tmp}:{CANON}",
            MISE_OFFLINE="1",
            MISE_AUTO_INSTALL="0",
            MISE_TASK_RUN_AUTO_INSTALL="0",
            MISE_YES="1",
        )
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [mise(), "run", "--output", "interleave", task],
            cwd=self.repo,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    def test_a_belt_task_runs_its_helper_under_the_belts_python(self) -> None:
        """The belt's stub answers, never the repository's."""
        done = self.run_task("lint:subject-budget")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f"belt {CANON / 'mise' / 'subject-budget.py'}\n", done.stdout)
        self.assertNotIn("repo ", done.stdout)

    def test_the_own_task_keeps_the_pinned_python(self) -> None:
        """PATH is untouched: the repository's task gets what it pins."""
        done = self.run_task("own")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("repo own.py\n", done.stdout)
        self.assertNotIn("belt ", done.stdout)

    def test_an_absent_belt_python_is_refused_with_its_install(self) -> None:
        """Never installed beside the repository's: red, naming the fix."""
        shutil.rmtree(self.data / "installs" / "python" / belt_version())
        done = self.run_task("lint:subject-budget")
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn(
            "ORG_PYTHON: unset — the belt Python is not installed here; run:"
            f" mise install python@{belt_version()} (CI: the canon action)",
            done.stderr,
        )
        self.assertNotIn("repo ", done.stdout)


if __name__ == "__main__":
    unittest.main()
