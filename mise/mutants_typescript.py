#!/usr/bin/env python3
r"""The judging half of audit:mutants' TypeScript leg (#1358).

Called by mutants-typescript.sh three times:

`config <stryker.config.json>`, before StrykerJS runs: the repository's
config is the tool's own, and the leg reads it only to refuse what buys
the denominator with no reason at any site:

- `mutate` must be a non-empty list of strings: the paths to mutate are
  the repository's, so a repository adopts its service-free code first
  and widens the list once it has measured the rest (#1358);
- `mutator.excludedMutations` drops a whole mutator everywhere;
- `ignoreStatic: true` drops every static mutant;
- `ignorers` are plugins that drop mutants by rule;
- `thresholds.break` is a score: the measure is never one, and a break
  makes StrykerJS exit non-zero over a run that judged everything.

Each is red by name. So is `incremental: true`, for another reason:
incremental mode is deferred (ruling on #1358). A runner starts with
no earlier results, so it would read as on and do nothing; the leg
runs every mutant every week. Nothing is printed when the config is
acceptable.

`share <stryker.config.json> --shard k/n --out <file>`, before the run
(#2153): StrykerJS has no sharding of its own, but `--mutate` takes the
files to mutate, so the leg shares them out itself. The config's
`mutate` list is expanded, in order, against the tracked files: an
entry adds what it matches and an entry starting with `!` subtracts,
as StrykerJS reads the list. Matching is StrykerJS's minimatch, spelled
for `PurePosixPath.full_match`: braces expanded, a wildcard never
matching a name that starts with `.`, `\x` a literal `x`. The result
is sorted and shard `k` takes every file whose index `i` has
`i % n == k`, the round-robin cut the other legs make. `--out` gets the
share as one `--mutate` argument: comma-separated, each file escaped
so minimatch reads it literally. Red by name: an entry with a mutation
range (`file:1-5`), which cannot be shared out by file; an extglob,
which `full_match` cannot spell; a file whose name StrykerJS's
`--mutate` cannot carry (a comma, or a trailing range); and a list that
matches no tracked file. A shard whose share is empty writes its zero
row when a summary directory is given, and `--out` is left empty.

`verdict <mutation.json>`, after it: StrykerJS 10's JSON report
(`reports/mutation/mutation.json`, mutation-testing-report-schema 1.0)
gives one `status` per mutant:

- Killed and Timeout are killed (the suite noticed, by failing or by
  hanging);
- Survived is a survivor; NoCoverage is a survivor no test reaches,
  named `not covered`: untested, never equivalent;
- Ignored is excluded when it carries its reason. StrykerJS's own
  per-site mechanism, `// Stryker disable next-line <Mutator>: <reason>`,
  puts the reason in `statusReason`; with no reason StrykerJS writes
  the literal `Ignored using a comment` (measured, 10.1.1), which is red
  as an exclusion without its reason;
- CompileError is counted on its own as not viable, never as killed: a
  checker rejected the mutant, so no test was asked anything;
- RuntimeError, Pending and anything else were not judged, and are red.

A whole run (`--shard k/1`) that holds no mutant mutated nothing and is
not judged; one shard of several may hold none, when its files give no
mutant.

Prints each survivor and one summary line; writes `typescript.tsv` for
the totals job when a summary directory is given and everything was
judged: `k/n`, the counts, and `shard`, since the excluded count is
this shard's own files'. Exit 1 on any red.
"""

import argparse
import posixpath
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import decoded
import findings as writer
import tracked

KILLED = frozenset({"Killed", "Timeout"})
NO_REASON = "Ignored using a comment"
# The leg's language: the subject of its findings, as --label names it.
LANGUAGE = "typescript"
MARK = "// Stryker disable next-line <Mutator>: <reason>"


@dataclass
class Verdict:
    """What the report says, classed."""

    total: int = 0
    killed: int = 0
    excluded: int = 0
    not_viable: int = 0
    survived: list[str] = field(default_factory=list)
    not_covered: list[str] = field(default_factory=list)
    unjudged: list[str] = field(default_factory=list)
    red: list[str] = field(default_factory=list)

    @property
    def green(self) -> bool:
        """Whether nothing is unexplained.

        Returns:
            True when no survivor, no unjudged mutant and no red remains.

        """
        return not (self.survived or self.not_covered or self.unjudged or self.red)


@dataclass(frozen=True)
class StrykerConfig:
    """What the leg asks of stryker.config.json, each answer read once."""

    # `mutate` is a non-empty list of non-empty paths.
    names_mutate: bool
    # `mutator.excludedMutations` is set.
    excludes_mutations: bool
    ignores_static: bool
    sets_ignorers: bool
    # `thresholds.break` is set.
    sets_break: bool
    incremental: bool


