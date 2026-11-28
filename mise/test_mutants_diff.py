#!/usr/bin/env python3
"""Table tests for what a change touched, mise/mutants_diff.py (#1432).

Every row plants a real git history in scratch and calls the helper's
`main` in this process (hermetic.call_main), so the base, the diff and
the scope come from real git, never a stand-in. Each row asserts what
the helper wrote and printed.

The pinned diff is held against the settings that made cosmic-ray's own
git filter select nothing and exit 0 (measured, #1432): a clone's and a
user's `diff.noprefix`, `diff.mnemonicPrefix` and `color.diff=always`,
planted in the fixture's own config files.

GIT ENVIRONMENT IS SCRUBBED (#857): git exports GIT_DIR to hooks run in
a linked worktree, and a fixture that inherited it would write into the
caller's index when this suite runs from the pre-push hook.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import functools
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override

import hermetic
import mutants_diff as helper

GIT = hermetic.tool("git")
BASE = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"


def lines(want: dict[str, set[int]]) -> dict[str, set[int]]:
    """Type a row's expected changed lines.

    Returns:
        The lines, as `read_lines` gives them.

    """
    return want


class Tree(unittest.TestCase):
    """A scratch repository with a base commit and a change on top."""

    @override
    def setUp(self) -> None:
        """Plant a repository whose first commit is the base."""
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.tree = self.root / "tree"
        self.tree.mkdir()
        self.env = hermetic.env(self.root)
        self.runs = 0
        self.git("init", "-q", "-b", "main")
        self.git("config", "commit.gpgsign", "false")
        self.write("pkg/m.py", BASE)
        self.write("cosmic-ray.toml", "[cosmic-ray]\n")
        self.base = self.commit("base")

    def git(self, *args: str) -> str:
        """Run git in the tree.

        Returns:
            Its output, stripped.

        """
        return subprocess.run(
            [GIT, *args],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def write(self, path: str, text: str) -> None:
        """Write a file of the tree."""
        where = self.tree / path
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text(text, encoding="utf-8")

    def commit(self, message: str) -> str:
        """Commit everything.

        Returns:
            The commit's id.

        """
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def scope(self, language: str = "python", **env: str) -> tuple[int, str, str, Path]:
        """Run `scope` for a language, into a directory of its own.

        Returns:
            The status, stdout, stderr and the directory it wrote.

        """
        self.runs += 1
        out = self.root / f"scope-{self.runs}"
        done = hermetic.call_main(
            lambda: helper.main(["scope", language, str(out)]),
            env=self.env | env,
            cwd=self.tree,
        )
        return done.returncode, done.stdout, done.stderr, out


class Lines(Tree):
    """The changed lines are HEAD's side of the pinned diff."""

    def test_an_edited_line_is_changed(self) -> None:
        """Line 6 edited: only line 6, and only in its file."""
        self.write("pkg/m.py", BASE.replace("a - b", "a - b - 0"))
        self.commit("pr")
        status, out, _, where = self.scope(ORG_MUTANTS_BASE=self.base)
        self.assertEqual(0, status)
        self.assertEqual(
            f"ci:mutants: python: judging the changed lines:"
            f" lines changed since {self.base[:12]}\n",
            out,
        )
        self.assertEqual("diff\n", (where / "scope").read_text(encoding="utf-8"))
        self.assertEqual(lines({"pkg/m.py": {6}}), helper.read_lines(where))

    def test_settings_outside_the_tree_change_nothing(self) -> None:
        """noprefix, mnemonicPrefix and colour: the same selection as none.

        Each setting is planted twice over: in the clone's own config and
        in the user's (the fixture HOME's .gitconfig), where cosmic-ray's
        filter selected nothing and exited 0 (#1432).
        """
        self.write("pkg/m.py", BASE.replace("a - b", "a - b - 0"))
        self.commit("pr")
        _, _, _, plain = self.scope(ORG_MUTANTS_BASE=self.base)
        want = helper.read_lines(plain)
        settings = {
            "diff.noprefix": "true",
            "diff.mnemonicPrefix": "true",
            "color.diff": "always",
        }
        for key, value in settings.items():
            self.git("config", key, value)
            self.git("config", "--global", key, value)
        self.assertIn("always", self.git("config", "--global", "color.diff"))
        _, _, _, planted = self.scope(ORG_MUTANTS_BASE=self.base)
        self.assertEqual(lines({"pkg/m.py": {6}}), want)
        self.assertEqual(want, helper.read_lines(planted))
        self.assertEqual(
            (plain / "patch.diff").read_text(encoding="utf-8"),
            (planted / "patch.diff").read_text(encoding="utf-8"),
        )

    def test_a_deletion_changes_no_line(self) -> None:
        """A line only deleted touches nothing on HEAD's side."""
        self.write("pkg/m.py", BASE.replace("\n\ndef sub", "\ndef sub"))
        self.commit("pr")
        _, _, _, where = self.scope(ORG_MUTANTS_BASE=self.base)
        self.assertEqual(lines({}), helper.read_lines(where))

    def test_a_rename_changes_only_its_edited_line(self) -> None:
        """Git mv plus one edit: the edited line, under the new name."""
        self.git("mv", "pkg/m.py", "pkg/n.py")
        self.write("pkg/n.py", BASE.replace("a + b", "a + b + 0"))
        self.commit("pr")
        _, _, _, where = self.scope(ORG_MUTANTS_BASE=self.base)
        self.assertEqual(lines({"pkg/n.py": {2}}), helper.read_lines(where))

    def test_the_patch_is_written_for_a_tool_that_reads_one(self) -> None:
        """patch.diff is the pinned diff, `a/` and `b/` prefixed."""
        self.write("pkg/m.py", BASE.replace("a - b", "a - b - 0"))
        self.commit("pr")
        self.git("config", "diff.noprefix", "true")
        _, _, _, where = self.scope(ORG_MUTANTS_BASE=self.base)
        patch = (where / "patch.diff").read_text(encoding="utf-8")
        self.assertIn("+++ b/pkg/m.py\n@@ -6 +6 @@", patch)

    def test_a_quoted_name_is_read(self) -> None:
        """A name git quotes in the header is read unquoted."""
        self.write('pkg/a"b.py', "x = 1\n")
        self.commit("pr")
        _, _, _, where = self.scope(ORG_MUTANTS_BASE=self.base)
        self.assertEqual(lines({'pkg/a"b.py': {1}}), helper.read_lines(where))


