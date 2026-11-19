#!/usr/bin/env python3
"""Report locked tools that lack a platform the release builds on.

A binary release leg runs `mise install --locked` on its target's own
runner, so every locked tool that carries per-platform tables at all
must carry one for every runner the release declares. Nothing joined the
two: the lock records whichever platforms someone happened to resolve,
the target list is declared in the publish stub, and the gate runs on
linux and macos-arm64 only. edtf v1.3.0's `x86_64-apple-darwin` leg was
the first thing ever to ask for `macos-x64`, after the tag was minted
(#810).

Usage:

    lock_platforms.py --defaults <canon publish.yml> --config <mise config>
                      --lock <mise.lock> < workflow-paths

The workflows, one path per line on stdin, are the repository's tracked
ones; the jobs among them that call the canon's publish.yml are the
publish stub. The declared
targets are read, never guessed: the stub's own `binary-targets:` /
`go-targets:` when it sets them, else the default the canon's
publish.yml declares for that input — the tree this belt came from.

Exit 0 with one summary line on stdout when nothing is missing, or when
the stub declares no binary class (a skip, said so). Exit 1 with the
missing entries and the resolve command on stderr. Exit 2 when the
declaration cannot be read as written — a block scalar, an expression,
an unknown target — because a target list this cannot read is not one
it may pass.
"""

import argparse
import pathlib
import re
import sys
from typing import TextIO

from decoded import UndecodableError, as_object, is_array, is_object, load_toml

NAME = "lint:lock-platforms"

# The binary classes, and the publish input that declares each one's
# targets. Other classes build on linux in containers or on the gate's
# own platforms; these two fan out to one runner per target.
CLASS_INPUT: dict[str, str] = {
    "rust-binary": "binary-targets",
    "go-binary": "go-targets",
}

# Target -> the mise platform key of the runner that builds it, as
# build-rust-binary.yml and build-go-binary.yml assign them. The linux
# musl targets build on glibc ubuntu runners, so the key is the plain
# `linux-*` one mise resolves there.
TARGET_PLATFORM: dict[str, dict[str, str]] = {
    "binary-targets": {
        "x86_64-unknown-linux-musl": "linux-x64",
        "aarch64-unknown-linux-musl": "linux-arm64",
        "x86_64-apple-darwin": "macos-x64",
        "aarch64-apple-darwin": "macos-arm64",
    },
    "go-targets": {
        "linux-amd64": "linux-x64",
        "linux-arm64": "linux-arm64",
        "darwin-amd64": "macos-x64",
        "darwin-arm64": "macos-arm64",
    },
}

PUBLISH_USES = re.compile(
    r"^(?:[\w.-]+/\.github|\.)/\.github/workflows/publish\.yml(?:@\S+)?$"
)
KEY_LINE = re.compile(r"^( *)[\"']?([A-Za-z0-9_-]+)[\"']?:(?:\s+(.*))?$")
COMMENT = re.compile(r"\s+#.*$")

Span = tuple[int, int, str]
Rows = list[tuple[str | None, list[str] | None]]


class Args(argparse.Namespace):
    """The parsed command line, typed."""

    defaults: pathlib.Path
    config: pathlib.Path
    lock: pathlib.Path


class UnreadableError(ValueError):
    """A declaration this check cannot read as a plain scalar."""


def scalar(raw: str, where: str) -> str:
    """Read a one-line YAML scalar, comment and quotes stripped.

    Returns:
        The bare value.

    Raises:
        UnreadableError: the value is a block, flow or expression form.

    """
    value = COMMENT.sub("", raw).strip()
    quoted = len(value) > 1 and value[0] in {"'", '"'} and value[-1] == value[0]
    if quoted:
        value = value[1:-1]
    if not value or value[0] in {">", "|", "[", "{", "&", "*"} or "${{" in value:
        msg = f"{where}: {raw.strip()!r} is not a plain comma-separated scalar"
        raise UnreadableError(msg)
    return value


def indent_of(line: str) -> int:
    """Count a line's leading spaces.

    Returns:
        The indent.

    """
    return len(line) - len(line.lstrip(" "))


def is_content(line: str) -> bool:
    """Say whether a line is neither blank nor a comment.

    Returns:
        True for a content line.

    """
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def blocks(lines: list[str], indent: int, start: int, end: int) -> dict[str, Span]:
    """Map each key at exactly `indent` in lines[start:end] to its span.

    The span ends at the next key at that indent, or at the first content
    line indented less, which closes the enclosing mapping.

    Returns:
        key -> (first body line, end line, inline value).

    """
    found: list[tuple[str, int, str]] = []
    for i in range(start, end):
        if not is_content(lines[i]):
            continue
        if indent_of(lines[i]) < indent:
            end = i
            break
        m = KEY_LINE.match(lines[i])
        if m and len(m.group(1)) == indent:
            found.append((m.group(2), i, m.group(3) or ""))
    out: dict[str, Span] = {}
    for n, (key, i, inline) in enumerate(found):
        stop = found[n + 1][1] if n + 1 < len(found) else end
        out[key] = (i + 1, stop, inline)
    return out


