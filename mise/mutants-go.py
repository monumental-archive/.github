#!/usr/bin/env python3
"""The verdict half of audit:mutants' Go leg (#1356), called by mutants-go.sh.

The shell half runs gomu, one file at a time, from a scratch directory
holding what this reads:

- `all.tsv`: every package — import path, directory, its Go files —
  and `shard.tsv`, the packages this shard mutated;
- `files.tsv`: the mutated population, import path and absolute file,
  generated files already removed;
- `cover.out`: the shard's packages' own coverage profile (`set` mode);
- `reports/*.json`: the JSON report of gomu v0.3.0, one per file;
- `gomu.log`: gomu's console output, and `runs.log`: a line per file
  whose run failed outright;
- `records.txt`: `git grep -n` of every `//mutants:equivalent` comment.

Each gomu result is classed by its `status`:

- KILLED and TIMED_OUT are killed;
- NOT_VIABLE is counted on its own and is never killed: gomu's compile
  check rejected the mutant, so no test was asked anything;
- SURVIVED on a line no test executes is NOT COVERED — gomu takes no
  coverage, so this is where that verdict is made, keyed on the
  report's file and line against the profile's executed blocks;
- SURVIVED on a covered line is explained by a record at its site of
  its type, or is a survivor;
- ERROR, or any other status, was not judged, and is red.

A RECORD is `//mutants:equivalent <type> <reason>`, either trailing the
mutant's line (code before the comment: its site is that line) or alone
on the line directly above it (its site is the next line). It explains
every covered survivor of its type at its site. It is red, by name, when
it gives no type or no reason, when it is in no mutated file, and — in
the shard that mutated its file — when the survivor at its site is of
another type, when that survivor is NOT COVERED (untested is not
equivalent: only a test fixes it), or when nothing survives there at all
(stale: the mutant is now killed, or gone).

EXCLUDED IS THE WHOLE POPULATION'S: the number of well-formed records in
mutated files, the same figure in every shard, because the totals across
shards take it once (mutation-totals.sh refuses shards that disagree).
Each record is held to its site in the one shard that mutated its file.

Prints each survivor and one summary line; writes `go.tsv` for the
totals when a summary directory is given and the run judged everything
it was given. Exit 1 on any red.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

MARK = "//mutants:equivalent"
KILLED = frozenset({"KILLED", "TIMED_OUT"})
SKIPPED = ("(error:", "(execution error:")


@dataclass
class Record:
    """One `//mutants:equivalent` comment."""

    path: str
    line: int
    site: int
    kind: str
    reason: str
    used: bool = False

    @property
    def sound(self) -> bool:
        """Whether it names a type and gives a reason."""
        return bool(self.kind and self.reason)

    @property
    def spelled(self) -> str:
        """Its location and mark, as every message about it begins."""
        return f"{self.path}:{self.line}: {MARK}"


@dataclass
class Verdict:
    """What one shard found."""

    killed: int = 0
    not_viable: int = 0
    total: int = 0
    survived: list[str] = field(default_factory=list)
    not_covered: list[str] = field(default_factory=list)
    red: list[str] = field(default_factory=list)
    unjudged: list[str] = field(default_factory=list)
    # (file, line) to each survivor there: its type, and whether a test runs it.
    at_site: dict[tuple[str, int], list[tuple[str, bool]]] = field(default_factory=dict)

    @property
    def green(self) -> bool:
        """Whether nothing is unexplained."""
        return not (self.survived or self.not_covered or self.red or self.unjudged)


@dataclass
class Shard:
    """The scratch directory's tables, read once."""

    root: str
    packages: dict[str, str]
    population: set[str]
    mine: set[str]
    covered: dict[str, list[range]]
    records: list[Record]


def parse_args() -> argparse.Namespace:
    """Read the shell half's arguments.

    Returns:
        The parsed arguments.

    """
    parser = argparse.ArgumentParser()
    for name in ("label", "where", "shard", "root", "scratch", "summary-dir"):
        parser.add_argument(f"--{name}", required=True)
    return parser.parse_args()