class Base(Tree):
    """The base is the event's own, or the merge base locally."""

    def test_no_base_judges_the_whole_population(self) -> None:
        """An empty base and git's all-zero commit are no base."""
        for named in ("", "0" * 40):
            with self.subTest(base=named):
                status, out, _, where = self.scope(ORG_MUTANTS_BASE=named)
                self.assertEqual(0, status)
                self.assertEqual(
                    "ci:mutants: python: judging the whole population:"
                    " no base to diff against\n",
                    out,
                )
                self.assertEqual(
                    "whole\n", (where / "scope").read_text(encoding="utf-8")
                )

    def test_a_base_not_in_the_history_is_red_by_name(self) -> None:
        """The event named a commit this history lacks: red, not whole."""
        status, _, err, _ = self.scope(ORG_MUTANTS_BASE="deadbeef" * 5)
        self.assertEqual(1, status)
        self.assertIn(
            f"python: ORG_MUTANTS_BASE={'deadbeef' * 5} names no commit in this"
            " history",
            err,
        )

    def test_locally_the_base_is_the_merge_base_with_the_default_branch(
        self,
    ) -> None:
        """origin/main's history meets HEAD at the base commit."""
        self.write("pkg/m.py", BASE.replace("a - b", "a - b - 0"))
        self.commit("pr")
        self.git("update-ref", "refs/remotes/origin/main", self.base)
        _, out, _, where = self.scope()
        self.assertIn(f"since {self.base[:12]}", out)
        self.assertEqual(lines({"pkg/m.py": {6}}), helper.read_lines(where))

    def test_origin_head_names_the_default_branch(self) -> None:
        """origin/HEAD points at a branch other than main: that branch."""
        self.git("update-ref", "refs/remotes/origin/trunk", self.base)
        self.git(
            "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk"
        )
        self.write("pkg/m.py", BASE.replace("a - b", "a - b - 0"))
        self.commit("pr")
        _, out, _, _ = self.scope()
        self.assertIn(f"since {self.base[:12]}", out)

    def test_locally_no_default_branch_is_no_base(self) -> None:
        """No origin at all: the whole population."""
        _, out, _, _ = self.scope()
        self.assertIn("judging the whole population: no base to diff against", out)


