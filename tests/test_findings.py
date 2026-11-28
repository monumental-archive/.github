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
from typing import NamedTuple

import pytest

from belt import decoded, findings
from belt.typed_pytest import Requested, fixture
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
class Writer:
    """One writer of findings, reporting as a task's script does.

    The Python writer is belt Python, called in this process; a shell
    writer is the reporter script under its bash, a child whose streams
    are this process's own.
    """

    name: str
    bash: str | None
    # The Python writer's records-file draw, patched in for the row.
    drawn: list[str]

    @property
    def script(self) -> str:
        """Name the reporter script a finding without a task names.

        Returns:
            Its file name.

        """
        return "reporter.py" if self.bash is None else "reporter.sh"

    def report(self, entry: str, *args: str) -> int:
        """Report one finding, as the reporter script given `args` does.

        Each report is one process: the Python writer's records file is
        drawn afresh, as a new process draws it.

        Returns:
            The status.

        """
        if self.bash is not None:
            argv = [self.bash, str(Path.cwd() / self.script), entry, *args]
            return subprocess.run(argv, check=False).returncode
        subject, message, *detail = args
        self.drawn.clear()
        findings.finding(subject, message, detail, annotate=entry == "annotated")
        if entry == "twice":
            findings.finding(subject, message, detail)
        return 0


WRITERS = ["python", "belt bash", *(["/bin/bash"] if SYSTEM_BASH.exists() else [])]


@fixture
def _as_a_task(
    tmp_path: Path, env: support.Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Give the row a task's world.

    The environment is a started process's, with no task named and no
    records file; the directory is the row's, holding the shell
    reporter; the running program is the Python reporter.
    """
    for name in list(os.environ):
        monkeypatch.delenv(name)
    for name, value in env(PYTHONUTF8="1").items():
        if name not in {"MISE_TASK_NAME", "ORG_FINDINGS"}:
            monkeypatch.setenv(name, value)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "reporter.sh").write_text(SHELL_REPORTER, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "reporter.py")])


@fixture(params=WRITERS)
def writer(
    request: Requested[str], _as_a_task: None, monkeypatch: pytest.MonkeyPatch
) -> Writer:
    """Give the row each writer in turn, in a task's world.

    Returns:
        The writer.

    """
    drawn: list[str] = []
    monkeypatch.setattr(findings, "_drawn", drawn)
    bashes = {"belt bash": support.tool("bash"), "/bin/bash": str(SYSTEM_BASH)}
    return Writer(request.param, bashes.get(request.param), drawn)


def records(file: Path) -> list[dict[str, object]]:
    """Read back every record in a file.

    Returns:
        The records, in order.

    """
    return [
        decoded.as_object(decoded.load_json(line), "a record")
        for line in file.read_text(encoding="utf-8").splitlines()
    ]


@fixture
def recording(writer: Writer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Name the task `lint:demo` and the records file, for `writer`.

    Returns:
        The records file, not yet written.

    """
    file = tmp_path / "out" / "job.jsonl"
    monkeypatch.setenv("MISE_TASK_NAME", "lint:demo")
    monkeypatch.setenv("ORG_FINDINGS", str(file))
    return file


class Found(NamedTuple):
    """A row: the finding's words, what it prints, and the row's name."""

    subject: str
    message: str
    detail: list[str]
    printed: str
    name: str


FOUND = [
    Found(
        "Cargo.toml",
        "a pin is loose",
        [],
        "lint:demo: Cargo.toml: a pin is loose\n",
        "the form is task, subject, message",
    ),
    Found(
        "",
        "nothing is tracked",
        [],
        "lint:demo: nothing is tracked\n",
        "an empty subject is left out and recorded empty",
    ),
    Found(
        "chrono@0.4.45",
        "RUSTSEC-2026-0543 is undecided",
        ["reached through object_store 0.14.2", "and through arrow 57.0.0"],
        "lint:demo: chrono@0.4.45: RUSTSEC-2026-0543 is undecided\n"
        "  reached through object_store 0.14.2\n"
        "  and through arrow 57.0.0\n",
        "each detail line is indented two spaces",
    ),
    Found(
        "a\tb",
        AWKWARD,
        [AWKWARD],
        f"lint:demo: a\tb: {AWKWARD}\n  {AWKWARD}\n",
        "a quote, a backslash, a tab and non-ASCII survive",
    ),
]


@pytest.mark.parametrize("row", FOUND, ids=[row.name for row in FOUND])
def test_a_finding_is_printed_and_recorded(
    writer: Writer, recording: Path, capfd: pytest.CaptureFixture[str], row: Found
) -> None:
    """The belt's form on stderr; one record reading back the same values."""
    status = writer.report("plain", row.subject, row.message, *row.detail)
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, "", row.printed)
    expected: list[dict[str, object]] = [
        {
            "v": 1,
            "task": "lint:demo",
            "subject": row.subject,
            "message": row.message,
            "detail": row.detail,
        }
    ]
    assert records(recording) == expected
    assert recording.read_text(encoding="utf-8").count("\n") == 1


