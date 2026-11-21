#!/usr/bin/env python3
"""Refuse an advisory `ignore` in a repository's deny.toml (#1926).

An advisory decision is written once, in the canon's `security/vex/`,
and `audit:deny` hands cargo-deny's scan to `stele assert advisories`,
which judges each finding against those decisions on the exact
(advisory, package, version) triple. An `ignore` entry in a
repository's deny.toml is a second copy of a decision, keyed by the
advisory alone: it excuses the advisory at every version, and it is
the only exit cargo-deny offers, so it is the copy that drifts.

So `lint:deny` refuses every entry of `[advisories] ignore` that names
an advisory, a bare string or a table with `id`, one finding each,
through mise/findings.py. An entry keyed by `crate` is a yanked
version: it names no advisory, so no decision could stand for it, and
it is not refused.

Usage:
  deny_advisories.py <deny.toml>

Exit 0 when the file keeps no advisory ignore; 1 with each one named,
or with the reason the file could not be read.
"""

import sys
from pathlib import Path

import findings
from decoded import UndecodableError, as_array, as_object, load_toml

# argv[0] plus the config path.
EXPECTED_ARGC = 2
MESSAGE = "an advisory ignore in deny.toml"
REMEDY = (
    "delete the entry: the decision is the canon's security/vex/ file",
    "for that advisory and package version, which audit:deny reads",
)


def advisories(config: dict[str, object]) -> list[str]:
    """Collect every ignore entry that names an advisory, in order.

    Returns:
        The advisory each entry names: a bare string, or a table's `id`.
        A `crate` table without an `id` names none. A `[advisories]`
        that is not a table, or an `ignore` that is not an array, is
        refused by `decoded`'s readers, naming which.

    """
    table = as_object(config.get("advisories", {}), "advisories")
    named: list[str] = []
    for item in as_array(table.get("ignore", []), "advisories.ignore"):
        advisory = item.get("id") if isinstance(item, dict) else item
        if isinstance(advisory, str):
            named.append(advisory)
    return named


def main(argv: list[str]) -> int:
    """Name every advisory ignore in the file.

    Returns:
        A process exit status.

    """
    if len(argv) != EXPECTED_ARGC:
        print("usage: deny_advisories.py <deny.toml>", file=sys.stderr)
        return 2
    source = Path(argv[1])
    try:
        named = advisories(load_toml(source.read_text(encoding="utf-8")))
    except UndecodableError as error:
        print(f"{findings.task_name()}: {source}: {error}", file=sys.stderr)
        return 1
    for advisory in named:
        findings.finding(advisory, MESSAGE, REMEDY)
    return 1 if named else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