def children(lines: list[str], span: Span) -> dict[str, Span]:
    """Map the keys of the mapping that is a span's body.

    Returns:
        key -> span, empty when the body has no content.

    """
    start, end, _ = span
    first = next((i for i in range(start, end) if is_content(lines[i])), None)
    if first is None:
        return {}
    return blocks(lines, indent_of(lines[first]), start, end)


def publish_calls(path: pathlib.Path) -> list[dict[str, str]]:
    """Read the `with:` scalars of each job in a workflow calling publish.yml.

    Returns:
        One input map per calling job.

    """
    lines = path.read_text(encoding="utf-8").splitlines()
    top = blocks(lines, 0, 0, len(lines))
    if "jobs" not in top:
        return []
    calls = []
    for job in children(lines, top["jobs"]).values():
        keys = children(lines, job)
        uses = COMMENT.sub("", keys.get("uses", (0, 0, ""))[2]).strip(" '\"")
        if not PUBLISH_USES.match(uses):
            continue
        with_keys = children(lines, keys["with"]) if "with" in keys else {}
        calls.append({key: span[2] for key, span in with_keys.items()})
    return calls


def canon_defaults(path: pathlib.Path) -> dict[str, str]:
    """Read the declared default of each target input in the canon's publish.yml.

    Returns:
        input name -> its default.

    Raises:
        UnreadableError: an input is absent or carries no plain default.

    """
    lines = path.read_text(encoding="utf-8").splitlines()
    top = blocks(lines, 0, 0, len(lines))
    trigger = children(lines, top.get("on", (0, 0, "")))
    call = children(lines, trigger.get("workflow_call", (0, 0, "")))
    inputs = children(lines, call.get("inputs", (0, 0, "")))
    out: dict[str, str] = {}
    for name in CLASS_INPUT.values():
        spec = children(lines, inputs[name]) if name in inputs else {}
        if "default" not in spec:
            msg = f"{path}: no default declared for input {name}"
            raise UnreadableError(msg)
        out[name] = scalar(spec["default"][2], f"{path} {name} default")
    return out


def targets_of(
    wf: pathlib.Path, inputs: dict[str, str], defaults: dict[str, str]
) -> list[tuple[str, str, str]]:
    """Resolve one publish call's declared binary targets.

    Returns:
        (platform key, target, source) for every declared target.

    Raises:
        UnreadableError: a declaration is not a plain scalar, or names a
            target no runner platform is known for.

    """
    raw_classes = inputs.get("classes", "")
    classes = scalar(raw_classes, f"{wf} classes").split(",") if raw_classes else []
    out = []
    for cls in (c.strip() for c in classes):
        name = CLASS_INPUT.get(cls)
        if name is None:
            continue
        if name in inputs:
            raw, origin = scalar(inputs[name], f"{wf} {name}"), name
        else:
            raw, origin = defaults[name], f"{name}, the canon default"
        for target in filter(None, (t.strip() for t in raw.split(","))):
            key = TARGET_PLATFORM[name].get(target)
            if key is None:
                msg = f"{wf}: {cls} target {target!r} has no known runner platform"
                raise UnreadableError(msg)
            out.append((key, target, f"{wf} ({cls}: {origin})"))
    return out


def declared(
    workflows: list[pathlib.Path], defaults: dict[str, str]
) -> tuple[dict[str, list[str]], list[str]]:
    """Collect the platforms every publish call declares.

    Returns:
        platform key -> its declaring sources, and the stubs read.

    """
    platforms: dict[str, list[str]] = {}
    stubs: list[str] = []
    for wf in workflows:
        for inputs in publish_calls(wf):
            stubs.append(str(wf))
            for key, _target, source in targets_of(wf, inputs, defaults):
                sources = platforms.setdefault(key, [])
                if source not in sources:
                    sources.append(source)
    return platforms, stubs


def configured(path: pathlib.Path) -> dict[str, Rows]:
    """Read a mise config's [tools] as tool -> [(version, os restriction)].

    A tool may be one version string, one table, or an array of either;
    a table's `os` restricts where mise installs it at all.

    Returns:
        tool -> one row per configured element. A document that is not
        TOML, or whose `tools` is not a table, is refused by `decoded`.

    """
    doc = load_toml(path.read_text(encoding="utf-8"))
    tools = as_object(doc.get("tools", {}), "tools")
    out: dict[str, Rows] = {}
    for tool, value in tools.items():
        rows: Rows = []
        elements: list[object] = value if is_array(value) else [value]
        for element in elements:
            if not is_object(element):
                rows.append((str(element), None))
                continue
            os_list = element.get("os")
            version = str(element["version"]) if "version" in element else None
            if is_array(os_list):
                rows.append((version, [str(o) for o in os_list]))
            else:
                rows.append((version, None))
        out[tool] = rows
    return out


