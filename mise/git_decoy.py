#!/usr/bin/env python3
"""Run a test suite with git pointed at a decoy repository (#1463).

git exports `GIT_DIR` to the hooks it runs in a linked worktree, and
`GIT_INDEX_FILE` to pre-commit, and obeys them whatever the working
directory (#857). A test that runs `git` without scrubbing its
environment therefore writes into the real repository when the suite
runs under a hook, while a direct run and CI stay green.

So the hazard is planted on every run instead of waited for:

- `plant DIR` makes a repository in DIR: one empty commit, a clean
  index, no config from the machine.
- `run DIR -- CMD...` runs CMD with every inherited `GIT_*` removed and
  `GIT_DIR` and `GIT_INDEX_FILE` set at the decoy, which is the hook's
  own topology. `GIT_WORK_TREE` is left unset on purpose: with it set,
  a leaked `git add` from an unrelated directory fails on its pathspec
  instead of staging, and the leak would look like an ordinary error.
  Afterwards the decoy is compared with its state before: HEAD, the
  refs, the index, the config and the working tree. Any difference is
  red, with what changed, and exits 1; otherwise CMD's own status is
  the answer.

Every git call this helper makes passes its own scrubbed environment:
it runs under the very hook it protects against.
"""

import argparse
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
from typing import NamedTuple

GIT = shutil.which("git") or "git"

# A git with no user or system config and an identity of its own, so
# the decoy's one commit needs nothing from the machine.
_IDENTITY = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "decoy",
    "GIT_AUTHOR_EMAIL": "decoy@example.invalid",
    "GIT_COMMITTER_NAME": "decoy",
    "GIT_COMMITTER_EMAIL": "decoy@example.invalid",
}

MESSAGE = "a test ran git in the caller's git environment"


def scrubbed() -> dict[str, str]:
    """Return the caller's environment with every `GIT_*` removed.

    Returns:
        A copy of os.environ that names no repository.

    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(decoy: pathlib.Path, *args: str) -> str:
    """Run git on the decoy itself, never on what the caller points at.

    Returns:
        git's standard output.

    """
    return subprocess.run(
        [GIT, "-C", str(decoy), *args],
        env={**scrubbed(), **_IDENTITY},
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def plant(decoy: pathlib.Path) -> None:
    """Make the decoy: one empty commit and a clean index."""
    git(decoy, "init", "-q")
    git(decoy, "commit", "-q", "--allow-empty", "-m", "decoy")


def digest(path: pathlib.Path) -> str:
    """Hash one file, or name its absence.

    Returns:
        The file's SHA-256, or `absent`.

    """
    if not path.is_file():
        return "absent"
    return hashlib.sha256(path.read_bytes()).hexdigest()


class State(NamedTuple):
    """What a leak could change in the decoy."""

    head: str  # what HEAD names, symbolic or not
    commit: str  # the commit HEAD resolves to
    refs: frozenset[str]  # every ref, as `<oid> <name>`
    index_hash: str  # the index file's hash (`index` is tuple's method)
    config: str  # the config file's hash
    status: str  # `git status --porcelain`, which is empty when clean


def state(decoy: pathlib.Path) -> State:
    """Read the decoy's state.

    Returns:
        The state, read through a scrubbed git.

    """
    gitdir = decoy / ".git"
    return State(
        head=(gitdir / "HEAD").read_text(encoding="utf-8").strip(),
        commit=git(decoy, "rev-parse", "HEAD").strip(),
        refs=frozenset(
            git(decoy, "for-each-ref", "--format=%(objectname) %(refname)").splitlines()
        ),
        index_hash=digest(gitdir / "index"),
        config=digest(gitdir / "config"),
        status=git(decoy, "status", "--porcelain", "--untracked-files=all"),
    )


def changes(decoy: pathlib.Path, before: State, after: State) -> list[str]:
    """Say what changed in the decoy, in terms that name the fixture.

    Returns:
        One line per change; empty when nothing changed.

    """
    out: list[str] = []
    if after.head != before.head:
        out.append(f"HEAD moved: {before.head} -> {after.head}")
    if after.commit != before.commit:
        subjects = git(
            decoy, "log", "--format=%s", after.commit, "--not", before.commit
        )
        out += [f"new commit: {s}" for s in subjects.splitlines()]
    out += [
        f"new or moved ref: {r.split(' ', 1)[1]}"
        for r in sorted(after.refs - before.refs)
    ]
    out += [f"lost ref: {r.split(' ', 1)[1]}" for r in sorted(before.refs - after.refs)]
    if after.config != before.config:
        out.append("config rewritten")
    if after.index_hash != before.index_hash or after.status:
        staged = git(decoy, "diff", "--cached", "--name-status", "HEAD")
        out += [f"staged: {line}" for line in staged.splitlines()]
        out += [f"status: {line}" for line in after.status.splitlines()]
        if not staged and not after.status:
            out.append("index rewritten")
    return out


def run(decoy: pathlib.Path, command: list[str]) -> int:
    """Run `command` with git aimed at the decoy, then compare.

    Returns:
        1 when the decoy changed, else the command's own status.

    """
    before = state(decoy)
    env = {
        **scrubbed(),
        "GIT_DIR": str(decoy / ".git"),
        "GIT_INDEX_FILE": str(decoy / ".git" / "index"),
    }
    status = subprocess.run(command, env=env, check=False).returncode
    found = changes(decoy, before, state(decoy))
    if found:
        print(f"git-decoy: {MESSAGE}, running: {' '.join(command)}", file=sys.stderr)
        for line in found:
            print(f"  {line}", file=sys.stderr)
        print(
            "  every git call in a test passes a scrubbed env= (#857)",
            file=sys.stderr,
        )
        return 1
    return status


class Args(argparse.Namespace):
    """The arguments, typed as the parser declares them."""

    verb: str
    decoy: pathlib.Path
    command: list[str]


def main(argv: list[str]) -> int:
    """Parse arguments and dispatch.

    Returns:
        0 after planting, or the command's exit status.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("plant", help="make the decoy repository in DIR")
    p.add_argument("decoy", type=pathlib.Path)
    r = sub.add_parser("run", help="run CMD with git aimed at the decoy")
    r.add_argument("decoy", type=pathlib.Path)
    r.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv, namespace=Args())
    decoy = args.decoy.resolve()
    if args.verb == "plant":
        plant(decoy)
        return 0
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("run: no command after the decoy")
    return run(decoy, command)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
