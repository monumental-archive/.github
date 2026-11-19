#!/usr/bin/env python3
"""The reading and judging half of audit:mutants' Rust leg (#1355, #1486).

mutants-rust.sh runs the commands: cargo metadata, cargo-mutants'
listings and runs, Postgres provisioning. Everything that reads or
decides is here, one subcommand per step the shell takes, so that it
comes under the canon's coverage figure and its own mutation run
(CONTRIBUTING.md, "A helper's language", #1352):

- `reasons CONFIG`: every `exclude_re` / `exclude_globs` entry of
  `.cargo/mutants.toml` sits on its own line under a `#` reason line.
  Read as TEXT, line by line, never with tomllib: the rule is about the
  comment above an entry, and a TOML parser drops comments.
- `default-major NAME`: the one `pgNN` a crate's `default` feature
  names, from `cargo metadata` on stdin; empty when there is not
  exactly one.
- `plan`: one run's listings (all mutants with no config, the
  configured ones, and the shard's), its "mutating" line, and whether
  the shard has anything to run.
- `verdict SCRATCH`: every run in the order the shell made them, from
  the scratch directory: which exit statuses of `cargo mutants` mean
  "judged" (0, 2 survivors, 3 timeouts), the tally from each
  `outcomes.json` (a count it does not state is red by name, never
  read as zero), the survivors from `missed.txt`. Prints them and the
  summary line, writes `rust.tsv` for the totals, and returns the leg's
  exit status.

The scratch directory's `runs.tsv` is the shell's log, one line per
event in order: `run <TAB> name <TAB> dir` for a cargo-mutants run, or
`red <TAB> message` for a crate the shell could not run. A run's
directory holds `counts` (written by `plan`), `status` (cargo mutants'
exit status, absent when there was nothing to run) and cargo-mutants'
own `mutants.out/`.

Stdlib only. Every message is the one the shell printed before #1486,
byte for byte: test_mutants_rust.py's stub rows hold them.
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import decoded
import typed_std

if TYPE_CHECKING:
    from collections.abc import Sequence

LABEL = "audit:mutants: rust"
CONFIG = ".cargo/mutants.toml"
# The statuses that mean cargo-mutants judged every mutant it was given:
# 0 (all caught), 2 (survivors), 3 (timeouts). Anything else judged
# nothing; a red baseline is 4.
JUDGED = frozenset({0, 2, 3})
EXIT_RED = 1

# awk's [[:space:]] on one line: never a newline, always these.
SPACE = " \t\r\f\v"
KEY_LINE = re.compile(rf"^[{SPACE}]*(exclude_re|exclude_globs)[{SPACE}]*=")
OPENING = re.compile(rf"^[^=]*=[{SPACE}]*\[?")
QUOTED = re.compile(rf"^[{SPACE}]*[\"']")
CLOSING = re.compile(rf"^[{SPACE}]*\]")
CLOSED_AT_END = re.compile(rf"\][{SPACE}]*$")
COMMENT = re.compile(rf"^[{SPACE}]*#")
BLANK = re.compile(rf"^[{SPACE}]*$")
TRAILING_COMMA = re.compile(rf",[{SPACE}]*$")
MAJOR = re.compile(r"^pg[0-9]+$")


def lines_of(text: str) -> list[str]:
    """Split text into lines as awk does: on newline only.

    Returns:
        The lines, without a last empty one after a final newline.

    """
    lines = text.split("\n")
    if lines and not lines[-1]:
        lines.pop()
    return lines


def entry_text(line: str) -> str:
    """Spell a list entry as the message names it.

    Returns:
        The line without its indentation or its trailing comma.

    """
    return TRAILING_COMMA.sub("", line.lstrip(SPACE))


def unexplained(text: str) -> list[tuple[int, str, str]]:
    """Find every exclusion entry with no `#` reason line above it.

    An entry on the `key = [` line itself has nowhere to put one and is
    named as such. Blank lines between the reason and its entry are
    allowed; any other line ends the reason's reach.

    Returns:
        (line number, key, entry text) for each unexplained entry.

    """
    found: list[tuple[int, str, str]] = []
    inside = False
    key = ""
    prev = ""
    for number, line in enumerate(lines_of(text), start=1):
        match = KEY_LINE.match(line)
        if match:
            key = typed_std.group(match, 1) or ""
            rest = OPENING.sub("", line, count=1)
            if QUOTED.match(rest):
                found.append((number, key, "an entry on the opening line"))
            inside = "]" not in line
            prev = ""
            continue
        if not inside:
            continue
        if CLOSING.match(line):
            inside = False
            continue
        if QUOTED.match(line):
            if not COMMENT.match(prev):
                found.append((number, key, entry_text(line)))
            if CLOSED_AT_END.search(line):
                inside = False
        if not BLANK.match(line):
            prev = line
    return found


def reasons(config: Path) -> int:
    """Hold every exclusion to its reason line; print each one without.

    Returns:
        0 when every entry has its reason, 1 otherwise.

    """
    text = config.read_text(encoding="utf-8", errors="replace")
    found = unexplained(text)
    for number, key, entry in found:
        print(
            f"{LABEL}: {config}:{number}: {key} entry {entry}"
            " has no # reason line above it",
            file=sys.stderr,
        )
    if found:
        print(
            "  an exclusion records a mutant the suite cannot kill;"
            " say why, one # line above each entry",
            file=sys.stderr,
        )
        return EXIT_RED
    return 0


@dataclass(frozen=True)
class Package:
    """A package of `cargo metadata`, as far as its default major goes."""

    name: str
    # The features its `default` feature names.
    default: tuple[str, ...]


def read_package(item: object, where: str) -> Package:
    """Decode one entry of `cargo metadata`'s `packages`.

    Returns:
        The package; no `features`, or no `default` in them, is none.

    """
    package = decoded.as_object(item, where)
    features = package.get("features")
    table = decoded.as_object(features, f"{where}.features") if features else {}
    default = table.get("default")
    named = decoded.as_array(default, f"{where}.features.default") if default else []
    return Package(
        name=decoded.as_str(package.get("name"), f"{where}.name"),
        default=tuple(
            decoded.as_str(feature, f"{where}.features.default[{index}]")
            for index, feature in enumerate(named)
        ),
    )


def read_metadata(value: object) -> tuple[Package, ...]:
    """Decode `cargo metadata --format-version 1` for the packages' defaults.

    A field of another shape is refused by name, through `decoded`'s
    readers.

    Returns:
        Every package, in cargo's order; none when `packages` is absent.

    """
    packages = decoded.as_object(value, "the metadata").get("packages", [])
    return tuple(
        read_package(item, f"packages[{index}]")
        for index, item in enumerate(decoded.as_array(packages, "packages"))
    )


def default_major(packages: Sequence[Package], name: str) -> str:
    """Name the one Postgres major a crate's `default` feature selects.

    Returns:
        The `pgNN` feature, or "" when the default names none or several.

    """
    for package in packages:
        if package.name != name:
            continue
        majors = [f for f in package.default if MAJOR.match(f)]
        return majors[0] if len(majors) == 1 else ""
    return ""


def count_listed(text: str) -> int:
    """Count the mutants a `cargo mutants --list` printed.

    Returns:
        The number of lines with anything but blanks and tabs on them.

    """
    return sum(1 for line in lines_of(text) if line.strip(" \t"))


@dataclass
class Plan:
    """One run's listings."""

    every: int
    listed: int
    here: int

    @property
    def excluded(self) -> int:
        """The whole population's exclusions: never a shard's."""
        return self.every - self.listed


