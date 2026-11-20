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
LEFTHOOK = hermetic.optional_tool("lefthook")
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


class HookFixture(unittest.TestCase):
    """A bare canon with two commits, a consumer pinning the first, a cache."""

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
        return run([hermetic.tool("git"), *args], cwd, self.env(), check=True)

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
            [hermetic.tool("sh"), "-c", hook_command()],
            self.consumer,
            self.env(**extra),
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
        """List the published trees: the cache's names, `.trees` aside.

        Returns:
            The names, sorted; empty when there is no cache yet.

        """
        root = self.cache / "org-belt"
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.iterdir() if p.name != ".trees")

    def trees(self) -> list[Path]:
        """List the fetched trees under `.trees`.

        Returns:
            Their paths, sorted; empty when none was kept.

        """
        root = self.cache / "org-belt" / ".trees"
        return sorted(root.iterdir()) if root.is_dir() else []

    def excluded(self) -> list[str]:
        """Read the clone's info/exclude lines that name the belt's directory.

        Returns:
            Every `.config/mise/` line.

        """
        exclude = self.consumer / ".git" / "info" / "exclude"
        text = exclude.read_text("utf-8") if exclude.exists() else ""
        return [ln for ln in text.splitlines() if ln == ".config/mise/"]

    def commit_all(self) -> None:
        """Commit the consumer's tree, so its status starts clean."""
        self.git(self.consumer, "add", "-A")
        self.git(self.consumer, "commit", "-q", "-m", "gate stub")


