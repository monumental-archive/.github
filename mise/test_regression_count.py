#!/usr/bin/env python3
"""Tests for regression-count.py, the fix-commit classifier (#1335).

Every row builds a real repository in a temporary directory, commits
into it, and asks the classifier what it sees: the classifier's input
is git history, so a hand-written diff would test a parser of a format
git never produces.
"""

import contextlib
import importlib.util
import io
import os
import pathlib
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from typing import ClassVar
from unittest import mock

_HERMETIC = importlib.util.spec_from_file_location(
    "hermetic", pathlib.Path(__file__).resolve().with_name("hermetic.py")
)
if _HERMETIC is None or _HERMETIC.loader is None:  # pragma: no cover
    msg = "cannot load mise/hermetic.py"
    raise RuntimeError(msg)
hermetic = importlib.util.module_from_spec(_HERMETIC)
sys.modules.setdefault("hermetic", hermetic)
_HERMETIC.loader.exec_module(hermetic)


_SPEC = importlib.util.spec_from_file_location(
    "regression_count", pathlib.Path(__file__).with_name("regression-count.py")
)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - import plumbing
    msg = "cannot load regression-count.py"
    raise RuntimeError(msg)
rc = importlib.util.module_from_spec(_SPEC)
sys.modules["regression_count"] = rc
_SPEC.loader.exec_module(rc)

# A git with no user or system config: no signing hook, no template,
# no default branch preference leaks in from the machine running this.
# Every inherited GIT_* goes too (#857): under the pre-push hook git
# exports GIT_DIR and friends, and a row would then read the real
# repository instead of the one it built.
_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


GIT = shutil.which("git") or "git"