class Whole(Tree):
    """An adoption file or a moved tool judges the whole population."""

    def test_a_touched_adoption_file(self) -> None:
        """cosmic-ray.toml edited: Python's whole population, by name."""
        self.write("cosmic-ray.toml", "[cosmic-ray]\n# edited\n")
        self.commit("pr")
        _, out, _, where = self.scope(ORG_MUTANTS_BASE=self.base)
        self.assertEqual(
            "ci:mutants: python: judging the whole population:"
            " the change touches cosmic-ray.toml\n",
            out,
        )
        self.assertEqual("whole\n", (where / "scope").read_text(encoding="utf-8"))

    def test_another_languages_adoption_file_is_only_a_line(self) -> None:
        """cosmic-ray.toml edited: Rust's scope is still the diff."""
        self.write("cosmic-ray.toml", "[cosmic-ray]\n# edited\n")
        self.commit("pr")
        _, out, _, _ = self.scope("rust", ORG_MUTANTS_BASE=self.base)
        self.assertIn("judging the changed lines", out)

    def test_a_nested_gomuignore(self) -> None:
        """Go's adoption is every tracked .gomuignore."""
        self.write("sub/.gomuignore", "")
        self.commit("pr")
        _, out, _, _ = self.scope("go", ORG_MUTANTS_BASE=self.base)
        self.assertIn("the change touches sub/.gomuignore", out)

    def test_the_tool_moved_in_a_tracked_mise_lock(self) -> None:
        """mise.lock moves pipx:cosmic-ray: whole; another tool: the diff."""
        lock = '[[tools."pipx:cosmic-ray"]]\nversion = "8.9.0"\n'
        other = '[[tools.ruff]]\nversion = "1"\n'
        self.write("mise.lock", lock + other)
        start = self.commit("lock")
        self.write("mise.lock", lock + other.replace('"1"', '"2"'))
        self.commit("other tool")
        _, out, _, _ = self.scope(ORG_MUTANTS_BASE=start)
        self.assertIn("judging the changed lines", out)
        self.write("mise.lock", lock.replace("8.9.0", "8.9.1") + other)
        self.commit("cosmic-ray")
        _, out, _, _ = self.scope(ORG_MUTANTS_BASE=start)
        self.assertIn("the change mise.lock moves pipx:cosmic-ray", out)

    def test_stryker_moved_in_a_package_lock(self) -> None:
        """package-lock.json moves @stryker-mutator/core: TypeScript whole."""
        entry = '{"packages": {"node_modules/@stryker-mutator/core": {"v": "VER"}}}'
        self.write("package-lock.json", entry.replace("VER", "1"))
        start = self.commit("lock")
        self.write("package-lock.json", entry.replace("VER", "2"))
        self.commit("bump")
        _, out, _, _ = self.scope("typescript", ORG_MUTANTS_BASE=start)
        self.assertIn("the change package-lock.json moves @stryker-mutator/*", out)

    def test_stryker_moved_in_a_pnpm_lock(self) -> None:
        """pnpm-lock.yaml spells it `@stryker-mutator/core@<version>`."""
        self.write("pnpm-lock.yaml", "  '@stryker-mutator/core@10.1.1':\n")
        start = self.commit("lock")
        self.write("pnpm-lock.yaml", "  '@stryker-mutator/core@10.2.0':\n")
        self.commit("bump")
        _, out, _, _ = self.scope("typescript", ORG_MUTANTS_BASE=start)
        self.assertIn("the change pnpm-lock.yaml moves @stryker-mutator/*", out)


class Reading(unittest.TestCase):
    """The pure readers, row by row."""

    def test_read_diff_counts_each_hunk(self) -> None:
        """A one-line hunk, a three-line hunk and a pure deletion."""
        text = (
            "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
            "@@ -1 +1 @@\n-a\n+b\n@@ -5,0 +6,3 @@\n+c\n+d\n+e\n@@ -9,2 +11,0 @@\n"
            "--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
        )
        self.assertEqual(lines({"x.py": {1, 6, 7, 8}}), helper.read_diff(text).lines)

    def test_touches_reads_a_span(self) -> None:
        """A span holding a changed line touches; one beside it does not."""
        lines = {"x.py": {11}}
        self.assertTrue(helper.touches(lines, "x.py", 10, 11))
        self.assertFalse(helper.touches(lines, "x.py", 10, 10))
        self.assertFalse(helper.touches(lines, "y.py", 11, 11))
        self.assertTrue(helper.touches(lines, "x.py", 11, 9))

    def test_unquote(self) -> None:
        """Git's C-style quoting, and a plain name left alone."""
        self.assertEqual('b/a"b.py', helper.unquote('"b/a\\"b.py"'))
        self.assertEqual("b/é.py", helper.unquote('"b/\\303\\251.py"'))
        self.assertEqual("b/plain.py", helper.unquote("b/plain.py"))

    def test_usage(self) -> None:
        """A malformed command line is the usage exit."""
        argvs: list[list[str]] = [[], ["scope", "cobol", "out"], ["lines", "x", "y"]]
        for argv in argvs:
            with self.subTest(argv=argv):
                done = hermetic.call_main(functools.partial(helper.main, argv))
                self.assertEqual(2, done.returncode)
                self.assertIn("usage: mutants_diff.py scope", done.stderr)


if __name__ == "__main__":
    unittest.main()