@pytest.mark.parametrize(
    "planted",
    [
        pytest.param({}, id="unset off a runner"),
        pytest.param(
            {"ORG_FINDINGS": "", "RUNNER_TEMP": "runner", "GITHUB_JOB": "mutants-rust"},
            id="empty, on a runner too",
        ),
    ],
)
def test_no_records_file_records_nothing(
    writer: Writer,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    planted: dict[str, str],
) -> None:
    """Without a records file the finding is printed and nothing is written."""
    monkeypatch.setenv("MISE_TASK_NAME", "lint:demo")
    for name, value in planted.items():
        monkeypatch.setenv(name, value)
    status = writer.report("plain", "x", "y")
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, "", "lint:demo: x: y\n")
    assert not any(tmp_path.rglob("*.jsonl"))


def test_a_failed_append_is_said_and_is_not_fatal(
    writer: Writer,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A path under a file: printed, one more line names it, status 0."""
    blocker = tmp_path / "a-file"
    blocker.write_text("", encoding="utf-8")
    target = blocker / "out" / "job.jsonl"
    monkeypatch.setenv("MISE_TASK_NAME", "lint:demo")
    monkeypatch.setenv("ORG_FINDINGS", str(target))
    status = writer.report("plain", "x", "y", "z")
    out, err = capfd.readouterr()
    assert (status, out) == (0, "")
    first, second, failed = err.splitlines()
    assert (first, second) == ("lint:demo: x: y", "  z")
    assert failed.startswith(f"lint:demo: the finding was not recorded in {target}: ")
    assert not target.exists()


def test_the_annotated_entry_escapes_as_the_runner_reads(
    writer: Writer, recording: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """`::error::`, with `%`, CR and LF escaped; the record is the same."""
    message = "50% done\nnext"
    status = writer.report("annotated", "s", message, "one detail")
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        "",
        "::error::lint:demo: s: 50%25 done%0Anext\n  one detail\n",
    )
    assert records(recording)[0]["message"] == message


def test_a_plain_line_cannot_open_a_command(
    writer: Writer, capfd: pytest.CaptureFixture[str]
) -> None:
    """A message of a newline and a command prints one line, the command escaped."""
    status = writer.report("plain", "", "x\n::error::forged", "::warning::w")
    out, err = capfd.readouterr()
    assert (status, out) == (0, "")
    first, second = err.splitlines()
    assert first.endswith(": x\\n::error::forged"), first
    assert second == "  \\::warning::w"


def test_the_task_is_mise_s_name_when_set(
    writer: Writer,
    recording: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MISE_TASK_NAME names the task in the line and the record."""
    monkeypatch.setenv("MISE_TASK_NAME", "audit:deny")
    status = writer.report("plain", "s", "m")
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, "", "audit:deny: s: m\n")
    assert records(recording)[0]["task"] == "audit:deny"


@pytest.mark.parametrize(
    "planted",
    [
        pytest.param({}, id="no task, off a runner"),
        pytest.param(
            {"RUNNER_TEMP": "/elsewhere", "GITHUB_JOB": "attach"},
            id="a runner's temp elsewhere",
        ),
        pytest.param(
            {"RUNNER_TEMP": "/", "GITHUB_JOB": "attach"}, id="below the runner's temp"
        ),
        pytest.param({"RUNNER_TEMP": "{tmp}", "GITHUB_JOB": ""}, id="no job"),
    ],
)
def test_the_task_is_the_script_s_name_otherwise(
    writer: Writer,
    recording: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    planted: dict[str, str],
) -> None:
    """Where mise named no task and no step script ran, the script's file name."""
    monkeypatch.delenv("MISE_TASK_NAME")
    for name, value in planted.items():
        monkeypatch.setenv(name, value.format(tmp=Path.cwd()))
    status = writer.report("plain", "s", "m")
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, "", f"{writer.script}: s: m\n")
    assert records(recording)[0]["task"] == writer.script


