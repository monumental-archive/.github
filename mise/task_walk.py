#!/usr/bin/env python3
"""Walk a mise task to the scripts `mise run` would run for it.

Org canon (#1608, ruled on #1547). The coverage legs that read the
repository's own `test` task (python, plpgsql, typescript) read it
through this one walk, so they measure the run the gate makes. It asks
mise the read-only `mise tasks info <name> --json` of each task it
reaches, never a nested `mise run` (the #82 race), and decodes each
answer through one validating function, `decode`.

The order is `mise run`'s, each task once: a task's `depends` first,
post-order; then its own `run`, a `{ task }` or `{ tasks }` entry walked
in place; then its `depends_post`. A pattern (`lint:*`) is expanded
against `mise tasks ls`: `tasks info <pattern>` answers its first match
only, where a run runs every match (measured on mise 2026.11.7,
`testdata/task-references`). `wait_for` adds no task to a run and is not
followed.

A form the walk cannot run is refused by name with its task, never
skipped: a reference that passes arguments or environment (the walk
runs a script as written), a file task, a pattern that matches nothing,
and an entry of a shape mise does not document.

    task_walk.py steps <label> [<task>]
        prints one step per line, `<dir> <script>`, each base64 (`-` for
        a task with no `dir`), for a bash read loop; or the refusal on
        stderr and exit 1. <task> is `test` unless named.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeIs

if TYPE_CHECKING:
    from collections.abc import Callable


class RefusedError(Exception):
    """A task the walk cannot run as written; the message names it."""


@dataclass(frozen=True)
class Reference:
    """A `depends`, `depends_post` or run entry naming other tasks."""

    names: tuple[str, ...]
    # What the entry passes that a script run as written cannot:
    # "arguments", "environment", or "".
    passes: str
    optional: bool


@dataclass(frozen=True)
class Task:
    """One `mise tasks info --json` answer, as the walk reads it."""

    name: str
    dir: str
    run: tuple[str | Reference, ...]
    depends: tuple[Reference, ...]
    depends_post: tuple[Reference, ...]
    file: str


@dataclass(frozen=True)
class Step:
    """One script of the run, and the task and directory it runs in."""

    task: str
    dir: str
    script: str


@dataclass(frozen=True)
class Walk:
    """The tasks reached, in order, and the scripts they run."""

    tasks: tuple[str, ...]
    steps: tuple[Step, ...]


def is_object(value: object) -> TypeIs[dict[str, object]]:
    """Say whether a JSON value is an object.

    Returns:
        True for a dict keyed by strings.

    """
    return isinstance(value, dict) and all(isinstance(k, str) for k in value)


def is_array(value: object) -> TypeIs[list[object]]:
    """Say whether a JSON value is an array.

    Returns:
        True for a list.

    """
    return isinstance(value, list)


def is_strings(value: object) -> TypeIs[list[str]]:
    """Say whether a JSON value is an array of strings.

    Returns:
        True for a list whose every item is a str.

    """
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def entries(value: object, task: str, field: str) -> list[object]:
    """Read a field mise reports as one entry or an array of them.

    Returns:
        The entries; none for a missing or null field.

    Raises:
        RefusedError: the field is neither.

    """
    if value is None:
        return []
    if is_array(value):
        return value
    if isinstance(value, (str, dict)):
        return [value]
    msg = f"task '{task}': its {field} is not a form mise documents"
    raise RefusedError(msg)


def reference(entry: object, task: str, field: str) -> Reference:
    """Decode one entry that names other tasks.

    mise reports a name or pattern as a string; a depends with
    arguments as `[name, args…]`; one with a `VAR=value` prefix, or a
    table, as `{"task": …}`; a run entry's parallel tasks as
    `{"tasks": […]}` (measured, `testdata/task-references`).

    Returns:
        The reference.

    Raises:
        RefusedError: the entry has a shape mise does not document.

    """
    if isinstance(entry, str):
        return Reference((entry,), "", optional=False)
    if is_strings(entry) and entry:
        return Reference((entry[0],), "arguments" if entry[1:] else "", optional=False)
    if is_object(entry) and set(entry) <= {"task", "tasks", "args", "env", "optional"}:
        one, many = entry.get("task"), entry.get("tasks")
        names = ([one] if isinstance(one, str) else []) + (
            many if is_strings(many) else []
        )
        args, env, optional = entry.get("args"), entry.get("env"), entry.get("optional")
        if (
            names
            and (one is None or isinstance(one, str))
            and (many is None or is_strings(many))
        ):
            passes = "arguments" if args else ("environment" if env else "")
            return Reference(tuple(names), passes, optional=optional is True)
    msg = f"task '{task}': a {field} entry the walk does not read, {json.dumps(entry)}"
    raise RefusedError(msg)


def decode(name: str, answer: object) -> Task:
    """Decode `mise tasks info <name> --json`: the one reader of it.

    A field mise leaves out reads as empty.

    Returns:
        The task.

    Raises:
        RefusedError: the answer is not a task, or holds a form the walk
            does not read.

    """
    if not is_object(answer):
        msg = f"cannot read task '{name}' with mise tasks info"
        raise RefusedError(msg)
    directory, file = answer.get("dir"), answer.get("file")
    run: list[str | Reference] = [
        e if isinstance(e, str) else reference(e, name, "run")
        for e in entries(answer.get("run"), name, "run")
    ]
    return Task(
        name=name,
        dir=directory if isinstance(directory, str) else "",
        run=tuple(run),
        depends=tuple(
            reference(e, name, "depends")
            for e in entries(answer.get("depends"), name, "depends")
        ),
        depends_post=tuple(
            reference(e, name, "depends_post")
            for e in entries(answer.get("depends_post"), name, "depends_post")
        ),
        file=file if isinstance(file, str) else "",
    )


def pattern_regex(pattern: str) -> str:
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
                out += "(?:" + "|".join(pattern_regex(a) for a in body.split(",")) + ")"
            else:
                negated = body.startswith("!")
                out += "[^" + body[1:] + "]" if negated else "[" + body + "]"
            i = close + 1
            continue
        out += {"*": "[^:]*", "?": "[^:]"}.get(c, re.escape(c))
        i += 1
    return out


class Walker:
    """One walk: each task read once, its steps in `mise run`'s order."""

    def __init__(
        self,
        info: Callable[[str], object],
        names: Callable[[], list[str]],
    ) -> None:
        """Walk with these answers to `tasks info` and `tasks ls`."""
        self.info, self.names = info, names
        self.listed: list[str] | None = None
        self.tasks: list[str] = []
        self.steps: list[Step] = []

    def expand(self, ref: Reference, task: str) -> list[str]:
        """Name the tasks a reference runs, its patterns expanded.

        Returns:
            The names.

        Raises:
            RefusedError: it passes what a script run as written cannot,
                or a pattern in it matches no task.

        """
        if ref.passes:
            msg = (
                f"task '{task}' runs '{', '.join(ref.names)}' with {ref.passes},"
                " which the measured run cannot pass"
            )
            raise RefusedError(msg)
        out: list[str] = []
        for name in ref.names:
            if not any(c in name for c in "*?[{"):
                out.append(name)
                continue
            self.listed = self.names() if self.listed is None else self.listed
            matched = [n for n in self.listed if re.fullmatch(pattern_regex(name), n)]
            if not matched and not ref.optional:
                msg = f"task '{task}' runs the pattern '{name}', which matches no task"
                raise RefusedError(msg)
            out += matched
        return out

    def visit(self, name: str) -> None:
        """Walk one task, once: depends, its run, then depends_post.

        Raises:
            RefusedError: a task on the way cannot be run as written.

        """
        if name in self.tasks:
            return
        self.tasks.append(name)
        task = decode(name, self.info(name))
        if task.file:
            msg = f"task '{name}' is a file task, which the measured run does not read"
            raise RefusedError(msg)
        for ref in task.depends:
            for dep in self.expand(ref, name):
                self.visit(dep)
        for entry in task.run:
            if isinstance(entry, str):
                self.steps.append(Step(name, task.dir, entry))
                continue
            for other in self.expand(entry, name):
                self.visit(other)
        for ref in task.depends_post:
            for dep in self.expand(ref, name):
                self.visit(dep)