class Args(argparse.Namespace):
    """One step's arguments, each typed as parsed."""

    command: str
    config: Path
    name: str
    where: str
    dir: str
    all: str
    listed: str
    here: str
    scratch: str
    shard: str


def plan(args: Args) -> int:
    """Count one run's listings, say what it mutates, record the counts.

    EXCLUDED IS THE WHOLE POPULATION'S, never a shard's: a shard is cut
    from the list AFTER exclusion, so the configured and unconfigured
    lists slice differently and a per-shard difference means nothing (it
    can go negative). Every shard reports the same figure.

    Measured on cargo-mutants 27.1.0: a shard with nothing in it exits 0
    and writes no outcomes.json, so nothing to run is said by name and
    the shell runs nothing; `empty` in the run's directory says so.

    Returns:
        0.

    """
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    every = count_listed(Path(args.all).read_text(encoding="utf-8"))
    listed = count_listed(Path(args.listed).read_text(encoding="utf-8"))
    here = listed
    if args.here:
        here = count_listed(Path(args.here).read_text(encoding="utf-8"))
    counts = Plan(every, listed, here)
    (out / "counts").write_text(f"{every}\t{listed}\t{here}\n", encoding="utf-8")
    print(
        f"{LABEL}: mutating {args.name}{args.where}"
        f" ({counts.here} mutants, {counts.excluded} excluded)"
    )
    if counts.here == 0:
        print(
            f"{LABEL}: {args.name}{args.where}:"
            " no mutants in this shard, nothing to run"
        )
        (out / "empty").touch()
    return 0


