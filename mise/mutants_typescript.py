#!/usr/bin/env python3
"""The judging half of audit:mutants' TypeScript leg (#1358).

Called by mutants-typescript.sh, twice:

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

Prints each survivor and one summary line; writes `typescript.tsv` for
the totals job when a summary directory is given and everything was
judged. Exit 1 on any red.
"""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import decoded

KILLED = frozenset({"Killed", "Timeout"})
NO_REASON = "Ignored using a comment"
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


def check_config(path: str, label: str) -> int:
    """Refuse a config that excludes without a reason at the site.

    Returns:
        0 when sound, 1 otherwise.

    """
    try:
        config = read_config(decoded.load_json(Path(path).read_bytes()))
    except (OSError, decoded.UndecodableError) as err:
        print(f"{label}: {path} is not readable JSON: {err}", file=sys.stderr)
        return 1
    found = findings(config)
    for line in found:
        print(f"{label}: {path} {line}", file=sys.stderr)
    if found:
        print(
            f"{label}: record each equivalent mutant at its site instead: {MARK}",
            file=sys.stderr,
        )
    incremental = config is not None and config.incremental
    if incremental:
        print(
            f"{label}: {path} sets `incremental` — a runner has no earlier"
            " results, so it would read as on and do nothing; the leg runs every"
            " mutant every week (deferred, #1358)",
            file=sys.stderr,
        )
    return 1 if found or incremental else 0


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


def classify(files: dict[str, tuple[Mutant, ...]] | None) -> Verdict:
    """Class every mutant of every file in the report.

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
    if verdict.total == 0:
        verdict.unjudged.append("the report holds no mutant — nothing was mutated")
    return verdict


def judge(path: str, label: str, summary_dir: str) -> int:
    """Read the report, print the verdict, write the counts.

    Returns:
        0 when nothing is unexplained, 1 otherwise.

    """
    try:
        files = read_report(decoded.load_json(Path(path).read_bytes()))
    except (OSError, decoded.UndecodableError) as err:
        print(f"{label}: no readable report at {path}: {err}", file=sys.stderr)
        return 1
    verdict = classify(files)
    for where in verdict.survived:
        print(f"{label}: survived: {where}")
    for where in verdict.not_covered:
        print(f"{label}: not covered: {where}")
    for line in verdict.unjudged:
        print(f"{label}: not judged: {line}", file=sys.stderr)
    for line in verdict.red:
        print(f"{label}: {line}", file=sys.stderr)
    print(
        f"{label}: {verdict.total} mutants, {verdict.killed} killed,"
        f" {len(verdict.survived)} survived, {len(verdict.not_covered)} not covered,"
        f" {verdict.not_viable} not viable, {verdict.excluded} excluded"
    )
    if summary_dir and not verdict.unjudged:
        red = len(verdict.survived) + len(verdict.not_covered)
        Path(summary_dir).mkdir(parents=True, exist_ok=True)
        Path(summary_dir, "typescript.tsv").write_text(
            f"0/1\t{verdict.total}\t{verdict.killed}\t{red}\t{verdict.excluded}\n",
            encoding="utf-8",
        )
    if not verdict.green:
        print(
            f"{label}: red — kill each survivor with an assertion, add a test for"
            f" each mutant no test reaches, or record an equivalent mutant at its"
            f" site: {MARK}",
            file=sys.stderr,
        )
    return 0 if verdict.green else 1


class Args(argparse.Namespace):
    """The subcommand and its arguments, each a string."""

    label: str
    command: str
    path: str
    summary_dir: str


def main() -> int:
    """Answer `config` or `verdict`.

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
    args = parser.parse_args(namespace=Args())
    if args.command == "config":
        return check_config(args.path, args.label)
    return judge(args.path, args.label, args.summary_dir)


if __name__ == "__main__":
    sys.exit(main())
