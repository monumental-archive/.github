#!/usr/bin/env python3
"""The reading and judging half of audit:mutants' Rust leg (#1355, #1486).

mutants-rust.sh runs the commands: cargo metadata, cargo-mutants'
listings and runs, Postgres provisioning. Everything that reads or
decides is here, one subcommand per step the shell takes, so that it
comes under the canon's coverage figure and its own mutation run
(CONTRIBUTING.md, "A helper's language", #1352):

- `reasons CONFIG`: every `exclude_re` / `exclude_globs` entry of
  `.cargo/mutants.toml` sits on its own line under a `#` reason line,
  and no `exclude_re` entry pins a position (`:<digits>:<digits>:`):
  a site's mutant is recorded at its site (#1987). Read as TEXT, line
  by line, never with tomllib: the rule is about the comment above an
  entry, and a TOML parser drops comments.
- `records`: the site records, `git grep -n` of every
  `// mutants:equivalent` comment on stdin. One with no mutation text
  or no reason is red by name; otherwise prints each one's derived
  `--exclude-re` pattern, one per line.
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
  read as zero), the survivors from `missed.txt`, and each site record
  held to the `--no-config` listings. Prints them and the summary line,
  writes `rust.tsv` for the totals, and returns the leg's exit status.

A RECORD is `// mutants:equivalent <mutation>: <reason>`, either
trailing the mutant's line (code before the comment: its site is that
line) or alone on the line directly above it (its site is the next
line). `<mutation>` is cargo-mutants' own text for the mutant without
its ` in <function>` suffix, and the record explains every mutant with
that text at its site: the leg passes
`^<path>:<site>:[0-9]+: <mutation>( in .*)?$` as `--exclude-re` to the
configured listings and the run, beside whatever the config holds. It
is red, by name, when it gives no mutation text or no reason, when it
is in no mutated file, and when its pattern matches nothing in the
`--no-config` listing (stale: the mutant is gone, or the line has no
such mutant).

The scratch directory's `runs.tsv` is the shell's log, one line per
event in order: `run <TAB> name <TAB> dir` for a cargo-mutants run, or
`red <TAB> message` for a crate the shell could not run. A run's
directory holds `all.txt` (the `--no-config` listing), `counts`
(written by `plan`), `status` (cargo mutants' exit status, absent when
there was nothing to run) and cargo-mutants' own `mutants.out/`.
`records.txt` beside it is the `git grep -n` the records came from.

Stdlib only. Every message is the one the shell printed before #1486,
byte for byte, but where #1987 moved the record to the site:
test_mutants_rust.py's stub rows hold them.
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import decoded
import findings
import typed_std

if TYPE_CHECKING:
    from collections.abc import Sequence

LABEL = "audit:mutants: rust"
# The leg's language: the subject of its findings, as LABEL names it.
LANGUAGE = "rust"
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
# A position as cargo-mutants names a mutant by it: `:line:col:`.
POSITION = re.compile(r":[0-9]+:[0-9]+:")

# The record of an equivalent mutant, at its site (#1987).
MARK = "// mutants:equivalent"
SPELLING = f"{MARK} <mutation>: <reason>"
# What follows the mark: the mutation text, then `: ` and the reason.
RECORD = re.compile(r"^(?P<mutation>.*?)(?::[ \t]+(?P<reason>.*)|:?)$")
# A mutant's name in a listing: `path:line:col: mutation`.
NAME = re.compile(r"^(?P<path>.+?):[0-9]+:[0-9]+: ")
# What Rust's `regex::escape` escapes: the pattern is cargo-mutants'.
META = frozenset("\\.+*?()|[]{}^$#&-~")


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


@dataclass(frozen=True)
class Entry:
    """One entry of an exclusion list, as the config's text spells it."""

    number: int
    key: str
    # The entry as the message names it; an entry on the opening line is
    # named as such.
    text: str
    # Whether a `#` reason line stands above it.
    explained: bool