def installs_on(rows: Rows, version: str, key: str) -> bool:
    """Say whether the configured element locked at `version` installs on `key`.

    The element is the one whose version matches the lock's, or the only
    one there is; a fuzzy request matching neither is held to every
    platform, because a restriction this cannot attribute is not one it
    may assume.

    Returns:
        False only when an `os` restriction excludes the platform.

    """
    match = [r for r in rows if r[0] == version] or (rows if len(rows) == 1 else [])
    restrictions = [r[1] for r in match]
    if not restrictions or any(r is None for r in restrictions):
        return True
    os_name, _, arch = key.partition("-")
    allowed = {o for r in restrictions if r for o in r}
    return bool(allowed & {os_name, f"{os_name}-{arch}", f"{os_name}/{arch}"})


def lock_entries(path: pathlib.Path) -> list[tuple[str, str, set[str]]]:
    """Read every lock entry that carries platform tables.

    Returns:
        (tool, version, platform keys) per entry. A document that is not
        TOML, or whose `tools` or an entry is not a table, is refused by
        `decoded`, naming which.

    """
    data = load_toml(path.read_text(encoding="utf-8"))
    out: list[tuple[str, str, set[str]]] = []
    for tool, entries in as_object(data.get("tools", {}), "tools").items():
        listed: list[object] = entries if is_array(entries) else [entries]
        for index, raw in enumerate(listed):
            entry = as_object(raw, f"tools.{tool}[{index}]")
            prefix = "platforms."
            keys = {k.removeprefix(prefix) for k in entry if k.startswith(prefix)}
            nested = entry.get("platforms")
            if is_object(nested):
                keys |= set(nested)
            if keys:
                out.append((tool, str(entry.get("version", "?")), keys))
    return out


def report(
    lock: pathlib.Path, platforms: dict[str, list[str]], missing: dict[str, list[str]]
) -> None:
    """Print the missing entries, their sources and the resolve commands."""
    err = sys.stderr
    print(f"{NAME}: {lock} lacks platforms the release builds on:", file=err)
    for key in sorted(missing):
        print(f"  {key}: {' '.join(missing[key])}", file=err)
        for source in platforms[key]:
            print(f"    declared by {source}", file=err)
    print(
        "  each binary leg runs `mise install --locked` on its target's own runner\n"
        "  and fails there, after the tag is minted (#810). Resolve them with:",
        file=err,
    )
    for key in sorted(missing):
        tools = sorted({t.rsplit("@", 1)[0] for t in missing[key]})
        print(f"    mise lock --platform {key} {' '.join(tools)}", file=err)


def read_documents(
    config: pathlib.Path, lock: pathlib.Path
) -> tuple[dict[str, Rows], list[tuple[str, str, set[str]]]] | None:
    """Read the mise config and its lock, or refuse the one that cannot be.

    Returns:
        The configured tools and the lock's entries, or None having said
        on stderr which file was refused and why.

    """
    path = config
    try:
        tools = configured(config)
        path = lock
        return tools, lock_entries(lock)
    except UndecodableError as err:
        print(
            f"{NAME}: cannot read {path} — {err}\n"
            "  a file this cannot read is not one it may pass",
            file=sys.stderr,
        )
        return None


def main(argv: list[str]) -> int:
    """Run the check; see the module docstring for the exit contract.

    Returns:
        The exit status.

    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--defaults", required=True, type=pathlib.Path)
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--lock", required=True, type=pathlib.Path)
    args = parser.parse_args(argv, namespace=Args())
    stdin: TextIO = sys.stdin
    workflows = [pathlib.Path(line) for line in stdin.read().splitlines() if line]
    try:
        platforms, stubs = declared(workflows, canon_defaults(args.defaults))
    except UnreadableError as err:
        print(
            f"{NAME}: cannot read the declared release targets — {err}\n"
            "  declare them as one plain line, e.g. binary-targets: a,b — a target\n"
            "  list this cannot read is not one it may pass",
            file=sys.stderr,
        )
        return 2
    if not stubs:
        print(f"{NAME}: no publish stub, skipped")
        return 0
    if not platforms:
        read = ", ".join(sorted(set(stubs)))
        print(f"{NAME}: {read} declares no binary class, skipped")
        return 0
    # Only what the leg installs: a lock entry for a tool the config no
    # longer names is stale and never installed — edtf v1.3.0's lock
    # carried nine such entries without macos-x64, and the leg named only
    # the two configured ones.
    read = read_documents(args.config, args.lock)
    if read is None:
        return 2
    tools, locked = read
    entries = [e for e in locked if e[0] in tools]
    missing: dict[str, list[str]] = {}
    for tool, version, keys in entries:
        for key in sorted(platforms):
            if key not in keys and installs_on(tools[tool], version, key):
                missing.setdefault(key, []).append(f"{tool}@{version}")
    if missing:
        report(args.lock, platforms, missing)
        return 1
    print(
        f"{NAME}: {len(entries)} configured entries with platform tables"
        f" in {args.lock} cover {','.join(sorted(platforms))}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
