#!/usr/bin/env python3
r"""Table tests for lint:bash-portability and its helper (#1911).

Each row plants workflows, shell files and mise configs and asserts
the exit status and every line of the bash 4+ message: each forbidden
form, the near misses that are not one, comment lines (dropped before
numbering), several hits in one file and across files, a named path
that is not a regular file, the `printf %b` expansion the message went
through (escapes, `\c`, a NUL, an unknown escape), an unguarded EXIT
trap (mise/exit_trap.py's finding, red alone or beside the syntax), a
file that is not UTF-8, and the usage error. Four rows run the task's
own body, read from mise/config.toml: the skip, untracked files (never
read), a clean tree counted, and hits end to end; the rest call the
helper's `main` in this process.

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

import bash_portability
import decoded
import hermetic

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:bash-portability"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


BELT = Path(__file__).resolve().parent
GIT = hermetic.tool("git")
HEAD = f"{TASK}: bash 4+ syntax found (macOS ships 3.2):"
TAIL = [
    "  these pass in CI and fail locally, which is how a script reaches",
    "  a release untested. Use tr, case, or an explicit loop instead.",
]
SKIPPED = ["lint:bash-portability: nothing to check, skipped"]
SCRIPT = "#!/usr/bin/env bash\nset -eu\n"
TRAP = "trap 'rm -f x' EXIT\n"


def same[T](case: unittest.TestCase, got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    case.assertEqual(got, want, msg or None)


def body() -> str:
    """Read lint:bash-portability's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


def red(*lines: str) -> list[str]:
    """Build the bash 4+ message with some hit lines.

    Each line is given as the shell built it (a file's first hit as
    `  <file>: <n>:<line>`, its later ones as `<n>:<line>`); the writer
    indents each two more.

    Returns:
        Its lines.

    """
    return [HEAD, *(f"  {line}" for line in lines), *TAIL]


def clean(count: int) -> str:
    """Build the clean line for some files.

    Returns:
        The line.

    """
    return f"lint:bash-portability: {count} files clean"


class Planted(unittest.TestCase):
    """A planted git tree."""

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

    def helper(self, *argv: str) -> hermetic.Called:
        """Call the helper's main in the planted tree.

        Returns:
            The status and what it printed.

        """
        args = list(argv)
        return hermetic.call_main(
            lambda: bash_portability.main(args), env=self.env, cwd=self.tree
        )

    def script(self, text: str) -> hermetic.Called:
        """Plant one shell file and check it alone.

        Returns:
            The status and what it printed.

        """
        rel = self.plant("a.sh", text)
        return self.helper("--", rel, "--")

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
    """The task's own body: its three selections and the guard."""

    def test_nothing_to_check_is_skipped(self) -> None:
        """No workflow, shell file or mise config: skipped, by name."""
        self.plant("README.md", "hi\n")
        done = self.task()
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), SKIPPED)

    def test_untracked_files_are_never_read(self) -> None:
        """Offending files git does not track: still a skip."""
        self.plant("a.sh", SCRIPT + "mapfile -t x\n", track=False)
        self.plant(".github/workflows/ci.yml", "x: ${a,,}\n", track=False)
        done = self.task()
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), SKIPPED)

    def test_a_clean_tree_is_counted(self) -> None:
        """Workflows and shell files counted, mise configs read for traps only."""
        self.plant(".github/workflows/ci.yml", "jobs: {}\n")
        self.plant("a.sh", SCRIPT + "echo ok\n")
        self.plant("mise.toml", "[tasks.a]\nrun = 'true'\n")
        done = self.task()
        same(self, done.returncode, 0, done.stderr)
        expected = [
            "exit-trap: 2 script(s) and task body(ies), every EXIT trap guarded",
            clean(2),
        ]
        same(self, done.stdout.splitlines(), expected)

    def test_hits_through_the_task(self) -> None:
        """A workflow's hit and a shell file's, workflows first."""
        self.plant("b.sh", SCRIPT + "declare -A m\n")
        self.plant(".github/workflows/ci.yml", "run: echo ${x^^}\n")
        done = self.task()
        same(self, done.returncode, bash_portability.EXIT_RED)
        expected = red(
            "  .github/workflows/ci.yml: 1:run: echo ${x^^}",
            "  b.sh: 2:declare -A m",
        )
        same(self, done.stderr.splitlines(), expected)