def entries(text: str) -> list[Entry]:
    """Find every exclusion entry, and whether a reason line is above it.

    An entry on the `key = [` line itself has nowhere to put one and is
    named as such. Blank lines between the reason and its entry are
    allowed; any other line ends the reason's reach.

    Returns:
        Each entry, in the file's order.

    """
    found: list[Entry] = []
    inside = False
    key = ""
    prev = ""
    for number, line in enumerate(lines_of(text), start=1):
        match = KEY_LINE.match(line)
        if match:
            key = typed_std.group(match, 1) or ""
            rest = OPENING.sub("", line, count=1)
            if QUOTED.match(rest):
                opening = "an entry on the opening line"
                found.append(Entry(number, key, opening, explained=False))
            inside = "]" not in line
            prev = ""
            continue
        if not inside:
            continue
        if CLOSING.match(line):
            inside = False
            continue
        if QUOTED.match(line):
            explained = COMMENT.match(prev) is not None
            found.append(Entry(number, key, entry_text(line), explained=explained))
            if CLOSED_AT_END.search(line):
                inside = False
        if not BLANK.match(line):
            prev = line
    return found


def unexplained(text: str) -> list[tuple[int, str, str]]:
    """Find every exclusion entry with no `#` reason line above it.

    Returns:
        (line number, key, entry text) for each unexplained entry.

    """
    return [(e.number, e.key, e.text) for e in entries(text) if not e.explained]


def pinned(text: str) -> list[tuple[int, str]]:
    """Find every `exclude_re` entry that pins a line and column (#1987).

    A position moves as soon as the code above it does, and the entry
    then points at nothing, or at another mutant: a site's mutant is
    recorded at its site. A rule that is not a site (`tests::`) stays.

    Returns:
        (line number, entry text) for each entry holding `:<n>:<n>:`.

    """
    return [
        (e.number, e.text)
        for e in entries(text)
        if e.key == "exclude_re" and POSITION.search(e.text)
    ]


def reasons(config: Path) -> int:
    """Hold every exclusion to its reason line, and to naming no position.

    Returns:
        0 when every entry has its reason and none pins a position, 1
        otherwise.

    """
    text = config.read_text(encoding="utf-8", errors="replace")
    found = unexplained(text)
    for number, key, entry in found:
        findings.finding(
            LANGUAGE,
            f"{config}:{number}: {key} entry {entry} has no # reason line above it",
            [
                (
                    "an exclusion records a mutant the suite cannot kill;"
                    " say why, one # line above each entry"
                )
            ],
        )
    positions = pinned(text)
    for number, entry in positions:
        findings.finding(
            LANGUAGE,
            f"{config}:{number}: exclude_re entry {entry} pins a line and column",
            [
                (
                    "record the mutant at its site instead, on its line or"
                    f" the line above: {SPELLING}"
                )
            ],
        )
    if found or positions:
        return EXIT_RED
    return 0


def escape(text: str) -> str:
    """Escape text as Rust's `regex::escape` does.

    Returns:
        The text with each regex meta character behind a backslash.

    """
    return "".join(f"\\{c}" if c in META else c for c in text)


@dataclass(frozen=True)
class Record:
    """One `// mutants:equivalent` comment."""

    path: str
    line: int
    site: int
    mutation: str
    reason: str

    @property
    def spelled(self) -> str:
        """Its location and mark, as every message about it begins."""
        return f"{self.path}:{self.line}: {MARK}"

    @property
    def pattern(self) -> str:
        """The `--exclude-re` it stands for: its mutation, at its site."""
        return (
            f"^{escape(self.path)}:{self.site}:[0-9]+:"
            f" {escape(self.mutation)}( in .*)?$"
        )


def read_records(text: str) -> list[Record]:
    """Parse every `// mutants:equivalent` comment `git grep -n` found.

    Returns:
        One record per comment, malformed ones included.

    """
    found = []
    for hit in lines_of(text):
        if not hit:
            continue
        path, number, content = hit.split(":", 2)
        before, _, after = content.partition(MARK)
        match = RECORD.match(after.strip())
        mutation = typed_std.group(match, "mutation") if match else None
        reason = typed_std.group(match, "reason") if match else None
        line = int(number)
        found.append(
            Record(
                path=path,
                line=line,
                site=line if before.strip() else line + 1,
                mutation=(mutation or "").strip(),
                reason=(reason or "").strip(),
            )
        )
    return found


