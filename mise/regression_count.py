#!/usr/bin/env python3
"""Sort a repository's `fix` commits by whether a test came with them.

Over a window of the current branch's history, every commit whose
subject is typed `fix` (conventional: `fix:`, `fix(scope):`, `fix!:`)
lands in exactly one class:

- **test added**: the diff adds or changes a test (the table below).
- **no test**: it changes code and no test.
- **not a code fix**: docs, lockfiles, data, or pins only; and the
  scopes Renovate types its commits with (`fix(deps)`, `fix(canon)`).
- **unclassifiable**: an empty diff, or a path this table cannot place
  and nothing it can. Listed with the reason, never dropped.

It prints the four lists and the ratio test added / (test added + no
test), and with `--summary` writes the report the weekly audit leg puts
in the job summary: the ratio over the window and the "no test" fixes
since `--recent`. A report, not a gate (#1335): every outcome exits 0.
Only a usage or git error exits non-zero, because a ratio read from a
repository this could not read is not a measurement.

A merge commit is classified on its diff against its first parent, the
change it brought to the branch; it is counted only if its own subject
is fix-typed, the same rule as any other commit.

Names no repository, path or organisation: what counts as a test, as
code and as data is the table here and nothing else.
"""

import argparse
import dataclasses
import os
import pathlib
import re
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import findings
import typed_std

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

GIT = shutil.which("git") or "git"

FIX_SUBJECT = re.compile(r"^fix(?:\((?P<scope>[^)]*)\))?!?: ")

# The scopes Renovate's commits carry in the org's presets. A dependency
# or pin update typed so that it ships is not a bug fix.
RENOVATE_SCOPES = frozenset({"deps", "canon"})

# What counts as a test, by language (#1335). A path is a test if its
# basename matches one of TEST_NAMES, or any directory on it is one of
# TEST_DIRS. A `.rs` file is a test too where the diff ADDS a line
# carrying one of RUST_TEST_ATTRS, because Rust keeps unit tests inside
# the file they test.
#
# `fixtures` is not in TEST_DIRS (#1447). `testdata` is reserved by Go's
# toolchain and `tests` is a convention about tests; `fixtures` alone
# says only that files are stored there, and a fixtures directory under
# either is a test by that directory.
TEST_NAMES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^test_.*\.py$"),  # Python, unittest/pytest
    re.compile(r"^.*_test\.go$"),  # Go
    re.compile(r"^[^.].*\.test\.[^.]+$"),  # JS/TS: `x.test.ts`
    re.compile(r"^[^.].*\.spec\.[^.]+$"),  # JS/TS: `x.spec.ts`
)
TEST_DIRS = frozenset({"tests", "testdata"})
RUST_TEST_ATTRS = re.compile(r"^\+\s*#\[(?:test|pg_test|tokio::test)\b")

# Code: a change here is a change in behaviour. TOML and YAML are code
# because tasks and workflow steps are written in them; a change to one
# that only moves a pin is caught by the pin rule below, not here.
CODE_SUFFIXES = frozenset({
    ".awk",
    ".bash",
    ".c",
    ".cc",
    ".cjs",
    ".cpp",
    ".cs",
    ".css",
    ".cue",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".jq",
    ".js",
    ".jsx",
    ".kt",
    ".lua",
    ".mjs",
    ".mts",
    ".cts",
    ".nix",
    ".php",
    ".pl",
    ".ps1",
    ".py",
    ".rb",
    ".rego",
    ".rs",
    ".sed",
    ".sh",
    ".sql",
    ".swift",
    ".tf",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
    ".zsh",
    ".control",
    ".html",
})
CODE_NAMES = frozenset({"Dockerfile", "Containerfile", "Makefile", "justfile"})

