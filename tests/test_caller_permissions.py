"""Table tests for lint:caller-permissions and its helper (#1911).

Each row plants a git tree and a stub `stele` on PATH that logs its
arguments, says a line on stderr, prints the row's report and exits
with the row's status, then asserts the exit status and every line
printed: the skip when the tree tracks no workflow, an untracked
workflow (still a skip), a passing join end to end, stele missing,
stele's FAIL with its findings and the fix, and a call with no canon or
no workflow. Three rows run the task's own body, read from
mise/config.toml; the rest call the helper's `main` in this process.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

import functools
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import caller_permissions, decoded, stele_findings
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:caller-permissions"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
GIT = support.tool("git")
# The real canon: the task body starts the belt from it, and the stub
# stele reads no policy, so the call names it and nothing reads it.
CANON = str(support.CANON)
STUB = """\
import os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:
    log.write("\\t".join(sys.argv[1:]) + "\\n")
print("stub stele: walking", file=sys.stderr)
with open(os.environ["STUB_DOC"], encoding="utf-8") as doc:
    sys.stdout.write(doc.read())
sys.exit(int(os.environ["STUB_RC"]))
"""
PASS: dict[str, object] = {
    "schema": 9,
    "target": "assert permissions",
    "subject": ".",
    "verdict": "PASS",
    "findings": [],
}
FAIL: dict[str, object] = {
    "schema": 9,
    "target": "assert permissions",
    "subject": ".",
    "verdict": "FAIL",
    "findings": [
        {
            "subject": ".github/workflows/release.yml",
            "assertion": "caller-grant",
            "expected": "id-token: write",
            "actual": "absent",
        }
    ],
}
FIX = [
    "lint:caller-permissions: see above — grant what the pinned canon's",
    "  callee jobs ask for, at the caller's uses: job (#358 seam 2).",
]


def same[T](got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    assert got == want, msg or None


def body() -> str:
    """Read lint:caller-permissions' run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


