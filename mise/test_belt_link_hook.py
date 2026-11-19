#!/usr/bin/env python3
"""Table tests for the hook that links a clone to its pin's belt (#1686).

`lefthook/org-hooks.yml`'s post-checkout, post-merge and post-rewrite
jobs share one inline command: read the tracked gate stub's pin, make
the canon tree of exactly that sha in the machine's cache once, and
link `.config/mise/conf.d/belt.toml` and `.config/mise/mise.lock` into
it.

The command under test is the job's own `run:`, read out of the canon's
REAL hook file and run by `sh` in a planted consumer, not a copy of it.
A local bare repository stands in for the canon: the consumer's HOME
carries a git `insteadOf` that sends the stub's GitHub URL to it, and
the cache is a directory of the row's own (XDG_CACHE_HOME).

One row runs the real lefthook, where it is installed, so the command
is proven to survive lefthook's own templating, as the hook runs it.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import hermetic

CANON = Path(__file__).resolve().parent.parent
HOOKS = CANON / "lefthook" / "org-hooks.yml"
GIT = shutil.which("git")
SH = shutil.which("sh")
LEFTHOOK = shutil.which("lefthook")
OWNER_REPO = "example-org/.github"

# A git that fetches the canon's main whatever sha it is asked for: a
# remote answering with the wrong commit.
WRONG_FETCH = """#!/bin/sh
if [ "$3" = fetch ]; then
  dir="$2"; shift 3
  last=""; for a in "$@"; do last="$a"; done
  set -- $(printf '%s\\n' "$@" | sed '$d')
  exec "{git}" -C "$dir" fetch "$@" refs/heads/main
fi
exec "{git}" "$@"
"""


def hook_command() -> str:
    """Read the post-checkout job's folded `run:` as YAML folds it.

    Returns:
        The command: the scalar's lines joined by single spaces.

    Raises:
        AssertionError: the job is not where the hook file puts it.

    """
    lines = HOOKS.read_text(encoding="utf-8").splitlines()
    start = lines.index("      run: &belt >-") + 1
    body = []
    for line in lines[start:]:
        if not line.startswith("        "):
            break
        body.append(line.strip())
    if not body:
        msg = "post-checkout's belt job has no run: body"
        raise AssertionError(msg)
    return " ".join(body)


def run(
    argv: list[str], cwd: Path, env: dict[str, str], *, check: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run one command, its output captured as text, within a minute.

    Returns:
        The run.

    """
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=check,
        timeout=120,
    )


def tool(found: str | None, name: str) -> str:
    """Return a tool's path, or fail by name.

    Returns:
        The path.

    Raises:
        RuntimeError: the tool is not on PATH.

    """
    if found is None:
        msg = f"{name} is not on PATH"
        raise RuntimeError(msg)
    return found