# Not code: prose, lockfiles, data, images, ignore lists.
DATA_SUFFIXES = frozenset({
    ".md",
    ".markdown",
    ".rst",
    ".adoc",
    ".txt",
    ".cff",
    ".bib",
    ".lock",
    ".sum",
    ".json",
    ".jsonc",
    ".csv",
    ".tsv",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".webp",
    ".pdf",
    ".xml",
})
DATA_NAMES = frozenset({
    "LICENSE",
    "LICENCE",
    "COPYING",
    "NOTICE",
    "AUTHORS",
    "CODEOWNERS",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    ".gitignore",
    ".gitattributes",
    ".dockerignore",
    ".lycheeignore",
    ".editorconfig",
    ".mailmap",
})

# The tokens a pin is made of. A file whose removed lines and added
# lines are the same once these are blanked out moved a pin and did
# nothing else, whatever language it is in.
PIN_TOKENS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[0-9a-f]{7,128}\b"), "<hash>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}(?:T[0-9:.]+Z?)?\b"), "<date>"),
    (re.compile(r"\bv?\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.-]+)?\b"), "<ver>"),
)

TEST_ADDED = "test added"
NO_TEST = "no test"
NOT_CODE = "not a code fix"
UNCLASSIFIABLE = "unclassifiable"
CLASSES = (TEST_ADDED, NO_TEST, NOT_CODE, UNCLASSIFIABLE)


@dataclasses.dataclass(frozen=True)
class Commit:
    """One fix-typed commit and the class it landed in."""

    sha: str
    date: str
    subject: str
    verdict: str
    reason: str


@dataclasses.dataclass(frozen=True)
class Change:
    """One path in a commit's diff against its first parent."""

    status: str  # git's A/M/D/T
    path: str


class GitError(RuntimeError):
    """git could not answer, so nothing here is a measurement."""


