#!/usr/bin/env python3
"""Table tests for the hook that links a clone to its pin's belt (#1686).

`lefthook/org-hooks.yml`'s `belt` job is one inline command, the first
job of every hook (#1988): read the tracked gate stub's pin, make the
canon tree of exactly that sha in the machine's cache once, and link
`.config/mise/conf.d/belt.toml` and `.config/mise/mise.lock` into it.

The command under test is the job's own `run:`, read out of the canon's
REAL hook file and run by `sh` in a planted consumer, not a copy of it.
A local bare repository stands in for the canon: the consumer's HOME
carries a git `insteadOf` that sends the stub's GitHub URL to it, and
the cache is a directory of the row's own (XDG_CACHE_HOME).

Rows run the real lefthook, where it is installed: the command
survives lefthook's own templating, and pre-commit, commit-msg and
pre-push each relink a nested worktree to its own pin before mise runs,
and stop when the link fails (#1988).

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import override

import hermetic

CANON = Path(__file__).resolve().parent.parent
HOOKS = CANON / "lefthook" / "org-hooks.yml"
LEFTHOOK = hermetic.optional_tool("lefthook")
# What the hook adds to .git/info/exclude, and the names it leaves there.
EXCLUDED = [".config/mise/"]
LINK_NAMES = ["belt.toml", "conf.d", "mise.lock"]
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
    """Read the `belt` job's folded `run:` (the `&belt` anchor) as YAML folds it.

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
        msg = "the belt job has no run: body"
        raise AssertionError(msg)
    return " ".join(body)


