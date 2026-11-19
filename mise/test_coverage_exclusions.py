#!/usr/bin/env python3
"""Planted rows for lint:coverage-exclusions (#1522).

One row per marker the lint refuses, each asserting the finding's
file, line and rule, not just the status. Then where a marker does NOT
count: quoted in a string literal it is text (Python by tokenizing,
the other languages by removing quoted text from the line first), and a
tracked `vendor/` tree is never read. The task rows run the body
decoded from the belt's config in a scratch repository, every git call
with a scrubbed environment (#857). The last row is the canon itself.
"""

import os
import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace

hermetic = SimpleNamespace(
    **runpy.run_path(str(Path(__file__).resolve().with_name("hermetic.py")))
)

_HERE = Path(__file__).resolve().parent
HELPER = _HERE / "coverage_exclusions.py"
GIT = shutil.which("git") or "git"
BASH = shutil.which("bash") or "bash"
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


def helper(root: Path, *paths: str) -> subprocess.CompletedProcess[str]:
    """Run the helper over `paths`, relative to `root`.

    Returns:
        The finished process.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    return subprocess.run(
        [sys.executable, str(HELPER)],
        input="\n".join(paths) + "\n",
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


class Markers(unittest.TestCase):
    """The helper over one planted file at a time."""

    def plant(self, name: str, body: str) -> Path:
        """Write one file into a scratch directory.

        Returns:
            The directory.

        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / name).write_text(body, encoding="utf-8")
        return root

    def test_each_marker_is_red_by_file_line_and_rule(self) -> None:
        """Every marker of every measured language is a finding, located."""
        for name, body, rule in MARKERS:
            with self.subTest(file=name, rule=rule, body=body):
                r = helper(self.plant(name, body), name)
                self.assertEqual(r.returncode, 1, r.stderr)
                line = next(
                    i
                    for i, text in enumerate(body.splitlines(), 1)
                    if ("cover" in text or "ignore" in text or "branch" in text)
                )
                self.assertIn(f"{name}:{line}: {rule}\n", r.stderr)
                self.assertIn(
                    "lint:coverage-exclusions: 1 coverage exclusion(s)", r.stderr
                )
                self.assertIn("There is no allow-list.", r.stderr)

    def test_a_quoted_marker_is_text_not_an_exclusion(self) -> None:
        """A marker inside a string literal is not a finding, in any language."""
        for name, body in QUOTED:
            with self.subTest(file=name, body=body):
                r = helper(self.plant(name, body), name)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stderr, "")

    def test_other_languages_are_not_read(self) -> None:
        """A marker in a file of a language the belt does not measure passes."""
        r = helper(self.plant("notes.md", f"{PRAGMA} cover\n"), "notes.md")
        self.assertEqual(r.returncode, 0, r.stderr)


def task_body() -> str:
    """Decode the lint's body from the belt's own config.

    Returns:
        The bash source mise runs.

    """
    config = tomllib.loads((_HERE / "config.toml").read_text(encoding="utf-8"))
    return config["tasks"]["lint:coverage-exclusions"]["run"]


class Task(unittest.TestCase):
    """The task body, in a scratch repository."""

    def repo(self, files: dict[str, str]) -> Path:
        """Make a repository tracking `files`.

        Returns:
            Its path.

        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, body in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(body, encoding="utf-8")
        for args in (["init", "-q"], ["add", "."]):
            # ruff: ignore[subprocess-without-shell-equals-true]
            subprocess.run(
                [GIT, *args], cwd=root, env=hermetic.env(**GIT_CONFIG), check=True
            )
        return root

    @staticmethod
    def run_task(root: Path) -> subprocess.CompletedProcess[str]:
        """Run the body under the interpreter mise uses.

        Returns:
            The finished process.

        """
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [BASH, "-euo", "pipefail", "-c", task_body()],
            cwd=root,
            env=hermetic.env(ORG_BELT_DIR=str(_HERE), **GIT_CONFIG),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_no_measured_language_skips_clean_by_name(self) -> None:
        """A tree with none of the languages says so and exits 0."""
        r = self.run_task(self.repo({"README.md": f"{PRAGMA} cover\n"}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(
            "lint:coverage-exclusions: no Python, Rust, Go, TypeScript or "
            "JavaScript tracked, skipped",
            r.stdout,
        )

    def test_a_tracked_marker_is_red(self) -> None:
        """The body hands tracked source to the helper."""
        r = self.run_task(self.repo({"src/a.py": f"x = 1  {PRAGMA} cover\n"}))
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("src/a.py:1: python: pragma no cover", r.stderr)

    def test_vendor_is_not_read(self) -> None:
        """A marker under vendor/ is outside the lint; the rest is read."""
        r = self.run_task(
            self.repo({
                "vendor/x/a.py": f"x = 1  {PRAGMA} cover\n",
                "src/b.py": "y = 2\n",
            })
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 file(s), no coverage exclusions", r.stdout)


class MissingSibling(unittest.TestCase):
    """The two helpers' own message, where their dead guard used to be.

    Both rows fail before #1522: the `_SPEC is None` guard could never
    run, so a missing sibling gave a raw FileNotFoundError traceback.
    """

    def alone(self, source: Path, relative: str) -> subprocess.CompletedProcess[str]:
        """Run one helper copied into a tree that lacks its sibling.

        Returns:
            The finished process.

        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        target = Path(tmp.name) / relative
        target.parent.mkdir(parents=True)
        shutil.copy(source, target)
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [sys.executable, str(target), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_exit_trap_without_belt_shell(self) -> None:
        """exit_trap.py names belt_shell.py and exits non-zero."""
        r = self.alone(_HERE / "exit_trap.py", "mise/exit_trap.py")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(
            r.stderr, "exit-trap: belt_shell.py did not arrive beside this file\n"
        )

    def test_publish_crates_without_crates_index(self) -> None:
        """publish_crates.py names mise/crates_index.py and exits non-zero."""
        r = self.alone(
            _HERE.parent / "release" / "publish_crates.py", "release/publish_crates.py"
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(
            r.stderr,
            "publish-crates: mise/crates_index.py did not arrive in the canon tree\n",
        )


class Canon(unittest.TestCase):
    """The canon holds its own rule."""

    def test_the_canon_carries_no_coverage_exclusion(self) -> None:
        """Every tracked source file in this repository is clean.

        The row that fails before #1522: seven `_SPEC is None` guards
        carried a coverage pragma.
        """
        root = _HERE.parent
        listing = hermetic.tracked(
            root, ":!vendor/**", "*.py", "*.rs", "*.go", "*.ts", "*.js", **GIT_CONFIG
        )
        r = helper(root, *listing)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