def test_a_step_script_s_finding_names_the_job(
    writer: Writer,
    recording: Path,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner's step script, directly under RUNNER_TEMP: GITHUB_JOB."""
    monkeypatch.delenv("MISE_TASK_NAME")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_JOB", "attach")
    status = writer.report("plain", "s", "m")
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, "", "attach: s: m\n")
    assert records(recording)[0]["task"] == "attach"


def test_findings_append(writer: Writer, recording: Path) -> None:
    """A second finding is a second line of the same file."""
    assert writer.report("plain", "", "one") == 0
    assert writer.report("plain", "", "two") == 0
    messages: list[object] = [r["message"] for r in records(recording)]
    expected: list[object] = ["one", "two"]
    assert messages == expected


@fixture
def on_a_runner(
    _as_a_task: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Make the row a job's step, ORG_FINDINGS unset (#1976), in a task's world.

    Returns:
        The runner's temp directory.

    """
    temp = tmp_path / "runner"
    temp.mkdir()
    monkeypatch.setenv("MISE_TASK_NAME", "lint:demo")
    monkeypatch.setenv("RUNNER_TEMP", str(temp))
    monkeypatch.setenv("GITHUB_JOB", "mutants-rust")
    return temp


def job_files(temp: Path) -> list[Path]:
    """List the files under a runner's temp, each a job's records file.

    Returns:
        The files, sorted.

    """
    files = sorted(p for p in temp.rglob("*") if p.is_file())
    for file in files:
        assert file.parent == temp / "findings"
        assert re.search(JOB_FILE, file.name)
    return files


def test_on_a_runner_the_writer_names_the_file(
    writer: Writer, on_a_runner: Path
) -> None:
    """Unset on a runner: one record in findings/<job>-<16 hex>.jsonl."""
    assert writer.report("plain", "s", "m", "d") == 0
    (file,) = job_files(on_a_runner)
    expected: list[dict[str, object]] = [
        {
            "v": 1,
            "task": "lint:demo",
            "subject": "s",
            "message": "m",
            "detail": ["d"],
        }
    ]
    assert records(file) == expected


def test_one_process_writes_one_file(writer: Writer, on_a_runner: Path) -> None:
    """Two findings of one process land in one file, in order."""
    assert writer.report("twice", "s", "m", "d") == 0
    (file,) = job_files(on_a_runner)
    messages = [r["message"] for r in records(file)]
    expected: list[object] = ["m", "m"]
    assert messages == expected


def test_two_processes_write_two_files(writer: Writer, on_a_runner: Path) -> None:
    """Two legs of one matrix share GITHUB_JOB and never a file."""
    assert writer.report("plain", "s", "m", "d") == 0
    assert writer.report("plain", "s", "m") == 0
    first, second = job_files(on_a_runner)
    assert first != second


def bashes() -> list[tuple[str, str]]:
    """List the shell writer's bashes: the belt's, and /bin/bash.

    Returns:
        Each bash's label and path.

    """
    out = [("belt bash", support.tool("bash"))]
    if SYSTEM_BASH.exists():
        out.append(("/bin/bash", str(SYSTEM_BASH)))
    return out


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
def test_each_line_is_one_finding(lines_rows: LinesRows, label: str, bash: str) -> None:
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
    lines_rows: LinesRows, label: str, bash: str
) -> None:
    """A program's own summary goes to stdout and records nothing."""
    done = lines_rows.feed(bash, "all clean\n")
    assert (done.returncode, done.stdout, done.stderr) == (0, "all clean\n", "")
    assert lines_rows.records() == NO_RECORDS


@pytest.mark.parametrize(("label", "bash"), bashes())
def test_a_last_line_without_a_newline_is_read(
    lines_rows: LinesRows, label: str, bash: str
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
        out, err = capfd.readouterr()
        lines = file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        got = decoded.as_object(decoded.load_json(lines[0]), "a record")
        assert got["message"] == "x\\ud800y"
        assert got["subject"] == "bad\\udcffname"
        assert (out, err) == ("", "t: bad\\udcffname: x\\ud800y\n")