@dataclass
class Verdict:
    """Every run's outcome, summed."""

    total: int = 0
    killed: int = 0
    survived: int = 0
    excluded: int = 0
    survivors: list[str] = field(default_factory=list)
    runs: list[str] = field(default_factory=list)


COUNTS = ("total_mutants", "caught", "timeout", "missed")


def stated(outcomes: object) -> dict[str, int]:
    """Read the counts outcomes.json states.

    A count that is absent, null or not a whole number is not stated:
    reading one as zero would pass a check on nothing (#1486).

    Returns:
        Each of COUNTS the document states, by name; none when it is not
        an object.

    """
    found: dict[str, int] = {}
    if not decoded.is_object(outcomes):
        return found
    for key in COUNTS:
        value = outcomes.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            found[key] = value
    return found


def missing_counts(outcomes: object) -> list[str]:
    """Name the counts outcomes.json does not state.

    Returns:
        The missing fields, in COUNTS order; all of them when the
        document is not an object.

    """
    counts = stated(outcomes)
    return [key for key in COUNTS if key not in counts]


def tally(outcomes: dict[str, int]) -> tuple[int, int, int]:
    """Read cargo-mutants' counts, every one of which is stated.

    A timeout counts as killed: the suite noticed, by hanging, and
    cargo-mutants itself calls it no survivor.

    Returns:
        Mutants, killed, survived.

    """
    return (
        outcomes["total_mutants"],
        outcomes["caught"] + outcomes["timeout"],
        outcomes["missed"],
    )


def judge_run(verdict: Verdict, name: str, run: Path) -> None:
    """Add one cargo-mutants run to the verdict."""
    every, listed, _ = (int(n) for n in (run / "counts").read_text().split("\t"))
    verdict.excluded += every - listed
    if (run / "empty").exists():
        return
    status = int((run / "status").read_text().strip())
    if status not in JUDGED:
        verdict.runs.append(
            f"{name}: cargo mutants exited {status}"
            " — nothing judged (a red baseline is exit 4)"
        )
        return
    outcomes = run / "mutants.out" / "outcomes.json"
    if not outcomes.is_file():
        verdict.runs.append(f"{name}: cargo mutants wrote no outcomes.json")
        return
    try:
        document = decoded.load_json(outcomes.read_bytes())
    except decoded.UndecodableError as err:
        verdict.runs.append(f"{name}: outcomes.json: {err}")
        return
    missing = missing_counts(document)
    if missing:
        verdict.runs.extend(f"{name}: outcomes.json states no {key}" for key in missing)
        return
    total, killed, missed = tally(stated(document))
    verdict.total += total
    verdict.killed += killed
    verdict.survived += missed
    if missed > 0:
        text = (run / "mutants.out" / "missed.txt").read_text(encoding="utf-8")
        verdict.survivors.extend(line for line in lines_of(text) if line)