def read_config(config: object) -> StrykerConfig | None:
    """Read the settings the leg judges from a decoded config.

    Returns:
        The answers, or None when the config is not a JSON object.

    """
    if not decoded.is_object(config):
        return None
    mutate = config.get("mutate")
    mutator = config.get("mutator")
    thresholds = config.get("thresholds")
    return StrykerConfig(
        names_mutate=decoded.is_strings(mutate) and bool(mutate) and all(mutate),
        excludes_mutations=decoded.is_object(mutator)
        and bool(mutator.get("excludedMutations")),
        ignores_static=config.get("ignoreStatic") is True,
        sets_ignorers=bool(config.get("ignorers")),
        sets_break=decoded.is_object(thresholds)
        and thresholds.get("break") is not None,
        incremental=config.get("incremental") is True,
    )


def findings(config: StrykerConfig | None) -> list[str]:
    """Name every setting that excludes mutants without a site's reason.

    Returns:
        One message per refused or missing setting; empty when sound.

    """
    if config is None:
        return ["is not a JSON object"]
    found: list[str] = []
    if not config.names_mutate:
        found.append(
            "names no `mutate` list — the paths to mutate are the repository's to state"
        )
    if config.excludes_mutations:
        found.append(
            "sets `mutator.excludedMutations` — it drops a mutator everywhere, with no"
            " reason at any site"
        )
    if config.ignores_static:
        found.append(
            "sets `ignoreStatic` — it drops every static mutant,"
            " with no reason at any site"
        )
    if config.sets_ignorers:
        found.append(
            "sets `ignorers` — they drop mutants by rule, with no reason at any site"
        )
    if config.sets_break:
        found.append(
            "sets `thresholds.break` — the measure is no unexplained survivor,"
            " never a score"
        )
    return found


def refusals(config: object) -> list[str]:
    """Name every setting of a decoded config the leg refuses.

    Returns:
        One message per refused or missing setting; empty when sound.

    """
    return findings(read_config(config))


def check_config(path: str) -> int:
    """Refuse a config that excludes without a reason at the site.

    Returns:
        0 when sound, 1 otherwise.

    """
    try:
        config = read_config(decoded.load_json(Path(path).read_bytes()))
    except (OSError, decoded.UndecodableError) as err:
        writer.finding(LANGUAGE, f"{path} is not readable JSON: {err}")
        return 1
    found = findings(config)
    for line in found:
        writer.finding(LANGUAGE, f"{path} {line}")
    if found:
        writer.finding(
            LANGUAGE, f"record each equivalent mutant at its site instead: {MARK}"
        )
    incremental = config is not None and config.incremental
    if incremental:
        writer.finding(
            LANGUAGE,
            f"{path} sets `incremental` — a runner has no earlier"
            " results, so it would read as on and do nothing; the leg runs every"
            " mutant every week (deferred, #1358)",
        )
    return 1 if found or incremental else 0


# StrykerJS's own spelling of a mutation range (`mutate-pattern-constants`,
# 10.1.1): `file:startLine[:startColumn]-endLine[:endColumn]`.
RANGE = re.compile(r"(.*?):((\d+)(?::(\d+))?-(\d+)(?::(\d+))?)$")
EXTGLOB = re.compile(r"[?*+@!]\(")
# What minimatch reads as syntax; each is escaped to name a file literally.
SYNTAX = re.compile(r"[\\*?\[\]{}()!]")


class ShareError(ValueError):
    """The config's `mutate` list cannot be shared out by file."""


def parse_shard(shard: str) -> tuple[int, int]:
    """Read `k/n`.

    Returns:
        k and n.

    Raises:
        ShareError: it is not two whole numbers with 0 <= k < n.

    """
    match = re.fullmatch(r"(\d+)/(\d+)", shard)
    if match is None or int(match[1]) >= int(match[2]):
        msg = f"MUTANTS_SHARD='{shard}' is not k/n with 0 <= k < n"
        raise ShareError(msg)
    return int(match[1]), int(match[2])


def groups(pattern: str) -> list[tuple[int, int]]:
    r"""Find every brace pair of a pattern, `\{` and `\}` never one.

    Returns:
        Each pair's opening and closing index, by opening index.

    """
    opened: list[int] = []
    pairs: list[tuple[int, int]] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            i += 2
            continue
        if char == "{":
            opened.append(i)
        elif char == "}" and opened:
            pairs.append((opened.pop(), i))
        i += 1
    return sorted(pairs)


