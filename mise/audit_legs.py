#!/usr/bin/env python3
"""Decide which of a scheduled workflow's jobs apply to this tree (#1891).

A scheduled job for a language a repository does not have used to take a
runner, install the whole pinned toolchain, and only then run a task
that found nothing and skipped: twelve of edtf's 24 audit jobs, about 40
runner-minutes a run. The question "does this job apply" was asked
inside the job, after the runner and the install were spent.

`mise/audit-legs.toml` says, for every job of the reusable subject
workflows, the tracked-file condition under which its task does not
skip. Each workflow's first job, `applies`, runs this helper over
`git ls-files` with no toolchain installed, and each conditional job
carries `if: needs.applies.outputs.<job> == 'true'`, so GitHub skips it
without starting a runner. The pathspec meaning and the tracked-file
listing are #1410's (`gate_tools.matches`, `gate_tools.tracked_files`):
one matcher, not two.

A job applies when:

- its row carries no `when` (every repository);
- or one of its `when` pathspecs matches a tracked file, and, where the
  row names a `floor` leg, the tracked `.coverage-floor` records that
  leg. A floor this cannot parse is left to the job, whose task reds on
  it by name.

    audit_legs.py [--root DIR] [--table FILE] WORKFLOW
        prints `<job>=true|false` for every row of WORKFLOW (the file's
        stem, `repo-deep-tests`) on stdout, for $GITHUB_OUTPUT, and the
        reason for each on stderr.

The table test (`mise/test_audit_legs.py`) holds the table to the
tasks: every guarded job has a row no narrower than its task's guard,
and an `if:` on it.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import coverage_floor
import decoded
import gate_tools
import workflow_command

HERE = Path(__file__).resolve().parent
TABLE = HERE / "audit-legs.toml"
FLOOR = ".coverage-floor"


@dataclass(frozen=True)
class Row:
    """One job's row of the table, as decoded from it."""

    when: tuple[str, ...] | None = None  # None: every repository
    floor: str | None = None  # a leg .coverage-floor must record


def row_of(where: str, value: object) -> Row:
    """Decode one row of the table.

    Returns:
        The row.

    Raises:
        UndecodableError: the row or a field has the wrong shape.

    """
    row = decoded.as_object(value, where)
    unknown = sorted(set(row) - {"when", "floor"})
    if unknown:
        msg = f"{where} carries {', '.join(unknown)}: a row has `when` and `floor`"
        raise decoded.UndecodableError(msg)
    when = row.get("when")
    if when is not None and not decoded.is_strings(when):
        msg = f"{where}.when is not a list of strings"
        raise decoded.UndecodableError(msg)
    floor = row.get("floor")
    if floor is not None and floor not in coverage_floor.SEPARATE:
        legs = " or ".join(coverage_floor.SEPARATE)
        msg = f"{where}.floor is not a leg held apart in .coverage-floor ({legs})"
        raise decoded.UndecodableError(msg)
    return Row(
        when=None if when is None else tuple(when),
        floor=None if floor is None else decoded.as_str(floor, f"{where}.floor"),
    )


def load_table(path: Path = TABLE) -> dict[str, dict[str, Row]]:
    """Read the table.

    Returns:
        Workflow stem to job id to its row.

    Raises:
        UndecodableError: the file is not TOML, or a row has the wrong
            shape; the text names the file and the field.

    """
    try:
        doc = decoded.load_toml(path.read_bytes())
        return {
            workflow: {
                job: row_of(f"{workflow}.{job}", row)
                for job, row in decoded.as_object(jobs, workflow).items()
            }
            for workflow, jobs in doc.items()
        }
    except decoded.UndecodableError as err:
        msg = f"{path}: {err}"
        raise decoded.UndecodableError(msg) from None


def recorded(root: Path, tracked: list[str], leg: str) -> str | None:
    """Say whether a floor leg holds a job back.

    Returns:
        None when the tracked .coverage-floor records the leg, or will
        not parse (the job's task names the defect); else the reason the
        job does not apply.

    """
    if FLOOR not in tracked:
        return f"no {FLOOR} is tracked"
    record, _ = coverage_floor.parse((root / FLOOR).read_text(encoding="utf-8"))
    if record is None or leg in (record.separate or {}):
        return None
    return f"no {leg} line in {FLOOR}"


def applies(rows: dict[str, Row], root: Path, tracked: list[str]) -> dict[str, str]:
    """Decide which jobs apply to the tree, and why.

    Returns:
        Every job id to a reason: "applies: ..." or "skipped: ...".

    """
    reasons: dict[str, str] = {}
    for job, row in rows.items():
        if row.when is None:
            reasons[job] = "applies: every repository"
            continue
        hit = next(
            (p for p in tracked for w in row.when if gate_tools.matches(w, p)), None
        )
        if hit is None:
            reasons[job] = "skipped: nothing it reads is tracked"
            continue
        held = recorded(root, tracked, row.floor) if row.floor else None
        reasons[job] = f"skipped: {held}" if held else f"applies: {hit} is tracked"
    return reasons


class Args(argparse.Namespace):
    """The command line, typed where argparse writes it."""

    root: Path
    table: Path
    workflow: str


def main(argv: list[str] | None = None) -> int:
    """Print one output per job of the workflow.

    Returns:
        0, or 1 when the table cannot be read or has no such workflow.

    """
    parser = argparse.ArgumentParser(prog="audit_legs.py")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--table", type=Path, default=TABLE)
    parser.add_argument("workflow")
    args = parser.parse_args(argv, namespace=Args())
    try:
        table = load_table(args.table)
    except decoded.UndecodableError as err:
        workflow_command.error(f"audit-legs: {err}", stream=sys.stderr)
        return 1
    rows = table.get(args.workflow)
    if rows is None:
        workflow_command.error(
            f"audit-legs: {args.table} has no table for {args.workflow}",
            stream=sys.stderr,
        )
        return 1
    reasons = applies(rows, args.root, gate_tools.tracked_files(args.root))
    for job, why in reasons.items():
        print(f"audit-legs: {job}: {why}", file=sys.stderr)
        print(f"{job}={'true' if why.startswith('applies') else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
