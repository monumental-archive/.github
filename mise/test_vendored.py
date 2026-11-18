#!/usr/bin/env python3
"""Table tests for lint:vendored, one row per branch, each on its message.

#1160: the belt skips vendor/ on purpose and nothing asked what was
there. mise/vendored.sh is the check; every row below builds a real git
tree, because `git ls-files` is what the script reads, and asserts the
exact message as well as the status — a red scored on exit status alone
can pass for the wrong reason.

GIT ENVIRONMENT IS SCRUBBED, deliberately — see #857. `git` reads
`GIT_DIR` and `GIT_INDEX_FILE` from the environment regardless of `cwd`,
and git exports `GIT_DIR` to hooks run in a linked worktree, so a harness
that inherits it writes its fixture into the caller's real index.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import hashlib
import importlib.util
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_HERMETIC = importlib.util.spec_from_file_location(
    "hermetic", Path(__file__).resolve().with_name("hermetic.py")
)
if _HERMETIC is None or _HERMETIC.loader is None:  # pragma: no cover
    msg = "cannot load mise/hermetic.py"
    raise RuntimeError(msg)
hermetic = importlib.util.module_from_spec(_HERMETIC)
sys.modules.setdefault("hermetic", hermetic)
_HERMETIC.loader.exec_module(hermetic)


CANON = Path(__file__).resolve().parent.parent
SCRIPT = CANON / "mise" / "vendored.sh"

BASH = shutil.which("bash")
GIT = shutil.which("git")

COMMIT = "90dacfc6c25f151fc3d48fdba150c677e9dddf1d"
SKILL = b"# upstream skill\n"
REFERENCE = b"# upstream reference\n"


def clean_env() -> dict[str, str]:
    """Build the caller's environment with git's own variables removed.

    Returns:
        A copy of os.environ carrying no GIT_* variable (#857).

    """
    return hermetic.env()


def sha(data: bytes) -> str:
    """Hash bytes the way the manifest records them.

    Returns:
        The lowercase hex sha256.

    """
    return hashlib.sha256(data).hexdigest()


def manifest(
    files: dict[str, bytes],
    commit: str = COMMIT,
    drop: str = "",
) -> str:
    """Render a PINNED.toml in the private repository's existing format.

    Returns:
        The manifest text, with an [omitted] table that must not matter.

    """
    head = {
        "repo": "https://github.com/ariga/atlas",
        "commit": commit,
        "upstream_path": "skills/atlas",
        "retrieved": "2026-11-13",
    }
    lines = ["# Pinned upstream copy - do NOT edit these files by hand."]
    lines += [f'{k} = "{v}"' for k, v in head.items() if k != drop]
    lines += ["", "[files]"]
    lines += [f'"{p}" = "{sha(b)}"' for p, b in files.items()]
    lines += ["", "[omitted]", '"references/cloud.md" = "not taken"']
    return "\n".join(lines) + "\n"


PINNED = {"SKILL.md": SKILL, "references/lint.md": REFERENCE}
MANIFEST = "vendor/atlas/PINNED.toml"


class Tree:
    """A throwaway repository whose tracked files are exactly `files`."""

    def __init__(self, root: str, files: dict[str, bytes | str]) -> None:
        """Write every file, then track all of them."""
        self.root = Path(root)
        for rel, body in files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(body, str):
                path.write_text(body)
            else:
                path.write_bytes(body)
        self.git("init", "-q", ".")
        self.git("add", "-A")

    def git(self, *args: str) -> None:
        """Run one git command against this tree, and nothing else (#857)."""
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [GIT, *args],
            cwd=self.root,
            env=clean_env(),
            check=True,
            capture_output=True,
        )

    def lint(self) -> subprocess.CompletedProcess[str]:
        """Run the real check over this tree.

        Returns:
            The completed process; rows assert on output and status.

        """
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, str(SCRIPT)],
            cwd=self.root,
            env=clean_env(),
            capture_output=True,
            text=True,
            check=False,
        )


def skill_tree(
    overrides: dict[str, bytes | str | None] | None = None,
) -> dict[str, bytes | str]:
    """Build a correct one-manifest tree, then apply `overrides`.

    A path mapped to None is removed.

    Returns:
        The tree's files, by path.

    """
    files: dict[str, bytes | str] = {
        "README.md": "ours\n",
        "vendor/atlas/PINNED.toml": manifest(PINNED),
        "vendor/atlas/SKILL.md": SKILL,
        "vendor/atlas/references/lint.md": REFERENCE,
    }
    for path, body in (overrides or {}).items():
        if body is None:
            files.pop(path)
        else:
            files[path] = body
    return files


def run_tree(files: dict[str, bytes | str]) -> subprocess.CompletedProcess[str]:
    """Lay out `files` in a fresh repository and lint it.

    Returns:
        The completed process.

    """
    with TemporaryDirectory() as d:
        return Tree(d, files).lint()


