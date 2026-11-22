#!/usr/bin/env python3
"""The two writers of findings (#1890).

Rows for `mise/findings.sh` and `mise/findings.py`, each run as a task
runs it: a script that sources or imports the writer, in a child
process. The shell rows run under the belt's bash and under
`/bin/bash` (3.2 on macOS), with `set -euo pipefail` as every task
body has.

For each writer: a finding with a subject, one without, one with two
detail lines, and one whose message holds a quote, a backslash, a tab
and a non-ASCII character print the belt's form and append a record
that `json.loads` reads back to the same values; with `ORG_FINDINGS`
unset nothing is written; with it naming a path under a file the
finding is still printed, one more line names the failure, and the
status is 0; the annotated entry prints `::error::` with the runner's
escaping; `task` is `MISE_TASK_NAME` when set; the job when the script is a
workflow step's own (directly under `RUNNER_TEMP`); the script's file
name otherwise. A character UTF-8 cannot carry, a lone surrogate in the
message or the subject, is printed and recorded as its backslash
escape, and the status is 0 (#1984).

stdlib `unittest`, matching the rest of mise/.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic

MISE = Path(__file__).resolve().parent
SHELL_WRITER = MISE / "findings.sh"
SYSTEM_BASH = Path("/bin/bash")

SHELL_REPORTER = f"""set -euo pipefail
. "{SHELL_WRITER}"
entry=$1
shift
if [[ $entry == annotated ]]; then
  org_finding_annotated "$@"
else
  org_finding "$@"
