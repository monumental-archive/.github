#!/usr/bin/env python3
"""Name the belt tools the gate installs for this tree (#1410).

The gate job used to install the whole belt in every repository and then
skip the lints that did not apply: 34 tools for 74 lints, 29 of which
printed "skipped" on stele. `mise/gate-tools.toml` says, for every tool
the belt pins, the tracked-file condition under which some gate task that
calls it does not skip; this helper reads the tracked files (mise/tracked.py)
against it.

A tool is needed when any of:

- its table row carries no `when` (every repository needs it);
- one of its `when` pathspecs matches a tracked file, with git's default
  pathspec meaning: a pattern without a glob character names a file or a
  directory, and a glob's `*` crosses `/`;
- a tool it is an install dependency of (`for`) is needed: uv installs
  every `pipx:` tool;
- the caller's OWN tasks call it. The gate runs the repository's `test`,
  `build` and `lint:*` tasks as well as the belt's, and they reach belt
  tools through the global config, so their bodies are read here: the
  caller's mise config, its file-task directories, and each tracked file
  a task body names by path. A call the scan cannot see (a tool spawned
  by a compiled test) fails loudly as "command not found": every tool is
  absent, never stale.

The tools the tree does not need are DISABLED, not listed: the gate job
sets `MISE_DISABLE_TOOLS` and installs with no `install_args`, so the
caller's own config installs exactly as it always did — every version,
`os` filters, every config file — and nothing of mise is reimplemented
here (#1410, amended: naming the caller's pins in `install_args` ignores
`os` filters, measured). Measured on mise 2026.11.6, a disabled tool is
not installed by `mise install`, not auto-installed by `mise run`, and
not on PATH: absent, so a lint that needs it reds by name. A tool the
repository pins itself is never disabled: mise disables by tool name,
and the repository's own pin would go with it (the name rule is
`release/caller_pins.py`'s).

    gate_tools.py [--root DIR] [--table FILE] list
        prints one needed belt key per line, the reason for every belt
        tool on stderr;
    gate_tools.py [--root DIR] [--table FILE] disable
        appends `MISE_DISABLE_TOOLS=<keys>` to $GITHUB_ENV, and the set's
        digest as `ORG_GATE_BELT_DIGEST` for the tool cache's key
        (mise/gate_cache_key.py), printing the reasons. Nothing disabled
        writes nothing.

The table test (`mise/test_gate_tools.py`) holds the table to the tasks:
every tool a gate task calls has a row, under a condition no narrower
than that task's guard.
"""

import argparse
import fnmatch
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# The canon's one reader of a caller's pins, beside this tree in release/.
# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "release"))

import caller_pins
import decoded
import findings
import typed_std
from tracked import tracked as git_tracked

HERE = Path(__file__).resolve().parent
TABLE = HERE / "gate-tools.toml"

# mise's own homes for a repository's tasks, besides its config's [tasks].
TASK_DIRS = (
    "mise-tasks/",
    ".mise-tasks/",
    "mise/tasks/",
    ".mise/tasks/",
    ".config/mise/tasks/",
)
CONFIGS = ("mise.toml", ".mise.toml", "mise/config.toml", ".config/mise.toml")

# A command stands at the start of a line, after a separator or
# keyword, or behind `xargs` and its flags, with any VAR=value prefixes.
_LEAD = (
    r"(?:^|[|;&(!]|\{\s|\$\(|`"
    r"|\b(?:xargs(?:\s+-[-\w=]+)*|then|do|else|if|elif|while|until|exec|time"
    r"|command\s+-v|env(?:\s+-u\s+\w+)*))"
    r"\s*(?:[A-Za-z_]\w*=[^\s(`]*\s+)*"
)


tool_name = caller_pins.tool_name
pinned_names = caller_pins.pinned_names


def strip_literals(text: str) -> str:
    """Blank out quoted text that cannot run a command.

    Single-quoted text never runs anything (a jq or awk program, a
    message); double-quoted text runs only inside `$(...)`, so a double
    quote holding none is blanked too. Continued lines are joined.

    Returns:
        The text with those literals emptied, line count preserved.

    """
    text = re.sub(r"\\\n", " \n", text)
    text = re.sub(r"'[^'\n]*'", "''", text)
    return re.sub(r'"(?:[^"\\\n]|\\.)*"', _keep_substitution, text)


def _keep_substitution(m: re.Match[str]) -> str:
    whole = typed_std.group(m) or ""
    return whole if "$(" in whole else '""'


