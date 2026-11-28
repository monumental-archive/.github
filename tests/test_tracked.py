"""Rows for the one producer of the tracked-file list (#1986).

`mise/tracked.sh` (`org_tracked`) and `mise/tracked.py` (`tracked`) list
exactly the files git tracks, NUL-separated and unquoted, and the belt's
linters read that list and nothing else. Two defects came from building
the list by hand, one way per task, and both are planted here:

- `lint:typos` walked the directory, so an untracked file (a local
  `cargo mutants` run's `mutants.out/log.txt`) reddened the gate;
- a newline-split, C-quoted list broke on a tracked name holding a space
  (`a b.md` became `a` and `b.md`) or a non-ASCII letter (git quoted
  `é.md` as its octal escapes), and the linter blamed a missing file.

Each row plants one repository, runs the task body as mise does
(`support.task_run`), and asserts its message, not only its status
(#650). The misspellings are assembled at runtime: written out here,
they would red this repository's own `lint:typos`.
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from belt import decoded, tracked
from tests import support
from tests import test_derive_badges as derive

BELT = support.MISE
BASH = support.tool("bash")
# The machine's own bash as well (#1986): on a Mac it is 3.2, which has
# no `declare -n` and no `printf -v` into an array element, and the
# helper fills its array by name.
SYSTEM_BASH = support.optional_tool("bash", path="/bin")
SHELLS = (BASH,) if SYSTEM_BASH in {None, BASH} else (BASH, str(SYSTEM_BASH))
GIT = support.tool("git")

# The three tracked names, and the untracked tool output beside them.
SPACED = "a b.md"
ACCENTED = "é.md"
PLAIN = "plain.md"
NAMES = (SPACED, ACCENTED, PLAIN)
WANT: list[str] = sorted(NAMES)
UNTRACKED = "mutants.out/log.txt"

# A misspelling typos knows, and what it asks for.
TYPO = "t" + "eh"
TYPO_MESSAGE = f"`{TYPO}` should be `the`"

CLEAN_MARKDOWN = "# Title\n\nSome text.\n"

# A git that fails every call, as git does outside a repository.
FAILING_GIT = "#!/bin/sh\necho 'git: planted failure' >&2\nexit 128\n"
FAILING_STUBS = {"git": FAILING_GIT}
# A cargo that answers locate-project with the tree's root manifest.
LOCATING_CARGO = (
    '#!/bin/sh\n[ "$1" = locate-project ] && echo "${PWD}/Cargo.toml"\nexit 0\n'
)


def read_into(
    shell: str, cwd: Path, pathspecs: str, tmpdir: Path
) -> subprocess.CompletedProcess[bytes]:
    """Run `org_tracked_read` in `cwd` and print what it filled, NUL-separated.

    `shell` is the bash it runs under; `tmpdir` is the run's TMPDIR, so
    a row can see the file it leaves.

    Returns:
        The finished run.

    """
    script = (
        f"listed=() && org_tracked_read listed {pathspecs}\n"
        '[[ -z ${listed[0]+set} ]] || printf "%s\\0" "${listed[@]}"\n'
    )
    return subprocess.run(
        [shell, "--norc", "-euo", "pipefail", "-c", script],
        cwd=cwd,
        env=support.env(cwd.parent / "home", TMPDIR=str(tmpdir))
        | {"BASH_ENV": str(BELT / "findings.sh")},
        capture_output=True,
        check=False,
    )


def body(task: str) -> str:
    """Read a task's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    entry = decoded.as_object(tasks[task], task)
    return decoded.as_str(entry["run"], f"{task}'s run")


def plant(tree: Path, files: dict[str, str], untracked: dict[str, str]) -> None:
    """Make `tree` a repository tracking `files`, with `untracked` beside them."""
    env = support.env(tree.parent / "home")
    for name, text in {**files, **untracked}.items():
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
    subprocess.run([GIT, "add", "--", *files], cwd=tree, env=env, check=True)


