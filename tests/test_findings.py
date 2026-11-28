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
unset off a runner, or empty, nothing is written; unset on a runner
(`RUNNER_TEMP` and `GITHUB_JOB` set) the record goes to
`${RUNNER_TEMP}/findings/<job>-<16 hex>.jsonl`, and two findings of one
process to one file (#1976); with it naming a path under a file the
finding is still printed, one more line names the failure, and the
status is 0; the annotated entry prints `::error::` with the runner's
escaping; `task` is `MISE_TASK_NAME` when set; the job when the script is a
workflow step's own (directly under `RUNNER_TEMP`); the script's file
name otherwise. A character UTF-8 cannot carry, a lone surrogate in the
message or the subject, is printed and recorded as its backslash
escape, and the status is 0 (#1984).
"""

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from belt import decoded, findings
from belt.typed_pytest import fixture
from tests import support

MISE = support.MISE
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
if [[ $entry == twice ]]; then
  org_finding "$@"
fi
"""

JOB_FILE = re.compile(r"^mutants-rust-[0-9a-f]{16}\.jsonl$")

AWKWARD = 'say "hi" \\ there\tnaïve ✓'


@dataclass(frozen=True)
class Ran:
    """What one writer's run answered."""

    returncode: int
    stdout: str
    stderr: str


class Writer:
    """One writer, run the way a task runs it.

    The Python writer is belt Python, so it is called in this process
    with the environment a task would give it; the shell writers are
    started as the task starts them.
    """

    def __init__(
        self,
        name: str,
        interpreter: list[str] | None,
        script: str,
        suffix: str,
        *,
        env: support.Env,
    ) -> None:
        """Hold the writer's interpreter and its reporter script."""
        self.make_env = env
        self.name = name
        self.interpreter = interpreter
        self.script = script
        self.suffix = suffix

    def run(
        self,
        root: Path,
        entry: str,
        *args: str,
        capfd: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        **env: str,
    ) -> Ran:
        """Run the reporter once.

        Returns:
            Its status and both streams.

        """
        reporter = root / f"reporter{self.suffix}"
        if self.interpreter is None:
            return self.called(reporter, entry, args, env, capfd, monkeypatch)
        reporter.write_text(self.script, encoding="utf-8")
        overrides = {"PYTHONUTF8": "1", **env}
        child = self.make_env(**overrides)
        for name in ("MISE_TASK_NAME", "ORG_FINDINGS"):
            if name not in env:
                child.pop(name, None)
        done = subprocess.run(
            [*self.interpreter, str(reporter), entry, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=child,
            check=False,
            cwd=root,
        )
        return Ran(done.returncode, done.stdout, done.stderr)

    @staticmethod
    def called(
        reporter: Path,
        entry: str,
        args: tuple[str, ...],
        env: dict[str, str],
        capfd: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> Ran:
        """Call the Python writer as the shell reporter calls its own.

        Returns:
            Its status and both streams.

        """
        subject, message, *detail = args
        for name in ("MISE_TASK_NAME", "ORG_FINDINGS"):
            if name not in env:
                monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(reporter.parent)
        # The running program's name, which a finding falls back to.
        monkeypatch.setattr(sys, "argv", [str(reporter)])
        # Each call is one process: its records file is drawn afresh.
        monkeypatch.setattr(findings, "_drawn", [])
        findings.finding(subject, message, detail, annotate=entry == "annotated")
        if entry == "twice":
            findings.finding(subject, message, detail)
        out, err = capfd.readouterr()
        return Ran(0, out, err)


def writers() -> list[Writer]:
    """List every writer the rows hold: Python, the belt's bash, /bin/bash.

    Returns:
        The writers.

    """
    out = [
        Writer("python", None, "", ".py", env=support.environment),
        Writer(
            "belt bash",
            [support.tool("bash")],
            SHELL_REPORTER,
            ".sh",
            env=support.environment,
        ),
    ]
    if SYSTEM_BASH.exists():
        out.append(
            Writer(
                "/bin/bash",
                [str(SYSTEM_BASH)],
                SHELL_REPORTER,
                ".sh",
                env=support.environment,
            )
        )
    return out


class FindingRows:
    """What each writer prints and records."""

    def __init__(self, tmp_path: Path) -> None:
        """Give each row a directory of its own."""
        self.root = Path(str(tmp_path))
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
        *,
        capfd: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
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
            capfd=capfd,
            monkeypatch=monkeypatch,
        )
        assert done.returncode == 0, done.stderr
        assert done.stdout == ""
        assert done.stderr == printed
        expected: list[dict[str, object]] = [
            {
                "v": 1,
                "task": "lint:demo",
                "subject": subject,
                "message": message,
                "detail": detail,
            }
        ]
        assert self.records() == expected
        assert self.file.read_text(encoding="utf-8").count("\n") == 1


@fixture
def finding_rows(tmp_path: Path) -> FindingRows:
    """Give each row its own FindingRows."""
    return FindingRows(tmp_path)


@pytest.mark.parametrize("writer", writers())
def test_a_finding_with_a_subject(
    finding_rows: FindingRows,
    writer: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The form is `<task>: <subject>: <message>`."""
    finding_rows.file.unlink(missing_ok=True)
    finding_rows.found(
        writer,
        "Cargo.toml",
        "a pin is loose",
        [],
        "lint:demo: Cargo.toml: a pin is loose\n",
        capfd=capfd,
        monkeypatch=monkeypatch,
    )


@pytest.mark.parametrize("writer", writers())
def test_a_finding_without_a_subject(
    finding_rows: FindingRows,
    writer: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty subject leaves `<task>: <message>`, and is recorded empty."""
    finding_rows.file.unlink(missing_ok=True)
    finding_rows.found(
        writer,
        "",
        "nothing is tracked",
        [],
        "lint:demo: nothing is tracked\n",
        capfd=capfd,
        monkeypatch=monkeypatch,
    )


@pytest.mark.parametrize("writer", writers())
def test_two_detail_lines_are_indented_two_spaces(
    finding_rows: FindingRows,
    writer: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each detail line follows the first, indented two spaces."""
    finding_rows.file.unlink(missing_ok=True)
    finding_rows.found(
        writer,
        "chrono@0.4.45",
        "RUSTSEC-2026-0543 is undecided",
        ["reached through object_store 0.14.2", "and through arrow 57.0.0"],
        "lint:demo: chrono@0.4.45: RUSTSEC-2026-0543 is undecided\n"
        "  reached through object_store 0.14.2\n"
        "  and through arrow 57.0.0\n",
        capfd=capfd,
        monkeypatch=monkeypatch,
    )


@pytest.mark.parametrize("writer", writers())
def test_a_quote_a_backslash_a_tab_and_non_ascii_survive(
    finding_rows: FindingRows,
    writer: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The record reads back to the same characters the call was given."""
    finding_rows.file.unlink(missing_ok=True)
    finding_rows.found(
        writer,
        "a\tb",
        AWKWARD,
        [AWKWARD],
        f"lint:demo: a\tb: {AWKWARD}\n  {AWKWARD}\n",
        capfd=capfd,
        monkeypatch=monkeypatch,
    )


@pytest.mark.parametrize("writer", writers())
def test_unset_records_nothing(
    finding_rows: FindingRows,
    writer: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ORG_FINDINGS the finding is printed and nothing is written."""
    done = writer.run(
        finding_rows.root,
        "plain",
        "x",
        "y",
        MISE_TASK_NAME="lint:demo",
        capfd=capfd,
        monkeypatch=monkeypatch,
    )
    assert done.returncode == 0, done.stderr
    assert done.stderr == "lint:demo: x: y\n"
    assert not any(finding_rows.root.rglob("*.jsonl"))


@pytest.mark.parametrize("writer", writers())
def test_empty_records_nothing(
    finding_rows: FindingRows,
    writer: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty ORG_FINDINGS records nothing, on a runner too."""
    done = writer.run(
        finding_rows.root,
        "plain",
        "x",
        "y",
        MISE_TASK_NAME="lint:demo",
        ORG_FINDINGS="",
        RUNNER_TEMP=str(finding_rows.root / "runner"),
        GITHUB_JOB="mutants-rust",
        capfd=capfd,
        monkeypatch=monkeypatch,
    )
    assert done.returncode == 0, done.stderr
    assert done.stderr == "lint:demo: x: y\n"
    assert not any(finding_rows.root.rglob("*.jsonl"))


@pytest.mark.parametrize("writer", writers())
def test_a_failed_append_is_said_and_is_not_fatal(
    finding_rows: FindingRows,
    writer: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A path under a file: printed, one more line names it, status 0."""
    blocker = finding_rows.root / "a-file"
    blocker.write_text("", encoding="utf-8")
    target = blocker / "out" / "job.jsonl"
    done = writer.run(
        finding_rows.root,
        "plain",
        "x",
        "y",
        "z",
        MISE_TASK_NAME="lint:demo",
        ORG_FINDINGS=str(target),
        capfd=capfd,
        monkeypatch=monkeypatch,
    )
    assert done.returncode == 0, done.stderr
    lines = done.stderr.splitlines()
    expected: list[str] = ["lint:demo: x: y", "  z"]
    assert lines[:2] == expected
    assert len(lines) == 3, done.stderr
    assert lines[2].startswith(
        f"lint:demo: the finding was not recorded in {target}: "
    ), lines[2]
    assert not target.exists()


@pytest.mark.parametrize("writer", writers())
def test_the_annotated_entry_escapes_as_the_runner_reads(
    finding_rows: FindingRows,
    writer: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`::error::`, with `%`, CR and LF escaped; the record is the same."""
    message = "50% done\nnext"
    finding_rows.file.unlink(missing_ok=True)
    done = writer.run(
        finding_rows.root,
        "annotated",
        "s",
        message,
        "one detail",
        MISE_TASK_NAME="lint:demo",
        ORG_FINDINGS=str(finding_rows.file),
        capfd=capfd,
        monkeypatch=monkeypatch,
    )
    assert done.returncode == 0, done.stderr
    assert done.stderr == "::error::lint:demo: s: 50%25 done%0Anext\n  one detail\n"
    assert finding_rows.records()[0]["message"] == message


@pytest.mark.parametrize("writer", writers())
def test_a_plain_line_cannot_open_a_command(
    finding_rows: FindingRows,
    writer: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A message of a newline and a command prints one line, the command escaped."""
    done = writer.run(
        finding_rows.root,
        "plain",
        "",
        "x\n::error::forged",
        "::warning::w",
        capfd=capfd,
        monkeypatch=monkeypatch,
    )
    assert done.returncode == 0, done.stderr
    lines = done.stderr.splitlines()
    assert len(lines) == 2, done.stderr
    assert lines[0].endswith(": x\\n::error::forged"), lines[0]
    assert lines[1] == "  \\::warning::w"


@pytest.mark.parametrize("writer", writers())
def test_the_task_is_mise_s_name_when_set(
    finding_rows: FindingRows,
    writer: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MISE_TASK_NAME names the task in the line and the record."""
    finding_rows.file.unlink(missing_ok=True)
    done = writer.run(
        finding_rows.root,
        "plain",
        "s",
        "m",
        MISE_TASK_NAME="audit:deny",
        ORG_FINDINGS=str(finding_rows.file),
        capfd=capfd,
        monkeypatch=monkeypatch,
    )
    assert done.stderr == "audit:deny: s: m\n"
    assert finding_rows.records()[0]["task"] == "audit:deny"


@pytest.mark.parametrize("writer", writers())
def test_the_task_is_the_script_s_name_when_unset(
    finding_rows: FindingRows,
    writer: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Where mise named no task, the running script's file name does."""
    finding_rows.file.unlink(missing_ok=True)
    name = f"reporter{writer.suffix}"
    done = writer.run(
        finding_rows.root,
        "plain",
        "s",
        "m",
        ORG_FINDINGS=str(finding_rows.file),
        capfd=capfd,
        monkeypatch=monkeypatch,
    )
    assert done.stderr == f"{name}: s: m\n"
    assert finding_rows.records()[0]["task"] == name


@pytest.mark.parametrize("writer", writers())
def test_a_step_script_s_finding_names_the_job(
    finding_rows: FindingRows,
    writer: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner's step script, directly under RUNNER_TEMP: GITHUB_JOB."""
    finding_rows.file.unlink(missing_ok=True)
    done = writer.run(
        finding_rows.root,
        "plain",
        "s",
        "m",
        ORG_FINDINGS=str(finding_rows.file),
        RUNNER_TEMP=str(finding_rows.root),
        GITHUB_JOB="attach",
        capfd=capfd,
        monkeypatch=monkeypatch,
    )
    assert done.stderr == "attach: s: m\n"
    assert finding_rows.records()[0]["task"] == "attach"


def test_a_script_elsewhere_keeps_its_name_in_a_job(
    finding_rows: FindingRows,
    capfd: pytest.CaptureFixture[str],
    env: support.Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not directly under RUNNER_TEMP, or no job: the script's name."""
    cases = {
        "elsewhere": {"RUNNER_TEMP": "/elsewhere", "GITHUB_JOB": "attach"},
        "below it": {"RUNNER_TEMP": "/", "GITHUB_JOB": "attach"},
        "no job": {"RUNNER_TEMP": "", "GITHUB_JOB": ""},
    }
    for writer in writers(env=env):
        name = f"reporter{writer.suffix}"
        for case, environ in cases.items():
            finding_rows.file.unlink(missing_ok=True)
            planted = dict(environ)
            if case == "no job":
                planted["RUNNER_TEMP"] = str(finding_rows.root)
            done = writer.run(
                finding_rows.root,
                "plain",
                "s",
                "m",
                ORG_FINDINGS=str(finding_rows.file),
                **planted,
                capfd=capfd,
                monkeypatch=monkeypatch,
            )
            assert done.stderr == f"{name}: s: m\n"
            assert finding_rows.records()[0]["task"] == name


@pytest.mark.parametrize("writer", writers())
def test_findings_append(
    finding_rows: FindingRows,
    writer: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second finding is a second line of the same file."""
    finding_rows.file.unlink(missing_ok=True)
    for message in ("one", "two"):
        writer.run(
            finding_rows.root,
            "plain",
            "",
            message,
            MISE_TASK_NAME="t",
            ORG_FINDINGS=str(finding_rows.file),
            capfd=capfd,
            monkeypatch=monkeypatch,
        )
    messages: list[object] = [r["message"] for r in finding_rows.records()]
    expected: list[object] = ["one", "two"]
    assert messages == expected


def bashes() -> list[tuple[str, str]]:
    """List the shell writer's bashes: the belt's, and /bin/bash.

    Returns:
        Each bash's label and path.

    """
    out = [("belt bash", support.tool("bash"))]
    if SYSTEM_BASH.exists():
        out.append(("/bin/bash", str(SYSTEM_BASH)))
    return out


def records(file: Path) -> list[dict[str, object]]:
    """Read back every record in a file.

    Returns:
        The records, in order.

    """
    return [
        decoded.as_object(decoded.load_json(line), "a record")
        for line in file.read_text(encoding="utf-8").splitlines()
    ]


class RunnerRows:
    """Where each writer records on a runner, ORG_FINDINGS unset (#1976)."""

    def __init__(self, tmp_path: Path) -> None:
        """Give each row a directory of its own."""
        self.root = Path(str(tmp_path))

    def on_a_runner(
        self,
        writer: Writer,
        entry: str,
        *,
        capfd: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> list[Path]:
        """Run one call as a job's step does, ORG_FINDINGS unset (#1976).

        Returns:
            Every file under the runner's temp, the findings directory's.

        """
        temp = self.root / "runner"
        temp.mkdir(exist_ok=True)
        for old in temp.rglob("*.jsonl"):
            old.unlink()
        done = writer.run(
            self.root,
            entry,
            "s",
            "m",
            "d",
            MISE_TASK_NAME="lint:demo",
            RUNNER_TEMP=str(temp),
            GITHUB_JOB="mutants-rust",
            capfd=capfd,
            monkeypatch=monkeypatch,
        )
        assert done.returncode == 0, done.stderr
        files = sorted(p for p in temp.rglob("*") if p.is_file())
        for file in files:
            assert file.parent == temp / "findings"
            assert re.search(JOB_FILE, file.name)
        return files


@fixture
def runner_rows(tmp_path: Path) -> RunnerRows:
    """Give each row its own RunnerRows."""
    return RunnerRows(tmp_path)


@pytest.mark.parametrize("writer", writers())
def test_on_a_runner_the_writer_names_the_file(
    runner_rows: RunnerRows,
    writer: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset on a runner: one record in findings/<job>-<16 hex>.jsonl."""
    files = runner_rows.on_a_runner(
        writer, "plain", capfd=capfd, monkeypatch=monkeypatch
    )
    assert len(files) == 1, files
    expected: list[dict[str, object]] = [
        {
            "v": 1,
            "task": "lint:demo",
            "subject": "s",
            "message": "m",
            "detail": ["d"],
        }
    ]
    assert records(files[0]) == expected


@pytest.mark.parametrize("writer", writers())
def test_one_process_writes_one_file(
    runner_rows: RunnerRows,
    writer: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two findings of one process land in one file, in order."""
    files = runner_rows.on_a_runner(
        writer, "twice", capfd=capfd, monkeypatch=monkeypatch
    )
    assert len(files) == 1, files
    assert len(records(files[0])) == 2


@pytest.mark.parametrize("writer", writers())
def test_two_processes_write_two_files(
    runner_rows: RunnerRows,
    writer: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two legs of one matrix share GITHUB_JOB and never a file."""
    first = runner_rows.on_a_runner(
        writer, "plain", capfd=capfd, monkeypatch=monkeypatch
    )
    temp = runner_rows.root / "runner"
    done = writer.run(
        runner_rows.root,
        "plain",
        "s",
        "m",
        MISE_TASK_NAME="lint:demo",
        RUNNER_TEMP=str(temp),
        GITHUB_JOB="mutants-rust",
        capfd=capfd,
        monkeypatch=monkeypatch,
    )
    assert done.returncode == 0, done.stderr
    both = sorted(temp.rglob("*.jsonl"))
    assert len(both) == 2, both
    assert first[0] in both


# Sourcing the shell writer runs no command (#1934).
def test_an_empty_path_still_loads_the_writer(env: support.Env) -> None:
    """PATH empty: no output, status 0, org_finding defined."""
    for _label, bash in bashes():
        for _how, source, cwd in (
            ("absolute", str(SHELL_WRITER), None),
            ("no slash", SHELL_WRITER.name, SHELL_WRITER.parent),
        ):
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
                env=env(PATH=""),
                cwd=cwd,
                check=False,
            )
            assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


LINES_READER = f""". "{SHELL_WRITER}"
org_finding_lines
"""
US = "\037"
NO_RECORDS: list[dict[str, object]] = []


class LinesRows:
    """org_finding_lines: one finding per stdin line, unit-separated (#1934)."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Give each row a directory of its own."""
        self.make_env = env
        self.file = Path(str(tmp_path)) / "out" / "job.jsonl"

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
            env=self.make_env(
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


@fixture
def lines_rows(tmp_path: Path, env: support.Env) -> LinesRows:
    """Give each row its own LinesRows."""
    return LinesRows(tmp_path, env=env)


@pytest.mark.parametrize(("label", "bash"), bashes())
def test_each_line_is_one_finding(
    lines_rows: LinesRows, label: object, bash: object
) -> None:
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
    done = lines_rows.feed(bash, text)
    assert (done.returncode, done.stdout) == (0, "")
    assert done.stderr == printed
    assert lines_rows.records() == expected


@pytest.mark.parametrize(("label", "bash"), bashes())
def test_a_line_with_no_separator_is_passed_through(
    lines_rows: LinesRows, label: object, bash: object
) -> None:
    """A program's own summary goes to stdout and records nothing."""
    done = lines_rows.feed(bash, "all clean\n")
    assert (done.returncode, done.stdout, done.stderr) == (0, "all clean\n", "")
    assert lines_rows.records() == NO_RECORDS


@pytest.mark.parametrize(("label", "bash"), bashes())
def test_a_last_line_without_a_newline_is_read(
    lines_rows: LinesRows, label: object, bash: object
) -> None:
    """The final finding is not lost for want of a newline."""
    done = lines_rows.feed(bash, f"{US}last")
    assert done.stderr == "lint:demo: last\n"


# A lone surrogate is recorded as its backslash escape (#1984).
def test_a_lone_surrogate_is_recorded_as_printed(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 0, one record json.loads reads, the stderr line the same text."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        file = root / "out" / "job.jsonl"
        monkeypatch.setenv("MISE_TASK_NAME", "t")
        monkeypatch.setenv("ORG_FINDINGS", str(file))
        findings.finding(os.fsdecode(b"bad\xffname"), "x\ud800y")
        done = Ran(0, *capfd.readouterr())
        assert done.returncode == 0, done.stderr
        lines = file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        got = decoded.as_object(decoded.load_json(lines[0]), "a record")
        assert got["message"] == "x\\ud800y"
        assert got["subject"] == "bad\\udcffname"
        assert done.stderr == "t: bad\\udcffname: x\\ud800y\n"
