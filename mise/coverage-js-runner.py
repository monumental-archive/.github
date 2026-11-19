#!/usr/bin/env python3
"""Name a JavaScript tree's test runner and its coverage provider.

Org canon (#1357). The `typescript` leg of `coverage-measure.sh` runs the
repository's own runner under that runner's own coverage provider, and
both are READ, never chosen: the runner and the provider are whatever
the repository's lockfile pins, and the belt pins neither. A second
instrumentation of the suite (c8 or nyc beside the runner) is rejected
in the issue, and so is a provider version the belt would own where the
repository's lockfile should.

Run from the repository root, with the tracked files among
`package.json` and the lockfiles as arguments (the caller asks git, so
an untracked lockfile is never read). Prints one line, or nothing:

    vitest <provider>     the leg applies; <provider> is `v8`,
                          `istanbul`, `none` (the lockfile carries no
                          provider) or `both` (it carries two, and the leg
                          does not choose)
    unsupported <runner>  the tree has a test runner the leg does not
                          support; the caller names it and skips
    (nothing)             no tracked root package.json, or one that
                          declares no test runner at all

The leg supports vitest. Any other runner is named, from the root
package.json's dependencies or its `test` script, so a skip says what
it skipped rather than reading as a tree with nothing to measure.

`--vitest-command <label> <task>` is the second question: WHICH vitest run
the leg measures. #1357 item 2 says the leg runs the repository's own
test command, so the suite measured is the suite the gate runs (#707),
never vitest's whole configured suite where the `test` task runs a
narrower one. The helper walks from the `test` task to every task it
runs (#1608), asking `mise tasks info <name> --json` of each: its
`depends`, its `depends_post`, and each `{ task }` and `{ tasks }`
entry in its `run`, in every form mise reports them, a pattern
expanded against `mise tasks ls`. Each task is read once, so a cycle
ends. The one plain vitest invocation in their `run` scripts is
printed as JSON,
`{"dir": <the task's dir>, "args": [<its arguments>]}`, and the caller
runs the repository's own vitest with exactly those arguments plus the
coverage flags. Running the task's scripts with a coverage-adding
`vitest` first on PATH was measured and refused: `pnpm exec` and `npx`
put `node_modules/.bin` at the head of PATH, so the repository's own
binary runs and the shim never does, and vitest reads no environment
variable that enables coverage.

A task whose vitest command cannot be read is refused by name, never
guessed at: no vitest command (a `pnpm test` behind a package.json
script, say), more than one, a shell expansion in it, vitest inside a
compound line, a vitest subcommand other than `run`, or a coverage
flag the leg would contradict.
"""

import json
import re
import shlex
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# Lockfiles the leg reads, in the order a package manager would prefer.
LOCKFILES = ("pnpm-lock.yaml", "package-lock.json", "yarn.lock", "bun.lock")

# A package's entry in any of the four: pnpm's `  vitest@4.1.12:` and
# `'@vitest/coverage-v8@4.1.12':`, yarn's `vitest@^4.1.12:` and berry's
# `"vitest@npm:4.1.12":`, bun's `"vitest@4.1.12"`, and npm's
# `"node_modules/vitest":`.
ENTRY = r'(?:(?:^|[\s"\'])({name})@(?:npm:)?[\^~]?\d|"node_modules/({name})")'
RUNNER = re.compile(ENTRY.format(name="vitest"), re.MULTILINE)
PROVIDER = re.compile(
    ENTRY.format(name=r"@vitest/coverage-(?:v8|istanbul)"),
    re.MULTILINE,
)

# Runners the leg does not support, by the package that provides them.
OTHER_RUNNERS = (
    "jest",
    "mocha",
    "ava",
    "jasmine",
    "tap",
    "uvu",
    "karma",
    "@playwright/test",
    "cypress",
)

# The `test` script npm writes into a new package.json: no runner at all.
NPM_PLACEHOLDER = "no test specified"


def providers(lock: str) -> set[str]:
    """Read the vitest coverage providers a lockfile carries.

    Returns:
        `v8` and/or `istanbul`.

    """
    found = set()
    for match in PROVIDER.finditer(lock):
        package = match.group(1) or match.group(2)
        found.add(package.rsplit("-", 1)[-1])
    return found