class Syntax(Planted):
    """The forms the lint names, and the ones it does not."""

    def test_each_form_is_named(self) -> None:
        """Every bash 4 form, on its own line."""
        for line in (
            "echo ${v,,}",
            "echo ${v^^}",
            "echo ${v,}",
            "echo ${v^}",
            "echo ${a[0],,}",
            "echo ${a[@]^}",
            "declare -A m",
            "typeset  -gA m",
            "local x; mapfile -t x < f",
            "readarray\tx",
            "cmd &>> log",
        ):
            with self.subTest(line=line):
                done = self.script(SCRIPT + line + "\n")
                same(self, done.returncode, bash_portability.EXIT_RED)
                same(self, done.stderr.splitlines(), red(f"  a.sh: 2:{line}"))

    def test_near_misses_are_clean(self) -> None:
        """Bash 3 forms that look alike: not named."""
        text = SCRIPT + "".join(
            f"{line}\n"
            for line in (
                "echo ${v:-a,b}",
                "echo ${1,,}",
                "declare -a list",
                "declare -r A=1",
                "my_mapfile x",
                "echo mapfile",
                "cmd &> log",
                "cmd >> log 2>&1",
            )
        )
        done = self.script(text)
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines()[-1], clean(1))

    def test_comments_are_dropped_before_numbering(self) -> None:
        """A comment naming a form is no hit, and numbers skip it."""
        text = SCRIPT + "# never ${v,,}\n  # nor mapfile x\n\necho ${v^}\n"
        done = self.script(text)
        same(self, done.returncode, bash_portability.EXIT_RED)
        same(self, done.stderr.splitlines(), red("  a.sh: 3:echo ${v^}"))

    def test_hits_in_one_file_and_across_files(self) -> None:
        """A file's first hit carries its name; its others do not."""
        one = self.plant(".github/workflows/a.yml", "a: ${x,}\nb: ok\nc: ${y^}\n")
        two = self.plant("b.sh", SCRIPT + "mapfile x\n")
        done = self.helper(one, "--", two, "--")
        same(self, done.returncode, bash_portability.EXIT_RED)
        expected = red(f"  {one}: 1:a: ${{x,}}", "3:c: ${y^}", f"  {two}: 2:mapfile x")
        same(self, done.stderr.splitlines(), expected)

    def test_a_path_that_is_not_a_file_is_counted_unread(self) -> None:
        """A named directory or a missing path: counted, never read."""
        (self.tree / "d.yml").mkdir()
        done = self.helper(".github/workflows/gone.yml", "d.yml", "--", "--")
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines()[-1], clean(2))


class Expansion(Planted):
    """The message went through `printf %b`, and a newline-split read."""

    def test_escapes_are_expanded(self) -> None:
        """Bash's escapes in a hit line expand; an unknown one stays."""
        for line, shown in (
            ("echo ${v,,} '\\t'", ["  a.sh: 2:echo ${v,,} '\t'"]),
            ("printf '\\101\\x42\\0103' ${v,,}", ["  a.sh: 2:printf 'ABC' ${v,,}"]),
            ("echo ${v,,} \\q \\\\", ["  a.sh: 2:echo ${v,,} \\q \\"]),
            ("echo ${v,,} 'a\\nb'", ["  a.sh: 2:echo ${v,,} 'a", "b'"]),
            ("echo ${v,,} \\n\\n x", ["  a.sh: 2:echo ${v,,} ", " x"]),
            ("echo ${v,,} \\u00e9 \\x4g", ["  a.sh: 2:echo ${v,,} é \x04g"]),
            ("echo ${v,,} \\", ["  a.sh: 2:echo ${v,,} \\"]),
        ):
            with self.subTest(line=line):
                done = self.script(SCRIPT + line + "\n")
                same(self, done.returncode, bash_portability.EXIT_RED)
                same(self, done.stderr.splitlines(), red(*shown))

    def test_backslash_c_and_nul_end_the_text(self) -> None:
        r"""`\c` stops printf, a NUL stops the read: later hits are dropped."""
        for stop in ("\\c", "\\0"):
            with self.subTest(stop=stop):
                one = self.plant("a.sh", SCRIPT + f"echo ${{v,,}} x{stop}y\n")
                two = self.plant("b.sh", SCRIPT + "mapfile x\n")
                done = self.helper("--", one, two, "--")
                same(self, done.returncode, bash_portability.EXIT_RED)
                expected = red("  a.sh: 2:echo ${v,,} x")
                same(self, done.stderr.splitlines(), expected)

    def test_the_expansion_by_itself(self) -> None:
        r"""Octal past a byte wraps, a big \U stays, \e is escape."""
        same(self, bash_portability.expand("\\777\\e\\E"), "\udcff\x1b\x1b")
        same(self, bash_portability.expand("\\UFFFFFFFF"), "\\UFFFFFFFF")
        same(self, bash_portability.expand("\\u"), "\\u")
        same(self, bash_portability.expand("\\a\\b\\f\\r\\v"), "\a\b\f\r\v")


