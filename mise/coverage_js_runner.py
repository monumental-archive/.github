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
narrower one. The scripts are the `test` task's run as `task_walk.py`
walks it (#1608), the one walk every leg reads. The one plain vitest
invocation in them is printed as JSON,
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
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

# The one walk of a task (#1608), a module beside this script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import decoded
import task_walk
import typed_std

if TYPE_CHECKING:
    from collections.abc import Sequence

    from task_walk import Step, Walk

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
    found: set[str] = set()
    for match in PROVIDER.finditer(lock):
        # PROVIDER takes one of its two groups in every match.
        package = typed_std.group(match, 1) or typed_std.group(match, 2) or ""
        found.add(package.rsplit("-", 1)[-1])
    return found


@dataclass(frozen=True)
class Manifest:
    """What the leg reads of a root package.json."""

    # Every package named in `dependencies` and `devDependencies`.
    declared: frozenset[str]
    # The `scripts.test` string, if there is one.
    test_script: str | None
    # Whether `vitest` appears anywhere in the manifest.
    mentions_vitest: bool


def read_manifest(value: object) -> Manifest:
    """Decode a package.json for the fields the leg reads.

    npm's own reading is the leg's: a manifest that is not an object, or
    a section or script of another shape, declares nothing.

    Returns:
        The manifest.

    """
    manifest = value if decoded.is_object(value) else {}
    declared: set[str] = set()
    for field in ("dependencies", "devDependencies"):
        section = manifest.get(field)
        if decoded.is_object(section):
            declared |= section.keys()
    scripts = manifest.get("scripts")
    script = scripts.get("test") if decoded.is_object(scripts) else None
    return Manifest(
        declared=frozenset(declared),
        test_script=script if isinstance(script, str) else None,
        mentions_vitest="vitest" in json.dumps(manifest),
    )


def other_runner(manifest: Manifest) -> str | None:
    """Name a test runner the leg does not support.

    Returns:
        The runner's package, the first word of the `test` script, or
        None when the manifest declares no test runner.

    """
    for runner in OTHER_RUNNERS:
        if runner in manifest.declared:
            return runner
    script = manifest.test_script
    if script is not None and script.strip() and NPM_PLACEHOLDER not in script:
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


def collect(steps: Sequence[Step]) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Read every step's script for vitest invocations.

    Returns:
        Each readable invocation as (its task's dir, its arguments), and
        each refused line as `<task>: <reason>`.

    """
    found: list[tuple[str, list[str]]] = []
    refused: list[str] = []
    for step in steps:
        for line in logical_lines(step.script):
            args, reason = read_line(line)
            if reason is not None:
                refused.append(f"{step.task}: {reason}")
            elif args is not None:
                found.append((step.dir or ".", args))
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


def vitest_command(label: str, walked: Walk) -> int:
    """Print the walked steps' one vitest invocation, or refuse it by name.

    Returns:
        0 with the JSON on stdout, or 1 with the refusal on stderr.

    """
    found, refused = collect(walked.steps)
    lines = problems(found, refused, list(walked.tasks))
    if lines:
        head = f"{label}: typescript: the leg measures the test task's own"
        print(f"{head} vitest command{lines[0]}", file=sys.stderr)
        for line in lines[1:]:
            print(line, file=sys.stderr)
        return 1
    directory, args = found[0]
    if not args or args[0] != "run":
        args = ["run", *args]
    record: dict[str, object] = {"dir": directory, "args": args}
    print(json.dumps(record))
    return 0


def walked_command(label: str, root: str) -> int:
    """Walk from the root task with mise and print its vitest command.

    Returns:
        vitest_command's status, or 1 when the walk refuses a task.

    """
    try:
        walked = task_walk.walk_mise(root)
    except task_walk.RefusedError as exc:
        print(f"{label}: typescript: {exc}", file=sys.stderr)
        return 1
    return vitest_command(label, walked)


def main(tracked: list[str]) -> int:
    """Print the runner line for the tree in the working directory.

    Returns:
        0, or 1 when the root package.json is not readable JSON.

    """
    if "package.json" not in tracked:
        return 0
    try:
        manifest = read_manifest(decoded.load_json(Path("package.json").read_bytes()))
    except (OSError, decoded.UndecodableError) as exc:
        print(f"coverage-js-runner: package.json: {exc}", file=sys.stderr)
        return 1
    lockfile = next((name for name in LOCKFILES if name in tracked), None)
    lock = Path(lockfile).read_text(encoding="utf-8") if lockfile else ""
    if RUNNER.search(lock):
        found = providers(lock)
        provider = found.pop() if len(found) == 1 else ("both" if found else "none")
        print(f"vitest {provider}")
        return 0
    runner = other_runner(manifest)
    if runner == "vitest" or (runner is None and manifest.mentions_vitest):
        # Declared, but the lockfile pins no vitest: nothing the leg may run.
        print("unsupported vitest-without-a-lockfile-entry")
        return 0
    if runner is not None:
        print(f"unsupported {runner}")
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--vitest-command"]:
        sys.exit(walked_command(sys.argv[2], sys.argv[3]))
    sys.exit(main(sys.argv[1:]))