def other_runner(manifest: dict) -> str | None:
    """Name a test runner the leg does not support.

    Returns:
        The runner's package, the first word of the `test` script, or
        None when the manifest declares no test runner.

    """
    declared: dict = {}
    for field in ("dependencies", "devDependencies"):
        section = manifest.get(field)
        if isinstance(section, dict):
            declared |= section
    for runner in OTHER_RUNNERS:
        if runner in declared:
            return runner
    scripts = manifest.get("scripts")
    script = scripts.get("test") if isinstance(scripts, dict) else None
    if isinstance(script, str) and script.strip() and NPM_PLACEHOLDER not in script:
        words = script.split()
        if words[:2] == ["node", "--test"]:
            return "node --test"
        return words[0]
    return None


# What may stand before `vitest` on its line: the package managers'
# ways of running a dependency's binary, or nothing. A path ending in
# `/vitest` (`node_modules/.bin/vitest`) is the binary itself.
LAUNCHERS = (
    (),
    ("pnpm",),
    ("pnpm", "exec"),
    ("npx",),
    ("npx", "--no-install"),
    ("npx", "--no"),
    ("yarn",),
    ("yarn", "exec"),
    ("npm", "exec"),
    ("npm", "exec", "--"),
)

# Characters that make a line more than one plain command, or make its
# arguments something only the shell can resolve.
COMPOUND = re.compile(r"[;&|<>()]")
EXPANSION = re.compile(r"[$`]")

# vitest subcommands other than `run`; arguments that start with neither
# are file filters, which `vitest run` takes as they are.
SUBCOMMANDS = frozenset(
    {"watch", "dev", "bench", "init", "list", "related", "typecheck"},
)

FORMS = (
    "`vitest …`, `pnpm exec vitest …`, `npx vitest …`, `yarn vitest …`"
    " or `node_modules/.bin/vitest …`"
)


def logical_lines(script: str) -> list[str]:
    """Split a run script into lines, joining backslash continuations.

    Returns:
        The script's logical lines, comments and blanks dropped.

    """
    out: list[str] = []
    pending = ""
    for raw in script.splitlines():
        line = pending + raw
        if line.rstrip().endswith("\\"):
            pending = line.rstrip()[:-1] + " "
            continue
        pending = ""
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            out.append(stripped)
    if pending.strip():
        out.append(pending.strip())
    return out


def is_vitest(word: str) -> bool:
    """Say whether a shell word names the vitest binary.

    Returns:
        True for `vitest` and for a path ending in `/vitest`.

    """
    return word == "vitest" or word.endswith("/vitest")


def refusal(line: str, words: list[str]) -> str | None:
    """Say why a line that runs vitest cannot be read, if it cannot.

    Returns:
        The reason, or None when the line is one plain invocation.

    """
    at = next(i for i, w in enumerate(words) if is_vitest(w))
    checks = (
        (EXPANSION.search(line), f"a shell expansion in `{line}`"),
        (COMPOUND.search(line), f"vitest inside a compound line, `{line}`"),
        (
            tuple(words[:at]) not in LAUNCHERS,
            f"vitest behind a command the leg does not read, `{line}`",
        ),
    )
    return next((reason for failed, reason in checks if failed), None)


def read_line(line: str) -> tuple[list[str] | None, str | None]:
    """Read one logical line for a vitest invocation.

    Returns:
        The arguments after `vitest` and None; None and the reason the
        line is refused; or (None, None) when the line runs no vitest.

    """
    try:
        words = shlex.split(line, comments=True)
    except ValueError:
        reason = f"an unreadable line, `{line}`" if "vitest" in line else None
        return None, reason
    if not any(is_vitest(w) for w in words):
        return None, None
    reason = refusal(line, words)
    if reason is not None:
        return None, reason
    at = next(i for i, w in enumerate(words) if is_vitest(w))
    return words[at + 1 :], None


