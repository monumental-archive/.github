#!/usr/bin/env python3
"""What a change touched, for `ci:mutants` (#1432).

`ci:mutants` runs the belt's mutation legs on what each change touches:
a surviving mutant on a line the change touched reds the gate, whether
the change made it or it was already there. You touch it, you kill it.
This module is the one reader of "what the change touched" that every
leg asks, so no two legs can read a change differently.

THE BASE IS THE EVENT'S OWN (`base`). ci.yml hands the event's base in
`ORG_MUTANTS_BASE`: the pull request's base commit, the merge queue's
base, or the commit before a push. Unset, as on a laptop, the base is
the merge base of HEAD with the default branch (`origin/HEAD`, else
`origin/main`). An empty base, git's all-zero commit (a new branch's
first push) or no default branch to meet is NO BASE, and the leg
judges the whole population. A base that names no commit in this
history is red by name: the event said there was one, so "whole" would
hide that the history is not the event's.

THE DIFF IS SPELLED IN FULL. Every git setting that changes what
`git diff` prints is pinned on its command line, where a flag outranks
any config: no colour, no external diff, no textconv, the `a/` and `b/`
prefixes, renames found, Myers with the indent heuristic, no relative
paths, zero context. Measured (#1432): cosmic-ray's own git filter runs
a bare `git diff`, and under `diff.noprefix`, `diff.mnemonicPrefix` or
`color.diff=always`, in a user's or a clone's config, it selected no
mutant and exited 0: a green whose cause is outside the tree. The diff
is of two commits, the base and HEAD, never of the working tree: every
leg refuses a dirty tree before it mutates anything.

A CHANGED LINE is one the diff adds on HEAD's side (`+start,count`). A
line only deleted changes no line of HEAD, so nothing there is judged;
a rename with no edit touches no line.

THE WHOLE POPULATION (`scope`) is judged when there is no base, when
the change touches the language's adoption file, or when it moves the
language's mutation tool in a lockfile the tree tracks. Each case is
named. A tool the tree does not lock (the belt's tools, locked in the
belt's own mise.lock, which a consumer does not track) moves with the
belt's pin; the weekly run judges that.

    mutants_diff.py scope LANGUAGE OUT   decide, and write OUT/scope,
                                         OUT/lines.tsv and OUT/patch.diff

`OUT/scope` holds `whole` or `diff`; `OUT/lines.tsv` one `path<TAB>line`
per changed line; `OUT/patch.diff` the diff itself, for a tool that
reads one (cargo-mutants' `--in-diff`). One line says what is judged.

stdlib only.
"""

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import decoded
import findings
import tracked

GIT = shutil.which("git") or "git"
ZERO = re.compile(r"^0+$")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
USAGE = "usage: mutants_diff.py scope <language> <out-dir>"
USAGE_EXIT = 2
ARGS = 3
# Every flag that pins what `git diff` prints, where config could change
# it. Flags outrank config.
DIFF_FLAGS = (
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--src-prefix=a/",
    "--dst-prefix=b/",
    "--find-renames",
    "--diff-algorithm=myers",
    "--indent-heuristic",
    "--no-relative",
    "--ignore-submodules=none",
    "--unified=0",
)
# Each language's adoption files, by git pathspec, and its mutation tool
# in each lockfile a tree may track: the lockfile's name, and the key
# its entry is under (a mise.lock tool, an npm package-name prefix).
ADOPTION: dict[str, tuple[str, ...]] = {
    "go": (".gomuignore", "*/.gomuignore"),
    "python": ("cosmic-ray.toml",),
    "rust": (".cargo/mutants.toml",),
    "typescript": ("stryker.config.json",),
}
MISE_LOCKS = ("mise.lock", "mise/mise.lock", ".config/mise/mise.lock")
MISE_TOOLS: dict[str, str] = {
    "go": "go:github.com/sivchari/gomu/cmd/gomu",
    "python": "pipx:cosmic-ray",
    "rust": "cargo:cargo-mutants",
}
NPM_SCOPE = "@stryker-mutator/"
NPM_LOCKS = ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock")
# A StrykerJS package and its version, as the text lockfiles spell it.
NPM_TEXT = re.compile(r"@stryker-mutator/[A-Za-z0-9._-]+@[0-9][^\s'\":,()]*")


