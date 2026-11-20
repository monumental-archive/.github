#!/usr/bin/env python3
"""Build a release's source archive twice and prove it deterministic.

The source-archive class's build step (#1384 moved it here from the
workflow's shell, refusals unchanged). Run from the checked-out tag
with VERSION and GITHUB_REPOSITORY; writes dist/<name>-<VERSION>.tar.gz,
`git archive` of the tag `v<VERSION>` under that same prefix.

The name is the repository's, after the owner, with a leading dot
stripped: the .github repository would otherwise get a hidden-file
basename, so its asset is visible and predictable.

Two refusals. The second build must equal the first byte for byte, or
the archive is not reproducible and nothing downstream can check it.
And the archive must list more than half as many entries as the tree
tracks: a smoke test that it holds the tagged tree, not an empty or
truncated stream.

Exit status: 0 built, 1 refused, else git's own failing status.
"""

import hashlib
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

# The canon's one writer of workflow commands, in mise/ (#1660).
# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import workflow_command


class RefusedError(Exception):
    """The archive is refused; the message says why."""


class GitFailedError(Exception):
    """git failed; its own output already said why."""

    def __init__(self, status: int) -> None:
        """Keep git's exit status, which the step exits with."""
        super().__init__(status)
        self.status = status


def git(*args: str) -> str:
    """Run git in the checkout.

    Returns:
        Its standard output.

    Raises:
        GitFailedError: git exited non-zero.

    """
    done = subprocess.run(
        # ruff: ignore[start-process-with-partial-path] - the runner's git, on PATH
        ["git", *args],
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise GitFailedError(done.returncode)
    return done.stdout


def archive(base: str, version: str, out: Path) -> str:
    """Archive the tag once.

    Returns:
        The archive's sha256.

    """
    git(
        "archive", "--format=tar.gz", f"--prefix={base}/", "-o", str(out), f"v{version}"
    )
    return hashlib.sha256(out.read_bytes()).hexdigest()


def build(env: Mapping[str, str]) -> str:
    """Build, rebuild, compare and count.

    Returns:
        The notice line for the step's log.

    Raises:
        RefusedError: the builds differ, or the archive is too short.

    """
    version = env["VERSION"]
    name = env["GITHUB_REPOSITORY"].partition("/")[2].removeprefix(".")
    base = f"{name}-{version}"
    dist = Path("dist")
    dist.mkdir(parents=True, exist_ok=True)
    shipped = dist / f"{base}.tar.gz"
    a = archive(base, version, shipped)
    with TemporaryDirectory() as tmp:
        b = archive(base, version, Path(tmp) / f"{base}.check.tar.gz")
    if a != b:
        msg = f"source archive is not deterministic ({a} vs {b})"
        raise RefusedError(msg)
    with tarfile.open(shipped, "r:gz") as tar:
        count = len(tar.getmembers())
    tracked = len(git("ls-files").splitlines())
    if count <= tracked // 2:
        msg = f"archive lists {count} entries for {tracked} tracked files"
        raise RefusedError(msg)
    return f"{base}.tar.gz: {count} entries, digest {a}"


def main(env: Mapping[str, str] | None = None) -> int:
    """Run the step.

    Returns:
        The exit status: 0 built, 1 refused, else git's.

    """
    env = os.environ if env is None else env
    try:
        workflow_command.notice(build(env))
    except RefusedError as e:
        workflow_command.error(str(e))
        return 1
    except GitFailedError as e:
        return e.status
    return 0


if __name__ == "__main__":
    sys.exit(main())
