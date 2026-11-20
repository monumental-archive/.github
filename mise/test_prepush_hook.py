#!/usr/bin/env python3
"""Table tests for the org's pre-push gate (#1281).

lefthook 2.2.2 skips a pre-push `run:` job when the files the push
changes come out empty, `glob` or no `glob` (build_command.go:71-81 at
v2.2.2). It builds that list from HEAD and keeps only paths that exist
on disk, so a push that only deletes files, or a branch pushed while
HEAD sits on a commit the remote already has, went out with the gate
never run and one grey `(skip) no matching push files` line to say so.

The job was then a `script:` (#1281), which lefthook runs from its
remote cache in the git directory every worktree shares; a hook in a
worktree on another canon ref deleted it mid-gate (#1426). It is now an
inline `run:` whose `{files}` template resolves to one file the tree
has, which lefthook does not skip (build_command.go:45-49).

Each row is a scratch bare remote and a consumer clone wired exactly
the way every org repository is: the canon's own `lefthook/org-hooks.yml`,
from THIS tree, served as a `file://` lefthook remote at a tag. `mise`
is a stub on PATH that writes a marker, so a row asserts the gate was
invoked, and invoked once, rather than reading lefthook's output. The
pin is the belt's, so these rows run in the gate; where `lefthook` is
absent the class skips by name.

Mutation, recorded on #1281's pull request: with the job put back to a
plain `run:` line, the delete-only, add-then-delete, HEAD-elsewhere and
already-merged rows go red with no marker.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from pathlib import Path
from typing import override

import hermetic

LEFTHOOK = hermetic.optional_tool("lefthook")
GIT = hermetic.optional_tool("git")

# What the stub records for one gate run: the job's `mise run ci`.
GATE_RAN = "run ci\n"

# The stub records its arguments. Where PREPUSH_EVICT names another
# worktree, it first runs a hook there, as a second session would, and
# records what is left of the shared remote cache afterwards.
STUB_MISE = """#!/usr/bin/env bash
if [[ -n ${PREPUSH_EVICT:-} ]]; then
  (cd "${PREPUSH_EVICT}" && PREPUSH_EVICT= lefthook run pre-commit > /dev/null)
  cache="$(git rev-parse --git-common-dir)/info/lefthook-remotes"
  ls "${cache}" > "${PREPUSH_MARKER}.cache"