def rows(path: Path) -> list[list[str]]:
    """Read a tab-separated file.

    Returns:
        Its non-empty lines, split on tabs.

    """
    text = path.read_text(encoding="utf-8")
    return [line.split("\t") for line in text.splitlines() if line]


def relative(path: str, root: str) -> str:
    """Spell a path from the repository root.

    Returns:
        The path relative to `root`; the root itself is "".

    """
    if path == root:
        return ""
    return path.removeprefix(root.rstrip("/") + "/")


def read_records(scratch: Path) -> list[Record]:
    """Parse every `//mutants:equivalent` comment `git grep -n` found.

    Returns:
        One record per comment, malformed ones included.

    """
    found = []
    text = (scratch / "records.txt").read_text(encoding="utf-8")
    for hit in text.splitlines():
        path, number, content = hit.split(":", 2)
        before, _, after = content.partition(MARK)
        words = after.split(None, 1)
        line = int(number)
        found.append(
            Record(
                path=path,
                line=line,
                site=line if before.strip() else line + 1,
                kind=words[0] if words else "",
                reason=words[1].strip() if len(words) > 1 else "",
            )
        )
    return found


def read_coverage(scratch: Path, packages: dict[str, str]) -> dict[str, list[range]]:
    """Read the profile's executed blocks, keyed by file from the root.

    `packages` maps an import path to its directory from the root; the
    profile names a file by its import path. A block with count 0 was
    not executed and is dropped.

    Returns:
        File to the line ranges of blocks a test executed.

    """
    covered: dict[str, list[range]] = {}
    text = (scratch / "cover.out").read_text(encoding="utf-8")
    for entry in text.splitlines():
        if not entry or entry.startswith("mode:"):
            continue
        spec, _statements, count = entry.rsplit(" ", 2)
        name, span = spec.rsplit(":", 1)
        package, _, base = name.rpartition("/")
        directory = packages.get(package)
        if int(count) == 0 or directory is None:
            continue
        start, end = span.split(",")
        path = f"{directory}/{base}" if directory else base
        block = range(int(start.split(".")[0]), int(end.split(".")[0]) + 1)
        covered.setdefault(path, []).append(block)
    return covered


def read_shard(args: argparse.Namespace) -> Shard:
    """Read the scratch directory's tables.

    Returns:
        The shard's population, coverage and records.

    """
    scratch = Path(args.scratch)
    root = args.root
    packages = {r[0]: relative(r[1], root) for r in rows(scratch / "all.tsv")}
    mutated = {s[0] for s in rows(scratch / "shard.tsv")}
    files = rows(scratch / "files.tsv")
    return Shard(
        root=root,
        packages=packages,
        population={relative(r[1], root) for r in files},
        mine={relative(r[1], root) for r in files if r[0] in mutated},
        covered=read_coverage(scratch, packages),
        records=read_records(scratch),
    )


def class_survivor(shard: Shard, verdict: Verdict, mutant: dict, site: str) -> None:
    """Class one SURVIVED mutant: not covered, explained, or a survivor."""
    path = relative(mutant["filePath"], shard.root)
    line = mutant["line"]
    runs = any(line in block for block in shard.covered.get(path, []))
    verdict.at_site.setdefault((path, line), []).append((mutant["type"], runs))
    if not runs:
        verdict.not_covered.append(site)
        return
    matched = [
        r
        for r in shard.records
        if r.sound and (r.path, r.site, r.kind) == (path, line, mutant["type"])
    ]
    for r in matched:
        r.used = True
    if not matched:
        verdict.survived.append(site)


def class_report(shard: Shard, verdict: Verdict, report: Path) -> None:
    """Class every result in one of gomu's JSON reports."""
    data = json.loads(report.read_text(encoding="utf-8"))
    results = data.get("results") or []
    counted = data.get("totalMutants", 0)
    if counted != len(results):
        verdict.unjudged.append(
            f"{report.name}: gomu counted {counted} mutants and reported {len(results)}"
        )
    for result in results:
        verdict.total += 1
        mutant = result["mutant"]
        path = relative(mutant["filePath"], shard.root)
        site = f"{path}:{mutant['line']}:{mutant['column']} {mutant['type']}"
        status = result["status"]
        if status in KILLED:
            verdict.killed += 1
        elif status == "NOT_VIABLE":
            verdict.not_viable += 1
        elif status == "SURVIVED":
            class_survivor(shard, verdict, mutant, site)
        else:
            verdict.unjudged.append(f"{status}: {site} — gomu did not judge it")


