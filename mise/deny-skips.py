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

Prints one `name<TAB>path` line per entry, nothing when there are none.

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
import tomllib

# argv[0] plus the config path.
EXPECTED_ARGC = 2


def entry_names(config: dict) -> list[str]:
    """Collect every crate named by a bans skip, in declaration order.

    Returns:
        The crate names, version suffixes stripped.

    """
    names: list[str] = []
    bans = config.get("bans", {})
    for key in ("skip", "skip-tree"):
        for item in bans.get(key, []):
            # Both spellings: a bare "crate@version" string, or a table
            # carrying the reason the org requires with every exception.
            name = item.get("crate") if isinstance(item, dict) else item
            if isinstance(name, str) and name:
                names.append(name.split("@", 1)[0])
    return names


def variant_without(lines: list[str], name: str) -> list[str] | None:
    """Drop the one array entry naming `name`.

    Returns:
        The remaining lines, or None when no entry matched.

    """
    # Anchored to the start of an array element so a crate mentioned in a
    # `deny` list or a comment cannot be mistaken for its skip entry.
    table = re.compile(rf'^\s*\{{\s*crate\s*=\s*"{re.escape(name)}(@[^"]*)?"')
    string = re.compile(rf'^\s*"{re.escape(name)}(@[^"]*)?"\s*,?\s*$')
    kept = [ln for ln in lines if not (table.match(ln) or string.match(ln))]
    return kept if len(kept) < len(lines) else None


def main(argv: list[str]) -> int:
    """Write one variant per declared skip and print `name<TAB>path`.

    Returns:
        A process exit status.

    """
    if len(argv) != EXPECTED_ARGC:
        print("usage: deny-skips.py <deny.toml>", file=sys.stderr)
        return 2
    source = pathlib.Path(argv[1])
    config = tomllib.loads(source.read_text(encoding="utf-8"))
    names = entry_names(config)
    if not names:
        return 0
    lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    out = pathlib.Path(tempfile.mkdtemp(prefix="deny-skips-"))
    for name in names:
        kept = variant_without(lines, name)
        if kept is None:
            # Declared but unmatchable by the line rule: report rather
            # than skip, because a skip this cannot test is a skip
            # nothing is checking.
            print(f"lint:deny: cannot isolate skip '{name}'", file=sys.stderr)
            return 1
        path = out / f"without-{name.replace('/', '_')}.toml"
        path.write_text("".join(kept), encoding="utf-8")
        print(f"{name}\t{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
