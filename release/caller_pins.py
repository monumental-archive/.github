#!/usr/bin/env python3
"""Read a caller's build-input pins: the canon's one reader of them (#1333).

A release job installs a named list of tools, never the caller's whole
mise config: the canon decides which tools a job installs, the caller's
config decides their versions, and a repository keeps no list. The belt's
tools are named in the workflow with the belt's own keys. The caller's
build inputs (Rust, cargo-auditable, wasm-pack, node, go, ...) are named
here by TOOL, and this reader answers with the caller's own `key@version`.

WHY `key@version` AND NEVER A BARE NAME. Measured with mise 2026.11.4:
`mise install rust` installs every configured version, and where the
caller's config was not read the bare name resolved to the newest release
— a silent wrong toolchain. An explicit version installs one thing, and
mise still applies the config entry's options to it (measured on
2026.11.5: `github:microsoft/typescript-go@7.0.2` installs through the
config's `version_prefix`, and 404s without the config), so a pinned
toolchain keeps its components.

BY TOOL, UNDER ANY BACKEND KEY. `cargo:cargo-pgrx` and
`github:pgcentralfoundation/cargo-pgrx` are the same tool, as are both
forms of cargo-auditable (#1334), and the canon reads either without
being told which; the key the caller wrote is the key emitted, so mise
and the lockfile see exactly the caller's pin.
The same tool under two keys is refused: mise would install both and put
one of them on PATH, and nothing here would say which.

THE VERSION. A list of versions is a repository declaring a second
toolchain beside the first (a dated nightly for audit:fuzz, an MSRV pin):
mise puts the first on PATH, and the first is the one a build uses
(.github#445), so a list yields its first entry. An inline table yields
its `version`.

A MAJOR, WHERE THE JOB RUNS ONE (#1408). A list can also be one tool at
several majors — a pgrx crate pins one Postgres per major it declares —
and a job that runs some of them names them with `--major TOOL=SPEC`:
SPEC is the JSON the gate's plan emits, one major (`18`) or a list
(`[14,15]`). Each major yields the one entry whose version begins with
it, so a leg installs its own Postgres and not the other four. A major
the pin has no entry for is refused by name, and so is a major with two
entries, which mise would install side by side for a job to take
either. An empty list asks for nothing: no major planned, no Postgres.

A NEEDED INPUT THE CONFIG DOES NOT PIN IS REFUSED BY NAME and never falls
back: no default, no newest, no belt copy. An input named with
`--if-pinned` is one a job needs only where the repository has it (a
Cargo.lock, a go.mod), so it is installed when pinned and skipped when
not; the task that uses it keeps its own guard and remedy.

Two outputs, one reader:

    caller_pins.py install --input-of TEXT [--if-pinned TOOL]...
            [--major TOOL=SPEC]... TOOL...
        prints `install=<key@version ...>` for $GITHUB_OUTPUT, the job's
        `install_args`.
    caller_pins.py env --input-of TEXT VAR=TOOL...
        prints `VAR=<version>` lines for $GITHUB_ENV, for a build that
        installs inside its own container (the pgrx-extension class).

The config is the first of `mise.toml`, `.mise.toml` and
`mise/config.toml` in the working directory, the rule the pgrx build
applied before this reader replaced its own.
"""

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

# The canon's one writer of workflow commands, in mise/ (#1660).
# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import workflow_command

CANDIDATES = ("mise.toml", ".mise.toml", "mise/config.toml")


class RefusalError(Exception):
    """A pin this reader will not answer for; the message is the remedy."""


def tool_name(key: str) -> str:
    """Name the tool a `[tools]` key pins.

    Returns:
        The key's last `:`- or `/`-separated segment, any `[options]`
        suffix dropped: `cargo:cargo-pgrx` and
        `github:pgcentralfoundation/cargo-pgrx` both name `cargo-pgrx`;
        `rust` names `rust`.

    """
    return re.split(r"[/:]", key.split("[", 1)[0])[-1]


