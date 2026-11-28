"""pinact's SARIF report, read into finding records (#1955).

Rows for `mise/pinact_findings.py`, run as a task runs it: a child
process with ORG_FINDINGS and MISE_TASK_NAME set, over a stub `pinact`
on PATH that logs its arguments, writes the diff line pinact writes to
stderr, prints the row's document and exits with the row's status.

`audit:action-pins` asks the GitHub API (`--verify-comment`,
`--verify-min-age`), so its fixture is pinact 5.0.2's own SARIF from one
real run by the lane, quoted in QUOTED with its command:

    pinact run --config mise/pinact.yaml --fix=false --verify-comment
      --verify-min-age --format sarif .github/workflows/a.yml

over a workflow whose line 6 pins actions/checkout v4.1.0's commit with
the comment `# v4.2.0`, and whose line 7 is `actions/checkout@v4`; it
exited 3; SOURCE is that workflow, planted wherever a row runs, since
the message is `<ruleId>: <the file's line, trimmed>` (the owner's
ruling on #1955). One row runs the belt's pinned pinact itself with
`--no-api`, which needs no network, on two unpinned lines in one file:
one rule, one text, two records with two messages. Another moves the
resolved SHA in the text and the message stays; a missing file and a
line past the end are refused by name.

The `Task` rows run `audit:action-pins`' own body, read from the belt's
config, over the stub in a planted, tracked tree: the quoted report is
its two records and pinact's 3, an empty one no record and 0. Run
against `origin/main`'s config, the record row fails: that body ran
pinact in its human mode and recorded nothing.
"""

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import decoded, pinact_findings
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from pathlib import Path

HERE = support.MISE
TASK = "audit:action-pins"
GIT = support.tool("git")
WORKFLOW = ".github/workflows/a.yml"
LIED = "8ade135a41bc03ea155e62e844d188df1ea18608"
FIXED = "11d5960a326750d5838078e36cf38b85af677262"
MISMATCH = (
    "failed to handle a line: action_version must be equal to"
    " commit_hash_of_version_annotation"
)
UNPINNED = (
    "Action should be pinned:       - uses: actions/checkout@v4 ->"
    f"       - uses: actions/checkout@{FIXED} # v4.4.0"
)


def location(line: int) -> dict[str, object]:
    """Write one SARIF location as pinact prints it.

    Returns:
        The location.

    """
    return {
        "physicalLocation": {
            "artifactLocation": {"uri": WORKFLOW},
            "region": {"startLine": line},
        }
    }


def result(rule: str, text: str, line: int) -> dict[str, object]:
    """Write one SARIF result as pinact prints it.

    Returns:
        The result.

    """
    return {
        "ruleId": rule,
        "level": "error",
        "message": {"text": text},
        "locations": [location(line)],
    }


def sarif(*results: dict[str, object], version: str = "2.1.0") -> str:
    """Write a SARIF log as pinact 5.0.2 prints it.

    Returns:
        The document.

    """
    rules = [
        {
            "id": "unpinned-action",
            "shortDescription": {"text": "GitHub Action is not pinned to a commit SHA"},
        },
        {
            "id": "parse-error",
            "shortDescription": {"text": "Failed to parse or process action"},
        },
    ]
    doc: dict[str, object] = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": version,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "pinact",
                        "informationUri": ("https://github.com/suzuki-shunsuke/pinact"),
                        "rules": rules,
                    }
                },
                "results": list(results),
            }
        ],
    }
    return json.dumps(doc, indent=2)


FIX: dict[str, object] = {
    "artifactChanges": [
        {
            "artifactLocation": {"uri": WORKFLOW},
            "replacements": [
                {
                    "deletedRegion": {"startLine": 7},
                    "insertedContent": {
                        "text": f"      - uses: actions/checkout@{FIXED} # v4.4.0"
                    },
                }
            ],
        }
    ]
}
QUOTED = sarif(
    result("parse-error", MISMATCH, 6),
    result("unpinned-action", UNPINNED, 7) | {"fixes": [FIX]},
)
# The workflow the real run read, as the lane planted it.
SOURCE = (
    "on: push\njobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps:\n"
    f"      - uses: actions/checkout@{LIED} # v4.2.0\n"
    "      - uses: actions/checkout@v4\n"
)
LIED_LINE = f"parse-error: - uses: actions/checkout@{LIED} # v4.2.0"
UNPINNED_LINE = "unpinned-action: - uses: actions/checkout@v4"
QUOTED_RECORDS: list[dict[str, object]] = [
    {
        "v": 1,
        "task": TASK,
        "subject": WORKFLOW,
        "message": LIED_LINE,
        "detail": [MISMATCH, "line 6"],
    },
    {
        "v": 1,
        "task": TASK,
        "subject": WORKFLOW,
        "message": UNPINNED_LINE,
        "detail": [UNPINNED, "line 7"],
    },
]