def git(repo: pathlib.Path, *args: str) -> str:
    """Run git in `repo`, with paths printed as they are.

    Returns:
        git's standard output.

    Raises:
        GitError: git exited non-zero; its stderr is the message.

    """
    proc = subprocess.run(
        [GIT, "-C", str(repo), "-c", "core.quotePath=false", *args],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        msg = f"git {' '.join(args)}: {proc.stderr.strip()}"
        raise GitError(msg)
    return proc.stdout


def has_commits(repo: pathlib.Path) -> bool:
    """Ask whether HEAD names a commit yet.

    Returns:
        False for a repository whose branch has no commits.

    """
    try:
        git(repo, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    except GitError:
        return False
    return True


def is_test_path(path: str) -> bool:
    """Decide whether the language table names `path` as a test.

    Returns:
        True for a test by basename or by a directory on its path.

    """
    parts = path.split("/")
    if any(part in TEST_DIRS for part in parts[:-1]):
        return True
    return any(pattern.match(parts[-1]) for pattern in TEST_NAMES)


def path_kind(path: str) -> str:
    """Place a path that is not a test.

    Returns:
        `code`, `data` or `unknown`.

    """
    name = path.rsplit("/", 1)[-1]
    if name in DATA_NAMES:
        return "data"
    if name in CODE_NAMES:
        return "code"
    suffix = pathlib.PurePosixPath(name).suffix
    if suffix in DATA_SUFFIXES:
        return "data"
    if suffix in CODE_SUFFIXES:
        return "code"
    return "unknown"


def normalise_pin_line(line: str) -> str:
    """Blank out every hash, date and version on a diff line.

    Returns:
        The line with each pin token replaced by its placeholder.

    """
    for pattern, token in PIN_TOKENS:
        line = pattern.sub(token, line)
    return line.strip()


def is_pin_only(patch: str) -> bool:
    """Decide whether a patch's lines differ only in pins.

    Returns:
        True when the removed and added lines are the same multiset
        once hashes, dates and versions are blanked; False for a patch
        that only adds or only removes.

    """
    removed: list[str] = []
    added: list[str] = []
    for line in patch.splitlines():
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("-"):
            removed.append(normalise_pin_line(line[1:]))
        elif line.startswith("+"):
            added.append(normalise_pin_line(line[1:]))
    return bool(removed) and sorted(removed) == sorted(added)


def empty_tree(repo: pathlib.Path) -> str:
    """Name the empty tree, the base a root commit is diffed against.

    Returns:
        Its id in this repository's object format.

    """
    return git(repo, "hash-object", "-t", "tree", os.devnull).strip()


def changes(repo: pathlib.Path, base: str, sha: str) -> list[Change]:
    """List every path `sha` changed against `base`.

    Returns:
        One Change per path; a rename is a delete and an add.

    """
    out = git(repo, "diff-tree", "-r", "-z", "--no-renames", "--name-status", base, sha)
    fields = [f for f in out.split("\0") if f]
    return [Change(fields[i][0], fields[i + 1]) for i in range(0, len(fields) - 1, 2)]


def patch_of(repo: pathlib.Path, base: str, sha: str, path: str) -> str:
    """Diff one path between `base` and `sha`.

    Returns:
        The zero-context patch.

    """
    return git(
        repo,
        "diff",
        "-U0",
        "--no-color",
        "--no-ext-diff",
        "--no-renames",
        base,
        sha,
        "--",
        f":(literal){path}",
    )


def judge_paths(
    repo: pathlib.Path, base: str, sha: str, changed: Sequence[Change]
) -> tuple[str, str]:
    """Classify a commit with no test path by what else it changed.

    Returns:
        (class, reason): test added for a Rust test attribute, no test
        for any code that is not a pin, unclassifiable for paths no
        rule places, and not a code fix for the rest.

    """
    code: list[str] = []
    unknown: list[str] = []
    for change in changed:
        kind = "test" if is_test_path(change.path) else path_kind(change.path)
        if kind == "unknown":
            unknown.append(change.path)
        if kind != "code":
            continue  # a deleted test is neither a test added nor code
        patch = patch_of(repo, base, sha, change.path)
        if change.path.endswith(".rs") and any(
            RUST_TEST_ATTRS.match(line) for line in patch.splitlines()
        ):
            return TEST_ADDED, f"{change.path} (test attribute)"
        if change.status != "M" or not is_pin_only(patch):
            code.append(change.path)
    if code:
        return NO_TEST, ", ".join(code)
    if unknown:
        return UNCLASSIFIABLE, "no rule places " + ", ".join(unknown)
    return NOT_CODE, "docs, lockfiles, data or pins only"


def classify(repo: pathlib.Path, sha: str, base: str, subject: str) -> tuple[str, str]:
    """Classify one fix-typed commit.

    Returns:
        (class, reason): the class it landed in and the paths or rule
        that put it there.

    """
    match = FIX_SUBJECT.match(subject)
    scope = (typed_std.group(match, "scope") if match else None) or ""
    if scope in RENOVATE_SCOPES:
        return NOT_CODE, f"Renovate scope fix({scope})"
    changed = changes(repo, base, sha)
    if not changed:
        return UNCLASSIFIABLE, "empty diff"
    tests = [c.path for c in changed if c.status != "D" and is_test_path(c.path)]
    if tests:
        return TEST_ADDED, ", ".join(tests)
    return judge_paths(repo, base, sha, changed)


def fix_commits(
    repo: pathlib.Path, rev: str, since: str | None, until: str | None
) -> Iterator[tuple[str, str, str, str]]:
    """Walk the window for fix-typed commits, newest first.

    Yields:
        (sha, first parent or the empty tree, date, subject).

    """
    if rev == "HEAD" and not has_commits(repo):
        return  # a branch with no commits yet has no fixes to count
    args = ["log", "--format=%H%x1f%P%x1f%cs%x1f%s%x00"]
    if since:
        args.append(f"--since={since}")
    if until:
        args.append(f"--until={until}")
    root = ""
    for raw in git(repo, *args, rev, "--").split("\0"):
        record = raw.strip("\n")
        if not record:
            continue
        sha, parents, date, subject = record.split("\x1f", 3)
        if not FIX_SUBJECT.match(subject):
            continue
        base = parents.split(" ", 1)[0]
        if not base:
            root = root or empty_tree(repo)
            base = root
        yield sha, base, date, subject


def measure(
    repo: pathlib.Path,
    rev: str = "HEAD",
    since: str | None = None,
    until: str | None = None,
) -> list[Commit]:
    """Classify every fix-typed commit in the window.

    Returns:
        The commits, newest first.

    """
    return [
        Commit(sha, date, subject, *classify(repo, sha, base, subject))
        for sha, base, date, subject in fix_commits(repo, rev, since, until)
    ]


def ratio_text(commits: Sequence[Commit]) -> str:
    """State test added / (test added + no test).

    Returns:
        `N of M (P%)`, or `no fixes` when nothing is in either class.

    """
    added = sum(1 for c in commits if c.verdict == TEST_ADDED)
    without = sum(1 for c in commits if c.verdict == NO_TEST)
    if added + without == 0:
        return "no fixes"
    percent = 100 * added / (added + without)
    return f"{added} of {added + without} ({percent:.1f}%)"


def text_report(commits: Sequence[Commit], window: str) -> str:
    """Render the four lists and the ratio for a log.

    Returns:
        The report text.

    """
    out: list[str] = []
    for name in CLASSES:
        members = [c for c in commits if c.verdict == name]
        out.append(f"== {name}: {len(members)}")
        out.extend(
            f"  {c.sha[:12]} {c.date} {c.subject}  [{c.reason}]" for c in members
        )
    out.append(
        f"regression-count: {window}: test added / (test added + no test)"
        f" = {ratio_text(commits)}"
    )
    return "\n".join(out) + "\n"


def summary_report(
    commits: Sequence[Commit],
    recent: Sequence[Commit],
    window: str,
    recent_window: str,
) -> str:
    """Render the job summary: the ratio and the recent fixes to look at.

    Returns:
        Markdown: the window's ratio, then the recent "no test" and
        unclassifiable fixes by commit and subject.

    """
    out = [
        "## Regression tests",
        "",
        f"Fixes with a test, {window}: **{ratio_text(commits)}**.",
        "",
    ]
    for name in (NO_TEST, UNCLASSIFIABLE):
        members = [c for c in recent if c.verdict == name]
        out.extend((
            f"`fix` commits classed **{name}**, {recent_window}: {len(members)}",
            "",
        ))
        out.extend(f"- `{c.sha[:12]}` {c.date} {c.subject}" for c in members)
        if members:
            out.append("")
    return "\n".join(out) + "\n"


class Args(argparse.Namespace):
    """The command line, each option typed as parsed."""

    repo: pathlib.Path
    rev: str
    since: str | None
    until: str | None
    recent: str | None
    summary: pathlib.Path | None


def main(argv: Sequence[str] | None = None) -> int:
    """Classify, print, and optionally write the job summary.

    Returns:
        0 for every measurement, whatever the ratio; 2 when git could
        not read the repository or the clone is shallow.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path())
    parser.add_argument("--rev", default="HEAD", help="the branch tip to walk")
    parser.add_argument("--since", help="window start, any git date")
    parser.add_argument("--until", help="window end, any git date")
    parser.add_argument("--recent", help="the summary lists fixes since this")
    parser.add_argument("--summary", type=pathlib.Path, help="append markdown here")
    args = parser.parse_args(argv, namespace=Args())
    window = f"since {args.since}" if args.since else "all history"
    try:
        if git(args.repo, "rev-parse", "--is-shallow-repository").strip() == "true":
            findings.finding(
                "",
                "the clone is shallow, so the window is cut"
                " short and the ratio would be a guess; fetch full history",
            )
            return 2
        commits = measure(args.repo, args.rev, args.since, args.until)
        recent = (
            measure(args.repo, args.rev, args.recent, args.until)
            if args.recent
            else commits
        )
    except GitError as err:
        findings.finding("", str(err))
        return 2
    sys.stdout.write(text_report(commits, window))
    if args.summary:
        recent_window = f"since {args.recent}" if args.recent else window
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(summary_report(commits, recent, window, recent_window))
    return 0


if __name__ == "__main__":
    sys.exit(main())