def braces(pattern: str) -> list[str]:
    """Expand a pattern's braces as minimatch does.

    A brace with a comma is a list, `{1..3}` and `{a..c}` are ranges,
    and a brace with neither is literal, while the braces inside it
    still expand.

    Returns:
        Every pattern the braces spell, in order.

    """
    for start, end in groups(pattern):
        items = alternatives(pattern[start + 1 : end])
        if items is not None:
            head, tail = pattern[:start], pattern[end + 1 :]
            return [x for item in items for x in braces(head + item + tail)]
    return [pattern]


def span(a: int, b: int) -> list[int]:
    """Count from a to b, either way, both ends included.

    Returns:
        The values, in order from a.

    """
    descending = b < a  # pragma: no mutate -- with a == b both orders are one value
    low, high = sorted((a, b))
    values = list(range(low, high + 1))
    return values[::-1] if descending else values


def alternatives(body: str) -> list[str] | None:
    """Split a brace's body into its alternatives.

    Returns:
        The alternatives, or None when the brace is literal.

    """
    numbers = re.fullmatch(r"(-?\d+)\.\.(-?\d+)", body)
    if numbers:
        return [str(x) for x in span(int(numbers[1]), int(numbers[2]))]
    letters = re.fullmatch(r"([a-zA-Z])\.\.([a-zA-Z])", body)
    if letters:
        return [chr(x) for x in span(ord(letters[1]), ord(letters[2]))]
    items: list[str] = []
    depth, start, i = 0, 0, 0
    while i < len(body):
        char = body[i]
        if char == "\\":
            i += 2
            continue
        depth += {"{": 1, "}": -1}.get(char, 0)
        if char == "," and depth == 0:
            items.append(body[start:i])
            start = i + 1
        i += 1
    return [*items, body[start:]] if items else None


def full_match_spelling(pattern: str) -> str:
    r"""Spell a minimatch glob for `PurePosixPath.full_match`.

    `\x` becomes `x`, bracketed where `x` is `*`, `?` or `[`, and a `[^`
    class becomes `[!`.

    Returns:
        The pattern `full_match` reads the same way.

    """
    out, i = [], 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\" and i + 1 < len(pattern):
            literal = pattern[i + 1]
            out.append(f"[{literal}]" if literal in "*?[" else literal)
            i += 2
            continue
        if char == "[" and pattern[i + 1 : i + 2] == "^":
            out.append("[!")
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out)


def matches(path: str, pattern: str) -> bool:
    """Say whether minimatch, with `dot: false`, matches a file to a glob.

    A name starting with `.` is matched only by a pattern part that
    itself starts with `.`, so `**` and `*` never reach one.

    Returns:
        True when the file matches.

    """
    pure = PurePosixPath(path)
    if not pure.full_match(full_match_spelling(pattern)):
        return False
    dotted = [part for part in pattern.split("/") if part.startswith(".")]
    return all(
        any(PurePosixPath(name).full_match(full_match_spelling(p)) for p in dotted)
        for name in pure.parts
        if name.startswith(".")
    )


def relative(pattern: str, root: str) -> str | None:
    """Resolve a pattern against the tree, as StrykerJS does.

    Returns:
        The pattern relative to the tree's top, or None outside it.

    """
    whole = posixpath.normpath(posixpath.join(root, pattern))
    return whole.removeprefix(root + "/") if whole.startswith(root + "/") else None


def expand(mutate: list[str], files: list[str], root: str) -> list[str]:
    """Expand `mutate` against the tracked files, in its own order.

    Returns:
        The files to mutate, sorted.

    Raises:
        ShareError: an entry has a range or an extglob, or the list
            matches no file.

    """
    chosen: set[str] = set()
    for entry in mutate:
        negate = entry.startswith("!")
        glob = entry.removeprefix("!")
        if RANGE.match(glob):
            msg = (
                f"`mutate` entry '{entry}' carries a mutation range; a range"
                " cannot be shared out by file"
            )
            raise ShareError(msg)
        if EXTGLOB.search(glob):
            msg = f"`mutate` entry '{entry}' is an extglob, which the leg cannot expand"
            raise ShareError(msg)
        globs = [g for g in (relative(x, root) for x in braces(glob)) if g]
        hit = {f for f in files if any(matches(f, g) for g in globs)}
        chosen = chosen - hit if negate else chosen | hit
    if not chosen:
        msg = "`mutate` matches no tracked file"
        raise ShareError(msg)
    return sorted(chosen)