fi
"""

PYTHON_REPORTER = f"""import sys
sys.path.insert(0, {str(MISE)!r})
import findings
findings.finding(
    sys.argv[2], sys.argv[3], sys.argv[4:], annotate=sys.argv[1] == "annotated"
)
"""

AWKWARD = 'say "hi" \\ there\tnaïve ✓'


class Writer:
    """One writer, run the way a task runs it."""

    def __init__(
        self, name: str, interpreter: list[str], script: str, suffix: str
    ) -> None:
        """Hold the writer's interpreter and its reporter script."""
        self.name = name
        self.interpreter = interpreter
        self.script = script
        self.suffix = suffix

    def run(
        self,
        root: Path,
        entry: str,
        *args: str,
        **env: str,
    ) -> subprocess.CompletedProcess[str]:
        """Run the reporter once.

        Returns:
            The finished process.

        """
        reporter = root / f"reporter{self.suffix}"
        reporter.write_text(self.script, encoding="utf-8")
        overrides = {"PYTHONUTF8": "1", **env}
        child = hermetic.env(**overrides)
        for name in ("MISE_TASK_NAME", "ORG_FINDINGS"):
            if name not in env:
                child.pop(name, None)
        return subprocess.run(
            [*self.interpreter, str(reporter), entry, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=child,
            check=False,
            cwd=root,
        )


def writers() -> list[Writer]:
    """List every writer the rows hold: Python, the belt's bash, /bin/bash.

    Returns:
        The writers.

    """
    out = [
        Writer("python", [sys.executable], PYTHON_REPORTER, ".py"),
        Writer("belt bash", [hermetic.tool("bash")], SHELL_REPORTER, ".sh"),
    ]
    if SYSTEM_BASH.exists():
        out.append(Writer("/bin/bash", [str(SYSTEM_BASH)], SHELL_REPORTER, ".sh"))
    return out


class FindingRows(unittest.TestCase):
    """What each writer prints and records."""

    @override
    def setUp(self) -> None:
        """Give each row a directory of its own."""
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.file = self.root / "out" / "job.jsonl"

    def records(self) -> list[dict[str, object]]:
        """Read back every record the row appended.

        Returns:
            The records, in order.

        """
        text = self.file.read_text(encoding="utf-8")
        return [
            decoded.as_object(decoded.load_json(line), "a record")
            for line in text.splitlines()
        ]

    def found(
        self,
        writer: Writer,
        subject: str,
        message: str,
        detail: list[str],
        printed: str,
    ) -> None:
        """Run one finding and hold its printed form and its record."""
        done = writer.run(
            self.root,
            "plain",
            subject,
            message,
            *detail,
            MISE_TASK_NAME="lint:demo",
            ORG_FINDINGS=str(self.file),
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "")
        self.assertEqual(done.stderr, printed)
        expected: list[dict[str, object]] = [
            {
                "v": 1,
                "task": "lint:demo",
                "subject": subject,
                "message": message,
                "detail": detail,
            }
        ]
        self.assertEqual(self.records(), expected)
        self.assertEqual(self.file.read_text(encoding="utf-8").count("\n"), 1)

    def test_a_finding_with_a_subject(self) -> None:
        """The form is `<task>: <subject>: <message>`."""
        for writer in writers():
            with self.subTest(writer=writer.name):
                self.file.unlink(missing_ok=True)
                self.found(
                    writer,
                    "Cargo.toml",
                    "a pin is loose",
                    [],
                    "lint:demo: Cargo.toml: a pin is loose\n",
                )

    def test_a_finding_without_a_subject(self) -> None:
        """An empty subject leaves `<task>: <message>`, and is recorded empty."""
        for writer in writers():
            with self.subTest(writer=writer.name):
                self.file.unlink(missing_ok=True)
                self.found(
                    writer,
                    "",
                    "nothing is tracked",
                    [],
                    "lint:demo: nothing is tracked\n",
                )

    def test_two_detail_lines_are_indented_two_spaces(self) -> None:
        """Each detail line follows the first, indented two spaces."""
        for writer in writers():
            with self.subTest(writer=writer.name):
                self.file.unlink(missing_ok=True)
                self.found(
                    writer,
                    "chrono@0.4.45",
                    "RUSTSEC-2026-0543 is undecided",
                    ["reached through object_store 0.14.2", "and through arrow 57.0.0"],
                    "lint:demo: chrono@0.4.45: RUSTSEC-2026-0543 is undecided\n"
                    "  reached through object_store 0.14.2\n"
                    "  and through arrow 57.0.0\n",
                )

    def test_a_quote_a_backslash_a_tab_and_non_ascii_survive(self) -> None:
        """The record reads back to the same characters the call was given."""
        for writer in writers():
            with self.subTest(writer=writer.name):
                self.file.unlink(missing_ok=True)
                self.found(
                    writer,
                    "a\tb",
                    AWKWARD,
                    [AWKWARD],
                    f"lint:demo: a\tb: {AWKWARD}\n  {AWKWARD}\n",
                )

    def test_unset_records_nothing(self) -> None:
        """Without ORG_FINDINGS the finding is printed and nothing is written."""
        for writer in writers():
            with self.subTest(writer=writer.name):
                done = writer.run(
                    self.root, "plain", "x", "y", MISE_TASK_NAME="lint:demo"
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stderr, "lint:demo: x: y\n")
                self.assertFalse(any(self.root.rglob("*.jsonl")))

    def test_empty_records_nothing(self) -> None:
        """An empty ORG_FINDINGS is unset."""
        for writer in writers():
            with self.subTest(writer=writer.name):
                done = writer.run(
                    self.root,
                    "plain",
                    "x",
                    "y",
                    MISE_TASK_NAME="lint:demo",
                    ORG_FINDINGS="",
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stderr, "lint:demo: x: y\n")
                self.assertFalse(any(self.root.rglob("*.jsonl")))

    def test_a_failed_append_is_said_and_is_not_fatal(self) -> None:
        """A path under a file: printed, one more line names it, status 0."""
        blocker = self.root / "a-file"
        blocker.write_text("", encoding="utf-8")
        target = blocker / "out" / "job.jsonl"
        for writer in writers():
            with self.subTest(writer=writer.name):
                done = writer.run(
                    self.root,
                    "plain",
                    "x",
                    "y",
                    "z",
                    MISE_TASK_NAME="lint:demo",
                    ORG_FINDINGS=str(target),
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                lines = done.stderr.splitlines()
                expected: list[str] = ["lint:demo: x: y", "  z"]
                self.assertEqual(lines[:2], expected)
                self.assertEqual(len(lines), 3, done.stderr)
                self.assertTrue(
                    lines[2].startswith(
                        f"lint:demo: the finding was not recorded in {target}: "
                    ),
                    lines[2],
                )
                self.assertFalse(target.exists())

    def test_the_annotated_entry_escapes_as_the_runner_reads(self) -> None:
        """`::error::`, with `%`, CR and LF escaped; the record is the same."""
        message = "50% done\nnext"
        for writer in writers():
            with self.subTest(writer=writer.name):
                self.file.unlink(missing_ok=True)
                done = writer.run(
                    self.root,
                    "annotated",
                    "s",
                    message,
                    "one detail",
                    MISE_TASK_NAME="lint:demo",
                    ORG_FINDINGS=str(self.file),
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(
                    done.stderr,
                    "::error::lint:demo: s: 50%25 done%0Anext\n  one detail\n",
                )
                self.assertEqual(self.records()[0]["message"], message)

    def test_a_plain_line_cannot_open_a_command(self) -> None:
        """A message of a newline and a command prints one line, the command escaped."""
        for writer in writers():
            with self.subTest(writer=writer.name):
                done = writer.run(
                    self.root, "plain", "", "x\n::error::forged", "::warning::w"
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                lines = done.stderr.splitlines()
                self.assertEqual(len(lines), 2, done.stderr)
                self.assertTrue(lines[0].endswith(": x\\n::error::forged"), lines[0])
                self.assertEqual(lines[1], "  \\::warning::w")

    def test_the_task_is_mise_s_name_when_set(self) -> None:
        """MISE_TASK_NAME names the task in the line and the record."""
        for writer in writers():
            with self.subTest(writer=writer.name):
                self.file.unlink(missing_ok=True)
                done = writer.run(
                    self.root,
                    "plain",
                    "s",
                    "m",
                    MISE_TASK_NAME="audit:deny",
                    ORG_FINDINGS=str(self.file),
                )
                self.assertEqual(done.stderr, "audit:deny: s: m\n")
                self.assertEqual(self.records()[0]["task"], "audit:deny")

    def test_the_task_is_the_script_s_name_when_unset(self) -> None:
        """Where mise named no task, the running script's file name does."""
        for writer in writers():
            with self.subTest(writer=writer.name):
                self.file.unlink(missing_ok=True)
                name = f"reporter{writer.suffix}"
                done = writer.run(
                    self.root, "plain", "s", "m", ORG_FINDINGS=str(self.file)
                )
                self.assertEqual(done.stderr, f"{name}: s: m\n")
                self.assertEqual(self.records()[0]["task"], name)

    def test_a_step_script_s_finding_names_the_job(self) -> None:
        """The runner's step script, directly under RUNNER_TEMP: GITHUB_JOB."""
        for writer in writers():
            with self.subTest(writer=writer.name):
                self.file.unlink(missing_ok=True)
                done = writer.run(
                    self.root,
                    "plain",
                    "s",
                    "m",
                    ORG_FINDINGS=str(self.file),
                    RUNNER_TEMP=str(self.root),
                    GITHUB_JOB="attach",
                )
                self.assertEqual(done.stderr, "attach: s: m\n")
                self.assertEqual(self.records()[0]["task"], "attach")

    def test_a_script_elsewhere_keeps_its_name_in_a_job(self) -> None:
        """Not directly under RUNNER_TEMP, or no job: the script's name."""
        cases = {
            "elsewhere": {"RUNNER_TEMP": "/elsewhere", "GITHUB_JOB": "attach"},
            "below it": {"RUNNER_TEMP": "/", "GITHUB_JOB": "attach"},
            "no job": {"RUNNER_TEMP": "", "GITHUB_JOB": ""},
        }
        for writer in writers():
            name = f"reporter{writer.suffix}"
            for case, env in cases.items():
                with self.subTest(writer=writer.name, case=case):
                    self.file.unlink(missing_ok=True)
                    planted = dict(env)
                    if case == "no job":
                        planted["RUNNER_TEMP"] = str(self.root)
                    done = writer.run(
                        self.root,
                        "plain",
                        "s",
                        "m",
                        ORG_FINDINGS=str(self.file),
                        **planted,
                    )
                    self.assertEqual(done.stderr, f"{name}: s: m\n")
                    self.assertEqual(self.records()[0]["task"], name)

    def test_findings_append(self) -> None:
        """A second finding is a second line of the same file."""
        for writer in writers():
            with self.subTest(writer=writer.name):
                self.file.unlink(missing_ok=True)
                for message in ("one", "two"):
                    writer.run(
                        self.root,
                        "plain",
                        "",
                        message,
                        MISE_TASK_NAME="t",
                        ORG_FINDINGS=str(self.file),
                    )
                messages: list[object] = [r["message"] for r in self.records()]
                expected: list[object] = ["one", "two"]
                self.assertEqual(messages, expected)


def bashes() -> list[tuple[str, str]]:
    """List the shell writer's bashes: the belt's, and /bin/bash.

    Returns:
        Each bash's label and path.

    """
    out = [("belt bash", hermetic.tool("bash"))]
    if SYSTEM_BASH.exists():
        out.append(("/bin/bash", str(SYSTEM_BASH)))
    return out


class LoadingRows(unittest.TestCase):
    """Sourcing the shell writer runs no command (#1934)."""

    def test_an_empty_path_still_loads_the_writer(self) -> None:
        """PATH empty: no output, status 0, org_finding defined."""
        for label, bash in bashes():
            for how, source, cwd in (
                ("absolute", str(SHELL_WRITER), None),
                ("no slash", SHELL_WRITER.name, SHELL_WRITER.parent),
            ):
                with self.subTest(bash=label, path=how):
                    done = subprocess.run(
                        [
                            bash,
                            "--norc",
                            "-euo",
                            "pipefail",
                            "-c",
                            f'. "{source}"; declare -F org_finding > /dev/null',
                        ],
                        capture_output=True,
                        text=True,
                        env=hermetic.env(PATH=""),
                        cwd=cwd,
                        check=False,
                    )
                    self.assertEqual(
                        (done.returncode, done.stdout, done.stderr), (0, "", "")
                    )


LINES_READER = f""". "{SHELL_WRITER}"
org_finding_lines
"""
US = "\037"
NO_RECORDS: list[dict[str, object]] = []


class LinesRows(unittest.TestCase):
    """org_finding_lines: one finding per stdin line, unit-separated (#1934)."""

    @override
    def setUp(self) -> None:
        """Give each row a directory of its own."""
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.file = Path(self.dir.name) / "out" / "job.jsonl"

    def feed(self, bash: str, text: str) -> subprocess.CompletedProcess[str]:
        """Pipe `text` into org_finding_lines under one bash.

        Returns:
            The finished process.

        """
        self.file.unlink(missing_ok=True)
        return subprocess.run(
            [bash, "-euo", "pipefail", "-c", LINES_READER],
            input=text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=hermetic.env(
                MISE_TASK_NAME="lint:demo", ORG_FINDINGS=str(self.file), PYTHONUTF8="1"
            ),
            check=False,
        )

    def records(self) -> list[dict[str, object]]:
        """Read back every record the row appended.

        Returns:
            The records, in order; none when no file was written.

        """
        if not self.file.exists():
            return NO_RECORDS
        return [
            decoded.as_object(decoded.load_json(line), "a record")
            for line in self.file.read_text(encoding="utf-8").splitlines()
        ]

    def test_each_line_is_one_finding(self) -> None:
        """Empty subject, detail, a tab in the message: printed and recorded."""
        text = f"{US}no subject\nf.yml{US}is wrong{US}first{US}second\ns{US}a\tb\n"
        printed = (
            "lint:demo: no subject\n"
            "lint:demo: f.yml: is wrong\n  first\n  second\n"
            "lint:demo: s: a\tb\n"
        )
        none: list[str] = []
        expected: list[dict[str, object]] = [
            {
                "v": 1,
                "task": "lint:demo",
                "subject": "",
                "message": "no subject",
                "detail": none,
            },
            {
                "v": 1,
                "task": "lint:demo",
                "subject": "f.yml",
                "message": "is wrong",
                "detail": ["first", "second"],
            },
            {
                "v": 1,
                "task": "lint:demo",
                "subject": "s",
                "message": "a\tb",
                "detail": none,
            },
        ]
        for label, bash in bashes():
            with self.subTest(bash=label):
                done = self.feed(bash, text)
                self.assertEqual((done.returncode, done.stdout), (0, ""))
                self.assertEqual(done.stderr, printed)
                self.assertEqual(self.records(), expected)

    def test_a_line_with_no_separator_is_passed_through(self) -> None:
        """A program's own summary goes to stdout and records nothing."""
        for label, bash in bashes():
            with self.subTest(bash=label):
                done = self.feed(bash, "all clean\n")
                self.assertEqual(
                    (done.returncode, done.stdout, done.stderr), (0, "all clean\n", "")
                )
                self.assertEqual(self.records(), NO_RECORDS)

    def test_a_last_line_without_a_newline_is_read(self) -> None:
        """The final finding is not lost for want of a newline."""
        for label, bash in bashes():
            with self.subTest(bash=label):
                done = self.feed(bash, f"{US}last")
                self.assertEqual(done.stderr, "lint:demo: last\n")


UNENCODABLE = f"""import os
import sys
sys.path.insert(0, {str(MISE)!r})
import findings
findings.finding(os.fsdecode(b"bad\\xffname"), "x\\ud800y")
"""


class UnencodableRows(unittest.TestCase):
    """A lone surrogate is recorded as its backslash escape (#1984)."""

    def test_a_lone_surrogate_is_recorded_as_printed(self) -> None:
        """Exit 0, one record json.loads reads, the stderr line the same text."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / "out" / "job.jsonl"
            reporter = root / "reporter.py"
            reporter.write_text(UNENCODABLE, encoding="utf-8")
            done = subprocess.run(
                [sys.executable, str(reporter)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=hermetic.env(
                    MISE_TASK_NAME="t", ORG_FINDINGS=str(file), PYTHONUTF8="1"
                ),
                check=False,
                cwd=root,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            lines = file.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            got = decoded.as_object(decoded.load_json(lines[0]), "a record")
            self.assertEqual(got["message"], "x\\ud800y")
            self.assertEqual(got["subject"], "bad\\udcffname")
            self.assertEqual(done.stderr, "t: bad\\udcffname: x\\ud800y\n")


if __name__ == "__main__":
    unittest.main()