def run_task(
    task: str,
    files: dict[str, str],
    untracked: dict[str, str] | None = None,
    *,
    shell: str = BASH,
    stubs: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Plant a repository and run one task's body in it.

    `stubs` are commands first on PATH, name to script text: with
    `FAILING_STUBS`, the body meets a `git` that says so on stderr and
    exits 128, as git does outside a repository.
    `shell` replaces the belt's bash, with mise's flags kept.

    Returns:
        The finished run, both streams captured.

    """
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "repo"
        tree.mkdir()
        plant(tree, files, untracked or {})
        env = support.env(Path(tmp) / "home", ORG_BELT_DIR=str(BELT))
        commands = dict(stubs or {})
        if commands:
            where = Path(tmp) / "stubs"
            where.mkdir()
            for name, text in commands.items():
                (where / name).write_text(text, encoding="utf-8")
                (where / name).chmod(0o755)
            env["PATH"] = f"{where}:{env.get('PATH', '')}"
        as_mise = support.task_run(task, body(task))
        return subprocess.run(
            [shell, *as_mise.argv[1:]],
            cwd=tree,
            env=env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


@pytest.mark.usefixtures("_stack")
class TestProducers:
    """org_tracked and tracked.tracked list exactly what git tracks."""

    @pytest.fixture
    def _stack(self, request: pytest.FixtureRequest) -> None:
        """Hold the row's cleanups, undone last first after it ends."""
        self.stack = contextlib.ExitStack()
        request.addfinalizer(self.stack.close)

    def planted(self) -> Path:
        """Plant the three names and the untracked log in a fresh tree.

        Returns:
            The tree.

        """
        scratch = tempfile.TemporaryDirectory()
        self.stack.callback(scratch.cleanup)
        tree = Path(scratch.name) / "repo"
        tree.mkdir()
        plant(tree, dict.fromkeys(NAMES, "x\n"), {UNTRACKED: "x\n"})
        return tree

    def test_org_tracked_prints_the_three_names_unquoted(self) -> None:
        """NUL-separated, a space and a non-ASCII letter kept as on disk."""
        tree = self.planted()
        done = subprocess.run(
            [BASH, "--norc", "-euo", "pipefail", "-c", "org_tracked"],
            cwd=tree,
            env=support.env(tree.parent / "home")
            | {"BASH_ENV": str(BELT / "findings.sh")},
            capture_output=True,
            check=False,
        )
        assert (done.returncode, done.stderr) == (0, b"")
        listed = [n.decode() for n in done.stdout.split(b"\0") if n]
        got: list[str] = sorted(listed)
        assert got == WANT
        assert UNTRACKED not in listed

    @pytest.mark.parametrize("shell", SHELLS)
    def test_org_tracked_read_fills_the_array_with_the_three_names(
        self, shell: object
    ) -> None:
        """One element per name, unquoted; no empty last element; no file left."""
        tree = self.planted()
        with tempfile.TemporaryDirectory() as tmp:
            done = read_into(shell, tree, "", Path(tmp))
            left: list[Path] = list(Path(tmp).iterdir())
        assert (done.returncode, done.stderr) == (0, b"")
        got: list[str] = sorted(n.decode() for n in done.stdout.split(b"\0")[:-1])
        assert got == WANT
        assert list[Path]() == left

    @pytest.mark.parametrize("shell", SHELLS)
    def test_org_tracked_read_of_nothing_is_an_empty_array(self, shell: object) -> None:
        """A pathspec matching nothing: exit 0 and no element at all."""
        tree = self.planted()
        with tempfile.TemporaryDirectory() as tmp:
            done = read_into(shell, tree, "'*.zz'", Path(tmp))
        assert (done.returncode, done.stdout, done.stderr) == (0, b"", b"")

    @pytest.mark.parametrize("shell", SHELLS)
    def test_org_tracked_read_fails_when_git_fails_and_leaves_no_file(
        self, shell: object
    ) -> None:
        """Outside a repository: git's error, exit 1 under errexit, no file."""
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "plain"
            outside.mkdir()
            scratch = Path(tmp) / "tmp"
            scratch.mkdir()
            done = read_into(shell, outside, "", scratch)
            left: list[Path] = list(scratch.iterdir())
        assert done.returncode == 1
        assert done.stdout == b""
        assert b"fatal: not a git repository" in done.stderr
        assert list[Path]() == left

    def test_tracked_lists_the_three_names_from_a_root(self) -> None:
        """The Python producer, the same list, the untracked log absent."""
        tree = self.planted()
        got: list[str] = sorted(tracked.tracked(root=tree))
        assert got == WANT

    def test_tracked_lists_the_working_directory_by_pathspec(self) -> None:
        """No root: the working directory's tree; a pathspec narrows it.

        The call inherits the environment, so it gets the fixture's: the
        suite runs under mise/git_decoy.py (#1463), which sets GIT_DIR
        and GIT_INDEX_FILE at an empty decoy repository, and an
        inheriting call would list the decoy's index. Production carries
        those two only in a git hook (pre-commit runs lint:typos,
        pre-push runs ci), where they name the repository the task runs
        in, so the list is that repository's own.
        """
        tree = self.planted()
        with pytest.MonkeyPatch.context() as patched, contextlib.chdir(tree):
            for name in list(os.environ):
                patched.delenv(name)
            for name, value in (support.env(tree.parent / "home")).items():
                patched.setenv(name, value)
            narrowed: list[str] = tracked.tracked("é*")
            every: list[str] = sorted(tracked.tracked())
        assert list[str]([ACCENTED]) == narrowed
        assert every == WANT

    def test_stdin_paths_reads_a_nul_separated_list(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A name with a space or a newline is one path; empties dropped."""
        with pytest.MonkeyPatch.context() as patched:
            patched.setattr(
                sys, "stdin", io.StringIO(f"{SPACED}\0\0a\nb.md\0{ACCENTED}\0")
            )
            status = len(tracked.stdin_paths()) - 3
        _out, _err = capfd.readouterr()
        assert status == 0

    def test_split_decodes_each_entry_as_a_file_name(self) -> None:
        """Bytes that are not UTF-8 survive as the name os.fsdecode makes."""
        want: list[str] = [SPACED, ACCENTED, "\udcff"]
        assert want == tracked.split(
            SPACED.encode() + b"\0" + ACCENTED.encode() + b"\0\xff\0"
        )


class TestTypos:
    """lint:typos reads the tracked list, never the directory."""

    def test_a_misspelling_in_untracked_tool_output_is_not_read(self) -> None:
        """mutants.out/log.txt is untracked: green, and typos never names it."""
        done = run_task(
            "lint:typos",
            dict.fromkeys(NAMES, CLEAN_MARKDOWN),
            {UNTRACKED: f"rustc: {TYPO} value\n"},
        )
        assert done.returncode == 0, done.stdout + done.stderr
        assert "mutants.out" not in done.stdout + done.stderr
        assert TYPO_MESSAGE not in done.stdout

    def test_a_misspelling_in_a_spaced_name_is_red_by_name(self) -> None:
        """`a b.md` is one file to typos, named whole in the finding."""
        files = dict.fromkeys(NAMES, CLEAN_MARKDOWN)
        files[SPACED] = f"# Title\n\nSome {TYPO} text.\n"
        done = run_task("lint:typos", files)
        assert done.returncode != 0
        assert TYPO_MESSAGE in done.stdout
        assert f"╭▸ {SPACED}:3:6" in done.stdout

    def test_a_misspelt_file_name_is_red(self) -> None:
        """A named file's NAME is still checked."""
        files = dict.fromkeys(NAMES, CLEAN_MARKDOWN)
        files[f"{TYPO}.md"] = CLEAN_MARKDOWN
        done = run_task("lint:typos", files)
        assert done.returncode != 0
        assert TYPO_MESSAGE in done.stdout
        assert f"{TYPO}.md" in done.stdout


class TestMarkdown:
    """lint:markdown hands rumdl one argument per tracked name."""

    @pytest.mark.parametrize("shell", SHELLS)
    def test_spaced_and_accented_names_pass_clean(self, shell: object) -> None:
        """Red at 1c4fbe22 with `File not found: a`; green now, both shells."""
        done = run_task(
            "lint:markdown", dict.fromkeys(NAMES, CLEAN_MARKDOWN), shell=shell
        )
        assert done.returncode == 0, done.stdout + done.stderr
        assert "Success: No issues found in 3 files" in done.stdout

    def test_a_violation_in_the_accented_name_is_red_by_name(self) -> None:
        """The finding names `é.md` as written, never git's octal escapes."""
        files = dict.fromkeys(NAMES, CLEAN_MARKDOWN)
        files[ACCENTED] = "# Title\n\nSome text. \n"
        done = run_task("lint:markdown", files)
        assert done.returncode == 1, done.stdout + done.stderr
        assert f"{ACCENTED}:3:11: [MD009] Trailing space found" in done.stdout
        assert (
            "lint:markdown: findings above — run fix:markdown for the fixable"
            " ones; the rest by hand"
        ) in done.stderr


class TestFailingGit:
    """A listing that fails is red, never "no files, skipped"."""

    @pytest.mark.parametrize("shell", SHELLS)
    def test_a_listing_task_fails_when_git_fails(self, shell: object) -> None:
        """lint:markdown, with git exiting 128: nonzero, git's words, no skip."""
        done = run_task(
            "lint:markdown",
            dict.fromkeys(NAMES, CLEAN_MARKDOWN),
            stubs=FAILING_STUBS,
            shell=shell,
        )
        assert done.returncode != 0
        assert "git: planted failure" in done.stderr
        assert "skipped" not in done.stdout + done.stderr


class TestShell:
    """lint:shell: shfmt --find and shellcheck over spaced and accented names."""

    CLEAN = '#!/usr/bin/env bash\necho "ok"\n'
    SCRIPTS = ("a b.sh", "é.sh")

    def test_spaced_and_accented_scripts_pass_clean(self) -> None:
        """Both found, both linted, the count says two."""
        done = run_task("lint:shell", dict.fromkeys(self.SCRIPTS, self.CLEAN))
        assert done.returncode == 0, done.stdout + done.stderr
        assert "lint:shell: 2 script(s) formatted and linted" in done.stdout

    def test_a_finding_in_the_accented_script_is_red_by_name(self) -> None:
        """An unbraced variable: shellcheck names `é.sh`."""
        files = dict.fromkeys(self.SCRIPTS, self.CLEAN)
        files["é.sh"] = '#!/usr/bin/env bash\nx=1\necho "$x"\n'
        done = run_task("lint:shell", files)
        assert done.returncode != 0
        assert "In é.sh line 3:" in done.stdout
        assert "SC2250" in done.stdout


class TestPython:
    """lint:python hands ruff one argument per tracked name."""

    CLEAN = '"""A module."""\n'
    MODULES = ("a b.py", "é.py")

    def test_spaced_and_accented_modules_pass_clean(self) -> None:
        """Both checked and formatted; ruff finds neither missing."""
        done = run_task("lint:python", dict.fromkeys(self.MODULES, self.CLEAN))
        assert done.returncode == 0, done.stdout + done.stderr
        assert "2 files already formatted" in done.stdout

    def test_a_finding_in_the_spaced_module_is_red_by_name(self) -> None:
        """An unused import (F401): ruff names `a b.py` whole."""
        files = dict.fromkeys(self.MODULES, self.CLEAN)
        files["a b.py"] = '"""A module."""\n\nimport os\n'
        done = run_task("lint:python", files)
        assert done.returncode == 1, done.stdout + done.stderr
        assert (
            "unused-import: [*] `os` imported but unused\n --> a b.py:3:8"
            in done.stdout
        )
        assert (
            "lint:python: findings above — run fix:python for ruff's safe"
            " fixes; the rest by hand"
        ) in done.stderr


def has_status(
    tree: Path, path: str, *, failing_git: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run `org_tracked_has <path>; echo "status $?"` in `tree`.

    Returns:
        The finished run, both streams captured.

    """
    env = support.env(tree.parent / "home") | {
        "BASH_ENV": str(BELT / "findings.sh"),
        "MISE_TASK_NAME": "fix:test",
    }
    if failing_git:
        stubs = tree.parent / "stubs"
        stubs.mkdir(exist_ok=True)
        (stubs / "git").write_text(FAILING_GIT, encoding="utf-8")
        (stubs / "git").chmod(0o755)
        env["PATH"] = f"{stubs}:{env.get('PATH', '')}"
    script = f'org_tracked_has {path} && s=0 || s=$?\necho "status ${{s}}"\n'
    return subprocess.run(
        [BASH, "--norc", "-euo", "pipefail", "-c", script],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.usefixtures("_stack")
class TestHas:
    """org_tracked_has and tracked.has answer from the index (#2013)."""

    @pytest.fixture
    def _stack(self, request: pytest.FixtureRequest) -> None:
        """Hold the row's cleanups, undone last first after it ends."""
        self.stack = contextlib.ExitStack()
        request.addfinalizer(self.stack.close)

    def planted(self) -> Path:
        """Track `kept` and `gone`, delete `gone`, leave `loose` untracked.

        Returns:
            The tree.

        """
        scratch = tempfile.TemporaryDirectory()
        self.stack.callback(scratch.cleanup)
        tree = Path(scratch.name) / "repo"
        tree.mkdir()
        plant(tree, {"kept": "x\n", "gone": "x\n"}, {"loose": "x\n"})
        (tree / "gone").unlink()
        return tree

    @pytest.mark.parametrize(("path", "want"), [("kept", 0), ("loose", 1), ("gone", 0)])
    def test_the_shell_answers_from_the_index(self, path: object, want: object) -> None:
        """Tracked 0, untracked on disk 1, tracked and deleted 0."""
        tree = self.planted()
        done = has_status(tree, path)
        assert (f"status {want}\n", "") == (done.stdout, done.stderr)

    def test_python_answers_from_the_index(self) -> None:
        """True, False, True: the same three."""
        tree = self.planted()
        got: list[bool] = [tracked.has(p, root=tree) for p in ("kept", "loose", "gone")]
        want: list[bool] = [True, False, True]
        assert want == got

    def test_the_shell_ends_on_a_git_that_cannot_answer(self) -> None:
        """Exit 128: the finding, the shell gone, no status line."""
        tree = self.planted()
        done = has_status(tree, "kept", failing_git=True)
        assert done.returncode == 1
        assert done.stdout == ""
        assert (
            done.stderr
            == "fix:test: git could not say whether kept is tracked (exit 128)\n"
        )

    def test_python_raises_on_a_git_that_cannot_answer(self) -> None:
        """Exit 128 is never False."""
        tree = self.planted()
        stub = tree.parent / "git"
        stub.write_text(FAILING_GIT, encoding="utf-8")
        stub.chmod(0o755)
        with (
            pytest.MonkeyPatch.context() as patched,
            pytest.raises(
                tracked.GitUnanswerableError,
                match=r"^git could not say whether kept is tracked \(exit 128\)$",
            ),
        ):
            patched.setattr(tracked, "GIT", str(stub))
            tracked.has("kept", root=tree)

    def test_a_task_whose_git_fails_is_red_not_skipped(self) -> None:
        """lint:fuzz-build, git exiting 128: the finding, nonzero, no skip."""
        done = run_task(
            "lint:fuzz-build", {"fuzz/Cargo.toml": "[package]\n"}, stubs=FAILING_STUBS
        )
        assert done.returncode == 1
        assert (
            "lint:fuzz-build: git could not say whether fuzz/Cargo.toml is"
            " tracked (exit 128)"
        ) in done.stderr
        assert "skipped" not in done.stdout + done.stderr


class TestUntrackedIsAbsent:
    """A file on disk that git does not track turns nothing on (#2013)."""

    def test_fuzz_build_skips_an_untracked_fuzz_crate(self) -> None:
        """Red at 50c77201: "fuzz/Cargo.lock is not committed"."""
        done = run_task(
            "lint:fuzz-build", {PLAIN: "x\n"}, {"fuzz/Cargo.toml": "[package]\n"}
        )
        assert (done.returncode, done.stdout) == (
            0,
            "lint:fuzz-build: no fuzz/, skipped\n",
        )

    def test_deny_finds_no_deny_toml_where_it_is_untracked(self) -> None:
        """Cargo.lock tracked, deny.toml beside it untracked: refused."""
        done = run_task(
            "lint:deny",
            {"Cargo.toml": "[workspace]\n", "Cargo.lock": "version = 4\n"},
            {"deny.toml": "[graph]\n"},
            # cargo names the root workspace, and does nothing else.
            stubs={"cargo": LOCATING_CARGO},
        )
        assert done.returncode == 1, done.stdout + done.stderr
        assert "lint:deny: Cargo.lock present but no deny.toml" in done.stderr

    def test_pgrx_plan_plans_nothing_for_an_untracked_manifest(self) -> None:
        """An untracked Cargo.toml is no crate: `[]`."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "repo"
            tree.mkdir()
            plant(tree, {PLAIN: "x\n"}, {"Cargo.toml": "[workspace]\n"})
            done = subprocess.run(
                [BASH, str(BELT / "pgrx-plan.sh"), str(BELT.parent)],
                cwd=tree,
                env=support.env(Path(tmp) / "home"),
                capture_output=True,
                text=True,
                check=False,
            )
        assert (done.returncode, done.stdout, done.stderr) == (0, "[]\n", "")

    def test_badges_render_no_coverage_shield_for_an_untracked_floor(self) -> None:
        """Red at 50c77201: refused for a floor no workflow feeds."""
        done, block = derive.render(
            {".coverage-floor": "80\n"}, untracked=(".coverage-floor",)
        )
        assert (done.returncode, done.stderr) == (0, "")
        assert "codecov.io" not in block