fi
printf "%s\\n" "$*" >> "${PREPUSH_MARKER}"
"""


def consumer_config(
    remote: Path, source_dir: str | None = None, ref: str = "v1"
) -> str:
    """Return a consumer's lefthook.yml: the scaffold's, at a local remote.

    Returns:
        The YAML text, with `source_dir` set first when one is given.

    """
    head = f"source_dir: {source_dir}\n" if source_dir else ""
    return (
        f"{head}remotes:\n"
        f"  - git_url: file://{remote}\n"
        f"    ref: {ref}\n"
        "    configs:\n"
        "      - lefthook/org-hooks.yml\n"
    )


def plant_canon(tree: Path, *also: str) -> None:
    """Plant the canon in `tree`: its hook config, and what `also` names."""
    hermetic.canon(tree, "lefthook/org-hooks.yml", *also)


def write(cwd: Path, name: str) -> None:
    """Write a one-line file named for its content."""
    (cwd / name).write_text(f"{name}\n", encoding="utf-8")


class PrePushGate(unittest.TestCase):
    """Every push runs the gate exactly once; none is skipped."""

    @override
    def setUp(self) -> None:
        """Build the canon remote, a seeded consumer remote and a clone."""
        if LEFTHOOK is None or GIT is None:
            self.skipTest("lefthook (or git) not on PATH")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.marker = self.root / "marker"

        stubs = self.root / "bin"
        stubs.mkdir()
        (stubs / "mise").write_text(STUB_MISE, encoding="utf-8")
        (stubs / "mise").chmod(0o755)

        env = hermetic.env()
        tools = [
            str(stubs),
            str(Path(hermetic.tool("lefthook")).parent),
            str(Path(hermetic.tool("git")).parent),
        ]
        env |= {
            "PATH": os.pathsep.join([*tools, env.get("PATH", "")]),
            "HOME": str(self.root),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "PREPUSH_MARKER": str(self.marker),
        }
        self.env = env

        # The canon, as a lefthook remote: this tree's hook and script.
        self.canon = self.root / "canon.git"
        work = self.root / "canon-work"
        self.git(self.root, "init", "-q", "--bare", "-b", "main", str(self.canon))
        self.git(self.root, "init", "-q", "-b", "main", str(work))
        plant_canon(work)
        self.commit(work, "canon")
        self.git(work, "tag", "v1")
        self.commit(work, "the next canon release")
        self.git(work, "tag", "v2")
        self.git(work, "push", "-q", str(self.canon), "main", "v1", "v2")

    def git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        """Run git in `cwd`; a setup step that fails fails the row.

        Returns:
            The finished process.

        """
        done = self.run_git(cwd, *args)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done

    def run_git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        """Run git in `cwd` and return whatever it did.

        Returns:
            The finished process, stdout and stderr captured as text.

        """
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [hermetic.tool("git"), *args],
            cwd=cwd,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def commit(self, cwd: Path, message: str) -> None:
        """Commit everything, hooks bypassed: setup is not under test."""
        self.git(cwd, "add", "-A")
        self.git(cwd, "commit", "-q", "--no-verify", "--allow-empty", "-m", message)

    def consumer(self, source_dir: str | None = None) -> Path:
        """Seed a consumer remote and return a hook-installed clone of it.

        Returns:
            The clone's path. It has origin/HEAD, as any clone of a
            non-empty remote does — the case in which lefthook's list
            falls back to the default branch.

        """
        remote = self.root / "remote.git"
        seed = self.root / "seed"
        self.git(self.root, "init", "-q", "--bare", "-b", "main", str(remote))
        self.git(self.root, "init", "-q", "-b", "main", str(seed))
        (seed / "lefthook.yml").write_text(
            consumer_config(self.canon, source_dir), encoding="utf-8"
        )
        write(seed, "kept.txt")
        write(seed, "doomed.txt")
        self.commit(seed, "seed")
        self.git(seed, "push", "-q", str(remote), "main")
        clone = self.root / "clone"
        self.git(self.root, "clone", "-q", str(remote), str(clone))
        self.git(clone, "rev-parse", "--verify", "refs/remotes/origin/HEAD")
        self.install(clone)
        return clone

    def install(self, clone: Path) -> None:
        """Install the hooks; lefthook fetches the canon remote here."""
        done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [hermetic.tool("lefthook"), "install"],
            cwd=clone,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def push(self, clone: Path, *args: str) -> subprocess.CompletedProcess[str]:
        """Push with the hook on, from a clean marker.

        Returns:
            The finished push.

        """
        self.marker.unlink(missing_ok=True)
        return self.run_git(clone, "push", *args)

    def assert_gated(self, done: subprocess.CompletedProcess[str]) -> None:
        """Assert the push went through and the gate ran exactly once."""
        output = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, output)
        self.assertTrue(self.marker.exists(), f"the gate never ran:\n{output}")
        self.assertEqual(self.marker.read_text(encoding="utf-8"), GATE_RAN, output)

    def test_a_new_branch_that_adds_a_file_is_gated(self) -> None:
        """The control: the row lefthook never skipped."""
        clone = self.consumer()
        self.git(clone, "switch", "-q", "-c", "adds")
        write(clone, "new.txt")
        self.commit(clone, "add")
        self.assert_gated(self.push(clone, "-u", "origin", "adds"))

    def test_a_push_that_only_deletes_files_is_gated(self) -> None:
        """Upstream set; the commit's one change is a deletion."""
        clone = self.consumer()
        self.git(clone, "switch", "-q", "-c", "deletes")
        write(clone, "new.txt")
        self.commit(clone, "add")
        self.git(clone, "push", "-q", "--no-verify", "-u", "origin", "deletes")
        self.git(clone, "rm", "-q", "doomed.txt")
        self.commit(clone, "delete")
        self.assert_gated(self.push(clone, "origin", "deletes"))

    def test_commits_that_add_then_delete_are_gated(self) -> None:
        """A new branch whose commits leave the tree as it was."""
        clone = self.consumer()
        self.git(clone, "switch", "-q", "-c", "roundtrip")
        write(clone, "brief.txt")
        self.commit(clone, "add")
        self.git(clone, "rm", "-q", "brief.txt")
        self.commit(clone, "delete")
        self.assert_gated(self.push(clone, "origin", "roundtrip"))

    def test_a_branch_pushed_while_head_is_elsewhere_is_gated(self) -> None:
        """A worktree's branch pushed from a checkout at origin/main."""
        clone = self.consumer()
        self.git(clone, "switch", "-q", "-c", "elsewhere")
        write(clone, "new.txt")
        self.commit(clone, "add")
        self.git(clone, "switch", "-q", "--detach", "origin/main")
        self.assert_gated(self.push(clone, "origin", "elsewhere"))

    def test_a_branch_already_on_the_default_branch_is_gated(self) -> None:
        """A new branch whose commits origin/main already has."""
        clone = self.consumer()
        self.git(clone, "switch", "-q", "-c", "merged")
        write(clone, "new.txt")
        self.commit(clone, "add")
        self.git(clone, "push", "-q", "--no-verify", "origin", "merged:main")
        self.git(clone, "fetch", "-q", "origin")
        self.git(clone, "switch", "-q", "-c", "again")
        self.assert_gated(self.push(clone, "origin", "again"))

    def test_another_source_dir_still_gets_the_gate(self) -> None:
        """A consumer's own `source_dir` changes nothing for an inline job.

        The job was a script, found under the consumer's `source_dir`,
        and a repository that set its own got a failed push (#1281). An
        inline `run:` has no file to find.
        """
        clone = self.consumer(source_dir=".hooks/")
        self.git(clone, "switch", "-q", "-c", "elsewhere-dir")
        write(clone, "new.txt")
        self.commit(clone, "add")
        self.assert_gated(self.push(clone, "origin", "elsewhere-dir"))

    def test_the_canon_tree_gates_once(self) -> None:
        """The canon consumes its own hooks and runs the gate once."""
        clone = self.consumer()
        self.git(clone, "switch", "-q", "-c", "canon")
        plant_canon(clone, "security/identity.toml")
        self.commit(clone, "be the canon")
        self.assert_gated(self.push(clone, "origin", "canon"))

    def test_a_consumer_planting_its_own_script_still_gets_the_gate(self) -> None:
        """A consumer's own pre-push ci.sh neither runs nor replaces the gate."""
        clone = self.consumer()
        self.git(clone, "switch", "-q", "-c", "planted")
        own = clone / ".lefthook" / "pre-push" / "ci.sh"
        own.parent.mkdir(parents=True)
        own.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        own.chmod(0o755)
        self.commit(clone, "plant a script")
        self.assert_gated(self.push(clone, "origin", "planted"))

    def test_a_worktree_on_another_canon_ref_cannot_evict_the_gate(self) -> None:
        """Another worktree's hook mid-gate does not fail this push (#1426).

        Two worktrees of one repository share .git/info/lefthook-remotes/,
        which holds one canon ref at a time. Mid-gate, the stub runs a
        hook in a second worktree pinned to v2; lefthook syncs v2 there
        and deletes this worktree's v1 checkout. The push still goes
        through, gated once: nothing the job runs lives in that cache.
        The cache listing proves the eviction happened.
        """
        clone = self.consumer()
        other = self.root / "other"
        self.git(clone, "worktree", "add", "-q", "-b", "other", str(other))
        (other / "lefthook.yml").write_text(
            consumer_config(self.canon, ref="v2"), encoding="utf-8"
        )
        self.commit(other, "pin v2")
        self.git(clone, "switch", "-q", "-c", "evicted")
        write(clone, "new.txt")
        self.commit(clone, "add")
        self.env["PREPUSH_EVICT"] = str(other)
        self.assert_gated(self.push(clone, "origin", "evicted"))
        cache = (self.root / "marker.cache").read_text(encoding="utf-8")
        self.assertEqual(cache, "canon-v2\n")


if __name__ == "__main__":
    unittest.main()