def first_version(value: object) -> str:
    """Read the version a `[tools]` value puts on PATH.

    Returns:
        The version string, or "" where the value pins none.

    """
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("version", "")
    return value if isinstance(value, str) else ""


def entries(value: object) -> list[str]:
    """Read every version a `[tools]` value pins.

    Returns:
        One version per entry, in the config's order; "" for an entry
        that pins none.

    """
    return [first_version(v) for v in (value if isinstance(value, list) else [value])]


def parse_majors(spec: str) -> list[int] | None:
    """Read a `--major` SPEC: the plan's JSON, one major or a list of them.

    The plan (`mise/pgrx-plan.sh`) prints its majors as strings,
    `["14","18"]`, and a leg's matrix value arrives bare, `18`: both
    are the same majors, so a string of digits reads as its number.

    Returns:
        The majors, or None for anything that is not a whole number, a
        string of digits, or a list of them.

    """
    try:
        value = json.loads(spec)
    except json.JSONDecodeError:
        return None
    majors: list[int] = []
    for m in value if isinstance(value, list) else [value]:
        if isinstance(m, int) and not isinstance(m, bool):
            majors.append(m)
        elif isinstance(m, str) and m.isascii() and m.isdigit():
            majors.append(int(m))
        else:
            return None
    return majors


def select_majors(
    name: str, config: str, versions: list[str], majors: list[int], input_of: str
) -> tuple[list[str], list[str]]:
    """Select the entry pinned at each major.

    Returns:
        The selected versions in the order the majors were named, and
        one refusal per major with no entry or with two.

    """
    chosen: list[str] = []
    errors: list[str] = []
    pinned = ", ".join(v for v in versions if v) or "none"
    for major in majors:
        hits = [v for v in versions if v.split(".", 1)[0] == str(major)]
        if not hits:
            errors.append(
                f"{name} pins no {major}.x in {config} (pinned: {pinned}); this"
                f" job runs major {major} — pin it beside the others; it is a"
                f" build input of {input_of}"
            )
        elif len(hits) > 1:
            errors.append(
                f"{name} pins major {major} {len(hits)} times in {config}"
                f" ({', '.join(hits)}); mise would install each and the job"
                " could take either, so pin one"
            )
        else:
            chosen.append(hits[0])
    return chosen, errors


def find_config(root: Path) -> Path | None:
    """Find the caller's mise config.

    Returns:
        The first candidate that exists, or None.

    """
    for name in CANDIDATES:
        path = root / name
        if path.is_file():
            return path
    return None


def tools_of(path: Path) -> dict | None:
    """Read a config's `[tools]` table.

    `tools` is a table in every config mise accepts; TOML also lets a
    file say `tools = 1` or `tools = ["rust"]`, and those pin nothing
    (#1556: both died here as a TypeError).

    Returns:
        The table, empty where the file has none, or None where `tools`
        is some other value.

    """
    tools = tomllib.loads(path.read_text("utf-8")).get("tools", {})
    return tools if isinstance(tools, dict) else None


# Every config file mise reads [tools] from in a checkout, for a question
# that must not miss one: which tools does this repository pin at all?
EVERY_CONFIG = (*CANDIDATES, ".config/mise.toml", ".config/mise/config.toml")


def pinned_names(root: Path) -> set[str]:
    """Name every tool the repository pins, under any backend key.

    The gate leaves belt tools out of a tree that does not need them
    (#1410, mise/gate_tools.py), and must never leave out one the
    repository pins itself: mise disables a tool by name, and the
    repository's own pin would go with it.

    Returns:
        The tool name of every `[tools]` key in every config file in
        EVERY_CONFIG that exists, by `tool_name`.

    """
    names: set[str] = set()
    for name in EVERY_CONFIG:
        path = root / name
        if path.is_file():
            names.update(tool_name(key) for key in tools_of(path) or {})
    return names