class TheCacheAndTheLinks(HookFixture):
    """Each row: what the hook fetches, publishes and links."""

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
        self.assertTrue(tree.is_symlink())
        self.assertEqual([tree.resolve()], [p.resolve() for p in self.trees()])
        self.assertTrue(self.trees()[0].name.startswith(f"{self.shas[0]}."))
        head = self.git(tree, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(head, self.shas[0])
        self.assertEqual(self.links(), self.linked(self.shas[0]))
        self.assertEqual(
            (self.consumer / ".config/mise/conf.d/belt.toml").read_text("utf-8"),
            "# belt 1\n",
        )
        self.assertEqual(self.excluded(), [".config/mise/"])

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
        self.assertEqual(self.excluded(), [".config/mise/"])

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
        self.assertEqual([tree.resolve()], [p.resolve() for p in self.trees()])
        self.assertEqual(sorted(p.name for p in tree.iterdir()), [".git", "mise"])
        self.assertEqual(self.git(tree, "status", "--porcelain").stdout, "")
        self.assertEqual(self.links(), self.linked(self.shas[0]))

    def test_fifty_pairs_racing_on_the_links_all_succeed(self) -> None:
        """Each pair finds both links missing or stale and replaces them (#1731).

        Fifty pairs, so the race between two hooks replacing one link is
        exercised, not sampled. Even pairs start with no links, odd ones
        with links to the other pin's tree; every hook exits 0 and leaves
        both links whole, with no temporary name beside them.
        """
        self.assertEqual(self.hook().returncode, 0)
        stale = self.linked(self.shas[1])
        conf = self.consumer / ".config" / "mise"
        for pair in range(50):
            for rel in self.linked(self.shas[0]):
                (self.consumer / rel).unlink(missing_ok=True)
                if pair % 2:
                    (self.consumer / rel).symlink_to(stale[rel])
            with ThreadPoolExecutor(max_workers=2) as pool:
                runs = list(pool.map(lambda _: self.hook(), range(2)))
            for done in runs:
                self.assertEqual((pair, done.returncode, done.stderr), (pair, 0, ""))
            self.assertEqual(self.links(), self.linked(self.shas[0]), pair)
            left = sorted(
                p.name for p in [*conf.iterdir(), *(conf / "conf.d").iterdir()]
            )
            self.assertEqual(left, ["belt.toml", "conf.d", "mise.lock"], pair)

    def test_a_killed_hooks_temporary_name_is_removed(self) -> None:
        """A `<link>.<pid>` whose hook is gone is removed by the next run."""
        self.assertEqual(self.hook().returncode, 0)
        conf = self.consumer / ".config" / "mise"
        sh = hermetic.tool("sh")
        gone = run([sh, "-c", "echo $$"], self.tmp, self.env()).stdout.strip()
        for rel in ("conf.d/belt.toml", "mise.lock"):
            (conf / f"{rel}.{gone}").symlink_to(conf / rel)
        done = self.hook()
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(self.links(), self.linked(self.shas[0]))
        left = sorted(p.name for p in [*conf.iterdir(), *(conf / "conf.d").iterdir()])
        self.assertEqual(left, ["belt.toml", "conf.d", "mise.lock"])

    def test_a_sibling_whose_suffix_is_not_a_pid_is_left(self) -> None:
        """Only `<link>.<digits>` is the hook's: `mise.lock.bak` stays."""
        self.assertEqual(self.hook().returncode, 0)
        conf = self.consumer / ".config" / "mise"
        planted = ["belt.toml.bak", "belt.toml.12x", "mise.lock.bak", "mise.lock."]
        for name in planted:
            where = conf / "conf.d" if name.startswith("belt") else conf
            (where / name).write_text("not the hook's\n", encoding="utf-8")
        done = self.hook()
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(self.links(), self.linked(self.shas[0]))
        left = sorted(p.name for p in [*conf.iterdir(), *(conf / "conf.d").iterdir()])
        want = sorted(["belt.toml", "conf.d", "mise.lock", *planted])
        self.assertEqual(left, want)

    def test_a_fetch_that_is_not_the_pin_is_refused(self) -> None:
        """The remote answers with another commit: red, nothing linked."""
        stubs = self.tmp / "stubs"
        stubs.mkdir()
        (stubs / "git").write_text(
            WRONG_FETCH.replace("{git}", hermetic.tool("git")), encoding="utf-8"
        )
        (stubs / "git").chmod(0o755)
        done = self.hook(PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(
            done.stderr,
            f"org-belt: fetched {self.shas[1]}, not the pinned {self.shas[0]};"
            " nothing linked\n",
        )
        self.assertEqual((self.cached(), self.trees()), ([], []))
        self.assertEqual(self.links(), dict.fromkeys(self.linked(self.shas[0])))


class TheCloneStaysItsOwn(HookFixture):
    """Each row: what the hook leaves in the clone's git state."""

    def test_a_fresh_clone_stays_clean(self) -> None:
        """The links are excluded in the clone, not by a tracked ignore."""
        self.commit_all()
        self.assertEqual(self.hook().returncode, 0)
        status = self.git(self.consumer, "status", "--porcelain").stdout
        self.assertEqual(status, "")
        self.assertFalse((self.consumer / ".gitignore").exists())

    def test_a_new_worktree_stays_clean(self) -> None:
        """A worktree links itself; the exclude is the shared one, once."""
        self.commit_all()
        self.assertEqual(self.hook().returncode, 0)
        worktree = self.tmp / "worktree"
        self.git(self.consumer, "worktree", "add", "-q", "-b", "side", str(worktree))
        done = run([hermetic.tool("sh"), "-c", hook_command()], worktree, self.env())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue((worktree / ".config/mise/conf.d/belt.toml").is_symlink())
        self.assertEqual(self.git(worktree, "status", "--porcelain").stdout, "")
        self.assertEqual(self.excluded(), [".config/mise/"])

    def test_git_variables_a_hook_inherits_do_not_reach_the_fetch(self) -> None:
        """Under a hook git exports GIT_DIR: the cache is not the clone's."""
        git_dir = self.consumer / ".git"
        refs_before = sorted(p.name for p in (git_dir / "refs" / "heads").iterdir())
        done = self.hook(
            GIT_DIR=str(git_dir),
            GIT_INDEX_FILE=str(git_dir / "index"),
            GIT_WORK_TREE=str(self.consumer),
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        tree = self.cache / "org-belt" / self.shas[0]
        head = self.git(tree, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(head, self.shas[0])
        self.assertEqual(self.links(), self.linked(self.shas[0]))
        refs_after = sorted(p.name for p in (git_dir / "refs" / "heads").iterdir())
        self.assertEqual(refs_after, refs_before)
        self.assertFalse((git_dir / "FETCH_HEAD").exists())

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
        argv = [hermetic.tool("lefthook"), "run", "post-checkout"]
        done = run(argv, self.consumer, self.env())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(
            f"org-belt: .config/mise links the belt of {OWNER_REPO}@{self.shas[0]}",
            done.stdout,
        )
        self.assertEqual(self.links(), self.linked(self.shas[0]))


if __name__ == "__main__":
    unittest.main()