class TheBeltLinkHook(unittest.TestCase):
    """Each row: a bare canon, a consumer pinning it, and the hook's work."""

    def setUp(self) -> None:
        """Plant the canon with two commits, a consumer, HOME and cache."""
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.cache = self.tmp / "cache"
        self.bare = self.tmp / "canon.git"
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.bare))
        self.git(self.bare, "config", "uploadpack.allowAnySHA1InWant", "true")
        (self.home / ".gitconfig").write_text(
            f'[url "file://{self.bare}"]\n'
            f"\tinsteadOf = https://github.com/{OWNER_REPO}\n",
            encoding="utf-8",
        )
        work = self.tmp / "canon"
        self.git(self.tmp, "init", "-q", "-b", "main", str(work))
        (work / "mise").mkdir()
        self.shas = []
        for n in ("1", "2"):
            (work / "mise" / "config.toml").write_text(f"# belt {n}\n", "utf-8")
            (work / "mise" / "mise.lock").write_text(f"# lock {n}\n", "utf-8")
            self.git(work, "add", "-A")
            self.git(work, "commit", "-q", "-m", f"belt {n}")
            self.shas.append(self.git(work, "rev-parse", "HEAD").stdout.strip())
        self.git(work, "push", "-q", str(self.bare), "main")
        self.consumer = self.tmp / "consumer"
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.consumer))
        self.pin(self.shas[0])

    def env(self, **extra: str) -> dict[str, str]:
        """Build the row's environment: its own HOME and cache.

        Returns:
            The environment.

        """
        return hermetic.env(self.home, XDG_CACHE_HOME=str(self.cache), **extra)

    def git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        """Run git in `cwd`, checked.

        Returns:
            The run.

        """
        return run([tool(GIT, "git"), *args], cwd, self.env(), check=True)

    def pin(self, sha: str) -> None:
        """Write the consumer's gate stub, pinning the canon at `sha`."""
        wf = self.consumer / ".github" / "workflows"
        wf.mkdir(parents=True, exist_ok=True)
        (wf / "gate.yml").write_text(
            "jobs:\n  ci:\n"
            f"    uses: {OWNER_REPO}/.github/workflows/ci.yml@{sha} # v1.0.0\n",
            encoding="utf-8",
        )

    def hook(self, **extra: str) -> subprocess.CompletedProcess[str]:
        """Run the job's command in the consumer, as lefthook does.

        Returns:
            The run.

        """
        return run(
            [tool(SH, "sh"), "-c", hook_command()], self.consumer, self.env(**extra)
        )

    def links(self) -> dict[str, str | None]:
        """Read both links in the consumer.

        Returns:
            Link path (relative) to its target, None when absent.

        """
        out: dict[str, str | None] = {}
        for rel in (".config/mise/conf.d/belt.toml", ".config/mise/mise.lock"):
            path = self.consumer / rel
            out[rel] = str(path.readlink()) if path.is_symlink() else None
        return out

    def linked(self, sha: str) -> dict[str, str | None]:
        """Name the links a consumer pinned at `sha` should carry.

        Returns:
            The expected `links()`.

        """
        tree = self.cache / "org-belt" / sha / "mise"
        return {
            ".config/mise/conf.d/belt.toml": str(tree / "config.toml"),
            ".config/mise/mise.lock": str(tree / "mise.lock"),
        }

    def cached(self) -> list[str]:
        """List the cache directory, hidden entries included.

        Returns:
            The names, sorted; empty when there is no cache yet.

        """
        root = self.cache / "org-belt"
        return sorted(p.name for p in root.iterdir()) if root.is_dir() else []

    def test_the_first_run_makes_the_tree_and_both_links(self) -> None:
        """Fetched once, HEAD at the pin, both links into it, one line."""
        done = self.hook()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout,
            f"org-belt: .config/mise links the belt of {OWNER_REPO}@{self.shas[0]}\n",
        )
        self.assertEqual(self.cached(), [self.shas[0]])
        tree = self.cache / "org-belt" / self.shas[0]
        head = self.git(tree, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(head, self.shas[0])
        self.assertEqual(self.links(), self.linked(self.shas[0]))
        self.assertEqual(
            (self.consumer / ".config/mise/conf.d/belt.toml").read_text("utf-8"),
            "# belt 1\n",
        )

    def test_a_second_run_offline_changes_nothing(self) -> None:
        """The remote gone: no fetch, no output, links untouched."""
        self.assertEqual(self.hook().returncode, 0)
        link = self.consumer / ".config/mise/conf.d/belt.toml"
        before = (link.lstat().st_mtime_ns, self.cached(), self.links())
        shutil.rmtree(self.bare)
        done = self.hook()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((done.stdout, done.stderr), ("", ""))
        self.assertEqual(
            (link.lstat().st_mtime_ns, self.cached(), self.links()), before
        )

    def test_a_changed_pin_relinks(self) -> None:
        """The stub moves to the second commit: a second tree, new links."""
        self.assertEqual(self.hook().returncode, 0)
        self.pin(self.shas[1])
        done = self.hook()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout,
            f"org-belt: .config/mise links the belt of {OWNER_REPO}@{self.shas[1]}\n",
        )
        self.assertEqual(self.cached(), sorted(self.shas))
        self.assertEqual(self.links(), self.linked(self.shas[1]))

    def test_two_runs_at_once_leave_one_whole_tree(self) -> None:
        """Both succeed; one tree, nothing left over, nothing nested in it."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            runs = list(pool.map(lambda _: self.hook(), range(2)))
        for done in runs:
            self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.cached(), [self.shas[0]])
        tree = self.cache / "org-belt" / self.shas[0]
        self.assertEqual(sorted(p.name for p in tree.iterdir()), [".git", "mise"])
        self.assertEqual(self.git(tree, "status", "--porcelain").stdout, "")
        self.assertEqual(self.links(), self.linked(self.shas[0]))

    def test_a_fetch_that_is_not_the_pin_is_refused(self) -> None:
        """The remote answers with another commit: red, nothing linked."""
        stubs = self.tmp / "stubs"
        stubs.mkdir()
        (stubs / "git").write_text(
            WRONG_FETCH.replace("{git}", tool(GIT, "git")), encoding="utf-8"
        )
        (stubs / "git").chmod(0o755)
        done = self.hook(PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(
            done.stderr,
            f"org-belt: fetched {self.shas[1]}, not the pinned {self.shas[0]};"
            " nothing linked\n",
        )
        self.assertEqual(self.cached(), [])
        self.assertEqual(self.links(), dict.fromkeys(self.linked(self.shas[0])))

    def test_a_clone_with_no_pin_does_nothing(self) -> None:
        """No gate stub (the canon itself): no cache, no link, no output."""
        shutil.rmtree(self.consumer / ".github")
        done = self.hook()
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
        self.assertEqual(self.cached(), [])
        self.assertFalse((self.consumer / ".config").exists())

    @unittest.skipIf(LEFTHOOK is None, "lefthook is not installed")
    def test_lefthook_runs_the_job_as_written(self) -> None:
        """Through lefthook itself: its templating leaves the command whole."""
        (self.consumer / "lefthook.yml").write_text(
            HOOKS.read_text(encoding="utf-8"), encoding="utf-8"
        )
        argv = [tool(LEFTHOOK, "lefthook"), "run", "post-checkout"]
        done = run(argv, self.consumer, self.env())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(
            f"org-belt: .config/mise links the belt of {OWNER_REPO}@{self.shas[0]}",
            done.stdout,
        )
        self.assertEqual(self.links(), self.linked(self.shas[0]))


if __name__ == "__main__":
    unittest.main()