def malformed(shard: Shard) -> list[str]:
    """Hold every record to its form, in every shard alike.

    Returns:
        One message per record that cannot explain anything.

    """
    found = []
    for r in shard.records:
        if r.path not in shard.population:
            found.append(f"{r.spelled} is in no mutated file")
        elif not r.kind:
            found.append(f"{r.spelled} names no mutator")
        elif not r.reason:
            found.append(f"{r.spelled} {r.kind} gives no reason")
    return found


def unmatched(shard: Shard, verdict: Verdict) -> list[str]:
    """Hold each sound record in this shard's files to its site.

    Returns:
        One message per record that explained nothing here.

    """
    found = []
    for r in shard.records:
        if r.used or not r.sound or r.path not in shard.mine:
            continue
        here = verdict.at_site.get((r.path, r.site), [])
        named = f"{r.spelled} {r.kind}"
        if any(kind == r.kind and not runs for kind, runs in here):
            found.append(
                f"{named} records a mutant no test runs — untested is not"
                " equivalent; only a test fixes it"
            )
        elif here:
            kinds = ", ".join(sorted({kind for kind, _ in here}))
            found.append(f"{named} names {r.kind}, but what survives here is {kinds}")
        else:
            found.append(
                f"{named} is stale: nothing survives at line {r.site} — delete it"
            )
    return found


def report(args: argparse.Namespace, verdict: Verdict, excluded: int) -> None:
    """Print the survivors, the reds and the summary line; write go.tsv."""
    label = args.label
    for site in verdict.survived:
        print(f"{label}: survived: {site}")
    for site in verdict.not_covered:
        print(f"{label}: not covered: {site}")
    for line in verdict.unjudged:
        print(f"{label}: not judged: {line}", file=sys.stderr)
    for line in verdict.red:
        print(f"{label}: {line}", file=sys.stderr)
    shard = args.where.removeprefix(", ")
    scope = f" {shard}:" if shard else ""
    print(
        f"{label}:{scope} {verdict.total} mutants, {verdict.killed} killed,"
        f" {len(verdict.survived)} survived, {len(verdict.not_covered)} not covered,"
        f" {verdict.not_viable} not viable, {excluded} excluded"
    )
    if args.summary_dir and not verdict.unjudged:
        red = len(verdict.survived) + len(verdict.not_covered)
        Path(args.summary_dir).mkdir(parents=True, exist_ok=True)
        Path(args.summary_dir, "go.tsv").write_text(
            f"{args.shard}\t{verdict.total}\t{verdict.killed}\t{red}\t{excluded}\n",
            encoding="utf-8",
        )
    if not verdict.green:
        print(
            f"{label}: red — kill each survivor with an assertion, add a test for"
            " each line no test runs, or record an equivalent mutant at its site:"
            f" {MARK} <type> <reason>",
            file=sys.stderr,
        )


def main() -> int:
    """Judge one shard.

    Returns:
        0 when nothing is unexplained, 1 otherwise.

    """
    args = parse_args()
    scratch = Path(args.scratch)
    shard = read_shard(args)
    verdict = Verdict()

    log = (scratch / "gomu.log").read_text(encoding="utf-8")
    verdict.unjudged.extend(
        line.strip() for line in log.splitlines() if any(s in line for s in SKIPPED)
    )
    runs = (scratch / "runs.log").read_text(encoding="utf-8")
    verdict.unjudged.extend(runs.splitlines())

    verdict.red.extend(malformed(shard))
    for path in sorted((scratch / "reports").glob("*.json"), key=lambda p: int(p.stem)):
        class_report(shard, verdict, path)
    verdict.red.extend(unmatched(shard, verdict))

    excluded = sum(1 for r in shard.records if r.sound and r.path in shard.population)
    report(args, verdict, excluded)
    return 0 if verdict.green else 1


if __name__ == "__main__":
    sys.exit(main())