def run_git(cwd: pathlib.Path | str, *args: str) -> str:
    """Run git in `cwd`, failing the row if git fails.

    Returns:
        git's standard output.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    return subprocess.run(
        [GIT, "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


class Repo:
    """A throwaway repository the rows commit into."""

    def __init__(self, root: pathlib.Path) -> None:
        """Initialise an empty repository at `root` with one base commit."""
        self.root = root
        self.git("init", "--quiet", "--initial-branch=main")
        self.commit("chore: start", {"README.md": "start\n"})

    def git(self, *args: str) -> str:
        """Run git in the repository.

        Returns:
            git's standard output.

        """
        return run_git(self.root, *args)

    def commit(
        self,
        subject: str,
        files: dict[str, str] | None = None,
        *,
        delete: tuple[str, ...] = (),
    ) -> str:
        """Write `files`, delete `delete`, and commit with `subject`.

        Returns:
            The new commit's sha.

        """
        for name, content in (files or {}).items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            self.git("add", "--", name)
        for name in delete:
            self.git("rm", "--quiet", "--", name)
        self.git("commit", "--quiet", "--allow-empty", "-m", subject)
        return self.git("rev-parse", "HEAD").strip()

    def verdicts(self, **window: str) -> dict[str, tuple[str, str]]:
        """Classify every fix in the window.

        Returns:
            Subject -> (class, reason).

        """
        return {
            c.subject: (c.verdict, c.reason)
            for c in rc.measure(self.root, since=window.get("since"))
        }


class RepoCase(unittest.TestCase):
    """A row with a fresh repository and an isolated git."""

    def setUp(self) -> None:
        """Isolate git's config, then build the repository."""
        clean = hermetic.env()
        patcher = mock.patch.dict(os.environ, {**clean, **_GIT_ENV}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Repo(pathlib.Path(tmp.name))

    def assert_class(self, subject: str, want: str) -> None:
        """Assert the commit with `subject` landed in class `want`."""
        got = self.repo.verdicts()[subject]
        self.assertEqual(got[0], want, f"{subject!r}: {got}")


class TestEachLanguage(RepoCase):
    """One commit of each class per language in the table."""

    LANGUAGES: ClassVar[dict[str, tuple[str, str, str, str, dict[str, str]]]] = {
        # language: (code file, code body, test file, test body,
        #            not-a-code-fix change)
        "python": (
            "tool/helper.py",
            "def f():\n    return 1\n",
            "tool/test_helper.py",
            "def test_f():\n    assert True\n",
            {"tool/requirements.lock": "pkg==2\n"},
        ),
        "go": (
            "cmd/main.go",
            "package main\nfunc f() int { return 1 }\n",
            "cmd/main_test.go",
            "package main\nfunc TestF(t *testing.T) {}\n",
            {"go.sum": "example.com/m v1.0.1 h1:x\n"},
        ),
        "rust": (
            "src/lib.rs",
            "pub fn f() -> u8 { 1 }\n",
            "tests/integration.rs",
            "#[test]\nfn f() {}\n",
            {"Cargo.lock": '[[package]]\nname = "x"\nversion = "2.0.0"\n'},
        ),
        "typescript": (
            "src/app.ts",
            "export const f = (): number => 1;\n",
            "src/app.test.ts",
            "test('f', () => {});\n",
            {"package-lock.json": '{"lockfileVersion": 3}\n'},
        ),
        "javascript": (
            "lib/app.js",
            "export const f = () => 1;\n",
            "lib/app.spec.js",
            "it('f', () => {});\n",
            {"docs/usage.md": "how to\n"},
        ),
    }

    def test_each_class_in_each_language(self) -> None:
        """Test added, no test, and not a code fix, per language."""
        for lang, (code, body, test, test_body, data) in self.LANGUAGES.items():
            self.repo.commit(f"feat: add {lang}", {code: body})
            self.repo.commit(
                f"fix: {lang} with test", {code: body + "# fixed\n", test: test_body}
            )
            self.repo.commit(f"fix: {lang} without test", {code: body + "# again\n"})
            self.repo.commit(f"fix: {lang} lockfile", data)
        got = {
            subject: verdict for subject, (verdict, _) in self.repo.verdicts().items()
        }
        for lang in self.LANGUAGES:
            with self.subTest(language=lang):
                self.assertEqual(got[f"fix: {lang} with test"], rc.TEST_ADDED)
                self.assertEqual(got[f"fix: {lang} without test"], rc.NO_TEST)
                self.assertEqual(got[f"fix: {lang} lockfile"], rc.NOT_CODE)

    def test_test_directories(self) -> None:
        """Any path under tests/ or testdata/ is a test, fixtures included."""
        self.repo.commit("feat: tool", {"tool/run.sh": "echo 1\n"})
        for directory in ("tests", "testdata", "tests/fixtures"):
            with self.subTest(directory=directory):
                subject = f"fix: tool, with {directory}"
                self.repo.commit(
                    subject,
                    {
                        "tool/run.sh": f"echo {directory}\n",
                        f"tool/{directory}/case.txt": "x\n",
                    },
                )
                self.assert_class(subject, rc.TEST_ADDED)

    def test_fixtures_alone_is_not_a_test(self) -> None:
        """A `fixtures` directory outside tests/ holds no tests (#1447).

        The row that fails before #1447: iiif-server's fixture-tool
        configuration, `tools/fixtures/mise.toml`, counted as a test.
        """
        self.repo.commit("feat: tools", {"tools/fixtures/x.toml": "a = 1\n"})
        self.repo.commit("fix: tool config", {"tools/fixtures/x.toml": "a = 2\n"})
        self.assert_class("fix: tool config", rc.NO_TEST)

    def test_rust_test_attribute_in_source(self) -> None:
        """An added `#[test]`-family line makes a `.rs` file a test."""
        self.repo.commit("feat: lib", {"src/lib.rs": "pub fn f() -> u8 { 1 }\n"})
        for attr in ("#[test]", "#[pg_test]", "#[tokio::test]"):
            with self.subTest(attribute=attr):
                subject = f"fix: f, held by {attr}"
                body = f"pub fn f() -> u8 {{ 2 }}\n{attr}\nfn t_{len(attr)}() {{}}\n"
                self.repo.commit(subject, {"src/lib.rs": body})
                self.assert_class(subject, rc.TEST_ADDED)

    def test_rust_attribute_removed_is_not_a_test(self) -> None:
        """Only an ADDED attribute line counts; deleting a test is not one."""
        self.repo.commit(
            "feat: lib", {"src/lib.rs": "pub fn f() {}\n#[test]\nfn t() {}\n"}
        )
        self.repo.commit("fix: drop the test", {"src/lib.rs": "pub fn f() { () }\n"})
        self.assert_class("fix: drop the test", rc.NO_TEST)

    def test_deleted_test_is_not_a_test_added(self) -> None:
        """A fix that deletes a test file and changes code has no test."""
        self.repo.commit(
            "feat: f", {"a.py": "x = 1\n", "test_a.py": "def test(): pass\n"}
        )
        self.repo.commit(
            "fix: f, test dropped", {"a.py": "x = 2\n"}, delete=("test_a.py",)
        )
        self.assert_class("fix: f, test dropped", rc.NO_TEST)

    def test_dotfile_named_like_a_test_is_not_one(self) -> None:
        """`.test.yml` is a hidden config, not `x.test.yml`."""
        self.assertFalse(rc.is_test_path(".test.yml"))
        self.assertTrue(rc.is_test_path("a/b.test.yml"))


class TestNotACodeFix(RepoCase):
    """Renovate's scopes, pins, docs and data are not code fixes."""

    def test_renovate_scopes(self) -> None:
        """fix(deps) and fix(canon) are Renovate's, whatever they touch."""
        self.repo.commit("fix(deps): update x", {"src/a.py": "x = 2\n"})
        self.repo.commit("fix(canon): update canon", {"src/b.py": "y = 2\n"})
        got = self.repo.verdicts()
        self.assertEqual(
            got["fix(deps): update x"], (rc.NOT_CODE, "Renovate scope fix(deps)")
        )
        self.assertEqual(got["fix(canon): update canon"][0], rc.NOT_CODE)

    def test_other_scopes_are_not_renovate(self) -> None:
        """A scope that only contains `deps` is an ordinary fix."""
        self.repo.commit("fix(depsolver): x", {"src/a.py": "x = 2\n"})
        self.assert_class("fix(depsolver): x", rc.NO_TEST)

    def test_pin_only_change_to_code_file(self) -> None:
        """A workflow whose only change is a SHA and its version comment."""
        old = "    uses: a/b@" + "1" * 40 + " # v1.2.3\n"
        new = "    uses: a/b@" + "2" * 40 + " # v1.3.0\n"
        self.repo.commit("ci: add", {"wf.yml": "steps:\n" + old})
        self.repo.commit("fix: bump the action", {"wf.yml": "steps:\n" + new})
        self.assert_class("fix: bump the action", rc.NOT_CODE)

    def test_pin_and_logic_is_code(self) -> None:
        """A pin moved together with a changed line is a code change."""
        self.repo.commit("ci: add", {"wf.yml": "a: v1.0.0\nb: on\n"})
        self.repo.commit("fix: bump and change", {"wf.yml": "a: v1.1.0\nb: off\n"})
        self.assert_class("fix: bump and change", rc.NO_TEST)

    def test_docs_only(self) -> None:
        """Prose is not code."""
        self.repo.commit("fix: typo", {"README.md": "started\n"})
        self.assert_class("fix: typo", rc.NOT_CODE)

    def test_non_fix_types_are_not_counted(self) -> None:
        """feat, chore, test and a revert of a fix are outside the count."""
        self.repo.commit("feat: f", {"a.py": "1\n"})
        self.repo.commit("test: f", {"test_a.py": "1\n"})
        self.repo.commit('Revert "fix: f"', {"a.py": "2\n"})
        self.repo.commit("fixup: f", {"a.py": "3\n"})
        self.assertEqual(self.repo.verdicts(), {})


class TestMergeAndUnclassifiable(RepoCase):
    """The commits a naive walk would drop or double."""

    def test_merge_of_mixed_paths(self) -> None:
        """A fix-typed merge is judged on its diff against the first parent."""
        self.repo.git("switch", "--quiet", "-c", "topic")
        self.repo.commit("wip: code", {"src/a.go": "package a\n"})
        self.repo.commit("wip: test", {"src/a_test.go": "package a\n"})
        self.repo.commit("wip: docs", {"docs/a.md": "a\n"})
        self.repo.git("switch", "--quiet", "main")
        self.repo.commit("chore: elsewhere", {"other.md": "o\n"})
        self.repo.git(
            "merge", "--quiet", "--no-ff", "-m", "fix: merge the topic", "topic"
        )
        got = self.repo.verdicts()
        self.assertEqual(set(got), {"fix: merge the topic"})
        self.assertEqual(got["fix: merge the topic"], (rc.TEST_ADDED, "src/a_test.go"))

    def test_merge_without_a_test(self) -> None:
        """The same merge with code and docs only has no test."""
        self.repo.git("switch", "--quiet", "-c", "topic")
        self.repo.commit("wip: code", {"src/a.go": "package a\n", "docs/a.md": "a\n"})
        self.repo.git("switch", "--quiet", "main")
        self.repo.commit("chore: elsewhere", {"other.md": "o\n"})
        self.repo.git(
            "merge", "--quiet", "--no-ff", "-m", "fix: merge the topic", "topic"
        )
        self.assertEqual(
            self.repo.verdicts()["fix: merge the topic"], (rc.NO_TEST, "src/a.go")
        )

    def test_empty_commit_is_listed(self) -> None:
        """A fix with no diff is listed as unclassifiable, not dropped."""
        self.repo.commit("fix: nothing at all")
        self.assertEqual(
            self.repo.verdicts()["fix: nothing at all"],
            (rc.UNCLASSIFIABLE, "empty diff"),
        )

    def test_unknown_path_is_listed(self) -> None:
        """A path no rule places, and nothing else, is unclassifiable."""
        self.repo.commit(
            "fix: the mystery", {"bin/frobnicate": "?\n", "NOTES.md": "n\n"}
        )
        self.assertEqual(
            self.repo.verdicts()["fix: the mystery"],
            (rc.UNCLASSIFIABLE, "no rule places bin/frobnicate"),
        )

    def test_unknown_beside_code_is_no_test(self) -> None:
        """Code is enough to say "no test"; the unknown path does not hide it."""
        self.repo.commit("fix: both", {"bin/frobnicate": "?\n", "a.py": "x = 1\n"})
        self.assert_class("fix: both", rc.NO_TEST)

    def test_root_commit_is_classified(self) -> None:
        """A fix-typed first commit diffs against the empty tree."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            run_git(tmp, "init", "--quiet")
            (root / "a.py").write_text("x = 1\n", encoding="utf-8")
            run_git(tmp, "add", "a.py")
            run_git(tmp, "commit", "--quiet", "-m", "fix: first")
            got = rc.measure(root)
        self.assertEqual(
            [(c.subject, c.verdict) for c in got], [("fix: first", rc.NO_TEST)]
        )


class TestReport(RepoCase):
    """The printed lists, the ratio, and the job summary."""

    def run_main(self, *args: str) -> tuple[int, str]:
        """Run main() against the row's repository.

        Returns:
            (exit status, stdout).

        """
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = rc.main(["--repo", str(self.repo.root), *args])
        return status, out.getvalue()

    def test_empty_range_prints_no_fixes(self) -> None:
        """A window with no fix commits says so and exits 0."""
        self.repo.commit("feat: something", {"a.py": "1\n"})
        status, out = self.run_main("--since", "2999-01-01")
        self.assertEqual(status, 0)
        self.assertIn("test added / (test added + no test) = no fixes", out)

    def test_branch_with_no_commits(self) -> None:
        """A repository whose branch has no commits yet has no fixes."""
        with tempfile.TemporaryDirectory() as tmp:
            run_git(tmp, "init", "--quiet")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                status = rc.main(["--repo", tmp])
        self.assertEqual(status, 0)
        self.assertIn("= no fixes", out.getvalue())

    def test_lists_and_ratio(self) -> None:
        """Every class is printed with its members, and the ratio below."""
        self.repo.commit("fix: a", {"a.py": "1\n", "test_a.py": "1\n"})
        self.repo.commit("fix: b", {"b.py": "1\n"})
        self.repo.commit("fix: c", {"c.py": "1\n"})
        self.repo.commit("fix: d", {"d.md": "1\n"})
        status, out = self.run_main()
        self.assertEqual(status, 0)
        self.assertIn("== test added: 1\n", out)
        self.assertIn("== no test: 2\n", out)
        self.assertIn("== not a code fix: 1\n", out)
        self.assertIn("== unclassifiable: 0\n", out)
        self.assertIn("= 1 of 3 (33.3%)", out)

    def test_summary_lists_only_recent_no_test(self) -> None:
        """The summary carries the window's ratio and the recent fixes only."""
        env = {"GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z"}
        with mock.patch.dict(os.environ, env):
            self.repo.commit("fix: old", {"old.py": "1\n"})
        self.repo.commit("fix: new", {"new.py": "1\n"})
        self.repo.commit("fix: tested", {"t.py": "1\n", "test_t.py": "1\n"})
        with tempfile.TemporaryDirectory() as tmp:
            summary = pathlib.Path(tmp) / "summary.md"
            status, _ = self.run_main(
                "--since",
                "2019-01-01",
                "--recent",
                "2021-01-01",
                "--summary",
                str(summary),
            )
            text = summary.read_text(encoding="utf-8")
        self.assertEqual(status, 0)
        self.assertIn("since 2019-01-01: **1 of 3 (33.3%)**", text)
        self.assertIn("fix: new", text)
        self.assertNotIn("fix: old", text)
        self.assertNotIn("fix: tested", text)

    def test_shallow_clone_is_refused(self) -> None:
        """A shallow clone cuts the window short; the ratio is not measured."""
        for i in range(3):
            self.repo.commit(f"fix: {i}", {f"{i}.py": "1\n"})
        with tempfile.TemporaryDirectory() as tmp:
            run_git(
                tmp, "clone", "--quiet", "--depth=1", f"file://{self.repo.root}", "."
            )
            err = io.StringIO()
            with (
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                status = rc.main(["--repo", tmp])
        self.assertEqual(status, 2)
        self.assertIn("the clone is shallow", err.getvalue())

    def test_not_a_repository(self) -> None:
        """A directory git cannot read is an error, not "no fixes"."""
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                status = rc.main(["--repo", tmp])
        self.assertEqual(status, 2)
        self.assertIn("regression-count: git", err.getvalue())


if __name__ == "__main__":
    unittest.main()