def run(
    argv: list[str], cwd: Path, env: dict[str, str], *, check: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run one command, its output captured as text, within a minute.

    Returns:
        The run.

    """
    return subprocess.run(
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

    # What each belt commit carries after its `# belt <n>` line; `{n}`
    # is the commit's number. Empty: the rows read the file's text.
    belt_tasks = ""

    @override
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
        self.shas: list[str] = []
        for n in ("1", "2"):
            (work / "mise" / "config.toml").write_text(
                f"# belt {n}\n" + self.belt_tasks.replace("{n}", n), "utf-8"
            )
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
        return hermetic.env(
            self.home,
            XDG_CACHE_HOME=str(self.cache),
            MISE_CONFIG_DIR=str(self.tmp / "mise-config"),
            MISE_DATA_DIR=str(self.tmp / "mise-data"),
            MISE_CACHE_DIR=str(self.tmp / "mise-cache"),
            MISE_STATE_DIR=str(self.tmp / "mise-state"),
            MISE_TRUSTED_CONFIG_PATHS=str(self.tmp),
            MISE_OFFLINE="1",
            MISE_YES="1",
            **extra,
        )

    def git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        """Run git in `cwd`, checked.

        Returns:
            The run.

        """
        return run([hermetic.tool("git"), *args], cwd, self.env(), check=True)

    def pin(
        self, sha: str, tree: Path | None = None, *, name: str = "gate.yml"
    ) -> None:
        """Write a gate stub (the consumer's by default) pinning `sha`.

        The stub is staged: the hook reads tracked workflows only
        (#2018). Any other `name` is a stray, left untracked.
        """
        where = tree or self.consumer
        wf = where / ".github" / "workflows"
        wf.mkdir(parents=True, exist_ok=True)
        (wf / name).write_text(
            "jobs:\n  ci:\n"
            f"    uses: {OWNER_REPO}/.github/workflows/ci.yml@{sha} # v1.0.0\n",
            encoding="utf-8",
        )
        if name == "gate.yml":
            self.git(where, "add", ".github/workflows/gate.yml")

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

    def links(self, tree: Path | None = None) -> dict[str, str | None]:
        """Read both links in a tree, the consumer by default.

        Returns:
            Link path (relative) to its target, None when absent.

        """
        out: dict[str, str | None] = {}
        for rel in (".config/mise/conf.d/belt.toml", ".config/mise/mise.lock"):
            path = (tree or self.consumer) / rel
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
        first = [self.shas[0]]
        self.assertEqual(self.cached(), first)
        tree = self.cache / "org-belt" / self.shas[0]
        self.assertTrue(tree.is_symlink())
        trees = [p.resolve() for p in self.trees()]
        made = [tree.resolve()]
        self.assertEqual(made, trees)
        self.assertTrue(self.trees()[0].name.startswith(f"{self.shas[0]}."))
        head = self.git(tree, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(head, self.shas[0])
        self.assertEqual(self.links(), self.linked(self.shas[0]))
        self.assertEqual(
            (self.consumer / ".config/mise/conf.d/belt.toml").read_text("utf-8"),
            "# belt 1\n",
        )
        self.assertEqual(self.excluded(), EXCLUDED)

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
        self.assertEqual(self.excluded(), EXCLUDED)

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
        both = sorted(self.shas)
        self.assertEqual(self.cached(), both)
        self.assertEqual(self.links(), self.linked(self.shas[1]))

    def test_two_runs_at_once_leave_one_whole_tree(self) -> None:
        """Both succeed; one tree, nothing left over, nothing nested in it."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            runs = list(pool.map(lambda _: self.hook(), range(2)))
        for done in runs:
            self.assertEqual(done.returncode, 0, done.stderr)
        first = [self.shas[0]]
        self.assertEqual(self.cached(), first)
        tree = self.cache / "org-belt" / self.shas[0]
        trees = [p.resolve() for p in self.trees()]
        made = [tree.resolve()]
        self.assertEqual(made, trees)
        names = sorted(p.name for p in tree.iterdir())
        checkout = [".git", "mise"]
        self.assertEqual(names, checkout)
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
                    target = stale[rel]
                    if target is None:
                        self.fail(f"no stale link for {rel}")
                    (self.consumer / rel).symlink_to(target)
            with ThreadPoolExecutor(max_workers=2) as pool:
                runs = list(pool.map(lambda _: self.hook(), range(2)))
            for done in runs:
                self.assertEqual((pair, done.returncode, done.stderr), (pair, 0, ""))
            self.assertEqual(self.links(), self.linked(self.shas[0]), pair)
            left = sorted(
                p.name for p in [*conf.iterdir(), *(conf / "conf.d").iterdir()]
            )
            self.assertEqual(left, LINK_NAMES, pair)

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
        self.assertEqual(left, LINK_NAMES)

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

    def test_an_untracked_stray_does_not_choose_the_belt(self) -> None:
        """A stray pinning the second commit sorts first: the tracked pin wins.

        Fails before #2018: the glob read the untracked stray.
        """
        self.pin(self.shas[1], name="a-stray.yml")
        done = self.hook()
        self.assertEqual(done.returncode, 0, done.stderr)
        first = [self.shas[0]]
        self.assertEqual(self.cached(), first)
        self.assertEqual(self.links(), self.linked(self.shas[0]))

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
        unlinked: dict[str, str | None] = dict.fromkeys(self.linked(self.shas[0]))
        self.assertEqual(self.links(), unlinked)


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
        self.assertEqual(self.excluded(), EXCLUDED)

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
        self.assertEqual(self.cached(), list[str]())
        self.assertFalse((self.consumer / ".config").exists())

    def test_lefthook_runs_the_job_as_written(self) -> None:
        """Through lefthook itself: its templating leaves the command whole."""
        if LEFTHOOK is None:
            self.skipTest("lefthook is not installed")
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


# Each belt commit's tasks: the ones the org hooks run, each recording
# which belt ran it, so a row can tell the pin's belt from another.
HOOK_TASKS = "".join(
    f'[tasks."{name}"]\nrun = \'echo {{n}}:{name} >> "$ORG_MARK"\'\n'
    for name in ("lint:typos", "lint:actions", "commits:check", "ci")
)


class EveryHookLinksFirst(HookFixture):
    """Each row: a hook of the real hook file, run by lefthook in a worktree (#1988).

    The consumer pins the first commit, A, and is linked to it; a
    worktree nested inside it, where the desktop app makes them, pins
    the second, B. The cache holds both trees. Every hook that runs mise
    must relink to B before mise runs, so its tasks are B's.
    """

    belt_tasks = HOOK_TASKS

    @override
    def setUp(self) -> None:
        """Plant the cache of A and B, the consumer on A, and the worktree."""
        super().setUp()
        if LEFTHOOK is None:
            self.skipTest("lefthook is not installed")
        self.commit_all()
        self.pin(self.shas[1])
        self.assertEqual(self.hook().returncode, 0)
        self.pin(self.shas[0])
        self.assertEqual(self.hook().returncode, 0)
        both = sorted(self.shas)
        self.assertEqual(self.cached(), both)
        self.tree = self.consumer / ".claude" / "worktrees" / "lane"
        self.git(self.consumer, "worktree", "add", "-q", "-b", "lane", str(self.tree))
        self.pin(self.shas[1], self.tree)
        # Staged, as a commit has it: lefthook skips a pre-commit with
        # nothing staged.
        self.git(self.tree, "add", ".github")
        (self.tree / "lefthook.yml").write_text(
            HOOKS.read_text(encoding="utf-8"), encoding="utf-8"
        )
        self.mark = self.tmp / "mark"

    def link_to_a(self) -> None:
        """Give the worktree a link naming A's tree, as a stale one does."""
        conf = self.tree / ".config" / "mise"
        (conf / "conf.d").mkdir(parents=True)
        linked = self.linked(self.shas[0])
        for rel, target in linked.items():
            if target is None:
                self.fail(f"no target for {rel}")
            (self.tree / rel).symlink_to(target)

    def lefthook(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run one hook through lefthook, in the worktree.

        Returns:
            The run.

        """
        argv = [hermetic.tool("lefthook"), "run", "--no-auto-install", *args]
        return run(argv, self.tree, self.env(ORG_MARK=str(self.mark)))

    def marks(self) -> list[str]:
        """Read which belt ran which task.

        Returns:
            The `<n>:<task>` lines, sorted; empty when none ran.

        """
        if not self.mark.exists():
            return []
        return sorted(self.mark.read_text("utf-8").splitlines())

    def tasks(self) -> list[str]:
        """List the tasks mise finds in the worktree, inside the ceiling.

        Returns:
            The task names.

        """
        argv = [hermetic.tool("mise"), "tasks", "ls", "--name-only"]
        ceiling = str(self.tree.parent)
        done = run(argv, self.tree, self.env(MISE_CEILING_PATHS=ceiling))
        self.assertEqual(done.returncode, 0, done.stderr)
        return sorted(done.stdout.split())

    def assert_ran_from_b(
        self, done: subprocess.CompletedProcess[str], *names: str
    ) -> None:
        """Assert the hook passed, linked B, and ran `names` from B."""
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.links(self.tree), self.linked(self.shas[1]))
        ran = sorted(f"2:{n}" for n in names)
        self.assertEqual(self.marks(), ran)
        want = sorted(["ci", "commits:check", "lint:actions", "lint:typos"])
        self.assertEqual(self.tasks(), want)

    def test_pre_commit_links_a_worktree_with_none(self) -> None:
        """No .config/mise at all: pre-commit links B, then runs B's lints."""
        self.assertFalse((self.tree / ".config").exists())
        done = self.lefthook("pre-commit")
        self.assert_ran_from_b(done, "lint:typos", "lint:actions")

    def test_pre_commit_relinks_a_link_to_another_pin(self) -> None:
        """A link to A: pre-commit relinks B before its lints run."""
        self.link_to_a()
        done = self.lefthook("pre-commit")
        self.assert_ran_from_b(done, "lint:typos", "lint:actions")

    def test_commit_msg_relinks_first(self) -> None:
        """commit-msg: B's commits:check, then the sign-off check."""
        self.link_to_a()
        msg = self.tmp / "COMMIT_EDITMSG"
        msg.write_text(
            "fix: a subject\n\nSigned-off-by: A Person <a@example.invalid>\n",
            encoding="utf-8",
        )
        done = self.lefthook("commit-msg", str(msg))
        self.assert_ran_from_b(done, "commits:check")

    def test_pre_push_relinks_first(self) -> None:
        """pre-push: B's ci."""
        self.link_to_a()
        done = self.lefthook("pre-push")
        self.assert_ran_from_b(done, "ci")

    def test_a_failed_link_stops_the_hook(self) -> None:
        """The belt job fails: no later job runs, and the hook is red."""
        self.link_to_a()
        self.pin("abc123", self.tree)
        self.git(self.tree, "add", ".github")
        done = self.lefthook("pre-commit")
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn(
            "org-belt: the gate stub pins abc123, not a 40-hex sha; nothing linked",
            done.stdout + done.stderr,
        )
        self.assertEqual(self.marks(), list[str]())


if __name__ == "__main__":
    unittest.main()