class TheTable(unittest.TestCase):
    """Every branch the issue names, red with its own message or green."""

    def assert_red(
        self, result: subprocess.CompletedProcess[str], message: str
    ) -> None:
        """Red, with `message` on stderr."""
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)

    def test_no_vendor_directory_skips_clean(self) -> None:
        """A linter that cannot skip cannot be universal."""
        result = run_tree({"README.md": "ours\n"})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "lint:vendored: no tracked file under a vendor/ directory, skipped\n",
            result.stdout,
        )

    def test_an_untracked_vendor_tree_skips_clean(self) -> None:
        """Tracked files only: a vendor/ git does not hold is not ours to ask."""
        with TemporaryDirectory() as d:
            tree = Tree(d, {"README.md": "ours\n"})
            (tree.root / "vendor").mkdir()
            (tree.root / "vendor" / "stray.md").write_text("untracked\n")
            result = tree.lint()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("skipped", result.stdout)

    def test_a_correct_tree_passes_and_counts(self) -> None:
        """Two files listed and matching; the manifest is not counted."""
        result = run_tree(skill_tree())
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "lint:vendored: 2 files verified against 1 manifests\n", result.stdout
        )

    def test_an_unlisted_file_reds(self) -> None:
        """Every file is covered, not only `.md` — the old check's gap."""
        result = run_tree(skill_tree({"vendor/atlas/extra/run.sh": b"#!/bin/sh\n"}))
        self.assert_red(
            result,
            "lint:vendored: vendor/atlas/extra/run.sh is tracked but not listed "
            "in vendor/atlas/PINNED.toml",
        )

    def test_a_listed_file_that_is_missing_reds(self) -> None:
        """Listed and not tracked: the manifest describes a tree that is not here."""
        result = run_tree(skill_tree({"vendor/atlas/references/lint.md": None}))
        self.assert_red(
            result,
            "lint:vendored: vendor/atlas/references/lint.md is listed in "
            "vendor/atlas/PINNED.toml but not tracked under it",
        )

    def test_a_drifted_file_reds(self) -> None:
        """Edited in place: the bytes are no longer the pinned commit's."""
        result = run_tree(skill_tree({"vendor/atlas/SKILL.md": b"# edited\n"}))
        self.assert_red(
            result,
            "lint:vendored: vendor/atlas/SKILL.md does not match its sha256 in "
            "vendor/atlas/PINNED.toml",
        )

    def test_a_short_commit_reds(self) -> None:
        """Seven characters is a prefix, and can come to name another commit."""
        result = run_tree(skill_tree({MANIFEST: manifest(PINNED, commit=COMMIT[:7])}))
        self.assert_red(
            result,
            "lint:vendored: vendor/atlas/PINNED.toml pins commit '90dacfc' — a full "
            "40-character commit SHA is required; a branch, a tag or a short SHA moves",
        )

    def test_a_branch_reds(self) -> None:
        """A branch name is the moving ref the pin exists to refuse."""
        result = run_tree(skill_tree({MANIFEST: manifest(PINNED, commit="master")}))
        self.assert_red(result, "pins commit 'master' — a full 40-character")

    def test_a_missing_key_reds(self) -> None:
        """repo, commit and upstream_path are each required."""
        for key in ("repo", "commit", "upstream_path"):
            with self.subTest(key=key):
                result = run_tree(skill_tree({MANIFEST: manifest(PINNED, drop=key)}))
                self.assert_red(
                    result,
                    f"lint:vendored: vendor/atlas/PINNED.toml is missing required "
                    f"key '{key}'",
                )

    def test_a_missing_manifest_reds(self) -> None:
        """A vendored file with nothing covering it is pinned to nothing."""
        result = run_tree(skill_tree({"vendor/atlas/PINNED.toml": None}))
        self.assert_red(
            result,
            "lint:vendored: vendor/atlas/SKILL.md has no PINNED.toml in its "
            "directory or any ancestor under vendor/ — vendored text must be "
            "pinned to an upstream commit",
        )

    def test_a_malformed_files_entry_reds(self) -> None:
        """A [files] line that does not parse is never silently skipped."""
        bad = manifest(PINNED).replace(
            f'"SKILL.md" = "{sha(SKILL)}"', '"SKILL.md" = "not-a-sha"'
        )
        result = run_tree(skill_tree({"vendor/atlas/PINNED.toml": bad}))
        self.assert_red(
            result,
            "lint:vendored: vendor/atlas/PINNED.toml:8 is not a "
            '"<path>" = "<sha256>" entry',
        )

    def test_the_nearest_manifest_covers_and_an_ancestor_one_reaches_down(
        self,
    ) -> None:
        """An ancestor's manifest covers a subtree that has none of its own."""
        nested = {"a/SKILL.md": SKILL, "a/deep/ref.md": REFERENCE}
        result = run_tree(
            skill_tree({
                "vendor/atlas/PINNED.toml": manifest({**PINNED, **nested}),
                "vendor/atlas/a/SKILL.md": SKILL,
                "vendor/atlas/a/deep/ref.md": REFERENCE,
                # A second tree with its own manifest: its files are its
                # own, and the outer manifest must not be asked about them.
                "vendor/atlas/b/PINNED.toml": manifest({"x.md": SKILL}),
                "vendor/atlas/b/x.md": SKILL,
            })
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "lint:vendored: 5 files verified against 2 manifests\n", result.stdout
        )

    def test_a_nested_vendor_directory_is_bounded_by_its_own_root(self) -> None:
        """A manifest above vendor/ is outside it and covers nothing inside."""
        result = run_tree({
            "web/PINNED.toml": manifest({"vendor/lib.js": SKILL}),
            "web/vendor/lib.js": SKILL,
        })
        self.assert_red(
            result,
            "lint:vendored: web/vendor/lib.js has no PINNED.toml in its "
            "directory or any ancestor under web/vendor/",
        )

    def test_the_summary_on_red_counts_what_did_verify(self) -> None:
        """One bad file does not hide how much of the tree held."""
        result = run_tree(skill_tree({"vendor/atlas/SKILL.md": b"# edited\n"}))
        self.assert_red(
            result,
            "lint:vendored: 1 files verified against 1 manifests, 1 problems above",
        )


if __name__ == "__main__":
    unittest.main()
