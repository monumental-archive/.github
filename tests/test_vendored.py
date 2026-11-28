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
"""

import hashlib
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from tests import support

CANON = support.CANON
SCRIPT = CANON / "mise" / "vendored.sh"

BASH = support.tool("bash")
GIT = support.tool("git")

COMMIT = "90dacfc6c25f151fc3d48fdba150c677e9dddf1d"
SKILL = b"# upstream skill\n"
REFERENCE = b"# upstream reference\n"


def clean_env(*, env: support.Env) -> dict[str, str]:
    """Build the caller's environment with git's own variables removed.

    Returns:
        A copy of os.environ carrying no GIT_* variable (#857).

    """
    return env()


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

    def __init__(
        self, root: str, files: dict[str, bytes | str], *, env: support.Env
    ) -> None:
        """Write every file, then track all of them."""
        self.make_env = env
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
        subprocess.run(
            [GIT, *args],
            cwd=self.root,
            env=clean_env(env=self.make_env),
            check=True,
            capture_output=True,
        )

    def lint(self) -> subprocess.CompletedProcess[str]:
        """Run the real check over this tree.

        Returns:
            The completed process; rows assert on output and status.

        """
        return subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=self.root,
            env=clean_env(env=self.make_env),
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


def run_tree(
    files: dict[str, bytes | str], *, env: support.Env
) -> subprocess.CompletedProcess[str]:
    """Lay out `files` in a fresh repository and lint it.

    Returns:
        The completed process.

    """
    with TemporaryDirectory() as d:
        return Tree(d, files, env=env).lint()


# Every branch the issue names, red with its own message or green.
def assert_red(result: subprocess.CompletedProcess[str], message: str) -> None:
    """Red, with `message` on stderr."""
    assert result.returncode == 1, result.stdout + result.stderr
    assert message in result.stderr


def test_no_vendor_directory_skips_clean(env: support.Env) -> None:
    """A linter that cannot skip cannot be universal."""
    result = run_tree({"README.md": "ours\n"}, env=env)
    assert result.returncode == 0, result.stderr
    assert (
        result.stdout
        == "lint:vendored: no tracked file under a vendor/ directory, skipped\n"
    )


def test_an_untracked_vendor_tree_skips_clean(env: support.Env) -> None:
    """Tracked files only: a vendor/ git does not hold is not ours to ask."""
    with TemporaryDirectory() as d:
        tree = Tree(d, {"README.md": "ours\n"}, env=env)
        (tree.root / "vendor").mkdir()
        (tree.root / "vendor" / "stray.md").write_text("untracked\n")
        result = tree.lint()
    assert result.returncode == 0, result.stderr
    assert "skipped" in result.stdout


def test_a_correct_tree_passes_and_counts(env: support.Env) -> None:
    """Two files listed and matching; the manifest is not counted."""
    result = run_tree(skill_tree(), env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "lint:vendored: 2 files verified against 1 manifests\n"


def test_an_unlisted_file_reds(env: support.Env) -> None:
    """Every file is covered, not only `.md` — the old check's gap."""
    result = run_tree(
        skill_tree({"vendor/atlas/extra/run.sh": b"#!/bin/sh\n"}), env=env
    )
    assert_red(
        result,
        "lint:vendored: vendor/atlas/extra/run.sh is tracked but not listed "
        "in vendor/atlas/PINNED.toml",
    )


def test_a_listed_file_that_is_missing_reds(env: support.Env) -> None:
    """Listed and not tracked: the manifest describes a tree that is not here."""
    result = run_tree(skill_tree({"vendor/atlas/references/lint.md": None}), env=env)
    assert_red(
        result,
        "lint:vendored: vendor/atlas/references/lint.md is listed in "
        "vendor/atlas/PINNED.toml but not tracked under it",
    )


def test_a_drifted_file_reds(env: support.Env) -> None:
    """Edited in place: the bytes are no longer the pinned commit's."""
    result = run_tree(skill_tree({"vendor/atlas/SKILL.md": b"# edited\n"}), env=env)
    assert_red(
        result,
        "lint:vendored: vendor/atlas/SKILL.md does not match its sha256 in "
        "vendor/atlas/PINNED.toml",
    )


def test_a_short_commit_reds(env: support.Env) -> None:
    """Seven characters is a prefix, and can come to name another commit."""
    result = run_tree(
        skill_tree({MANIFEST: manifest(PINNED, commit=COMMIT[:7])}), env=env
    )
    assert_red(
        result,
        "lint:vendored: vendor/atlas/PINNED.toml pins commit '90dacfc' — a full "
        "40-character commit SHA is required; a branch, a tag or a short SHA moves",
    )


def test_a_branch_reds(env: support.Env) -> None:
    """A branch name is the moving ref the pin exists to refuse."""
    result = run_tree(
        skill_tree({MANIFEST: manifest(PINNED, commit="master")}), env=env
    )
    assert_red(result, "pins commit 'master' — a full 40-character")


@pytest.mark.parametrize("key", ["repo", "commit", "upstream_path"])
def test_a_missing_key_reds(key: str, env: support.Env) -> None:
    """repo, commit and upstream_path are each required."""
    result = run_tree(skill_tree({MANIFEST: manifest(PINNED, drop=key)}), env=env)
    assert_red(
        result,
        f"lint:vendored: vendor/atlas/PINNED.toml is missing required key '{key}'",
    )


def test_a_missing_manifest_reds(env: support.Env) -> None:
    """A vendored file with nothing covering it is pinned to nothing."""
    result = run_tree(skill_tree({"vendor/atlas/PINNED.toml": None}), env=env)
    assert_red(
        result,
        "lint:vendored: vendor/atlas/SKILL.md has no PINNED.toml in its "
        "directory or any ancestor under vendor/ — vendored text must be "
        "pinned to an upstream commit",
    )


def test_a_malformed_files_entry_reds(env: support.Env) -> None:
    """A [files] line that does not parse is never silently skipped."""
    bad = manifest(PINNED).replace(
        f'"SKILL.md" = "{sha(SKILL)}"', '"SKILL.md" = "not-a-sha"'
    )
    result = run_tree(skill_tree({"vendor/atlas/PINNED.toml": bad}), env=env)
    assert_red(
        result,
        "lint:vendored: vendor/atlas/PINNED.toml:8 is not a "
        '"<path>" = "<sha256>" entry',
    )


def test_the_nearest_manifest_covers_and_an_ancestor_one_reaches_down(
    env: support.Env,
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
        }),
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "lint:vendored: 5 files verified against 2 manifests\n"


def test_a_nested_vendor_directory_is_bounded_by_its_own_root(env: support.Env) -> None:
    """A manifest above vendor/ is outside it and covers nothing inside."""
    result = run_tree(
        {
            "web/PINNED.toml": manifest({"vendor/lib.js": SKILL}),
            "web/vendor/lib.js": SKILL,
        },
        env=env,
    )
    assert_red(
        result,
        "lint:vendored: web/vendor/lib.js has no PINNED.toml in its "
        "directory or any ancestor under web/vendor/",
    )


def test_the_summary_on_red_counts_what_did_verify(env: support.Env) -> None:
    """One bad file does not hide how much of the tree held."""
    result = run_tree(skill_tree({"vendor/atlas/SKILL.md": b"# edited\n"}), env=env)
    assert_red(
        result,
        "lint:vendored: 1 files verified against 1 manifests, 1 problems above",
    )
