"""Planted rows for lint:coverage-exclusions (#1522).

One row per marker the lint refuses, each asserting the finding's
file, line and rule, not just the status. Then where a marker does NOT
count: quoted in a string literal it is text (Python by tokenizing,
the other languages by removing quoted text from the line first), and a
tracked `vendor/` tree is never read. The task rows run the body
decoded from the belt's config in a scratch repository, every git call
with a scrubbed environment (#857). The last row is the canon itself.
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import coverage_exclusions as ce
from belt import decoded
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:coverage-exclusions"


@pytest.fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


_HERE = support.MISE
HELPER = _HERE / "coverage_exclusions.py"
GIT = support.tool("git")
BASH = support.tool("bash")
# No user or system git config reaches a fixture repository.
GIT_CONFIG = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

# The markers are assembled, never written whole, so this file's own
# comments and strings stay outside the lint's reach by construction.
NO = "no"
PRAGMA = "# pragma: " + NO

MARKERS = [
    ("a.py", f"x = 1  {PRAGMA} cover\n", "python: pragma no cover"),
    ("a.py", f"if x:  {PRAGMA} branch\n    pass\n", "python: pragma no branch"),
    ("a.py", f"x = 1  #pragma:{NO}cover\n", "python: pragma no cover"),
    ("a.rs", "#[coverage(off)]\nfn f() {}\n", "rust: coverage(off)"),
    (
        "a.rs",
        "#[cfg_attr(coverage_nightly, coverage(off))]\nfn f() {}\n",
        "rust: coverage(off)",
    ),
    ("a.rs", "#![coverage(off)]\n", "rust: coverage(off)"),
    ("a.rs", "#[cfg(not(coverage))]\nfn f() {}\n", "rust: cfg(coverage)"),
    ("a.ts", "/* istanbul ignore next */\nf();\n", "script: istanbul ignore"),
    ("a.js", "// c8 ignore next\nf();\n", "script: c8 ignore"),
    ("a.mjs", "/* v8 ignore start */\n", "script: v8 ignore"),
    ("a.go", "//go:build !coverage\n\npackage a\n", "go: build constraint on coverage"),
    ("a.go", "package a\n\nfunc f() {} // coverage:ignore\n", "go: coverage:ignore"),
]

# The same markers, quoted: text in a string, not an exclusion.
QUOTED = [
    ("a.py", f'FIXTURE = "x = 1  {PRAGMA} cover"\n'),
    ("a.py", f'FIXTURE = """\n{PRAGMA} cover\n"""\n'),
    ("a.rs", 'const S: &str = "#[coverage(off)]";\n'),
    ("a.ts", 'const s = "/* istanbul ignore next */";\n'),
    ("a.go", 'package a\n\nvar s = "// coverage:ignore"\n'),
]


def helper(
    root: Path, *paths: str, capfd: pytest.CaptureFixture[str]
) -> tuple[int, str, str]:
    """Call the helper's `main` in this process over `paths`, from `root` (#1696).

    Returns:
        The status `main` returned and what it wrote to each stream.

    """
    with pytest.MonkeyPatch.context() as patched:
        patched.chdir(root)
        patched.setattr(
            sys, "stdin", io.StringIO("".join(f"{path}\0" for path in paths))
        )
        status = ce.main([])
    out, err = capfd.readouterr()
    return status, out, err


@pytest.mark.usefixtures("_stack")
class TestMarkers:
    """The helper over one planted file at a time."""

    @pytest.fixture
    def _stack(self, request: pytest.FixtureRequest) -> None:
        """Hold the row's cleanups, undone last first after it ends."""
        self.stack = contextlib.ExitStack()
        request.addfinalizer(self.stack.close)

    def plant(self, name: str, body: str) -> Path:
        """Write one file into a scratch directory.

        Returns:
            The directory.

        """
        tmp = tempfile.TemporaryDirectory()
        self.stack.callback(tmp.cleanup)
        root = Path(tmp.name)
        (root / name).write_text(body, encoding="utf-8")
        return root

    @pytest.mark.parametrize(("name", "body", "rule"), MARKERS)
    def test_each_marker_is_red_by_file_line_and_rule(
        self,
        name: object,
        body: object,
        rule: object,
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        """Every marker of every measured language is a finding, located."""
        r = helper(self.plant(name, body), name, capfd=capfd)
        assert r.returncode == 1, r.stderr
        line = next(
            i
            for i, text in enumerate(body.splitlines(), 1)
            if ("cover" in text or "ignore" in text or "branch" in text)
        )
        assert f"{name}:{line}: {rule}\n" in r.stderr
        assert "lint:coverage-exclusions: 1 coverage exclusion(s)" in r.stderr
        assert "There is no allow-list." in r.stderr

    @pytest.mark.parametrize(("name", "body"), QUOTED)
    def test_a_quoted_marker_is_text_not_an_exclusion(
        self, name: object, body: object, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A marker inside a string literal is not a finding, in any language."""
        r = helper(self.plant(name, body), name, capfd=capfd)
        assert r.returncode == 0, r.stderr
        assert r.stderr == ""

    def test_other_languages_are_not_read(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A marker in a file of a language the belt does not measure passes."""
        status, out, err = helper(
            self.plant("notes.md", f"{PRAGMA} cover\n"), "notes.md", capfd=capfd
        )
        assert status == 0, err
        assert out == "lint:coverage-exclusions: 1 file(s), no coverage exclusions\n"

    def test_the_helper_runs_as_a_script(self) -> None:
        """The script contract: it reads stdin, reports, and exits 1 on a marker."""
        name, body, rule = MARKERS[0]
        r = subprocess.run(
            [sys.executable, str(HELPER)],
            input=f"{name}\0",
            cwd=self.plant(name, body),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 1, r.stderr
        assert f"{name}:1: {rule}\n" in r.stderr


def task_body() -> str:
    """Decode the lint's body from the belt's own config.

    Returns:
        The bash source mise runs.

    """
    config = decoded.load_toml((_HERE / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    name = "lint:coverage-exclusions"
    return decoded.as_str(decoded.as_object(tasks[name], name)["run"], f"{name}'s run")


@pytest.mark.usefixtures("_stack")
class TestTask:
    """The task body, in a scratch repository."""

    @pytest.fixture
    def _stack(self, request: pytest.FixtureRequest) -> None:
        """Hold the row's cleanups, undone last first after it ends."""
        self.stack = contextlib.ExitStack()
        request.addfinalizer(self.stack.close)

    def repo(self, files: dict[str, str]) -> Path:
        """Make a repository tracking `files`.

        Returns:
            Its path.

        """
        tmp = tempfile.TemporaryDirectory()
        self.stack.callback(tmp.cleanup)
        root = Path(tmp.name)
        for name, body in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(body, encoding="utf-8")
        for args in (["init", "-q"], ["add", "."]):
            subprocess.run(
                [GIT, *args], cwd=root, env=support.env(**GIT_CONFIG), check=True
            )
        return root

    @staticmethod
    def run_task(root: Path) -> subprocess.CompletedProcess[str]:
        """Run the body under the interpreter mise uses.

        Returns:
            The finished process.

        """
        as_mise = support.task_run("lint:coverage-exclusions", task_body())
        return subprocess.run(
            as_mise.argv,
            cwd=root,
            env=support.env(ORG_BELT_DIR=str(_HERE), **GIT_CONFIG) | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_no_measured_language_skips_clean_by_name(self) -> None:
        """A tree with none of the languages says so and exits 0."""
        r = self.run_task(self.repo({"README.md": f"{PRAGMA} cover\n"}))
        assert r.returncode == 0, r.stderr
        assert (
            "lint:coverage-exclusions: no Python, Rust, Go, TypeScript or "
            "JavaScript tracked, skipped"
        ) in r.stdout

    def test_a_tracked_marker_is_red(self) -> None:
        """The body hands tracked source to the helper."""
        r = self.run_task(self.repo({"src/a.py": f"x = 1  {PRAGMA} cover\n"}))
        assert r.returncode == 1, r.stderr
        assert "src/a.py:1: python: pragma no cover" in r.stderr

    def test_vendor_is_not_read(self) -> None:
        """A marker under vendor/ is outside the lint; the rest is read."""
        r = self.run_task(
            self.repo({
                "vendor/x/a.py": f"x = 1  {PRAGMA} cover\n",
                "src/b.py": "y = 2\n",
            })
        )
        assert r.returncode == 0, r.stderr
        assert "1 file(s), no coverage exclusions" in r.stdout


@pytest.mark.usefixtures("_stack")
class TestMissingSibling:
    """The two helpers' own message, where their dead guard used to be.

    Both rows fail before #1522: the `_SPEC is None` guard could never
    run, so a missing sibling gave a raw FileNotFoundError traceback.
    """

    @pytest.fixture
    def _stack(self, request: pytest.FixtureRequest) -> None:
        """Hold the row's cleanups, undone last first after it ends."""
        self.stack = contextlib.ExitStack()
        request.addfinalizer(self.stack.close)

    def alone(
        self, source: Path, relative: str, *beside: str
    ) -> subprocess.CompletedProcess[str]:
        """Run one helper copied into a tree that lacks its sibling.

        `beside` names the files copied with it: the finding writer, for
        a helper that reports the missing sibling through it.

        Returns:
            The finished process.

        """
        tmp = tempfile.TemporaryDirectory()
        self.stack.callback(tmp.cleanup)
        target = Path(tmp.name) / relative
        target.parent.mkdir(parents=True)
        shutil.copy(source, target)
        for name in beside:
            shutil.copy(source.parent / name, target.parent / name)
        return subprocess.run(
            [sys.executable, str(target), "--help"],
            capture_output=True,
            text=True,
            check=False,
            env=support.env(MISE_TASK_NAME="lint:bash-portability"),
        )

    def test_exit_trap_without_belt_shell(self) -> None:
        """exit_trap.py names belt_shell.py and exits non-zero."""
        r = self.alone(
            _HERE / "exit_trap.py",
            "mise/exit_trap.py",
            "findings.py",
            "workflow_command.py",
        )
        assert r.returncode != 0
        assert (
            r.stderr
            == "lint:bash-portability: belt belt-shell did not arrive beside this file\n"
        )

    def test_publish_crates_without_crates_index(self) -> None:
        """publish_crates.py names mise/crates_index.py and exits non-zero."""
        tmp = tempfile.TemporaryDirectory()
        self.stack.callback(tmp.cleanup)
        helper = "release/publish_crates.py"
        # A stub of its own text, so the helper resolves its siblings in
        # this tree; the writer beside it, the missing sibling removed.
        root = support.canon(
            tmp.name, stubs={helper: (_HERE.parent / helper).read_text()}
        )
        (root / "mise" / "crates_index.py").unlink()
        r = subprocess.run(
            [sys.executable, str(root / helper), "--help"],
            capture_output=True,
            text=True,
            check=False,
            env=support.env(MISE_TASK_NAME="lint:bash-portability"),
        )
        assert r.returncode != 0
        assert r.stderr == (
            "lint:bash-portability: mise/crates_index.py did not arrive in the"
            " canon tree\n"
        )


class TestCanon:
    """The canon holds its own rule."""

    def test_the_canon_carries_no_coverage_exclusion(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Every tracked source file in this repository is clean.

        The row that fails before #1522: seven `_SPEC is None` guards
        carried a coverage pragma.
        """
        root = _HERE.parent
        listing = support.tracked(
            root, ":!vendor/**", "*.py", "*.rs", "*.go", "*.ts", "*.js", **GIT_CONFIG
        )
        status, _out, err = helper(root, *listing, capfd=capfd)
        assert status == 0, err
