#!/usr/bin/env python3
"""Emit deny.toml variants, each with one `bans` skip entry removed.

A duplicate-version skip is an exception to a rule the org otherwise
enforces absolutely, and cargo-deny will not tell you when one stops
being needed: a `skip`/`skip-tree` entry that matches nothing is silent
(measured). So the exception cannot retire itself the way a
`#[expect(..., reason = "...")]` does, and a skip written for an upstream
lag outlives the lag by however long nobody looks.

This turns necessity into something testable. For every declared skip it
writes a copy of the config with exactly that entry gone; `lint:deny`
then runs `cargo deny check bans` against each. A variant that still
PASSES proves its entry was doing nothing, and the gate says so by name.

Prints one `crate[@version]<TAB>path` line per entry, nothing when there
are none.

The removal is line-based on purpose. Rewriting parsed TOML would mean
re-serialising a file full of the reasons those exceptions exist, and a
comment lost in that round trip is the whole record of a decision. Every
`*.toml` in the org is taplo-formatted by `lint:toml`, so an array entry
is one line, which is what makes the narrow match below safe.
"""

import pathlib
import re
import sys
import tempfile

import findings
from decoded import UndecodableError, as_array, as_object, load_toml
from typed_std import group

# argv[0] plus the config path.
EXPECTED_ARGC = 2


SKIP_KEYS = ("skip", "skip-tree")
# The line that opens a skip array, under `[bans]` or dotted at the top.
ARRAY_OPEN = re.compile(r"^\s*(?:bans\.)?(skip|skip-tree)\s*=\s*\[\s*(?:#.*)?$")
ARRAY_CLOSE = re.compile(r"^\s*\]")


def entries(config: dict[str, object]) -> list[tuple[str, str]]:
    """Collect every bans skip, in declaration order within each key.

    Returns:
        (key, spec) per entry: `skip` or `skip-tree`, and the crate as
        the entry writes it, version included. Two skips of one crate at
        two versions are two entries, each tested on its own. A `bans`
        that is not a table, or a skip key that is not an array, is
        refused by `decoded`'s readers, naming which.

    """
    found: list[tuple[str, str]] = []
    bans = as_object(config.get("bans", {}), "bans")
    for key in SKIP_KEYS:
        for item in as_array(bans.get(key, []), f"bans.{key}"):
            # Both spellings: a bare "crate@version" string, or a table
            # carrying the reason the org requires with every exception.
            spec = item.get("crate") if isinstance(item, dict) else item
            if isinstance(spec, str) and spec:
                found.append((key, spec))
    return found


def variant_without(
    lines: list[str], key: str, spec: str, occurrence: int = 0
) -> list[str] | None:
    """Drop exactly one entry: the `occurrence`-th `spec` of the `key` array.

    Only a line inside that array is a candidate, so the same crate in a
    `deny` or `allow` list, or skipped at another version, is never
    removed with it (#1555).

    Returns:
        The remaining lines, or None when no entry matched.

    """
    # Anchored to the start of an array element, and to the whole spec,
    # so `foo` never takes `foo-bar`'s line nor `foo@1` take `foo@2`'s.
    table = re.compile(rf'^\s*\{{\s*crate\s*=\s*"{re.escape(spec)}"')
    string = re.compile(rf'^\s*"{re.escape(spec)}"\s*,?\s*(?:#.*)?$')
    inside: str | None = None
    seen = 0
    for index, line in enumerate(lines):
        opened = ARRAY_OPEN.match(line)
        if opened:
            inside = group(opened, 1)
            continue
        if inside is not None and ARRAY_CLOSE.match(line):
            inside = None
            continue
        if inside == key and (table.match(line) or string.match(line)):
            if seen == occurrence:
                return lines[:index] + lines[index + 1 :]
            seen += 1
    return None


def main(argv: list[str]) -> int:
    """Write one variant per declared skip and print `spec<TAB>path`.

    Returns:
        A process exit status.

    """
    if len(argv) != EXPECTED_ARGC:
        findings.finding("", "usage: deny_skips.py <deny.toml>")
        return 2
    source = pathlib.Path(argv[1])
    try:
        skips = entries(load_toml(source.read_text(encoding="utf-8")))
    except UndecodableError as error:
        findings.finding(str(source), str(error))
        return 1
    if not skips:
        return 0
    lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    out = pathlib.Path(tempfile.mkdtemp(prefix="deny-skips-"))
    counted: dict[tuple[str, str], int] = {}
    for index, (key, spec) in enumerate(skips):
        occurrence = counted.get((key, spec), 0)
        counted[key, spec] = occurrence + 1
        kept = variant_without(lines, key, spec, occurrence)
        if kept is None:
            # Declared but unmatchable by the line rule: report rather
            # than skip, because a skip this cannot test is a skip
            # nothing is checking.
            findings.finding("", f"cannot isolate {key} '{spec}'")
            return 1
        path = out / f"without-{index}-{spec.replace('/', '_')}.toml"
        path.write_text("".join(kept), encoding="utf-8")
        print(f"{spec}\t{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