def entry_names(entry: object) -> list[str]:
    """Name the tasks one `depends`, `depends_post` or `run` entry runs.

    The forms are mise's as `tasks info --json` reports them (#1608,
    measured at the belt's pin in testdata/task-references): a name or
    pattern; `[name, args…]`, which is also how a depends string with
    arguments is reported; `{"task": name, …}`, also an env-prefixed
    depends string; and a run entry's `{"tasks": [names]}`.

    Returns:
        The names and patterns the entry runs; none for a run script.

    """
    if isinstance(entry, str):
        return [entry]
    if isinstance(entry, list):
        return entry[:1] if entry and isinstance(entry[0], str) else []
    if not isinstance(entry, dict):
        return []
    named = entry.get("tasks")
    names = [n for n in named if isinstance(n, str)] if isinstance(named, list) else []
    task = entry.get("task")
    return [task, *names] if isinstance(task, str) else names


def references(task: dict) -> list[str]:
    """Name every task a task runs: depends, depends_post, run entries.

    `wait_for` is not one: it adds no task to the run.

    Returns:
        The names and patterns, in the order mise lists them.

    """
    out: list[str] = []
    for key in ("depends", "depends_post", "run"):
        entries = task.get(key) or []
        if key == "run" and isinstance(entries, str):
            continue
        for entry in entries if isinstance(entries, list) else [entries]:
            if key != "run" or not isinstance(entry, str):
                out += entry_names(entry)
    return out


def glob_regex(pattern: str) -> str:
    """Translate a mise task pattern to a regular expression.

    mise's wildcards (docs/tasks/running-tasks.md): `?` one character,
    `*` any characters within one `:`-separated group, `**` any number
    of groups, `{a,b}` either, `[abc]` and `[!abc]` a set.

    Returns:
        The expression, to be matched in full against a task name.

    """
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**", i):
            out, i = out + ".*", i + 2
            continue
        close = pattern.find("}" if c == "{" else "]", i)
        if c in "{[" and close > i:
            body = pattern[i + 1 : close]
            if c == "{":
                out += "(?:" + "|".join(glob_regex(a) for a in body.split(",")) + ")"
            else:
                negated = body.startswith("!")
                out += "[^" + body[1:] + "]" if negated else "[" + body + "]"
            i = close + 1
            continue
        out += {"*": "[^:]*", "?": "[^:]"}.get(c, re.escape(c))
        i += 1
    return out


def walk(
    root: str,
    info: Callable[[str], dict | None],
    names: Callable[[], list[str]],
) -> tuple[list[dict], str | None]:
    """Read the root task and every task it runs, each once.

    `info` answers `mise tasks info <name> --json` (None when mise
    cannot), and `names` every task's name, for patterns.

    Returns:
        Each task read, with its name, in the order reached; and the
        name mise could not read, or None.

    """
    queue, seen, tasks = [root], set(), []
    listed: list[str] | None = None
    while queue:
        name = queue.pop(0)
        if any(c in name for c in "*?[{"):
            listed = names() if listed is None else listed
            queue[:0] = [n for n in listed if re.fullmatch(glob_regex(name), n)]
            continue
        if name in seen:
            continue
        seen.add(name)
        task = info(name)
        if not isinstance(task, dict):
            return tasks, name
        tasks.append(task | {"name": name})
        queue += references(task)
    return tasks, None


