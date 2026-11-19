#!/usr/bin/env python3
"""Locate a pinned Postgres for every major a pgrx crate declares.

Org canon — the provisioning half of #813. The gate compiles and tests a
pgrx extension, which needs a real Postgres per major: `pg_config`, the
server headers bindgen parses, and `initdb`. Measured on both platforms
before anything was written (the measurement is recorded on #813):
`aqua:theseus-rs/postgresql-binaries` is an aqua-registry package with
per-asset checksums, it installs in 2-8s, it carries `include/server`,
`cargo pgrx init` accepts its `pg_config` in about a second, and edtf's
extension suite passes at pg14-pg18 against it on macOS and on the
runner with **no apt or brew package installed**.

WHY THE REPOSITORY PINS IT AND NOT THE BELT. Belt tools install in every
repository in the org, so five majors in `mise/config.toml` would cost
stele, monumental-archive, iiif-server and the canon ~200M each for a
Postgres they never use. The org's own rule settles it — the belt carries
what the BELT needs to run, repositories carry what they BUILD with — and
Postgres is a build input of a pgrx crate exactly like its `cargo-pgrx`
pin. So the repository pins the majors and the belt owns everything else:
the task, the derivation, and this agreement check.

WHICH MAKES THE MAJORS A FACT IN TWO PLACES, so the two are compared
rather than either trusted — #820's shape, one issue later. The crate's
`[features]` are the source: they are what cargo compiles, so a major
declared there with no pin is red, and the remedy names the pin to add.
The failure that prevents is not "no Postgres" — it is a **gate that
quietly tested four majors while the publish path shipped five**, which
is the same silence #813 exists to end.

`mise which pg_config` does not answer this: measured on the runner, the
package extracts to a nested `postgresql-<version>-<triple>/` directory
with no top-level `bin/`, so mise shims nothing and prints `pg_config NOT
shimmed by mise`. `mise ls --json` plus that single nested directory is
the equivalent, and it resolves identically on both platforms.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from decoded import UndecodableError, as_array, as_object, load_json
from typed_std import stdin_text

NAME = "pgrx-postgres"

# The package the belt provisions from. Named here rather than in each
# task, so a repository's remedy line and the check that produced it can
# never name different packages.
PACKAGE = "aqua:theseus-rs/postgresql-binaries"


class Args(argparse.Namespace):
    """The parsed command line, typed."""

    majors: str


@dataclass(frozen=True)
class Entry:
    """One entry of `mise ls <pkg> --json`, as this check reads it."""

    version: str
    active: bool
    installed: bool
    install_path: str


def entries(listing: object) -> list[Entry]:
    """Decode `mise ls <pkg> --json` into its entries.

    The fields are read as the check always read them: `active` and
    `installed` by truth, `version` and `install_path` as text.

    Returns:
        The entries, in listing order. A listing that is not a list of
        objects is refused by `decoded`'s readers, naming where.

    """
    out: list[Entry] = []
    for index, item in enumerate(as_array(listing, "the listing")):
        raw = as_object(item, f"listing[{index}]")
        out.append(
            Entry(
                version=str(raw.get("version", "")),
                active=bool(raw.get("active")),
                installed=bool(raw.get("installed")),
                install_path=str(raw.get("install_path", "")),
            )
        )
    return out


def digits(text: str) -> bool:
    """Say whether text is a run of ASCII digits.

    `str.isdigit` also accepts `²` and other digits `int()` refuses
    (#1555: a version `²` raised ValueError), and no major spells one.

    Returns:
        True for one or more of 0-9 and nothing else.

    """
    return text.isascii() and text.isdigit()


def pinned(listing: list[Entry]) -> tuple[dict[int, Entry], list[str]]:
    """Read the Postgres majors this repository's config actually pins.

    Only ACTIVE entries count, the same rule as the MSRV toolchain check:
    `mise ls` reports every version installed on the machine, and a
    package somebody installed by hand is not a pin — it would pass
    locally and be absent in CI.

    Returns:
        Each pinned major mapped to its entry, and the majors that are
        declared but not installed.

    """
    found: dict[int, Entry] = {}
    absent: list[str] = []
    for entry in listing:
        if not entry.active:
            continue
        raw = entry.version
        head = raw.split(".", 1)[0]
        if not digits(head):
            continue
        if entry.installed:
            found[int(head)] = entry
        else:
            absent.append(raw)
    return found, absent


def pg_config(entry: Entry) -> Path | None:
    """Find the `pg_config` inside one installed package.

    Returns:
        Its path, or None when the install carries none — which is a
        broken install rather than a missing pin, and is worded as one.

    """
    root = Path(entry.install_path)
    if not root.is_dir():
        return None
    direct = root / "bin" / "pg_config"
    if direct.is_file():
        return direct
    nested = (hit for hit in sorted(root.glob("*/bin/pg_config")) if hit.is_file())
    return next(nested, None)


def plan(
    majors: list[int],
    listing: list[Entry],
) -> tuple[list[str], list[str]]:
    """Resolve every declared major to a `pg_config`.

    Returns:
        One record per major, and one finding per major this cannot
        serve. Records are emitted only when there are no findings —
        a partial plan would test some majors and report success.

    """
    found, absent = pinned(listing)
    records: list[str] = []
    findings: list[str] = []
    for major in majors:
        entry = found.get(major)
        if entry is None:
            findings.append(
                f"pg{major} is declared by the crate's features and no "
                f"Postgres {major}.x is pinned",
            )
            continue
        where = pg_config(entry)
        if where is None:
            findings.append(
                f"pg{major} is pinned at {entry.version} and that "
                "install carries no bin/pg_config — reinstall it",
            )
            continue
        records.append(f"pg\t{major}\t{where}")
    if not findings:
        return records, []
    findings.extend(
        (
            "pin them in this repository's mise.toml, beside cargo-pgrx:",
            f'  "{PACKAGE}" = ["18.6.0", "17.11.0", ...]',
            (
                "  a major the crate declares and the gate cannot test is "
                "a major the release is the first to try"
            ),
        ),
    )
    if absent:
        findings.append(f"  ({', '.join(absent)} is pinned but not installed)")
    # No partial plan: a caller handed some of the majors would test some
    # of them and report success, which is the failure this exists to
    # prevent wearing a smaller hat.
    return [], findings


def main(argv: list[str] | None = None) -> int:
    """Emit the Postgres plan for one crate's declared majors.

    Returns:
        A process exit status.

    """
    parser = argparse.ArgumentParser(description="the gate's Postgres provisioning")
    parser.add_argument("--majors", required=True)
    args = parser.parse_args(argv, namespace=Args())

    try:
        document = load_json(stdin_text())
    except UndecodableError as bad:
        print(f"{NAME}: `mise ls {PACKAGE}` did not give JSON", file=sys.stderr)
        print(f"  {bad}", file=sys.stderr)
        return 1
    try:
        listing = entries(document)
    except UndecodableError as bad:
        print(f"{NAME}: `mise ls {PACKAGE}`: {bad}", file=sys.stderr)
        return 1

    majors = [int(m) for m in args.majors.split(",") if digits(m.strip())]
    if not majors:
        print(f"{NAME}: --majors named no Postgres major", file=sys.stderr)
        return 1

    records, findings = plan(majors, listing)
    if findings:
        print(
            f"{NAME}: the crate declares a major this repository does not pin",
            file=sys.stderr,
        )
        for line in findings:
            print(f"  {line}", file=sys.stderr)
        return 1

    print("\n".join(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
