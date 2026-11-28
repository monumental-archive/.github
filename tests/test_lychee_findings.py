"""lychee's report, read into finding records (#1955).

Rows for `mise/lychee_findings.py`, run as a task runs it: a child
process with ORG_FINDINGS and MISE_TASK_NAME set, over a stub `lychee`
on PATH that logs its arguments, says a line on stderr, prints the
row's document and exits with the row's status.

The `Task` rows run `audit:links`' own body, read from the belt's
config, under the belt's pinned lychee in a planted, tracked tree whose
one link is relative, so lychee needs no network: a broken link is the
quoted record and lychee's 2, a sound one no record and 0. Run against
`origin/main`'s config, the record row fails: that body ran lychee in
its human mode and recorded nothing.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from belt import decoded, lychee_findings
from belt.typed_pytest import fixture
from tests import support

HERE = support.MISE
TASK = "audit:links"
GIT = support.tool("git")
NOT_FOUND = "File not found. Check if file exists and path is correct"

STUB = """\
import os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:
    log.write("\\t".join(sys.argv[1:]) + "\\n")
print("stub lychee: checking", file=sys.stderr)
with open(os.environ["STUB_DOC"], encoding="utf-8") as doc:
    sys.stdout.write(doc.read())
sys.exit(int(os.environ["STUB_RC"]))
"""


def entry(url: str, text: str, line: int) -> dict[str, object]:
    """Write one `error_map` entry as lychee 0.24.2 prints it.

    Returns:
        The entry.

    """
    return {
        "url": url,
        "status": {"text": text, "details": text},
        "span": {"line": line, "column": 5},
        "duration": {"secs": 0, "nanos": 618750},
    }


def report(errors: dict[str, list[dict[str, object]]]) -> str:
    """Write a report document as lychee 0.24.2 prints it.

    Returns:
        The document.

    """
    count = sum(len(v) for v in errors.values())
    doc: dict[str, object] = {
        "total": count,
        "unique": count,
        "successful": 0,
        "errors": count,
        "success_map": {},
        "error_map": errors,
        "timeout_map": {},
        "excluded_map": {},
        "detailed_stats": False,
    }
    return json.dumps(doc, indent=2)


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


class Adapter:
    """The adapter over a stub lychee."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Lay out a stub lychee, its log, and the findings file."""
        self.make_env = env
        self.dir = Path(str(tmp_path))
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        stub = self.bin / "lychee"
        stub.write_text(f"#!{sys.executable}\n{STUB}", encoding="utf-8")
        stub.chmod(0o755)
        self.log = self.dir / "lychee.log"
        self.doc = self.dir / "doc.json"
        self.file = self.dir / "findings" / "job.jsonl"

    def enter(self, monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
        """Stand in the row's directory as a task runs the adapter, under `env`.

        The tool is looked up on `env`'s PATH, as a process started with
        it would find it.
        """
        found = support.optional_tool("lychee", path=env["PATH"]) or "lychee"
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(lychee_findings, "LYCHEE", found)
        monkeypatch.chdir(self.dir)

    def answer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        document: str,
        status: int,
        path: str | None = None,
    ) -> None:
        """Have the stub answer `document` and `status` when the adapter runs."""
        self.doc.write_text(document, encoding="utf-8")
        env = self.make_env(
            PATH=path or f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            ORG_FINDINGS=str(self.file),
            MISE_TASK_NAME=TASK,
            STUB_LOG=str(self.log),
            STUB_DOC=str(self.doc),
            STUB_RC=str(status),
            PYTHONUTF8="1",
        )
        self.enter(monkeypatch, env)


@fixture
def adapter(tmp_path: Path, env: support.Env) -> Adapter:
    """Give each row its own Adapter.

    Returns:
        The row's Adapter.

    """
    return Adapter(tmp_path, env=env)