def argument(files: list[str]) -> str:
    """Spell files as one `--mutate` argument, each read literally.

    Returns:
        The files, escaped, joined by commas.

    Raises:
        ShareError: a file's name cannot be carried.

    """
    for path in files:
        if "," in path or RANGE.match(path):
            msg = (
                f"{path}: StrykerJS's --mutate cannot name this file (a comma,"
                " or a trailing range)"
            )
            raise ShareError(msg)
    return ",".join(SYNTAX.sub(r"\\\g<0>", path) for path in files)


def shares(path: str, shard: str) -> tuple[list[str], int]:
    """Read the config and cut this shard's share of its files.

    Returns:
        The shard's files and how many the whole list holds.

    Raises:
        ShareError: the shard, the list or a file cannot be shared out.

    """
    k, n = parse_shard(shard)
    mutate = decoded.as_object(decoded.load_json(Path(path).read_bytes()), path).get(
        "mutate"
    )
    if not decoded.is_strings(mutate):
        msg = f"{path} names no `mutate` list"
        raise ShareError(msg)
    root = Path.cwd()
    every = expand(mutate, tracked.tracked(root=root), root.as_posix())
    return every[k::n], len(every)


def share(path: str, label: str, shard: str, out: str, summary_dir: str) -> int:
    """Write this shard's share of the files to mutate.

    Returns:
        0 when shared, 1 on any red.

    """
    try:
        mine, every = shares(path, shard)
        text = argument(mine)
    except (OSError, decoded.UndecodableError, ShareError) as err:
        writer.finding(LANGUAGE, str(err))
        return 1
    print(f"{label}: shard {shard}: {len(mine)} of {every} files")
    Path(out).write_text(text, encoding="utf-8")
    if not mine and summary_dir:
        Path(summary_dir).mkdir(parents=True, exist_ok=True)
        Path(summary_dir, "typescript.tsv").write_text(
            f"{shard}\t0\t0\t0\t0\tshard\n", encoding="utf-8"
        )
    return 0


@dataclass(frozen=True)
class Mutant:
    """One mutant of StrykerJS's JSON report, as the verdict reads it."""

    # Where it starts, as printed: "?" when the report leaves it out.
    line: str
    column: str
    mutator: str
    replacement: str
    status: str
    # Why it is ignored; "" when the report gives no reason.
    reason: str


def optional_object(value: object, where: str) -> dict[str, object]:
    """Read an object the report may leave out.

    Returns:
        The object, or an empty one when it is absent or null.

    """
    return {} if value is None else decoded.as_object(value, where)


def read_mutant(value: object, where: str) -> Mutant:
    """Decode one mutant of the report.

    Returns:
        The mutant; a place, mutator or replacement left out reads as
        `?`, `?` and "".

    """
    mutant = decoded.as_object(value, where)
    location = optional_object(mutant.get("location"), f"{where}.location")
    start = optional_object(location.get("start"), f"{where}.location.start")
    line, column = start.get("line"), start.get("column")
    mutator = mutant.get("mutatorName")
    replacement = mutant.get("replacement")
    reason = mutant.get("statusReason")
    return Mutant(
        line="?" if line is None else str(decoded.as_int(line, f"{where}.line")),
        column="?"
        if column is None
        else str(decoded.as_int(column, f"{where}.column")),
        mutator="?"
        if mutator is None
        else decoded.as_str(mutator, f"{where}.mutatorName"),
        replacement=""
        if replacement is None
        else decoded.as_str(replacement, f"{where}.replacement"),
        status=decoded.as_str(mutant.get("status"), f"{where}.status"),
        reason=decoded.as_str(reason, f"{where}.statusReason") if reason else "",
    )


def read_report(report: object) -> dict[str, tuple[Mutant, ...]] | None:
    """Decode StrykerJS's JSON report: each file's mutants.

    A field of another shape is refused by name, through `decoded`'s
    readers.

    Returns:
        Each file's mutants, by path; None when the report has no
        `files` object.

    """
    files = report.get("files") if decoded.is_object(report) else None
    if not decoded.is_object(files):
        return None
    out: dict[str, tuple[Mutant, ...]] = {}
    for path, entry in files.items():
        where = f'files["{path}"]'
        mutants = optional_object(entry, where).get("mutants")
        out[path] = tuple(
            read_mutant(item, f"{where}.mutants[{index}]")
            for index, item in enumerate(
                [] if mutants is None else decoded.as_array(mutants, f"{where}.mutants")
            )
        )
    return out