def read_pins(
    root: Path,
    needed: list[str],
    optional: list[str],
    input_of: str,
    majors: dict[str, list[int]] | None = None,
) -> dict[str, tuple[str, list[str]]]:
    """Resolve each named tool to the caller's own key and versions.

    Returns:
        Tool name to (key, versions) for every needed tool and every
        optional one that is pinned, in the order named, then each tool
        named by major. A tool has one version; a tool named by major
        has one per major, in the order the majors were named.

    Raises:
        RefusalError: no config, a `tools` that is not a table, a needed
            tool unpinned, a tool pinned under two keys, or a major with
            no entry or two; every such tool is named.

    """
    majors = {name: wanted for name, wanted in (majors or {}).items() if wanted}
    config = find_config(root)
    if config is None:
        names = ", ".join([*needed, *majors])
        msg = (
            f"no mise config in this repository ({', '.join(CANDIDATES)}),"
            f" so {names} cannot be read; pin them in this repository's mise"
            f" config: they are build inputs of {input_of}"
        )
        raise RefusalError(msg)
    tools = tools_of(config)
    if tools is None:
        msg = (
            f"tools in {config.name} is not a table, so no tool is pinned"
            f" there; pin the build inputs of {input_of} under [tools]"
        )
        raise RefusalError(msg)
    keys: dict[str, list[str]] = {}
    for key in tools:
        keys.setdefault(tool_name(key), []).append(key)
    errors = []
    pins: dict[str, tuple[str, list[str]]] = {}
    for name in [*needed, *optional, *majors]:
        found = keys.get(name, [])
        if len(found) > 1:
            errors.append(
                f"{name} is pinned under {len(found)} keys in {config.name}"
                f" ({', '.join(found)}); mise would install each and put one"
                " on PATH, so pin it under one key"
            )
            continue
        if found and name in majors:
            chosen, refused = select_majors(
                name, config.name, entries(tools[found[0]]), majors[name], input_of
            )
            errors += refused
            pins[name] = (found[0], chosen)
            continue
        version = first_version(tools[found[0]]) if found else ""
        if version:
            pins[name] = (found[0], [version])
        elif name in needed or name in majors:
            errors.append(
                f"{name} missing — pin {name} in this repository's mise"
                f" config; it is a build input of {input_of}"
            )
    if errors:
        raise RefusalError("\n".join(errors))
    return pins


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    """Print the requested pins, or refuse with one error line per tool.

    Returns:
        0 when every needed pin was read, 1 on a refusal, 2 on bad usage.

    """
    parser = argparse.ArgumentParser(prog="caller_pins.py")
    sub = parser.add_subparsers(dest="mode", required=True)
    install = sub.add_parser("install")
    install.add_argument("--input-of", required=True)
    install.add_argument("--if-pinned", action="append", default=[])
    install.add_argument("--major", action="append", default=[])
    install.add_argument("tools", nargs="*")
    env = sub.add_parser("env")
    env.add_argument("--input-of", required=True)
    env.add_argument("pairs", nargs="+")
    args = parser.parse_args(argv)
    root = root or Path.cwd()
    if args.mode == "env":
        pairs = [p.partition("=") for p in args.pairs]
        if any(not var or not sep or not name for var, sep, name in pairs):
            parser.error("env takes VAR=TOOL pairs")
        needed, optional, majors = [name for _, _, name in pairs], [], {}
    else:
        needed, optional, majors = args.tools, args.if_pinned, {}
        for pair in args.major:
            name, sep, spec = pair.partition("=")
            wanted = parse_majors(spec)
            if not name or not sep or wanted is None:
                parser.error(f"--major takes TOOL=SPEC, one major or a list: {pair}")
            majors[name] = wanted
        if not needed and not optional and not majors:
            parser.error("install names at least one tool")
    try:
        pins = read_pins(root, needed, optional, args.input_of, majors)
    except RefusalError as refusal:
        for line in str(refusal).splitlines():
            workflow_command.error(line, stream=sys.stderr)
        return 1
    if args.mode == "env":
        for var, _, name in pairs:
            print(f"{var}={pins[name][1][0]}")
    else:
        print("install=" + " ".join(f"{k}@{v}" for k, vs in pins.values() for v in vs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