def test_one_broken_link_is_one_record(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """One entry: one record, the line in the detail, lychee's 2."""
    doc = report({"a.md": [entry("file:///t/missing.md", NOT_FOUND, 3)]})
    adapter.answer(monkeypatch, doc, 2)
    status = lychee_findings.main(["a.md"])
    out, err = capfd.readouterr()
    same(status, 2, err)
    want: list[dict[str, object]] = [
        {
            "v": 1,
            "task": TASK,
            "subject": "a.md",
            "message": f"file:///t/missing.md: {NOT_FOUND}",
            "detail": ["line 3"],
        }
    ]
    same(records(adapter.file), want)
    same(
        err.splitlines(),
        [
            "stub lychee: checking",
            f"{TASK}: a.md: file:///t/missing.md: {NOT_FOUND}",
            "  line 3",
        ],
    )
    same(out, f"{TASK}: lychee: 1 broken link(s)\n")


def test_a_clean_report_records_nothing(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """An empty `error_map`: no record, lychee's 0."""
    adapter.answer(monkeypatch, report({}), 0)
    status = lychee_findings.main(["a.md"])
    out, err = capfd.readouterr()
    same(status, 0, err)
    same(records(adapter.file), [])
    same(out, f"{TASK}: lychee: 0 broken link(s)\n")


def test_every_entry_is_a_record_in_order(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Two files, three entries: three records in the report's order."""
    doc = report({
        "b.md": [
            entry("https://x.invalid/", "Network error", 9),
            entry("file:///t/b.md#gone", "Cannot find fragment", 2),
        ],
        "a.md": [entry("file:///t/c.md", NOT_FOUND, 1)],
    })
    adapter.answer(monkeypatch, doc, 2)
    status = lychee_findings.main(["a.md"])
    _out, err = capfd.readouterr()
    same(status, 2, err)
    got = [(r["subject"], r["message"], r["detail"]) for r in records(adapter.file)]
    want: list[tuple[object, object, object]] = [
        ("b.md", "https://x.invalid/: Network error", ["line 9"]),
        ("b.md", "file:///t/b.md#gone: Cannot find fragment", ["line 2"]),
        ("a.md", f"file:///t/c.md: {NOT_FOUND}", ["line 1"]),
    ]
    same(got, want)


def test_an_unknown_shape_is_refused_by_name(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A report with no `error_map`: refused by name, exit 1, its one record."""
    adapter.answer(monkeypatch, '{"fail_map": {}}', 2)
    status = lychee_findings.main(["a.md"])
    out, err = capfd.readouterr()
    same(status, 1, err)
    assert (
        f"{TASK}: lychee exited 2: the output is not lychee's report:"
        " error_map is not an object"
    ) in err
    refused(adapter.file, err)
    same(out, "")


def test_output_that_is_not_json_is_refused_by_name(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A human report: refused with lychee's status, exit 1."""
    adapter.answer(monkeypatch, "[a.md]:\n  [ERROR] missing.md\n", 2)
    status = lychee_findings.main(["a.md"])
    _out, err = capfd.readouterr()
    same(status, 1, err)
    assert f"{TASK}: lychee exited 2: the output is not lychee's report" in err
    refused(adapter.file, err)


def test_a_malformed_entry_records_none(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """One entry without a line: refused, none of the report's recorded."""
    bad = entry("file:///t/x.md", NOT_FOUND, 4)
    del bad["span"]
    doc = report({"a.md": [entry("file:///t/y.md", NOT_FOUND, 1), bad]})
    adapter.answer(monkeypatch, doc, 2)
    status = lychee_findings.main(["a.md"])
    _out, err = capfd.readouterr()
    same(status, 1, err)
    assert "error_map[a.md][1]: span is not an object" in err
    refused(adapter.file, err)


def test_lychee_s_status_is_the_exit(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A 3 from lychee with an empty report exits 3."""
    adapter.answer(monkeypatch, report({}), 3)
    status = lychee_findings.main(["a.md"])
    _out, err = capfd.readouterr()
    same(status, 3, err)
    same(records(adapter.file), [])


def test_arguments_pass_unchanged_after_the_format(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The adapter calls `lychee --format json` and the arguments, once."""
    args = ["--no-progress", "--config", "a b.toml", "a.md", "b.md"]
    adapter.answer(monkeypatch, report({}), 0)
    status = lychee_findings.main(["--", *args])
    _out, err = capfd.readouterr()
    same(status, 0, err)
    same(
        adapter.log.read_text(encoding="utf-8").splitlines(),
        ["\t".join(["--format", "json", *args])],
    )


def test_no_lychee_is_refused_by_name(
    adapter: Adapter, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """No lychee on PATH: refused by name, exit 1, its one record."""
    adapter.answer(monkeypatch, report({}), 0, path=str(adapter.dir / "empty"))
    status = lychee_findings.main(["a.md"])
    _out, err = capfd.readouterr()
    same(status, 1, err)
    assert f"{TASK}: lychee is not on PATH" in err
    refused(adapter.file, err)


class Task:
    """audit:links' body under the belt's pinned lychee, offline by planting."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant a tracked tree with one markdown file linking `other.md`."""
        self.make_env = env
        if support.optional_tool("lychee") is None:
            pytest.skip("the belt's lychee is not on PATH")
        self.tmp = tmp_path
        self.root = self.tmp / "tree"
        self.root.mkdir()
        self.file = self.tmp / "findings" / "job.jsonl"
        (self.root / "a.md").write_text("# a\n\nsee [o](other.md)\n", encoding="utf-8")
        git = [GIT, "-C", str(self.root)]
        subprocess.run([*git, "init", "-q"], env=self.make_env(), check=True)

    def run_task(self) -> subprocess.CompletedProcess[str]:
        """Track the tree and run audit:links' body in it.

        Returns:
            The finished process.

        """
        git = [GIT, "-C", str(self.root)]
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
            env=self.make_env(
                **run.env,
                ORG_BELT_DIR=str(HERE),
                ORG_PYTHON=sys.executable,
                ORG_FINDINGS=str(self.file),
                PYTHONUTF8="1",
            ),
            cwd=self.root,
            check=False,
        )


@fixture
def task(tmp_path: Path, env: support.Env) -> Task:
    """Give each row its own Task.

    Returns:
        The row's Task.

    """
    return Task(tmp_path, env=env)


def test_a_broken_link_is_the_record_and_lychee_s_red(task: Task) -> None:
    """`other.md` missing: one record read back, lychee's 2."""
    done = task.run_task()
    same(done.returncode, 2, done.stderr)
    url = (task.root / "other.md").as_uri()
    want: list[dict[str, object]] = [
        {
            "v": 1,
            "task": TASK,
            "subject": "a.md",
            "message": f"{url}: {NOT_FOUND}",
            "detail": ["line 3"],
        }
    ]
    same(records(task.file), want)


def test_a_sound_link_is_no_record_and_lychee_s_green(task: Task) -> None:
    """`other.md` present: no record, lychee's 0."""
    (task.root / "other.md").write_text("# other\n", encoding="utf-8")
    done = task.run_task()
    same(done.returncode, 0, done.stderr)
    same(records(task.file), [])
    same(done.stdout, f"{TASK}: lychee: 0 broken link(s)\n")