def site(path: str, mutant: Mutant) -> str:
    """Spell a mutant's place and change for a reader.

    Returns:
        `file:line:column: Mutator: replacement`, the replacement's
        first line only.

    """
    replacement = mutant.replacement.splitlines() or [""]
    return (
        f"{path}:{mutant.line}:{mutant.column}:"
        f" {mutant.mutator}: {replacement[0].strip()}"
    )


def class_mutant(verdict: Verdict, mutant: Mutant, where: str) -> None:
    """Class one mutant by its status into the verdict."""
    status = mutant.status
    if status in KILLED:
        verdict.killed += 1
    elif status == "Survived":
        verdict.survived.append(where)
    elif status == "NoCoverage":
        verdict.not_covered.append(where)
    elif status == "Ignored":
        reason = mutant.reason.strip()
        if reason and reason != NO_REASON:
            verdict.excluded += 1
        else:
            verdict.red.append(f"excluded without a reason: {where}")
    elif status == "CompileError":
        verdict.not_viable += 1
    else:
        verdict.unjudged.append(f"{status}: {where}")


def classify(
    files: dict[str, tuple[Mutant, ...]] | None, *, whole: bool = True
) -> Verdict:
    """Class every mutant of every file in the report.

    A whole run that holds no mutant mutated nothing, and is not
    judged; one shard of several may hold none, when its files have no
    mutant to give (#2153).

    Returns:
        The verdict.

    """
    verdict = Verdict()
    if files is None:
        verdict.unjudged.append("the report has no `files` object")
        return verdict
    for path in sorted(files):
        for mutant in files[path]:
            verdict.total += 1
            class_mutant(verdict, mutant, site(path, mutant))
    if verdict.total == 0 and whole:
        verdict.unjudged.append("the report holds no mutant — nothing was mutated")
    return verdict


def judge(path: str, label: str, summary_dir: str, shard: str = "0/1") -> int:
    """Read the report, print the verdict, write the counts.

    Returns:
        0 when nothing is unexplained, 1 otherwise.

    """
    try:
        files = read_report(decoded.load_json(Path(path).read_bytes()))
    except (OSError, decoded.UndecodableError) as err:
        writer.finding(LANGUAGE, f"no readable report at {path}: {err}")
        return 1
    verdict = classify(files, whole=shard.endswith("/1"))
    for where in verdict.survived:
        print(f"{label}: survived: {where}")
    for where in verdict.not_covered:
        print(f"{label}: not covered: {where}")
    for line in verdict.unjudged:
        writer.finding(LANGUAGE, f"not judged: {line}")
    for line in verdict.red:
        writer.finding(LANGUAGE, line)
    print(
        f"{label}: {verdict.total} mutants, {verdict.killed} killed,"
        f" {len(verdict.survived)} survived, {len(verdict.not_covered)} not covered,"
        f" {verdict.not_viable} not viable, {verdict.excluded} excluded"
    )
    if summary_dir and not verdict.unjudged:
        red = len(verdict.survived) + len(verdict.not_covered)
        Path(summary_dir).mkdir(parents=True, exist_ok=True)
        Path(summary_dir, "typescript.tsv").write_text(
            f"{shard}\t{verdict.total}\t{verdict.killed}\t{red}"
            f"\t{verdict.excluded}\tshard\n",
            encoding="utf-8",
        )
    if not verdict.green:
        writer.finding(
            LANGUAGE,
            "red — delete code a survivor proves redundant, kill each survivor"
            " with an assertion, add a test for each mutant no test reaches, or"
            " record an equivalent mutant at its"
            f" site: {MARK}",
        )
    return 0 if verdict.green else 1


class Args(argparse.Namespace):
    """The subcommand and its arguments, each a string."""

    label: str
    command: str
    path: str
    summary_dir: str
    shard: str
    out: str


def main(argv: list[str]) -> int:
    """Answer `config`, `share` or `verdict`.

    Returns:
        The subcommand's exit status.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", default="audit:mutants: typescript")
    sub = parser.add_subparsers(dest="command", required=True)
    config = sub.add_parser("config")
    config.add_argument("path")
    verdict = sub.add_parser("verdict")
    verdict.add_argument("path")
    verdict.add_argument("--summary-dir", default="")
    verdict.add_argument("--shard", default="0/1")
    shares = sub.add_parser("share")
    shares.add_argument("path")
    shares.add_argument("--shard", default="0/1")
    shares.add_argument("--out", required=True)
    shares.add_argument("--summary-dir", default="")
    args = parser.parse_args(argv, namespace=Args())
    if args.command == "config":
        return check_config(args.path)
    if args.command == "share":
        return share(args.path, args.label, args.shard, args.out, args.summary_dir)
    return judge(args.path, args.label, args.summary_dir, args.shard)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