def shell_calls(text: str, commands: list[str]) -> int | None:
    """Find the first line where shell code runs one of the commands.

    A `cargo-<sub>` command also counts as `cargo <sub>`.

    Returns:
        The 0-based line index of the first call, or None.

    """
    pattern = _call_pattern(tuple(commands))
    for i, line in enumerate(strip_literals(text).splitlines()):
        if not re.match(r"\s*#", line) and pattern.search(line):
            return i
    return None


# One compiled pattern per command list, built on first use.
_PATTERNS: dict[tuple[str, ...], re.Pattern[str]] = {}


def _call_pattern(commands: tuple[str, ...]) -> re.Pattern[str]:
    if commands in _PATTERNS:
        return _PATTERNS[commands]
    forms: list[str] = []
    for cmd in commands:
        forms.append(re.escape(cmd))
        if cmd.startswith("cargo-"):
            forms.append(r"cargo\s+" + re.escape(cmd[len("cargo-") :]))
    pattern = re.compile(_LEAD + "(?:" + "|".join(forms) + r")(?![\w./-]|\+?=)")
    _PATTERNS[commands] = pattern
    return pattern


def matches(pattern: str, path: str) -> bool:
    """Match a path against one pathspec, with git's default meaning.

    Returns:
        True when the pattern names the path, a directory above it, or
        (with a glob character) matches it with `*` crossing `/`.

    """
    if any(c in pattern for c in "*?["):
        return fnmatch.fnmatchcase(path, pattern)
    base = pattern.rstrip("/")
    return path == base or path.startswith(base + "/")


@dataclass(frozen=True)
class Row:
    """One tool's row of the table, as decoded from it."""

    tasks: tuple[str, ...] = ()
    when: tuple[str, ...] | None = None  # None: every repository
    needs: tuple[str, ...] = ()  # the row's `for`: installed for these keys
    run: tuple[str, ...] | None = None  # None: the key's own tool name


def _strings(row: dict[str, object], field: str, where: str) -> tuple[str, ...] | None:
    """Read one optional list-of-strings field of a row.

    Returns:
        The strings, or None when the row does not carry the field.

    Raises:
        UndecodableError: the field is not a list of strings.

    """
    if field not in row:
        return None
    value = row[field]
    if not decoded.is_strings(value):
        msg = f"{where}.{field} is not a list of strings"
        raise decoded.UndecodableError(msg)
    return tuple(value)


def row_of(key: str, value: object) -> Row:
    """Decode one row of the table.

    A row or a field of the wrong shape raises `UndecodableError`.

    Returns:
        The row.

    """
    where = f"tools.{key}"
    row = decoded.as_object(value, where)
    return Row(
        tasks=_strings(row, "tasks", where) or (),
        when=_strings(row, "when", where),
        needs=_strings(row, "for", where) or (),
        run=_strings(row, "run", where),
    )


def load_table(path: Path = TABLE) -> dict[str, Row]:
    """Read the table.

    Returns:
        Belt key to its row: `tasks`, optional `when`, `for`, `run`.

    Raises:
        UndecodableError: the file is not TOML, or a row has the wrong
            shape; the text names the file and the field.

    """
    try:
        doc = decoded.load_toml(path.read_bytes())
        tools = decoded.as_object(doc.get("tools"), "[tools]")
        return {key: row_of(key, value) for key, value in tools.items()}
    except decoded.UndecodableError as err:
        msg = f"{path}: {err}"
        raise decoded.UndecodableError(msg) from None


def commands(key: str, row: Row) -> list[str]:
    """Name the commands a tool puts on PATH that the belt runs.

    Returns:
        The row's `run` list, or the key's tool name.

    """
    return list(row.run) if row.run is not None else [tool_name(key)]


def caller_text(root: Path, tracked: list[str]) -> str:
    """Gather the code of the caller's own tasks.

    Returns:
        The task bodies of every tracked mise config the canon reads, the
        tracked files under mise's file-task directories, and every
        tracked file those name by path, each once.

    """
    files = set(tracked)
    seen: set[str] = set()
    out: list[str] = []
    pending = [
        p for p in tracked if p in CONFIGS or any(p.startswith(d) for d in TASK_DIRS)
    ]
    while pending:
        path = pending.pop()
        if path in seen or not (root / path).is_file():
            continue
        seen.add(path)
        text = (root / path).read_text("utf-8", errors="replace")
        if path.endswith(".toml"):
            text = _task_bodies(text)
        out.append(text)
        named = {w.removeprefix("./") for w in _words(text)}
        pending += sorted((named & files) - seen)
    return "\n".join(out)


def _words(text: str) -> list[str]:
    """Split text into the path-like words a task can name a file by.

    Returns:
        Each run of word characters, dots, slashes and dashes.

    """
    return [typed_std.group(found) or "" for found in re.finditer(r"[\w./-]+", text)]