def collect(tasks: list[dict]) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Read every task's run scripts for vitest invocations.

    Returns:
        Each readable invocation as (its task's dir, its arguments), and
        each refused line as `<task>: <reason>`.

    """
    found: list[tuple[str, list[str]]] = []
    refused: list[str] = []
    for task in tasks:
        run = task.get("run") or []
        for script in [run] if isinstance(run, str) else run:
            # A `{ task = … }` entry runs another task (#1555), which
            # walk() reads as a task of its own: no script of this one's.
            if not isinstance(script, str):
                continue
            for line in logical_lines(script):
                args, reason = read_line(line)
                if reason is not None:
                    refused.append(f"{task.get('name', 'test')}: {reason}")
                elif args is not None:
                    found.append((task.get("dir") or ".", args))
    return found, refused


def problems(
    found: list[tuple[str, list[str]]], refused: list[str], walked: list[str]
) -> list[str]:
    """Say why the collected invocations are not one measurable run.

    Returns:
        The refusal's lines after its head, or none when there is one
        plain `vitest run`.

    """
    if refused:
        return [
            ", and cannot read it —",
            *(f"  {reason}" for reason in refused),
            f"  write it as one plain line: {FORMS}",
        ]
    if not found:
        return [
            ", and the test task and its depends run none —",
            f"  tasks walked: {', '.join(walked)}",
            f"  run vitest from the test task as {FORMS}",
        ]
    if len(found) > 1:
        return [
            f", and the test task and its depends run {len(found)} —",
            "  the leg measures one suite; run vitest once",
        ]
    args = found[0][1]
    if args and args[0] in SUBCOMMANDS:
        return [
            f", and `vitest {args[0]}` is not a run —",
            "  the leg measures `vitest run`",
        ]
    if any(a.startswith("--coverage") for a in args):
        return [
            ", and it sets coverage itself —",
            "  the leg sets the coverage flags; drop them from the test task",
        ]
    return []


def vitest_command(label: str, tasks: list[dict]) -> int:
    """Print the walked tasks' one vitest invocation, or refuse it by name.

    Returns:
        0 with the JSON on stdout, or 1 with the refusal on stderr.

    """
    found, refused = collect(tasks)
    walked = [str(t.get("name", "test")) for t in tasks]
    lines = problems(found, refused, walked)
    if lines:
        head = f"{label}: typescript: the leg measures the test task's own"
        print(f"{head} vitest command{lines[0]}", file=sys.stderr)
        for line in lines[1:]:
            print(line, file=sys.stderr)
        return 1
    directory, args = found[0]
    if not args or args[0] != "run":
        args = ["run", *args]
    print(json.dumps({"dir": directory, "args": args}))
    return 0


def main(tracked: list[str]) -> int:
    """Print the runner line for the tree in the working directory.

    Returns:
        0, or 1 when the root package.json is not readable JSON.

    """
    if "package.json" not in tracked:
        return 0
    try:
        manifest = json.loads(Path("package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"coverage-js-runner: package.json: {exc}", file=sys.stderr)
        return 1
    if not isinstance(manifest, dict):
        manifest = {}
    lockfile = next((name for name in LOCKFILES if name in tracked), None)
    lock = Path(lockfile).read_text(encoding="utf-8") if lockfile else ""
    if RUNNER.search(lock):
        found = providers(lock)
        provider = found.pop() if len(found) == 1 else ("both" if found else "none")
        print(f"vitest {provider}")
        return 0
    runner = other_runner(manifest)
    if runner == "vitest" or (runner is None and "vitest" in json.dumps(manifest)):
        # Declared, but the lockfile pins no vitest: nothing the leg may run.
        print("unsupported vitest-without-a-lockfile-entry")
        return 0
    if runner is not None:
        print(f"unsupported {runner}")
    return 0


def mise_json(*args: str) -> object:
    """Ask mise a read-only question in JSON.

    Returns:
        The decoded answer, or None when mise cannot give one.

    """
    mise = shutil.which("mise")
    if mise is None:
        return None
    # ruff: ignore[subprocess-without-shell-equals-true]
    r = subprocess.run(
        [mise, *args, "--json"], capture_output=True, text=True, check=False
    )
    try:
        return json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        return None


def task_names() -> list[str]:
    """Name every task mise lists, hidden ones too.

    Returns:
        The names, or none when mise cannot list them.

    """
    listed = mise_json("tasks", "ls", "--hidden")
    rows = listed if isinstance(listed, list) else []
    return [r["name"] for r in rows if isinstance(r, dict) and "name" in r]


def walked_command(label: str, root: str) -> int:
    """Walk from the root task and print its one vitest invocation.

    Returns:
        vitest_command's status, or 1 when a task cannot be read.

    """
    tasks, unread = walk(root, lambda n: mise_json("tasks", "info", n), task_names)
    if unread is not None:
        print(
            f"{label}: typescript: cannot read task '{unread}' with mise tasks info",
            file=sys.stderr,
        )
        return 1
    return vitest_command(label, tasks)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--vitest-command"]:
        sys.exit(walked_command(sys.argv[2], sys.argv[3]))
    sys.exit(main(sys.argv[1:]))