def malformed(records: Sequence[Record]) -> list[str]:
    """Hold every record to its form.

    Returns:
        One message per record that cannot explain anything.

    """
    found = []
    for r in records:
        if not r.mutation:
            found.append(f"{r.spelled} names no mutation")
        elif not r.reason:
            found.append(f"{r.spelled} {r.mutation} gives no reason")
    return found


def records(text: str) -> int:
    """Hold the site records to their form; print each one's pattern.

    Returns:
        0 when every record is well formed, 1 otherwise.

    """
    found = read_records(text)
    wrong = malformed(found)
    for message in wrong:
        findings.finding(
            LANGUAGE,
            message,
            [
                (
                    f"{SPELLING} — the mutation is cargo-mutants' own text"
                    " for the mutant, without its ' in <function>'"
                )
            ],
        )
    if wrong:
        return EXIT_RED
    for r in found:
        print(r.pattern)
    return 0


def unmatched(found: Sequence[Record], names: Sequence[str]) -> list[str]:
    """Hold each record to a mutant the `--no-config` listings name.

    Returns:
        One message per record in no mutated file, or matching nothing.

    """
    population = set()
    for name in names:
        match = NAME.match(name)
        if match:
            population.add(typed_std.group(match, "path"))
    reds = []
    for r in found:
        if r.path not in population:
            reds.append(f"{r.spelled} {r.mutation} is in no mutated file")
            continue
        pattern = re.compile(r.pattern)
        if not any(pattern.search(name) for name in names):
            reds.append(
                f"{r.spelled} {r.mutation} is stale: no such mutant"
                f" at line {r.site} — delete it"
            )
    return reds


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
    # Every mutant the `--no-config` listings name, in every run.
    names: list[str] = field(default_factory=list)
    # The site records that explain nothing.
    records: list[str] = field(default_factory=list)


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
    listing = (run / "all.txt").read_text(encoding="utf-8")
    verdict.names.extend(line for line in lines_of(listing) if line.strip(" \t"))
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
    """Replay the shell's log of runs, in order; then hold the records.

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
    grepped = scratch / "records.txt"
    if grepped.is_file():
        found = read_records(grepped.read_text(encoding="utf-8"))
        verdict.records = unmatched(found, verdict.names)
    return verdict


def red(verdict: Verdict) -> bool:
    """Say whether the leg is red.

    Returns:
        True for any survivor, any run that judged nothing, or any
        record that explains nothing.

    """
    return bool(verdict.survived or verdict.runs or verdict.records)


def report(args: Args) -> int:
    """Print the survivors, the runs that judged nothing and the summary.

    Only a shard that judged everything it was given writes `rust.tsv`:
    a red run (as opposed to red survivors or records) writes nothing,
    and the totals step names the shard that is missing.

    Returns:
        0 when green, 1 when red.

    """
    verdict = read_runs(Path(args.scratch))
    for survivor in verdict.survivors:
        print(f"{LABEL}: survived: {survivor}")
    for run in verdict.runs:
        findings.finding(LANGUAGE, str(run))
    for record in verdict.records:
        findings.finding(LANGUAGE, record)
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
        findings.finding(
            LANGUAGE,
            "red — delete code a survivor proves redundant, kill each survivor"
            " with an assertion, or record an equivalent mutant at its site",
            [f"on its line or the line above: {SPELLING}"],
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
    sub.add_parser("records")
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
    if args.command == "records":
        return records(typed_std.stdin_text())
    if args.command == "default-major":
        try:
            packages = read_metadata(decoded.load_json(typed_std.stdin_text()))
        except decoded.UndecodableError as err:
            findings.finding(LANGUAGE, f"cargo metadata: {err}")
            return EXIT_RED
        print(default_major(packages, args.name))
        return 0
    if args.command == "plan":
        return plan(args)
    return report(args)


if __name__ == "__main__":
    sys.exit(main())
