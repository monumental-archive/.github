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
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

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


def refusals(config: object) -> list[str]:
    """Name every setting that excludes mutants without a site's reason.

    Returns:
        One message per refused or missing setting; empty when sound.

    """
    if not isinstance(config, dict):
        return ["is not a JSON object"]
    found: list[str] = []
    mutate = config.get("mutate")
    if (
        not isinstance(mutate, list)
        or not mutate
        or not all(isinstance(m, str) and m for m in mutate)
    ):
        found.append(
            "names no `mutate` list — the paths to mutate are the repository's to state"
        )
    mutator = config.get("mutator")
    if isinstance(mutator, dict) and mutator.get("excludedMutations"):
        found.append(
            "sets `mutator.excludedMutations` — it drops a mutator everywhere, with no"
            " reason at any site"
        )
    if config.get("ignoreStatic") is True:
        found.append(
            "sets `ignoreStatic` — it drops every static mutant,"
            " with no reason at any site"
        )
    if config.get("ignorers"):
        found.append(
            "sets `ignorers` — they drop mutants by rule, with no reason at any site"
        )
    thresholds = config.get("thresholds")
    if isinstance(thresholds, dict) and thresholds.get("break") is not None:
        found.append(
            "sets `thresholds.break` — the measure is no unexplained survivor,"
            " never a score"
        )
    return found


def check_config(path: str, label: str) -> int:
    """Refuse a config that excludes without a reason at the site.

    Returns:
        0 when sound, 1 otherwise.

    """
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        print(f"{label}: {path} is not readable JSON: {err}", file=sys.stderr)
        return 1
    found = refusals(config)
    for line in found:
        print(f"{label}: {path} {line}", file=sys.stderr)
    if found:
        print(
            f"{label}: record each equivalent mutant at its site instead: {MARK}",
            file=sys.stderr,
        )
    incremental = isinstance(config, dict) and config.get("incremental") is True
    if incremental:
        print(
            f"{label}: {path} sets `incremental` — a runner has no earlier"
            " results, so it would read as on and do nothing; the leg runs every"
            " mutant every week (deferred, #1358)",
            file=sys.stderr,
        )
    return 1 if found or incremental else 0


def site(path: str, mutant: dict) -> str:
    """Spell a mutant's place and change for a reader.

    Returns:
        `file:line:column: Mutator: replacement`, the replacement's
        first line only.

    """
    start = mutant.get("location", {}).get("start", {})
    replacement = str(mutant.get("replacement", "")).splitlines() or [""]
    return (
        f"{path}:{start.get('line', '?')}:{start.get('column', '?')}:"
        f" {mutant.get('mutatorName', '?')}: {replacement[0].strip()}"
    )


def class_mutant(verdict: Verdict, mutant: dict, where: str) -> None:
    """Class one mutant by its status into the verdict."""
    status = mutant.get("status")
    if status in KILLED:
        verdict.killed += 1
    elif status == "Survived":
        verdict.survived.append(where)
    elif status == "NoCoverage":
        verdict.not_covered.append(where)
    elif status == "Ignored":
        reason = str(mutant.get("statusReason") or "").strip()
        if reason and reason != NO_REASON:
            verdict.excluded += 1
        else:
            verdict.red.append(f"excluded without a reason: {where}")
    elif status == "CompileError":
        verdict.not_viable += 1
    else:
        verdict.unjudged.append(f"{status}: {where}")


def classify(report: dict) -> Verdict:
    """Class every mutant of every file in the report.

    Returns:
        The verdict.

    """
    verdict = Verdict()
    files = report.get("files")
    if not isinstance(files, dict):
        verdict.unjudged.append("the report has no `files` object")
        return verdict
    for path in sorted(files):
        for mutant in files[path].get("mutants", []):
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
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        print(f"{label}: no readable report at {path}: {err}", file=sys.stderr)
        return 1
    verdict = classify(report if isinstance(report, dict) else {})
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
    args = parser.parse_args()
    if args.command == "config":
        return check_config(args.path, args.label)
    return judge(args.path, args.label, args.summary_dir)


if __name__ == "__main__":
    sys.exit(main())