class Planted:
    """A planted git tree, and a stub stele first on PATH."""

    def __init__(
        self, tmp_path: Path, request: pytest.FixtureRequest, *, env: support.Env
    ) -> None:
        """Plant an empty tree and the stub, which answers PASS."""
        self.make_env = env
        self.tree = tmp_path
        self.bin = Path(tempfile.mkdtemp())
        request.addfinalizer(functools.partial(shutil.rmtree, self.bin))
        stele = self.bin / "stele"
        stele.write_text(f"#!{sys.executable}\n{STUB}", encoding="utf-8")
        stele.chmod(0o755)
        self.log = self.bin / "log"
        self.doc = self.bin / "doc.json"
        base = self.make_env(
            MISE_TASK_NAME=TASK,
            ORG_BELT_DIR=str(BELT),
            ORG_CANON_DIR=CANON,
            ORG_PYTHON=sys.executable,
            STUB_LOG=str(self.log),
            STUB_DOC=str(self.doc),
        )
        self.env = base | {"PATH": f"{self.bin}:{base.get('PATH', '')}"}
        self.answer(PASS, 0)
        self.git("init", "-q")

    def answer(self, doc: dict[str, object], status: int) -> None:
        """Set what the stub prints and exits with."""
        self.doc.write_text(json.dumps(doc), encoding="utf-8")
        self.env["STUB_RC"] = str(status)

    def git(self, *args: str) -> None:
        """Run git in the planted tree."""
        subprocess.run(
            [GIT, *args],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            check=True,
        )

    def plant(self, name: str, *, track: bool = True) -> str:
        """Write one workflow, tracked unless asked otherwise.

        Returns:
            Its path from the tree's top.

        """
        rel = f".github/workflows/{name}"
        path = self.tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("on: push\n", encoding="utf-8")
        if track:
            self.git("add", "--", rel)
        return rel

    def logged(self) -> list[str]:
        """Read the stub's argument log.

        Returns:
            One line per call, its arguments tab-joined.

        """
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    def enter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stand in the planted tree as the task's main meets it."""
        for name, value in self.env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(self.tree)
        # stele as stele_findings resolved it at import, before PATH had the stub.
        monkeypatch.setattr(stele_findings, "STELE", str(self.bin / "stele"))

    def task(self) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree.

        Returns:
            The finished process.

        """
        as_mise = support.task_run(TASK, body())
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


@fixture
def planted(
    tmp_path: Path, request: pytest.FixtureRequest, env: support.Env
) -> Planted:
    """Give each row its own Planted."""
    return Planted(tmp_path, request, env=env)


CALL = "\t".join([
    "assert",
    "permissions",
    "--json",
    "--policy",
    f"{CANON}/slsa/assert-policy.json",
    "--tree",
    CANON,
    "--callers",
    ".",
])
SKIPPED = ["lint:caller-permissions: no workflows, skipped"]


def test_no_workflows_is_skipped_by_name(planted: Planted) -> None:
    """No tracked workflow: the task says it skipped, stele never runs."""
    done = planted.task()
    same(done.returncode, 0, done.stderr)
    same(done.stdout.splitlines(), SKIPPED)
    same(planted.logged(), [])


def test_an_untracked_workflow_is_no_workflow(planted: Planted) -> None:
    """A workflow git does not track: still skipped."""
    planted.plant("ci.yml", track=False)
    done = planted.task()
    same(done.returncode, 0, done.stderr)
    same(done.stdout.splitlines(), SKIPPED)


def test_a_pass_through_the_task(planted: Planted) -> None:
    """A tracked workflow: the body calls stele on the canon tree, PASS."""
    planted.plant("ci.yml")
    done = planted.task()
    same(done.returncode, 0, done.stderr)
    same(done.stdout.splitlines(), ["lint:caller-permissions: stele: PASS"])
    same(done.stderr.splitlines(), ["stub stele: walking"])
    same(planted.logged(), [CALL])


def test_stele_missing_is_red(
    planted: Planted, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No stele on PATH: red by name, nothing run."""
    rel = planted.plant("ci.yml")
    planted.env["PATH"] = str(planted.tree)
    planted.enter(monkeypatch)
    status = caller_permissions.main([CANON, rel])
    out, err = capfd.readouterr()
    same(status, caller_permissions.EXIT_RED)
    same(out, "")
    expected = ["lint:caller-permissions: stele missing from the belt"]
    same(err.splitlines(), expected)
    same(planted.logged(), [])


def test_a_short_grant_is_red_with_the_fix(
    planted: Planted, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A FAIL from stele: its findings, its verdict, then the fix."""
    rel = planted.plant("ci.yml")
    planted.answer(FAIL, 1)
    planted.enter(monkeypatch)
    status = caller_permissions.main([CANON, rel])
    out, err = capfd.readouterr()
    same(status, caller_permissions.EXIT_RED)
    same(out.splitlines(), ["lint:caller-permissions: stele: FAIL"])
    expected = [
        "stub stele: walking",
        "lint:caller-permissions: .github/workflows/release.yml: caller-grant",
        "  expected: id-token: write",
        "  actual: absent",
        *FIX,
    ]
    same(err.splitlines(), expected)
    same(planted.logged(), [CALL])


def test_a_pass_is_quiet(
    planted: Planted, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PASS from stele: its report alone, exit 0."""
    rel = planted.plant("ci.yml")
    planted.enter(monkeypatch)
    status = caller_permissions.main([CANON, rel])
    out, err = capfd.readouterr()
    same(status, 0, err)
    same(out.splitlines(), ["lint:caller-permissions: stele: PASS"])


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param(
            [], "the following arguments are required: CANON_DIR, WORKFLOW", id="none"
        ),
        pytest.param(
            [CANON], "the following arguments are required: WORKFLOW", id="no workflow"
        ),
        pytest.param(["", "ci.yml"], "the canon directory is empty", id="empty canon"),
    ],
)
def test_a_bad_call_is_a_usage_error(
    argv: list[str],
    error: str,
    planted: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No canon, an empty canon, or no workflow: argparse's exit 2, its error."""
    planted.enter(monkeypatch)
    with pytest.raises(SystemExit) as ended:
        caller_permissions.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt caller-permissions "), err
    assert err.splitlines()[-1] == f"belt caller-permissions: error: {error}"
    same(planted.logged(), [])
