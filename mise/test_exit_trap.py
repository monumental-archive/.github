#!/usr/bin/env python3
"""Table tests for the EXIT-trap completion guard (#1233).

Two tables. The lint rows plant scripts and task bodies and assert the
exact message each one reds with, strict on the clean rows, so a row
cannot pass by reddening for some other reason. The pattern rows run
the guard itself under `/bin/bash` where that is 3.2 — macOS, the shell
the defect lives in — and assert the exit code of each: the defect as
measured with a bare trap, then the same bodies under the guard. Where
`/bin/bash` is not 3.2 (every runner) they skip by name; bash 5 never
had the defect, so there is nothing for them to show there.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import importlib.util
import io
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

_SPEC = importlib.util.spec_from_file_location(
    "exit_trap",
    Path(__file__).with_name("exit-trap.py"),
)
et = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(et)

BASH = Path("/bin/bash")
GUARDED = et.GUARD.format(cleanup='rm -f "${tmp}"')
BARE = "trap 'rm -f \"${tmp}\"' EXIT"
NOT_GUARDED = "EXIT trap without the completion guard"
NOT_MARKED = "has an EXIT trap and its last statement is not org_done=1"
# The shape travels with every red, so the message hands over the lines to
# paste rather than a description of them.
SHAPE_LINE = et.GUARD.format(cleanup="<cleanup>")


def bash_version() -> str:
    """Report what `/bin/bash` is, or "" when there is none.

    Returns:
        The `BASH_VERSION` it prints.

    """
    if not BASH.is_file():
        return ""
    # ruff: ignore[subprocess-without-shell-equals-true]
    done = subprocess.run(
        [str(BASH), "-c", 'echo "${BASH_VERSION}"'],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


IS_32 = bash_version().startswith("3.2")


def lint(*named: tuple[str, str]) -> tuple[int, list[str], bool]:
    """Run the lint over planted files, the way the task does.

    Returns:
        The exit status; the finding lines, with the temporary directory
        stripped so the rows read as relative paths; and whether the
        shape to paste was printed.

    """
    with TemporaryDirectory() as scratch:
        paths = []
        for name, text in named:
            path = Path(scratch) / name
            path.write_text(text, encoding="utf-8")
            paths.append(str(path))
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            status = et.main(paths)
        lines = [
            line.removeprefix(f"exit-trap: {scratch}/")
            for line in err.getvalue().splitlines()
            if line.startswith("exit-trap: ")
        ]
        return status, lines, SHAPE_LINE in err.getvalue()


def script(*body: str) -> str:
    """Build a script under the interpreter flags the belt runs with.

    Returns:
        The script text.

    """
    return "\n".join(["#!/usr/bin/env bash", "set -euo pipefail", *body, ""])


class Lint(unittest.TestCase):
    """The rule over planted scripts and task bodies."""

    def test_unguarded_trap_reds_both_ways(self) -> None:
        """A bare trap owes the guard, and the mark it reads."""
        status, lines, shaped = lint(("a.sh", script("tmp=$(mktemp)", BARE, "echo ok")))
        self.assertEqual((status, shaped), (1, True))
        self.assertEqual(
            lines,
            [f"a.sh:4: {NOT_GUARDED}", f"a.sh:5: {NOT_MARKED}"],
        )

    def test_guarded_without_the_mark_reds(self) -> None:
        """The guard alone turns every clean run into a failure."""
        status, lines, shaped = lint((
            "a.sh",
            script("tmp=$(mktemp)", GUARDED, "echo ok"),
        ))
        self.assertEqual((status, shaped), (1, True))
        self.assertEqual(lines, [f"a.sh:5: {NOT_MARKED}"])

    def test_mark_not_last_reds(self) -> None:
        """A mark followed by more script is not the last statement."""
        status, lines, shaped = lint(
            ("a.sh", script(GUARDED, "org_done=1", 'exit "${status}"')),
        )
        self.assertEqual((status, shaped), (1, True))
        self.assertEqual(lines, [f"a.sh:5: {NOT_MARKED}"])

    def test_guard_on_a_combined_signal_list_reds(self) -> None:
        """The shape is EXIT alone; `EXIT INT` is a different trap."""
        status, lines, shaped = lint(
            ("a.sh", script(GUARDED.replace("' EXIT", "' EXIT INT"), "org_done=1")),
        )
        self.assertEqual((status, shaped), (1, True))
        self.assertEqual(lines, [f"a.sh:3: {NOT_GUARDED}"])

    def test_guarded_and_marked_is_clean(self) -> None:
        """The two lines, with comments and blanks after the mark."""
        status, lines, shaped = lint(
            (
                "a.sh",
                script(
                    "tmp=$(mktemp)",
                    et.DIRECTIVE,
                    GUARDED,
                    "echo ok",
                    "org_done=1  # the last statement",
                    "",
                    "# trailing prose",
                ),
            ),
        )
        self.assertEqual((status, lines, shaped), (0, [], False))

    def test_no_trap_and_reset_owe_nothing(self) -> None:
        """No EXIT trap, a comment naming one, and `trap - EXIT`."""
        status, lines, shaped = lint(
            ("a.sh", script("echo ok")),
            ("b.sh", script(f"# {BARE}", "trap - EXIT", "echo ok")),
        )
        self.assertEqual((status, lines, shaped), (0, [], False))

    def test_task_body_points_at_the_config_line(self) -> None:
        """Each body is its own script; findings carry the file line."""
        config = (
            '[tasks."lint:a"]\n'
            "run = '''\n"
            "tmp=$(mktemp)\n"
            f"{BARE}\n"
            "echo ok\n"
            "'''\n\n"
            '[tasks."lint:b"]\n'
            "run = '''\n"
            f"{GUARDED}\n"
            "org_done=1\n"
            "'''\n"
        )
        status, lines, shaped = lint(("mise.toml", config))
        self.assertEqual((status, shaped), (1, True))
        self.assertEqual(
            lines,
            [
                f"mise.toml:4: task lint:a: {NOT_GUARDED}",
                f"mise.toml:5: task lint:a: {NOT_MARKED}",
            ],
        )

    def test_one_line_body_is_its_run_line(self) -> None:
        """A `run = "…"` body starts on the `run =` line itself."""
        status, lines, shaped = lint(
            ("mise.toml", "[tasks.a]\nrun = \"trap 'rm -f x' EXIT\"\n"),
        )
        self.assertEqual((status, shaped), (1, True))
        self.assertEqual(
            lines,
            [
                f"mise.toml:2: task a: {NOT_GUARDED}",
                f"mise.toml:2: task a: {NOT_MARKED}",
            ],
        )


def run_bash(*body: str) -> int:
    """Run a script under `/bin/bash` the way the belt runs one.

    Returns:
        Its exit status.

    """
    with TemporaryDirectory() as scratch:
        path = Path(scratch) / "s.sh"
        path.write_text(script("tmp=$(mktemp)", *body), encoding="utf-8")
        # ruff: ignore[subprocess-without-shell-equals-true]
        done = subprocess.run(
            [str(BASH), str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        return done.returncode


@unittest.skipUnless(IS_32, "/bin/bash is not 3.2; the defect is 3.2's (macOS)")
class Pattern(unittest.TestCase):
    """The defect, then the guard, on the shell that has the defect."""

    def test_bare_trap_reproduces_the_defect(self) -> None:
        """The issue's four rows, as measured on 3.2.57."""
        rows = [
            ((BARE, 'echo "${nope}"'), 0),
            ((BARE, ': "${nope:?unset}"'), 0),
            ((BARE, "false"), 1),
            (('echo "${nope}"',), 1),
        ]
        for body, code in rows:
            with self.subTest(body=body):
                self.assertEqual(run_bash(*body), code)

    def test_guard_fails_the_unfinished_run(self) -> None:
        """Both unset-variable deaths exit 1 under the guard."""
        for death in ('echo "${nope}"', ': "${nope:?unset}"'):
            with self.subTest(death=death):
                self.assertEqual(run_bash(GUARDED, death, "org_done=1"), 1)

    def test_guard_passes_the_finished_run(self) -> None:
        """A run that reaches the mark exits 0."""
        self.assertEqual(run_bash(GUARDED, "echo ok", "org_done=1"), 0)

    def test_guard_keeps_an_explicit_code(self) -> None:
        """A non-zero exit, explicit or from errexit, keeps its code."""
        self.assertEqual(run_bash(GUARDED, "exit 3", "org_done=1"), 3)
        self.assertEqual(run_bash(GUARDED, "false", "org_done=1"), 1)

    def test_early_exit_zero_needs_the_mark(self) -> None:
        """`exit 0` reads as a death unless the mark is set first."""
        self.assertEqual(run_bash(GUARDED, "exit 0", "org_done=1"), 1)
        self.assertEqual(run_bash(GUARDED, "org_done=1", "exit 0", "org_done=1"), 0)

    def test_cleanup_still_runs(self) -> None:
        """The guard wraps the cleanup; it does not replace it."""
        with TemporaryDirectory() as scratch:
            left = Path(scratch) / "left"
            status = run_bash(
                f'tmp="{left}"',
                'touch "${tmp}"',
                GUARDED,
                'echo "${nope}"',
                "org_done=1",
            )
            self.assertEqual(status, 1)
            self.assertFalse(left.exists())


class Shape(unittest.TestCase):
    """The printed shape is the one the rule accepts."""

    def test_guard_matches_its_own_rule(self) -> None:
        """GUARD and GUARD_RE cannot drift apart."""
        self.assertRegex(GUARDED, et.GUARD_RE)
        self.assertIsNone(re.match(et.GUARD_RE, BARE))


if __name__ == "__main__":
    unittest.main()
