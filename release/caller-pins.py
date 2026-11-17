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

BY TOOL, UNDER ANY BACKEND KEY. `cargo:cargo-auditable` and
`github:rust-secure-code/cargo-auditable` are the same tool, and the
canon reads either without being told which; the key the caller wrote is
the key emitted, so mise and the lockfile see exactly the caller's pin.
The same tool under two keys is refused: mise would install both and put
one of them on PATH, and nothing here would say which.

THE VERSION. A list of versions is a repository declaring a second
toolchain beside the first (a dated nightly for audit:fuzz, an MSRV pin):
mise puts the first on PATH, and the first is the one a build uses
(.github#445), so a list yields its first entry. An inline table yields
its `version`.

A NEEDED INPUT THE CONFIG DOES NOT PIN IS REFUSED BY NAME and never falls
back: no default, no newest, no belt copy. An input named with
`--if-pinned` is one a job needs only where the repository has it (a
Cargo.lock, a go.mod), so it is installed when pinned and skipped when
not; the task that uses it keeps its own guard and remedy.

Two outputs, one reader:

    caller-pins.py install --input-of TEXT [--if-pinned TOOL]... TOOL...
        prints `install=<key@version ...>` for $GITHUB_OUTPUT, the job's
        `install_args`.
    caller-pins.py env --input-of TEXT VAR=TOOL...
        prints `VAR=<version>` lines for $GITHUB_ENV, for a build that
        installs inside its own container (the pgrx-extension class).

The config is the first of `mise.toml`, `.mise.toml` and
`mise/config.toml` in the working directory, the rule the pgrx build
applied before this reader replaced its own.
"""

import argparse
import re
import sys
import tomllib
from pathlib import Path

CANDIDATES = ("mise.toml", ".mise.toml", "mise/config.toml")


class RefusalError(Exception):
    """A pin this reader will not answer for; the message is the remedy."""


def tool_name(key: str) -> str:
    """Name the tool a `[tools]` key pins.

    Returns:
        The key's last `:`- or `/`-separated segment, any `[options]`
        suffix dropped: `cargo:cargo-auditable` and
        `github:rust-secure-code/cargo-auditable` both name
        `cargo-auditable`; `rust` names `rust`.

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


def read_pins(
    root: Path, needed: list[str], optional: list[str], input_of: str
) -> dict[str, tuple[str, str]]:
    """Resolve each named tool to the caller's own key and version.

    Returns:
        Tool name to (key, version) for every needed tool and every
        optional one that is pinned, in the order named.

    Raises:
        RefusalError: no config, a needed tool unpinned, or a tool pinned
            under two keys; every such tool is named.

    """
    config = find_config(root)
    if config is None:
        names = ", ".join(needed)
        msg = (
            f"no mise config in this repository ({', '.join(CANDIDATES)}),"
            f" so {names} cannot be read; pin them in this repository's mise"
            f" config: they are build inputs of {input_of}"
        )
        raise RefusalError(msg)
    tools = tomllib.loads(config.read_text("utf-8")).get("tools", {})
    keys: dict[str, list[str]] = {}
    for key in tools:
        keys.setdefault(tool_name(key), []).append(key)
    errors = []
    pins: dict[str, tuple[str, str]] = {}
    for name in [*needed, *optional]:
        found = keys.get(name, [])
        if len(found) > 1:
            errors.append(
                f"{name} is pinned under {len(found)} keys in {config.name}"
                f" ({', '.join(found)}); mise would install each and put one"
                " on PATH, so pin it under one key"
            )
            continue
        version = first_version(tools[found[0]]) if found else ""
        if version:
            pins[name] = (found[0], version)
        elif name in needed:
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
    parser = argparse.ArgumentParser(prog="caller-pins.py")
    sub = parser.add_subparsers(dest="mode", required=True)
    install = sub.add_parser("install")
    install.add_argument("--input-of", required=True)
    install.add_argument("--if-pinned", action="append", default=[])
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
        needed, optional = [name for _, _, name in pairs], []
    else:
        needed, optional = args.tools, args.if_pinned
        if not needed and not optional:
            parser.error("install names at least one tool")
    try:
        pins = read_pins(root, needed, optional, args.input_of)
    except RefusalError as refusal:
        for line in str(refusal).splitlines():
            print(f"::error::{line}", file=sys.stderr)
        return 1
    if args.mode == "env":
        for var, _, name in pairs:
            print(f"{var}={pins[name][1]}")
    else:
        print("install=" + " ".join(f"{k}@{v}" for k, v in pins.values()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