def git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run git here, its output captured as text.

    Returns:
        The finished process.

    """
    return subprocess.run(
        [GIT, "-c", "core.quotePath=false", *args],
        capture_output=True,
        text=True,
        check=False,
    )


class BaseError(ValueError):
    """The event named a base this history does not hold."""


def commit(name: str) -> str | None:
    """Resolve a name to a commit of this history.

    Returns:
        Its full id, or None when it names no commit.

    """
    done = git("rev-parse", "--verify", "--quiet", f"{name}^{{commit}}")
    return done.stdout.strip() if done.returncode == 0 else None


def default_branch() -> str | None:
    """Name the remote default branch this clone knows.

    Returns:
        `origin/HEAD`'s target, else `origin/main`, or None for neither.

    """
    head = git("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if head.returncode == 0 and commit(head.stdout.strip()):
        return head.stdout.strip()
    return "refs/remotes/origin/main" if commit("refs/remotes/origin/main") else None


def base(env: dict[str, str]) -> str | None:
    """Find the change's base: the event's, or the merge base locally.

    Returns:
        The base commit, or None for no base.

    Raises:
        BaseError: the event named a commit this history does not hold.

    """
    if "ORG_MUTANTS_BASE" in env:
        named = env["ORG_MUTANTS_BASE"].strip()
        if not named or ZERO.match(named):
            return None
        found = commit(named)
        if found is None:
            msg = f"ORG_MUTANTS_BASE={named} names no commit in this history"
            raise BaseError(msg)
        return found
    branch = default_branch()
    if branch is None:
        return None
    met = git("merge-base", "HEAD", branch)
    return met.stdout.strip() if met.returncode == 0 and met.stdout.strip() else None


def unquote(path: str) -> str:
    r"""Read a path as git's diff header spells it.

    git quotes a name holding a `"`, a `\`, a control character or (with
    core.quotePath, pinned off here) a non-ASCII byte, C-style.

    Returns:
        The path, unquoted.

    """
    if not (path.startswith('"') and path.endswith('"')):
        return path
    raw = path[1:-1].encode("latin-1", "backslashreplace")
    return os.fsdecode(raw.decode("unicode_escape").encode("latin-1"))


@dataclass
class Diff:
    """A change, read: each path's changed lines on HEAD's side."""

    text: str = ""
    lines: dict[str, set[int]] = field(default_factory=dict)


def read_diff(text: str) -> Diff:
    """Read a zero-context unified diff.

    Returns:
        Each new-side path's added lines; a deleted file adds none.

    """
    out = Diff(text)
    path: str | None = None
    for line in text.splitlines():
        if line.startswith("+++ "):
            name = unquote(line[4:])
            path = name[2:] if name.startswith("b/") else None
            continue
        hunk = HUNK.match(line)
        if hunk is None or path is None:
            continue
        start = int(hunk.group(1))
        count = 1 if hunk.group(2) is None else int(hunk.group(2))
        out.lines.setdefault(path, set()).update(range(start, start + count))
    return out


def diff(since: str) -> Diff:
    """Diff the base against HEAD, every printing setting pinned.

    Returns:
        The change.

    Raises:
        BaseError: git could not diff the two commits.

    """
    done = git("diff", *DIFF_FLAGS, since, "HEAD", "--")
    if done.returncode != 0:
        msg = f"git diff {since} HEAD failed: {done.stderr.strip()}"
        raise BaseError(msg)
    return read_diff(done.stdout)


def show(rev: str, path: str) -> str | None:
    """Read one file as a commit holds it.

    Returns:
        Its text, or None where the commit has no such file.

    """
    done = git("show", f"{rev}:{path}")
    return done.stdout if done.returncode == 0 else None


def mise_entry(text: str | None, tool: str) -> object:
    """Read one tool's entry of a mise.lock.

    Returns:
        The entry, or None where the lock has none or does not decode.

    """
    if text is None:
        return None
    try:
        tools = decoded.as_object(decoded.load_toml(text).get("tools", {}), "tools")
    except decoded.UndecodableError:
        return text
    return tools.get(tool)


def npm_entries(name: str, text: str | None) -> object:
    """Read the StrykerJS packages a JavaScript lockfile resolves.

    Returns:
        package-lock's `packages` entries under the scope, or each text
        lockfile's `@stryker-mutator/<package>@<version>` spellings;
        None where the commit has no such file.

    """
    if text is None:
        return None
    if name.endswith(".json"):
        try:
            packages = decoded.as_object(
                decoded.as_object(decoded.load_json(text), name).get("packages", {}),
                f"{name} packages",
            )
        except decoded.UndecodableError:
            return text
        return {key: value for key, value in packages.items() if NPM_SCOPE in key}
    return sorted({found.group(0) for found in NPM_TEXT.finditer(text)})


def moved_tool(language: str, since: str, touched: set[str]) -> str | None:
    """Say whether the change moves the language's tool in a tracked lockfile.

    Returns:
        The lockfile it moved in, or None.

    """
    tool = MISE_TOOLS.get(language)
    for lock in MISE_LOCKS if tool else ():
        if lock in touched and tool is not None:
            before = mise_entry(show(since, lock), tool)
            if before != mise_entry(show("HEAD", lock), tool):
                return f"{lock} moves {tool}"
    if language != "typescript":
        return None
    for lock in sorted(p for p in touched if p.rsplit("/", 1)[-1] in NPM_LOCKS):
        leaf = lock.rsplit("/", 1)[-1]
        before = npm_entries(leaf, show(since, lock))
        if before != npm_entries(leaf, show("HEAD", lock)):
            return f"{lock} moves {NPM_SCOPE}*"
    return None


def changed_paths(since: str) -> set[str]:
    """Name every path the change adds, deletes, edits or renames.

    Returns:
        Both sides of each rename, and every other path once.

    """
    done = git("diff", "--name-status", "-z", "--no-renames", since, "HEAD", "--")
    fields = [f for f in done.stdout.split("\0") if f]
    return set(fields[1::2])


@dataclass
class Scope:
    """What one language's leg judges."""

    whole: bool
    reason: str
    change: Diff = field(default_factory=Diff)


def scope(language: str, env: dict[str, str]) -> Scope:
    """Decide what the language's leg judges for this change.

    Returns:
        The whole population, with why, or the changed lines. A base the
        history lacks raises BaseError, from `base`.

    """
    since = base(env)
    if since is None:
        return Scope(whole=True, reason="no base to diff against")
    touched = changed_paths(since)
    adopted = set(tracked.tracked(*ADOPTION[language])) & touched
    if adopted:
        return Scope(whole=True, reason=f"the change touches {min(adopted)}")
    moved = moved_tool(language, since, touched)
    if moved is not None:
        return Scope(whole=True, reason=f"the change {moved}")
    return Scope(
        whole=False, reason=f"lines changed since {since[:12]}", change=diff(since)
    )


def write(out: Path, found: Scope) -> None:
    """Write the decision where the leg reads it."""
    out.mkdir(parents=True, exist_ok=True)
    kind = "whole" if found.whole else "diff"
    (out / "scope").write_text(f"{kind}\n", encoding="utf-8")
    rows = sorted((p, n) for p, lines in found.change.lines.items() for n in lines)
    (out / "lines.tsv").write_text(
        "".join(f"{p}\t{n}\n" for p, n in rows), encoding="utf-8"
    )
    (out / "patch.diff").write_text(found.change.text, encoding="utf-8")
    (out / "reason").write_text(found.reason + "\n", encoding="utf-8")


def read_lines(out: Path) -> dict[str, set[int]]:
    """Read the changed lines a `scope` wrote.

    Returns:
        Each path's changed lines.

    """
    lines: dict[str, set[int]] = {}
    for row in (out / "lines.tsv").read_text(encoding="utf-8").splitlines():
        path, _, number = row.rpartition("\t")
        if path and number.isdigit():
            lines.setdefault(path, set()).add(int(number))
    return lines


def touches(lines: dict[str, set[int]], path: str, first: int, last: int) -> bool:
    """Say whether a mutant's span holds a changed line.

    Returns:
        True when any line from `first` to `last` of `path` changed.

    """
    return bool(lines.get(path, set()) & set(range(first, max(first, last) + 1)))


def main(argv: list[str]) -> int:
    """Decide one language's scope and write it.

    Returns:
        0, 1 for a base the history does not hold, 2 for a bad command line.

    """
    if len(argv) != ARGS or argv[0] != "scope" or argv[1] not in ADOPTION:
        print(USAGE, file=sys.stderr)
        return USAGE_EXIT
    language, out = argv[1], Path(argv[2])
    try:
        found = scope(language, dict(os.environ))
    except BaseError as error:
        findings.finding(language, str(error))
        return 1
    write(out, found)
    what = "the whole population" if found.whole else "the changed lines"
    print(f"ci:mutants: {language}: judging {what}: {found.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