def walk(
    root: str,
    info: Callable[[str], object],
    names: Callable[[], list[str]],
) -> Walk:
    """Walk from a task to every script its run runs.

    `info` answers `mise tasks info <name> --json` (None when mise
    cannot), `names` every task's name, for patterns.

    A task on the way that cannot be run as written raises RefusedError
    from the walker, naming it.

    Returns:
        The tasks reached in order, and the steps.

    """
    walker = Walker(info, names)
    walker.visit(root)
    return Walk(tuple(walker.tasks), tuple(walker.steps))


def mise_json(*args: str) -> object:
    """Ask mise a read-only question in JSON.

    Returns:
        The decoded answer, or None when mise cannot give one.

    """
    if shutil.which("mise") is None:
        return None
    # ruff: ignore[subprocess-without-shell-equals-true, start-process-with-partial-path]
    r = subprocess.run(
        ["mise", *args, "--json"], capture_output=True, text=True, check=False
    )
    if r.returncode != 0:
        return None
    try:
        answer: object = json.loads(r.stdout)
    except ValueError:
        return None
    return answer


def task_names() -> list[str]:
    """Name every task mise lists, hidden ones too.

    Returns:
        The names, or none when mise cannot list them.

    """
    listed = mise_json("tasks", "ls", "--hidden")
    rows = listed if is_array(listed) else []
    out: list[str] = []
    for row in rows:
        name = row.get("name") if is_object(row) else None
        if isinstance(name, str):
            out.append(name)
    return out


def walk_mise(root: str) -> Walk:
    """Walk from a task with mise's own answers.

    Returns:
        The walk.

    """
    return walk(root, lambda n: mise_json("tasks", "info", n), task_names)


def encode(text: str) -> str:
    """Base64 one field of a step record.

    Returns:
        The encoding.

    """
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def steps_command(label: str, root: str) -> int:
    """Print the root task's steps, one record per line, or refuse.

    Returns:
        0, or 1 with the refusal on stderr.

    """
    try:
        found = walk_mise(root)
    except RefusedError as exc:
        print(f"{label}: {exc}", file=sys.stderr)
        return 1
    if not found.steps:
        print(
            f"{label}: the test task and its depends run nothing — the legs measure\n"
            "  the repository's own suite and cannot measure a task that runs nothing",
            file=sys.stderr,
        )
        return 1
    for step in found.steps:
        print(f"{encode(step.dir) if step.dir else '-'} {encode(step.script)}")
    return 0


def main(argv: list[str]) -> int:
    """Run the command line.

    Returns:
        The exit status.

    """
    match argv:
        case ["steps", label]:
            return steps_command(label, "test")
        case ["steps", label, root]:
            return steps_command(label, root)
        case _:
            print("usage: task_walk.py steps <label> [<task>]", file=sys.stderr)
            return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
