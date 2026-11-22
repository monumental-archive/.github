#!/usr/bin/env python3
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
(`hermetic.task_run`), and asserts its message, not only its status
(#650). The misspellings are assembled at runtime: written out here,
they would red this repository's own `lint:typos`.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import contextlib
import subprocess
import tempfile
import unittest
from pathlib import Path

import decoded
import hermetic
import tracked

BELT = Path(__file__).resolve().parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")

# The three tracked names, and the untracked tool output beside them.
SPACED = "a b.md"
ACCENTED = "é.md"
PLAIN = "plain.md"
NAMES = (SPACED, ACCENTED, PLAIN)
UNTRACKED = "mutants.out/log.txt"

# A misspelling typos knows, and what it asks for.
TYPO = "t" + "eh"
TYPO_MESSAGE = f"`{TYPO}` should be `the`"

CLEAN_MARKDOWN = "# Title\n\nSome text.\n"


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
    env = hermetic.env(tree.parent / "home")
    for name, text in {**files, **untracked}.items():
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
    subprocess.run([GIT, "add", "--", *files], cwd=tree, env=env, check=True)


def run_task(
    task: str, files: dict[str, str], untracked: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Plant a repository and run one task's body in it.

    Returns:
        The finished run, both streams captured.

    """
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "repo"
        tree.mkdir()
        plant(tree, files, untracked or {})
        as_mise = hermetic.task_run(task, body(task))
        return subprocess.run(
            as_mise.argv,
            cwd=tree,
            env=hermetic.env(Path(tmp) / "home", ORG_BELT_DIR=str(BELT)) | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


class Producers(unittest.TestCase):
    """org_tracked and tracked.tracked list exactly what git tracks."""

    def planted(self) -> Path:
        """Plant the three names and the untracked log in a fresh tree.

        Returns:
            The tree.

        """
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
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
            env=hermetic.env(tree.parent / "home")
            | {"BASH_ENV": str(BELT / "findings.sh")},
            capture_output=True,
            check=False,
        )
        self.assertEqual((0, b""), (done.returncode, done.stderr))
        listed = [n.decode() for n in done.stdout.split(b"\0") if n]
        self.assertEqual(sorted(NAMES), sorted(listed))
        self.assertNotIn(UNTRACKED, listed)

    def test_tracked_lists_the_three_names_from_a_root(self) -> None:
        """The Python producer, the same list, the untracked log absent."""
        tree = self.planted()
        self.assertEqual(sorted(NAMES), sorted(tracked.tracked(root=tree)))

    def test_tracked_lists_the_working_directory_by_pathspec(self) -> None:
        """No root: the working directory's tree; a pathspec narrows it."""
        tree = self.planted()
        with contextlib.chdir(tree):
            self.assertEqual([ACCENTED], tracked.tracked("é*"))
            self.assertEqual(sorted(NAMES), sorted(tracked.tracked()))

    def test_stdin_paths_reads_a_nul_separated_list(self) -> None:
        """A name with a space or a newline is one path; empties dropped."""
        done = hermetic.call_main(
            lambda: len(tracked.stdin_paths()) - 3,
            stdin=f"{SPACED}\0\0a\nb.md\0{ACCENTED}\0",
        )
        self.assertEqual(0, done.returncode)

    def test_split_decodes_each_entry_as_a_file_name(self) -> None:
        """Bytes that are not UTF-8 survive as the name os.fsdecode makes."""
        self.assertEqual(
            [SPACED, ACCENTED, "\udcff"],
            tracked.split(SPACED.encode() + b"\0" + ACCENTED.encode() + b"\0\xff\0"),
        )


class Typos(unittest.TestCase):
    """lint:typos reads the tracked list, never the directory."""

    def test_a_misspelling_in_untracked_tool_output_is_not_read(self) -> None:
        """mutants.out/log.txt is untracked: green, and typos never names it."""
        done = run_task(
            "lint:typos",
            dict.fromkeys(NAMES, CLEAN_MARKDOWN),
            {UNTRACKED: f"rustc: {TYPO} value\n"},
        )
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("mutants.out", done.stdout + done.stderr)
        self.assertNotIn(TYPO_MESSAGE, done.stdout)

    def test_a_misspelling_in_a_spaced_name_is_red_by_name(self) -> None:
        """`a b.md` is one file to typos, named whole in the finding."""
        files = dict.fromkeys(NAMES, CLEAN_MARKDOWN)
        files[SPACED] = f"# Title\n\nSome {TYPO} text.\n"
        done = run_task("lint:typos", files)
        self.assertNotEqual(0, done.returncode)
        self.assertIn(TYPO_MESSAGE, done.stdout)
        self.assertIn(f"╭▸ {SPACED}:3:6", done.stdout)

    def test_a_misspelt_file_name_is_red(self) -> None:
        """A named file's NAME is still checked."""
        files = dict.fromkeys(NAMES, CLEAN_MARKDOWN)
        files[f"{TYPO}.md"] = CLEAN_MARKDOWN
        done = run_task("lint:typos", files)
        self.assertNotEqual(0, done.returncode)
        self.assertIn(TYPO_MESSAGE, done.stdout)
        self.assertIn(f"{TYPO}.md", done.stdout)


class Markdown(unittest.TestCase):
    """lint:markdown hands rumdl one argument per tracked name."""

    def test_spaced_and_accented_names_pass_clean(self) -> None:
        """Red at 1c4fbe22 with `File not found: a`; green now."""
        done = run_task("lint:markdown", dict.fromkeys(NAMES, CLEAN_MARKDOWN))
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("File not found", done.stdout + done.stderr)

    def test_a_violation_in_the_accented_name_is_red_by_name(self) -> None:
        """The finding names `é.md` as written, never git's octal escapes."""
        files = dict.fromkeys(NAMES, CLEAN_MARKDOWN)
        files[ACCENTED] = "# Title\n\nSome text. \n"
        done = run_task("lint:markdown", files)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn(f"{ACCENTED}:3:11: [MD009] Trailing space found", done.stdout)
        self.assertIn(
            "lint:markdown: findings above — run fix:markdown for the fixable"
            " ones; the rest by hand",
            done.stderr,
        )


class Shell(unittest.TestCase):
    """lint:shell: shfmt --find and shellcheck over spaced and accented names."""

    CLEAN = '#!/usr/bin/env bash\necho "ok"\n'
    SCRIPTS = ("a b.sh", "é.sh")

    def test_spaced_and_accented_scripts_pass_clean(self) -> None:
        """Both found, both linted, the count says two."""
        done = run_task("lint:shell", dict.fromkeys(self.SCRIPTS, self.CLEAN))
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("lint:shell: 2 script(s) formatted and linted", done.stdout)

    def test_a_finding_in_the_accented_script_is_red_by_name(self) -> None:
        """An unbraced variable: shellcheck names `é.sh`."""
        files = dict.fromkeys(self.SCRIPTS, self.CLEAN)
        files["é.sh"] = '#!/usr/bin/env bash\nx=1\necho "$x"\n'
        done = run_task("lint:shell", files)
        self.assertNotEqual(0, done.returncode)
        self.assertIn("In é.sh line 3:", done.stdout)
        self.assertIn("SC2250", done.stdout)


class Python(unittest.TestCase):
    """lint:python hands ruff one argument per tracked name."""

    CLEAN = '"""A module."""\n'
    MODULES = ("a b.py", "é.py")

    def test_spaced_and_accented_modules_pass_clean(self) -> None:
        """Both checked and formatted; ruff finds neither missing."""
        done = run_task("lint:python", dict.fromkeys(self.MODULES, self.CLEAN))
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("2 files already formatted", done.stdout)

    def test_a_finding_in_the_spaced_module_is_red_by_name(self) -> None:
        """An unused import (F401): ruff names `a b.py` whole."""
        files = dict.fromkeys(self.MODULES, self.CLEAN)
        files["a b.py"] = '"""A module."""\n\nimport os\n'
        done = run_task("lint:python", files)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn(
            "unused-import: [*] `os` imported but unused\n --> a b.py:3:8", done.stdout
        )
        self.assertIn(
            "lint:python: findings above — run fix:python for ruff's safe"
            " fixes; the rest by hand",
            done.stderr,
        )


if __name__ == "__main__":
    unittest.main()