def _task_bodies(text: str) -> str:
    try:
        doc = decoded.load_toml(text)
    except decoded.UndecodableError:
        return text
    tasks = doc.get("tasks", {})
    if not decoded.is_object(tasks):
        # A config this cannot read as tasks is searched as text, as one
        # that is not TOML at all is.
        return text
    bodies: list[object] = []
    for task in tasks.values():
        run = task.get("run", "") if decoded.is_object(task) else task
        bodies.extend(run if decoded.is_array(run) else [run])
    return "\n".join(b for b in bodies if isinstance(b, str))


def needed(
    table: dict[str, Row],
    tracked: list[str],
    caller: str = "",
    pinned: frozenset[str] | set[str] = frozenset(),
) -> dict[str, str]:
    """Decide which belt tools this tree needs, and why.

    Returns:
        Every belt key to a reason: "needed: ..." or "not needed: ...".

    """
    reasons: dict[str, str] = {}
    for key, row in table.items():
        when = row.when
        if when is None:
            reasons[key] = "needed: every repository"
            continue
        if tool_name(key) in pinned:
            reasons[key] = f"needed: this repository pins {tool_name(key)} itself"
            continue
        hit = next((p for p in tracked for w in when if matches(w, p)), None)
        if hit:
            reasons[key] = f"needed: {hit} is tracked"
        elif caller and shell_calls(caller, commands(key, row)) is not None:
            reasons[key] = "needed: this repository's own tasks call it"
        else:
            reasons[key] = "not needed: " + (
                "no gate task calls it" if not when else "nothing it lints is tracked"
            )
    for key, row in table.items():
        users = [k for k in row.needs if reasons[k].startswith("needed")]
        if users and not reasons[key].startswith("needed"):
            reasons[key] = f"needed: installs {users[0]}"
    return reasons


def tracked_files(root: Path) -> list[str]:
    """List the tree's tracked files.

    Returns:
        The tracked files in the root, from mise/tracked.py (#1986),
        which keeps every `GIT_*` variable from git (#857).

    """
    return git_tracked(root=root)


class Args(argparse.Namespace):
    """The command line, typed where argparse writes it."""

    root: Path
    table: Path
    mode: str


def main(argv: list[str] | None = None) -> int:
    """Name the belt tools this tree needs, or disable the rest.

    Returns:
        0, or 1 when the table names a key the belt does not pin, or the
        table or the belt's config cannot be read.

    """
    parser = argparse.ArgumentParser(prog="gate_tools.py")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--table", type=Path, default=TABLE)
    modes: tuple[str, ...] = ("list", "disable")
    parser.add_argument("mode", choices=modes)
    args = parser.parse_args(argv, namespace=Args())
    config = args.table.parent / "config.toml"
    try:
        table = load_table(args.table)
        belt = decoded.as_object(
            decoded.load_toml(config.read_bytes()).get("tools"), f"{config}: [tools]"
        )
    except decoded.UndecodableError as err:
        findings.finding("", str(err), annotate=True)
        return 1
    unknown = sorted(set(belt) ^ set(table))
    if unknown:
        for key in unknown:
            findings.finding(
                "",
                f"{key} is in one of mise/config.toml's"
                " [tools] and mise/gate-tools.toml but not the other",
                annotate=True,
            )
        return 1
    tracked = tracked_files(args.root)
    reasons = needed(
        table,
        tracked,
        caller_text(args.root, tracked),
        caller_pins.pinned_names(args.root),
    )
    keys = [k for k, why in reasons.items() if why.startswith("needed")]
    out = sys.stdout if args.mode == "disable" else sys.stderr
    for key, why in reasons.items():
        print(f"gate-tools: {key}: {why}", file=out)
    print(f"gate-tools: {len(keys)} of {len(reasons)} belt tools needed", file=out)
    if args.mode == "list":
        print("\n".join(keys))
        return 0
    lines = disable_env([k for k in reasons if k not in keys])
    if lines:
        with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as fh:
            fh.write(lines)
    return 0


def disable_env(keys: list[str]) -> str:
    """Render the environment that leaves the given belt tools out.

    Returns:
        `MISE_DISABLE_TOOLS` and `ORG_GATE_BELT_DIGEST` lines, or "" when
        nothing is left out: the key a full belt has always had.

    """
    if not keys:
        return ""
    joined = ",".join(sorted(keys))
    digest = hashlib.sha256(joined.encode()).hexdigest()[:12]
    return f"MISE_DISABLE_TOOLS={joined}\nORG_GATE_BELT_DIGEST={digest}\n"


if __name__ == "__main__":
    sys.exit(main())