def read_runs(scratch: Path) -> Verdict:
    """Replay the shell's log of runs, in order.

    Returns:
        The summed verdict.

    """
    verdict = Verdict()
    log = scratch / "runs.tsv"
    text = log.read_text(encoding="utf-8") if log.is_file() else ""
    for line in lines_of(text):
        kind, _, rest = line.partition("\t")
        if kind == "red":
            verdict.runs.append(rest)
        elif kind == "run":
            name, _, directory = rest.partition("\t")
            judge_run(verdict, name, Path(directory))
    return verdict


def red(verdict: Verdict) -> bool:
    """Say whether the leg is red.

    Returns:
        True for any survivor, or any run that judged nothing.

    """
    return bool(verdict.survived or verdict.runs)


def report(args: Args) -> int:
    """Print the survivors, the runs that judged nothing and the summary.

    Only a shard that judged everything it was given writes `rust.tsv`:
    a red run (as opposed to red survivors) writes nothing, and the
    totals step names the shard that is missing.

    Returns:
        0 when green, 1 when red.

    """
    verdict = read_runs(Path(args.scratch))
    for survivor in verdict.survivors:
        print(f"{LABEL}: survived: {survivor}")
    for run in verdict.runs:
        print(f"{LABEL}: {run}", file=sys.stderr)
    scope = args.where.removeprefix(",")
    scope = f"{scope}:" if scope else ""
    print(
        f"{LABEL}:{scope} {verdict.total} mutants, {verdict.killed} killed,"
        f" {verdict.survived} survived, {verdict.excluded} excluded"
    )
    summary_dir = os.environ.get("MUTANTS_SUMMARY_DIR", "")
    if summary_dir and not verdict.runs:
        Path(summary_dir).mkdir(parents=True, exist_ok=True)
        Path(summary_dir, "rust.tsv").write_text(
            f"{args.shard}\t{verdict.total}\t{verdict.killed}"
            f"\t{verdict.survived}\t{verdict.excluded}\n",
            encoding="utf-8",
        )
    if red(verdict):
        print(
            f"{LABEL}: red — kill each survivor with an assertion,"
            f" or record it in {CONFIG}",
            file=sys.stderr,
        )
        print(
            "  as an exclude_re entry under a # line"
            " saying why the mutant is equivalent",
            file=sys.stderr,
        )
        return EXIT_RED
    return 0


def parse_args(argv: list[str] | None) -> Args:
    """Read the subcommand and its arguments.

    Returns:
        The parsed arguments.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reasons").add_argument("config", type=Path)
    sub.add_parser("default-major").add_argument("name")
    planned = sub.add_parser("plan")
    for name in ("name", "where", "dir", "all", "listed"):
        planned.add_argument(f"--{name}", required=True)
    planned.add_argument("--here", default="")
    judged = sub.add_parser("verdict")
    judged.add_argument("scratch")
    judged.add_argument("--where", required=True)
    judged.add_argument("--shard", required=True)
    return parser.parse_args(argv, namespace=Args())


def main(argv: list[str] | None = None) -> int:
    """Run one step.

    Returns:
        The step's exit status.

    """
    args = parse_args(argv)
    if args.command == "reasons":
        return reasons(args.config)
    if args.command == "default-major":
        try:
            packages = read_metadata(decoded.load_json(typed_std.stdin_text()))
        except decoded.UndecodableError as err:
            print(f"{LABEL}: cargo metadata: {err}", file=sys.stderr)
            return EXIT_RED
        print(default_major(packages, args.name))
        return 0
    if args.command == "plan":
        return plan(args)
    return report(args)


if __name__ == "__main__":
    sys.exit(main())