class Traps(Planted):
    """The EXIT trap half, mise/exit_trap.py's own."""

    def test_an_unguarded_trap_is_red_alone(self) -> None:
        """No bash 4 syntax, an unguarded trap: red, the trap's finding only."""
        done = self.script(SCRIPT + TRAP)
        same(self, done.returncode, bash_portability.EXIT_RED)
        same(self, done.stdout, "")
        self.assertNotIn(HEAD, done.stderr)
        self.assertIn(f"{TASK}: a.sh", done.stderr)

    def test_both_halves_red(self) -> None:
        """An unguarded trap and bash 4 syntax: both findings, the trap's first."""
        done = self.script(SCRIPT + TRAP + "mapfile x\n")
        same(self, done.returncode, bash_portability.EXIT_RED)
        head, _, rest = done.stderr.partition(HEAD + "\n")
        self.assertIn(f"{TASK}: a.sh", head)
        same(self, [HEAD, *rest.splitlines()], red("  a.sh: 3:mapfile x"))


class Calls(Planted):
    """Reading the files, and the call itself."""

    def test_a_workflow_not_utf8_is_judged_on_its_bytes(self) -> None:
        """A workflow that is not UTF-8 is read as grep reads it."""
        rel = self.plant("a.yml", b"x: \xff\nmapfile x\n")
        done = self.helper(rel, "--", "--")
        same(self, done.returncode, bash_portability.EXIT_RED)
        same(self, done.stderr.splitlines(), red(f"  {rel}: 2:mapfile x"))

    def test_a_script_exit_trap_cannot_read_is_an_error(self) -> None:
        """A shell file or config not UTF-8, or a directory: exit 2."""
        rel = self.plant("a.sh", b"#!/bin/bash\n\xff\n")
        conf = self.plant("mise.toml", b"[tasks.a]\nrun = '\xff'\n")
        (self.tree / "d.sh").mkdir()
        for name, said in (
            (rel, f"{TASK}: {rel}: cannot be read as UTF-8:"),
            (conf, f"{TASK}: {conf}: cannot be read as UTF-8:"),
            ("d.sh", f"{TASK}: mise/exit_trap.py cannot read a file:"),
        ):
            with self.subTest(name=name):
                lists = ("--", name, "--") if name != conf else ("--", "--", name)
                done = self.helper(*lists)
                same(self, done.returncode, bash_portability.EXIT_ERROR)
                self.assertTrue(done.stderr.startswith(said), done.stderr)

    def test_a_bad_call_is_a_usage_error(self) -> None:
        """Not two separators: exit 2 with the usage line."""
        usage = f"{TASK}: {bash_portability.USAGE}"
        for argv in ([], ["a.sh"], ["--"], ["--", "--", "--"]):
            with self.subTest(argv=argv):
                done = self.helper(*argv)
                same(self, done.returncode, bash_portability.EXIT_ERROR)
                same(self, done.stderr.splitlines(), [usage])


if __name__ == "__main__":
    unittest.main()
