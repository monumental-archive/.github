#!/usr/bin/env python3
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
exited 3. One row runs the belt's pinned pinact itself with `--no-api`,
which needs no network, on that unpinned line.

The `Task` rows run `audit:action-pins`' own body, read from the belt's
config, over the stub in a planted, tracked tree: the quoted report is
its two records and pinact's 3, an empty one no record and 0. Run
against `origin/main`'s config, the record row fails: that body ran
pinact in its human mode and recorded nothing.

stdlib `unittest`, matching the rest of mise/.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic

HERE = Path(__file__).resolve().parent
ADAPTER = HERE / "pinact_findings.py"
TASK = "audit:action-pins"
GIT = hermetic.tool("git")
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
QUOTED_RECORDS: list[dict[str, object]] = [
    {
        "v": 1,
        "task": TASK,
        "subject": WORKFLOW,
        "message": MISMATCH,
        "detail": ["line 6"],
    },
    {
        "v": 1,
        "task": TASK,
        "subject": WORKFLOW,
        "message": UNPINNED,
        "detail": ["line 7"],
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


def same[T](case: unittest.TestCase, got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    case.assertEqual(got, want, msg or None)


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


class Stubbed(unittest.TestCase):
    """A stub pinact on PATH, its log, the findings file."""

    @override
    def setUp(self) -> None:
        """Lay out the stub, its log, and the findings file."""
        self.dir = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        stub = self.bin / "pinact"
        stub.write_text(f"#!{sys.executable}\n{STUB}", encoding="utf-8")
        stub.chmod(0o755)
        self.log = self.dir / "pinact.log"
        self.doc = self.dir / "doc.json"
        self.file = self.dir / "findings" / "job.jsonl"

    def stub_env(self, document: str, status: int, path: str | None) -> dict[str, str]:
        """Give a run the stub's answer and the findings file.

        Returns:
            The environment.

        """
        self.doc.write_text(document, encoding="utf-8")
        return hermetic.env(
            PATH=path or f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            ORG_FINDINGS=str(self.file),
            MISE_TASK_NAME=TASK,
            STUB_LOG=str(self.log),
            STUB_DOC=str(self.doc),
            STUB_RC=str(status),
            PYTHONUTF8="1",
        )


class Adapter(Stubbed):
    """The adapter over a stub pinact."""

    def run_adapter(
        self, document: str, status: int, *args: str, path: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the adapter as a task runs it, the stub answering.

        Returns:
            The finished process.

        """
        return subprocess.run(
            [sys.executable, str(ADAPTER), *(args or (WORKFLOW,))],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.stub_env(document, status, path),
            cwd=self.dir,
            check=False,
        )

    def test_the_quoted_report_is_two_records(self) -> None:
        """The real run's log: two records, the lines in the detail, its 3."""
        done = self.run_adapter(QUOTED, 3)
        same(self, done.returncode, 3, done.stderr)
        same(self, records(self.file), QUOTED_RECORDS)
        same(
            self,
            done.stderr.splitlines(),
            [
                "-       - uses: actions/checkout@v4",
                f"{TASK}: {WORKFLOW}: {MISMATCH}",
                "  line 6",
                f"{TASK}: {WORKFLOW}: {UNPINNED}",
                "  line 7",
            ],
        )
        same(self, done.stdout, f"{TASK}: pinact: 2 finding(s)\n")

    def test_a_clean_report_records_nothing(self) -> None:
        """No results: no record, pinact's 0."""
        done = self.run_adapter(sarif(), 0)
        same(self, done.returncode, 0, done.stderr)
        same(self, records(self.file), [])
        same(self, done.stdout, f"{TASK}: pinact: 0 finding(s)\n")

    def test_another_sarif_version_is_refused_by_name(self) -> None:
        """SARIF 2.2: refused, both versions named, exit 1, no record."""
        done = self.run_adapter(sarif(version="2.2.0"), 0)
        same(self, done.returncode, 1, done.stderr)
        self.assertIn(
            "the report is SARIF 2.2.0; this adapter reads SARIF 2.1.0",
            done.stderr,
        )
        same(self, records(self.file), [])
        same(self, done.stdout, "")

    def test_an_unknown_shape_is_refused_by_name(self) -> None:
        """A log with no `runs`: refused by name, exit 1, no record."""
        done = self.run_adapter('{"version": "2.1.0"}', 3)
        same(self, done.returncode, 1, done.stderr)
        self.assertIn(
            f"{TASK}: pinact exited 3: the output is not pinact's report:"
            " runs is not a list",
            done.stderr,
        )
        same(self, records(self.file), [])
        same(self, done.stdout, "")

    def test_output_that_is_not_json_is_refused_by_name(self) -> None:
        """A human report: refused with pinact's status, exit 1."""
        done = self.run_adapter("ERROR failed to handle a line\n", 3)
        same(self, done.returncode, 1, done.stderr)
        self.assertIn(
            f"{TASK}: pinact exited 3: the output is not pinact's report",
            done.stderr,
        )
        same(self, records(self.file), [])

    def test_a_malformed_result_records_none(self) -> None:
        """One result with no location: refused before any is recorded."""
        bad = result("parse-error", MISMATCH, 6) | {"locations": []}
        done = self.run_adapter(sarif(result("parse-error", "x", 1), bad), 3)
        same(self, done.returncode, 1, done.stderr)
        self.assertIn("runs[0].results[1]: locations is empty", done.stderr)
        same(self, records(self.file), [])

    def test_pinact_s_status_is_the_exit(self) -> None:
        """A 2 from pinact with a clean log exits 2."""
        done = self.run_adapter(sarif(), 2)
        same(self, done.returncode, 2, done.stderr)
        same(self, records(self.file), [])

    def test_arguments_pass_unchanged_after_the_format(self) -> None:
        """The adapter calls `pinact run --format sarif` and the arguments."""
        args = ["--config", "a b.yaml", "--fix=false", WORKFLOW, "x/action.yml"]
        done = self.run_adapter(sarif(), 0, *args)
        same(self, done.returncode, 0, done.stderr)
        same(
            self,
            self.log.read_text(encoding="utf-8").splitlines(),
            ["\t".join(["run", "--format", "sarif", *args])],
        )

    def test_no_pinact_is_refused_by_name(self) -> None:
        """No pinact on PATH: refused by name, exit 1, no record."""
        done = self.run_adapter(sarif(), 0, path=str(self.dir / "empty"))
        same(self, done.returncode, 1, done.stderr)
        self.assertIn(f"{TASK}: pinact is not on PATH", done.stderr)
        same(self, records(self.file), [])

    def test_the_pinned_pinact_offline_gives_the_record(self) -> None:
        """An unpinned action, judged by pinact itself with `--no-api`."""
        if hermetic.optional_tool("pinact") is None:
            self.skipTest("the belt's pinact is not on PATH")
        (self.dir / ".github" / "workflows").mkdir(parents=True)
        (self.dir / WORKFLOW).write_text(
            "on: push\njobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps:\n"
            "      - uses: actions/checkout@v4\n",
            encoding="utf-8",
        )
        done = subprocess.run(
            [sys.executable, str(ADAPTER), "--fix=false", "--no-api", WORKFLOW],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=hermetic.env(
                ORG_FINDINGS=str(self.file), MISE_TASK_NAME=TASK, PYTHONUTF8="1"
            ),
            cwd=self.dir,
            check=False,
        )
        self.assertNotEqual(done.returncode, 0, done.stderr)
        want: list[dict[str, object]] = [
            {
                "v": 1,
                "task": TASK,
                "subject": WORKFLOW,
                "message": "failed to handle a line: action can't be pinned",
                "detail": ["line 6"],
            }
        ]
        same(self, records(self.file), want)


class Task(Stubbed):
    """audit:action-pins' body over the stub, in a planted tracked tree."""

    def run_task(self, document: str, status: int) -> subprocess.CompletedProcess[str]:
        """Plant and track a workflow, then run audit:action-pins' body.

        Returns:
            The finished process.

        """
        root = self.dir / "tree"
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / WORKFLOW).write_text("on: push\n", encoding="utf-8")
        git = [GIT, "-C", str(root)]
        subprocess.run([*git, "init", "-q"], env=hermetic.env(), check=True)
        subprocess.run([*git, "add", "-A"], env=hermetic.env(), check=True)
        config = decoded.load_toml((HERE / "config.toml").read_bytes())
        tasks = decoded.as_object(config["tasks"], "tasks")
        body = decoded.as_str(decoded.as_object(tasks[TASK], TASK)["run"], "run")
        run = hermetic.task_run(TASK, body)
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

    def test_the_quoted_report_is_the_records_and_pinact_s_red(self) -> None:
        """The real run's log: its two records, pinact's 3, one call."""
        done = self.run_task(QUOTED, 3)
        same(self, done.returncode, 3, done.stderr)
        same(self, records(self.file), QUOTED_RECORDS)
        same(self, len(self.log.read_text(encoding="utf-8").splitlines()), 1)

    def test_a_clean_report_is_no_record_and_pinact_s_green(self) -> None:
        """No results: no record, pinact's 0, the adapter's line."""
        done = self.run_task(sarif(), 0)
        same(self, done.returncode, 0, done.stderr)
        same(self, records(self.file), [])
        same(self, done.stdout, f"{TASK}: pinact: 0 finding(s)\n")


if __name__ == "__main__":
    unittest.main()
