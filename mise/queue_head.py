#!/usr/bin/env python3
"""Refuse a merge group not built from its pull request's current head.

The gate job's merge-group step calls this as its one command (#1422).

WHY. On edtf#265 the head was force-pushed in the same second the pull
request was queued, and the queue built its group from the head as it
was before the push. The gate ran green on that, the squash took the new
title, and the default branch got the new title over the old tree.
Nothing was red at any point. The same race is open to every armed pull
request.

THE CHECK. A queue squashes, merges or rebases, and each lands the same
tree: the pull request's head merged onto the group's base. A squash
never makes the head an ancestor of the group commit, so the check
compares TREES: `git merge-tree --write-tree <base> <current head>` must
equal the group commit's tree. A difference means the pull request
moved after it was queued, and the step is red with both heads (the
group's and the pull request's). The queue removes the entry, and the
next enqueue is built from the real head. A batch needs nothing more:
each entry has its own queue ref and run, and its base is the entry
ahead of it, which is what `base_sha` says.

UNVERIFIED IS RED, each by name: a `head_ref` that is not a queue ref,
a base or group commit missing from the checkout, a fetch that fails,
a merge that conflicts.

INPUTS. From the environment, as the step sets them:

- `ORG_QUEUE_BASE`: `github.event.merge_group.base_sha`;
- `ORG_QUEUE_GROUP`: `github.event.merge_group.head_sha`;
- `ORG_QUEUE_REF`: `github.event.merge_group.head_ref`,
  `refs/heads/gh-readonly-queue/<base>/pr-<n>-<sha>`, the one place the
  pull request's number appears;
- `ORG_QUEUE_TOKEN`: the job's own token. The checkout keeps no
  credentials and a private repository refuses an anonymous fetch, so
  the token is handed to the one `git fetch` through git's environment
  config, never its argv. Unset, the fetch is anonymous.

It fetches `refs/pull/<n>/head` from `origin` with git: no API request.
It names no organisation, repository or branch.

stdlib only.
"""

from __future__ import annotations

import base64
import os
import re
import shutil

# ruff: ignore[suspicious-subprocess-import]
import subprocess
import sys

import workflow_command

GIT = shutil.which("git") or "git"

# The queue's branch for one entry. The base branch may hold slashes;
# the entry's own segment is the last one.
QUEUE_REF = re.compile(
    r"^refs/heads/gh-readonly-queue/.+/pr-(?P<number>[1-9][0-9]*)-[0-9a-f]{40,64}$"
)


class StepError(Exception):
    """A refusal: the message is printed as the step's error."""


def git(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one git command and capture it.

    Returns:
        The run.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    return subprocess.run(
        [GIT, *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def number_of(ref: str) -> str:
    """Read the pull request's number from the group's queue ref.

    Returns:
        The number, as written in the ref.

    Raises:
        StepError: The ref is not a merge-queue ref.

    """
    match = QUEUE_REF.match(ref)
    if match is None:
        msg = (
            f"the merge group's head_ref {ref!r} is not a merge-queue ref"
            " (refs/heads/gh-readonly-queue/<base>/pr-<n>-<sha>), so the pull"
            " request it came from is unknown and the group is unverified"
        )
        raise StepError(msg)
    return match["number"]


def commit(sha: str, what: str) -> str:
    """Resolve a commit the checkout must already hold.

    Returns:
        The full commit id.

    Raises:
        StepError: The commit is not in the checkout.

    """
    done = git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    if not sha or done.returncode != 0:
        msg = (
            f"the merge group's {what} commit {sha!r} is not in the checkout,"
            " so the group is unverified"
        )
        raise StepError(msg)
    return done.stdout.strip()


def fetch_head(number: str, token: str) -> str:
    """Fetch the pull request's current head from `origin`.

    Returns:
        The head's commit id.

    Raises:
        StepError: The fetch fails.

    """
    ref = f"refs/queue-head/pr-{number}"
    env = dict(os.environ)
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
        env["GIT_CONFIG_COUNT"] = str(count + 1)
        env[f"GIT_CONFIG_KEY_{count}"] = "http.extraheader"
        env[f"GIT_CONFIG_VALUE_{count}"] = f"AUTHORIZATION: basic {basic}"
    done = git(
        "fetch",
        "--no-tags",
        "--quiet",
        "origin",
        f"+refs/pull/{number}/head:{ref}",
        env=env,
    )
    if done.returncode != 0:
        msg = (
            f"fetching pull request #{number}'s head (refs/pull/{number}/head)"
            f" from origin failed, so the group is unverified: {done.stderr.strip()}"
        )
        raise StepError(msg)
    return commit(ref, "pull request head")


def check(base: str, group: str, number: str, head: str) -> str:
    """Hold the group's tree to the current head merged onto the base.

    Returns:
        The tree both agree on.

    Raises:
        StepError: The merge conflicts, or the trees differ.

    """
    merged = git("merge-tree", "--write-tree", base, head)
    if merged.returncode == 1:
        msg = (
            f"pull request #{number}'s current head {head} conflicts with the"
            f" group's base {base}, so the group is unverified"
        )
        raise StepError(msg)
    if merged.returncode != 0:
        msg = (
            f"merging pull request #{number}'s current head {head} onto the"
            f" group's base {base} failed, so the group is unverified:"
            f" {merged.stderr.strip()}"
        )
        raise StepError(msg)
    want = merged.stdout.splitlines()[0].strip()
    have = git("rev-parse", f"{group}^{{tree}}").stdout.strip()
    if want != have:
        msg = (
            f"the merge group {group} was not built from pull request #{number}'s"
            f" current head {head}: the pull request moved after it was queued."
            f" The group's tree is {have}; {head} merged onto the base {base} is"
            f" {want}. Re-queue it, and the group is built from the real head"
        )
        raise StepError(msg)
    return want


def main() -> int:
    """Verify the merge group against its pull request's current head.

    Returns:
        The step's exit status.

    """
    env = os.environ
    try:
        number = number_of(env.get("ORG_QUEUE_REF", ""))
        base = commit(env.get("ORG_QUEUE_BASE", ""), "base")
        group = commit(env.get("ORG_QUEUE_GROUP", ""), "group")
        head = fetch_head(number, env.get("ORG_QUEUE_TOKEN", ""))
        tree = check(base, group, number, head)
    except StepError as err:
        workflow_command.error(f"queue-head: {err}")
        return 1
    print(
        f"queue-head: merge group {group} is pull request #{number}'s current"
        f" head {head} merged onto {base} (tree {tree})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