STUB = """\
import os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:
    log.write("\\t".join(sys.argv[1:]) + "\\n")
print("-       - uses: actions/checkout@v4", file=sys.stderr)
with open(os.environ["STUB_DOC"], encoding="utf-8") as doc:
    sys.stdout.write(doc.read())
sys.exit(int(os.environ["STUB_RC"]))
"""


def same[T](got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    assert got == want, msg or None


def records(file: Path) -> list[dict[str, object]]:
    """Read every record appended to a findings file.

    Returns:
        The records, in order; none when the file was not written.

    """
    if not file.exists():
        return []
    return [
        decoded.as_object(decoded.load_json(line), "a record")
        for line in file.read_text(encoding="utf-8").splitlines()
    ]


def refused(file: Path, stderr: str) -> None:
    """Hold a refusal to one record: its own, none of the report's (#1934)."""
    got = records(file)
    same(len(got), 1)
    same(got[0]["subject"], "")
    assert f"{TASK}: {got[0]['message']}\n" in stderr


class Stubbed:
    """A stub pinact on PATH, its log, the findings file."""

    def __init__(
        self, tmp_path: Path, capfd: pytest.CaptureFixture[str], *, env: support.Env
    ) -> None:
        """Lay out the stub, its log, and the findings file."""
        self.make_env = env
        self.capfd = capfd
        self.dir = tmp_path
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        stub = self.bin / "pinact"
        stub.write_text(f"#!{sys.executable}\n{STUB}", encoding="utf-8")
        stub.chmod(0o755)
        self.log = self.dir / "pinact.log"
        self.doc = self.dir / "doc.json"
        self.file = self.dir / "findings" / "job.jsonl"
        (self.dir / ".github" / "workflows").mkdir(parents=True)
        (self.dir / WORKFLOW).write_text(SOURCE, encoding="utf-8")

    def adapt(
        self, argv: list[str], env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        """Call the adapter as a task runs it, in process, under `env`.

        The tool is looked up on `env`'s PATH, as a process started with
        it would find it.

        Returns:
            Its status and what it wrote, as a finished process's.

        """
        found = support.optional_tool("pinact", path=env["PATH"]) or "pinact"
        with pytest.MonkeyPatch.context() as patched:
            for name, value in env.items():
                patched.setenv(name, value)
            patched.setattr(pinact_findings, "PINACT", found)
            patched.chdir(self.dir)
            status = pinact_findings.main(argv)
        out, err = self.capfd.readouterr()
        return subprocess.CompletedProcess(argv, status, out, err)

    def stub_env(self, document: str, status: int, path: str | None) -> dict[str, str]:
        """Give a run the stub's answer and the findings file.

        Returns:
            The environment.

        """
        self.doc.write_text(document, encoding="utf-8")
        return self.make_env(
            PATH=path or f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            ORG_FINDINGS=str(self.file),
            MISE_TASK_NAME=TASK,
            STUB_LOG=str(self.log),
            STUB_DOC=str(self.doc),
            STUB_RC=str(status),
            PYTHONUTF8="1",
        )


@fixture
def stubbed(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], env: support.Env
) -> Stubbed:
    """Give each row its own Stubbed."""
    return Stubbed(tmp_path, capfd, env=env)


class Adapter(Stubbed):
    """The adapter over a stub pinact."""

    def run_adapter(
        self, document: str, status: int, *args: str, path: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the adapter as a task runs it, the stub answering.

        Returns:
            The finished process.

        """
        return self.adapt(
            [*(args or (WORKFLOW,))], self.stub_env(document, status, path)
        )


@fixture
def adapter(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], env: support.Env
) -> Adapter:
    """Give each row its own Adapter."""
    return Adapter(tmp_path, capfd, env=env)


def test_the_quoted_report_is_two_records(adapter: Adapter) -> None:
    """The real run's log: two records, the lines in the detail, its 3."""
    done = adapter.run_adapter(QUOTED, 3)
    same(done.returncode, 3, done.stderr)
    same(records(adapter.file), QUOTED_RECORDS)
    same(
        done.stderr.splitlines(),
        [
            "-       - uses: actions/checkout@v4",
            f"{TASK}: {WORKFLOW}: {LIED_LINE}",
            f"  {MISMATCH}",
            "  line 6",
            f"{TASK}: {WORKFLOW}: {UNPINNED_LINE}",
            f"  {UNPINNED}",
            "  line 7",
        ],
    )
    same(done.stdout, f"{TASK}: pinact: 2 finding(s)\n")


def test_a_clean_report_records_nothing(adapter: Adapter) -> None:
    """No results: no record, pinact's 0."""
    done = adapter.run_adapter(sarif(), 0)
    same(done.returncode, 0, done.stderr)
    same(records(adapter.file), [])
    same(done.stdout, f"{TASK}: pinact: 0 finding(s)\n")


def test_another_sarif_version_is_refused_by_name(adapter: Adapter) -> None:
    """SARIF 2.2: refused, both versions named, exit 1, its one record."""
    done = adapter.run_adapter(sarif(version="2.2.0"), 0)
    same(done.returncode, 1, done.stderr)
    assert "the report is SARIF 2.2.0; this adapter reads SARIF 2.1.0" in done.stderr
    refused(adapter.file, done.stderr)
    same(done.stdout, "")


def test_an_unknown_shape_is_refused_by_name(adapter: Adapter) -> None:
    """A log with no `runs`: refused by name, exit 1, its one record."""
    done = adapter.run_adapter('{"version": "2.1.0"}', 3)
    same(done.returncode, 1, done.stderr)
    assert (
        f"{TASK}: pinact exited 3: the output is not pinact's report:"
        " runs is not a list"
    ) in done.stderr
    refused(adapter.file, done.stderr)
    same(done.stdout, "")


def test_output_that_is_not_json_is_refused_by_name(adapter: Adapter) -> None:
    """A human report: refused with pinact's status, exit 1."""
    done = adapter.run_adapter("ERROR failed to handle a line\n", 3)
    same(done.returncode, 1, done.stderr)
    assert f"{TASK}: pinact exited 3: the output is not pinact's report" in done.stderr
    refused(adapter.file, done.stderr)


def test_a_malformed_result_records_none(adapter: Adapter) -> None:
    """One result with no location: refused, none of the report's recorded."""
    bad = result("parse-error", MISMATCH, 6) | {"locations": []}
    done = adapter.run_adapter(sarif(result("parse-error", "x", 1), bad), 3)
    same(done.returncode, 1, done.stderr)
    assert "runs[0].results[1]: locations is empty" in done.stderr
    refused(adapter.file, done.stderr)


def test_another_resolved_sha_leaves_the_message(adapter: Adapter) -> None:
    """A release moves the SHA in the text: the message is unchanged."""
    later = UNPINNED.replace(FIXED, "f" * 40)
    done = adapter.run_adapter(sarif(result("unpinned-action", later, 7)), 3)
    same(done.returncode, 3, done.stderr)
    got = [(r["message"], r["detail"]) for r in records(adapter.file)]
    want: list[tuple[object, object]] = [(UNPINNED_LINE, [later, "line 7"])]
    same(got, want)


def test_a_missing_file_is_refused_by_name(adapter: Adapter) -> None:
    """A result naming a file that is not there: refused, 1, its one record."""
    (adapter.dir / WORKFLOW).unlink()
    done = adapter.run_adapter(QUOTED, 3)
    same(done.returncode, 1, done.stderr)
    assert (
        f"{TASK}: pinact exited 3: the output is not pinact's report:"
        f" runs[0].results[0]: locations[0]: {WORKFLOW} cannot be read"
    ) in done.stderr
    refused(adapter.file, done.stderr)


def test_a_line_past_the_end_is_refused_by_name(adapter: Adapter) -> None:
    """A result past the file's last line: refused, 1, its one record."""
    done = adapter.run_adapter(sarif(result("parse-error", MISMATCH, 8)), 3)
    same(done.returncode, 1, done.stderr)
    assert f"runs[0].results[0]: locations[0]: {WORKFLOW} has no line 8" in done.stderr
    refused(adapter.file, done.stderr)


def test_pinact_s_status_is_the_exit(adapter: Adapter) -> None:
    """A 2 from pinact with a clean log exits 2."""
    done = adapter.run_adapter(sarif(), 2)
    same(done.returncode, 2, done.stderr)
    same(records(adapter.file), [])


def test_arguments_pass_unchanged_after_the_format(adapter: Adapter) -> None:
    """The adapter calls `pinact run --format sarif` and the arguments."""
    args = ["--config", "a b.yaml", "--fix=false", WORKFLOW, "x/action.yml"]
    done = adapter.run_adapter(sarif(), 0, *args)
    same(done.returncode, 0, done.stderr)
    same(
        adapter.log.read_text(encoding="utf-8").splitlines(),
        ["\t".join(["run", "--format", "sarif", *args])],
    )


def test_no_pinact_is_refused_by_name(adapter: Adapter) -> None:
    """No pinact on PATH: refused by name, exit 1, its one record."""
    done = adapter.run_adapter(sarif(), 0, path=str(adapter.dir / "empty"))
    same(done.returncode, 1, done.stderr)
    assert f"{TASK}: pinact is not on PATH" in done.stderr
    refused(adapter.file, done.stderr)


def test_the_pinned_pinact_offline_gives_two_messages(
    adapter: Adapter, env: support.Env
) -> None:
    """Two unpinned actions in one file, judged by pinact with `--no-api`.

    One rule, one text, one file: two records with two messages.
    """
    if support.optional_tool("pinact") is None:
        pytest.skip("the belt's pinact is not on PATH")
    (adapter.dir / WORKFLOW).write_text(
        "on: push\njobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-go@v5\n",
        encoding="utf-8",
    )
    done = adapter.adapt(
        ["--fix=false", "--no-api", WORKFLOW],
        env(ORG_FINDINGS=str(adapter.file), MISE_TASK_NAME=TASK, PYTHONUTF8="1"),
    )
    assert done.returncode != 0, done.stderr
    cannot = "failed to handle a line: action can't be pinned"
    want: list[dict[str, object]] = [
        {
            "v": 1,
            "task": TASK,
            "subject": WORKFLOW,
            "message": "parse-error: - uses: actions/checkout@v4",
            "detail": [cannot, "line 6"],
        },
        {
            "v": 1,
            "task": TASK,
            "subject": WORKFLOW,
            "message": "parse-error: - uses: actions/setup-go@v5",
            "detail": [cannot, "line 7"],
        },
    ]
    same(records(adapter.file), want)


class Task(Stubbed):
    """audit:action-pins' body over the stub, in a planted tracked tree."""

    def run_task(self, document: str, status: int) -> subprocess.CompletedProcess[str]:
        """Plant and track a workflow, then run audit:action-pins' body.

        Returns:
            The finished process.

        """
        root = self.dir / "tree"
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / WORKFLOW).write_text(SOURCE, encoding="utf-8")
        git = [GIT, "-C", str(root)]
        subprocess.run([*git, "init", "-q"], env=self.make_env(), check=True)
        subprocess.run([*git, "add", "-A"], env=self.make_env(), check=True)
        config = decoded.load_toml((HERE / "config.toml").read_bytes())
        tasks = decoded.as_object(config["tasks"], "tasks")
        body = decoded.as_str(decoded.as_object(tasks[TASK], TASK)["run"], "run")
        run = support.task_run(TASK, body)
        return subprocess.run(
            run.argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.stub_env(document, status, None)
            | run.env
            | {"ORG_BELT_DIR": str(HERE), "ORG_PYTHON": sys.executable},
            cwd=root,
            check=False,
        )


@fixture
def task(tmp_path: Path, capfd: pytest.CaptureFixture[str], env: support.Env) -> Task:
    """Give each row its own Task."""
    return Task(tmp_path, capfd, env=env)


def test_the_quoted_report_is_the_records_and_pinact_s_red(task: Task) -> None:
    """The real run's log: its two records, pinact's 3, one call."""
    done = task.run_task(QUOTED, 3)
    same(done.returncode, 3, done.stderr)
    same(records(task.file), QUOTED_RECORDS)
    same(len(task.log.read_text(encoding="utf-8").splitlines()), 1)


def test_a_clean_report_is_no_record_and_pinact_s_green(task: Task) -> None:
    """No results: no record, pinact's 0, the adapter's line."""
    done = task.run_task(sarif(), 0)
    same(done.returncode, 0, done.stderr)
    same(records(task.file), [])
    same(done.stdout, f"{TASK}: pinact: 0 finding(s)\n")
