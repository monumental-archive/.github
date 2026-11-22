#!/usr/bin/env python3
"""Decide which of a scheduled workflow's jobs apply to this tree (#1891).

A scheduled job for a language a repository does not have used to take a
runner, install the whole pinned toolchain, and only then run a task
that found nothing and skipped: twelve of edtf's 24 audit jobs, about 40
runner-minutes a run. The question "does this job apply" was asked
inside the job, after the runner and the install were spent.

`mise/audit-legs.toml` says, for every job of the reusable subject
workflows, the tracked-file condition under which its task does not
skip. Each workflow's first job, `applies`, runs this helper over the
tracked files (mise/tracked.py) with no toolchain installed, and each
conditional job carries `if: needs.applies.outputs.<job> == 'true'`, so GitHub skips it
without starting a runner. The pathspec meaning and the tracked-file
listing are #1410's (`gate_tools.matches`, `gate_tools.tracked_files`):
one matcher, not two.

A job applies when:

- its row carries no `when` (every repository);
- or one of its `when` pathspecs matches a tracked file, and, where the
  row names a `floor` leg, the tracked `.coverage-floor` records that
  leg. A floor this cannot parse is left to the job, whose task reds on
  it by name.

A job that runs one runner per entry of a list read from the tree
(MATRICES) applies only where the list is not empty, and the list is an
output of its own: `repo-deep-tests`'s `fuzz` job runs one runner per
fuzz target (#1899), from `fuzz-targets`. The list is the one
`coverage-measure.sh`'s `fuzz_targets` measures (#1896): every `[[bin]]`
of a tracked fuzz/Cargo.toml, and every `func Fuzz…(… *testing.F)` in a
tracked `_test.go` of any tracked Go module, the root's or a nested
one's. A target the gate's floor holds
is a target the nightly search runs, and no other.

A job whose task reads a file's CONTENT before it runs (REFINES)
applies only where the content says so: `repo-deep-tests`'s
`rust-properties` job runs where a tracked Cargo.lock locks proptest
(#1930), answered by rust_properties.py, the reading its task uses.

    audit_legs.py [--root DIR] [--table FILE] WORKFLOW
        prints `<job>=true|false` for every row of WORKFLOW (the file's
        stem, `repo-deep-tests`) on stdout, for $GITHUB_OUTPUT, then
        `<output>=<JSON list>` for each of its matrices, and the reason
        for each job on stderr.

The table test (`mise/test_audit_legs.py`) holds the table to the
tasks: every guarded job has a row no narrower than its task's guard,
and an `if:` on it.
"""

import argparse
import json
import posixpath
import re
import string
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import coverage_floor
import decoded
import findings
import gate_tools
import rust_properties

HERE = Path(__file__).resolve().parent
TABLE = HERE / "audit-legs.toml"
FLOOR = ".coverage-floor"
FUZZ_MANIFEST = "fuzz/Cargo.toml"
# coverage-measure.sh's own sed, line by line.
GO_FUZZ = re.compile(
    r"^func (Fuzz[A-Z0-9_][A-Za-z0-9_]*|Fuzz)\([A-Za-z_][A-Za-z0-9_]* \*testing\.F\)",
    re.MULTILINE,
)

ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)

# One runner's entry: `kind`, `target`, its floor `leg` and, for Go,
# `package` and `module` (directories from the repository root). The
# leg is unique per target (coverage-measure.sh refuses two that derive
# one), so it names the runner's artifacts.
Target = dict[str, str]


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


def bin_names(manifest: bytes) -> list[str]:
    """Read a fuzz crate manifest's `[[bin]]` names, in order.

    A manifest that is not TOML, or a `[[bin]]` with no string name,
    raises decoded's UndecodableError, naming the field.

    Returns:
        The names.

    """
    bins = decoded.as_array(decoded.load_toml(manifest).get("bin", []), "[[bin]]")
    names: list[str] = []
    for i, entry in enumerate(bins, 1):
        name = decoded.as_object(entry, f"[[bin]] {i}").get("name")
        names.append(decoded.as_str(name, f"[[bin]] {i}.name"))
    return names


def fuzz_targets(root: Path, tracked: list[str]) -> list[Target]:
    """List the fuzz targets, as coverage-measure.sh's `fuzz_targets` does.

    Rust: each `[[bin]]` name of a tracked fuzz/Cargo.toml, in its
    order. Go: each `func Fuzz…(… *testing.F)` in a tracked `_test.go`
    under no vendor/ directory, with its file's directory as `package`
    (`.` at the root) and as `module` the deepest tracked go.mod's
    directory above it, as Go resolves a package's module. A test no
    tracked module owns is no target.

    Returns:
        One entry per target.

    Raises:
        UndecodableError: fuzz/Cargo.toml is not TOML, or a `[[bin]]`
            has no string name; the text names the file.

    """
    found: list[Target] = []
    if FUZZ_MANIFEST in tracked:
        try:
            names = bin_names((root / FUZZ_MANIFEST).read_bytes())
        except decoded.UndecodableError as err:
            msg = f"{FUZZ_MANIFEST}: {err}"
            raise decoded.UndecodableError(msg) from None
        found += [
            {"kind": "rust", "target": name, "leg": leg_name(name)} for name in names
        ]
    modules = [
        posixpath.dirname(p) or "."
        for p in tracked
        if posixpath.basename(p) == "go.mod" and not vendored(p)
    ]
    for path in tracked:
        if not path.endswith("_test.go") or vendored(path):
            continue
        package = posixpath.dirname(path) or "."
        module = owner(package, modules)
        if module is None:
            continue
        text = (root / path).read_text(encoding="utf-8")
        for match in GO_FUZZ.finditer(text):
            name = str(match.group(1))
            short = name.removeprefix("Fuzz")
            found.append({
                "kind": "go",
                "target": name,
                "leg": leg_name(short if package == "." else f"{package}-{short}"),
                "package": package,
                "module": module,
            })
    return found


def leg_name(name: str) -> str:
    """Derive a fuzz target's floor leg, as fuzz-measure-lib.sh does.

    `org_fuzz_leg_name`: `fuzz-<name>`, ASCII-lower-cased, every
    character but [a-z0-9] turned into `-`.

    Returns:
        The leg.

    """
    lowered = name.translate(ASCII_LOWER)
    return "fuzz-" + re.sub(r"[^a-z0-9]", "-", lowered)


def vendored(path: str) -> bool:
    """Say whether a tracked path is under a vendor/ directory.

    Returns:
        True for `vendor/…` and `…/vendor/…`.

    """
    return "vendor" in path.split("/")[:-1]


def owner(package: str, modules: list[str]) -> str | None:
    """Find the module a package directory belongs to.

    Returns:
        The deepest module directory at or above it, `.` for the root;
        None when no module owns it.

    """
    owning = [m for m in modules if m == "." or f"{package}/".startswith(f"{m}/")]
    nested = [m for m in owning if m != "."]
    if nested:
        return max(nested, key=len)
    return "." if owning else None


# Workflow stem to job id to the job's list output, how the list is
# read, and why the job is skipped when the list is empty.
Enumerate = Callable[[Path, list[str]], list[Target]]
MATRICES: dict[str, dict[str, tuple[str, Enumerate, str]]] = {
    "repo-deep-tests": {
        "fuzz": ("fuzz-targets", fuzz_targets, "no fuzz target is declared"),
    },
}


def matrices(
    workflow: str, reasons: dict[str, str], root: Path, tracked: list[str]
) -> tuple[dict[str, str], dict[str, list[Target]]]:
    """Read each matrix job's list, and skip the job where it is empty.

    Returns:
        The reasons, a matrix job with an empty list skipped; and each
        list output to its entries. A list that cannot be read raises
        its reader's UndecodableError (`fuzz_targets`).

    """
    reasons = dict(reasons)
    lists: dict[str, list[Target]] = {}
    for job, (output, enumerate_, empty) in MATRICES.get(workflow, {}).items():
        if job not in reasons:
            continue
        lists[output] = enumerate_(root, tracked)
        if reasons[job].startswith("applies") and not lists[output]:
            reasons[job] = f"skipped: {empty}"
    return reasons, lists


# Workflow stem to job id to what the job's task reads in the tree, and
# why the job is skipped when it finds nothing.
Predicate = Callable[[Path, list[str]], bool]
REFINES: dict[str, dict[str, tuple[Predicate, str]]] = {
    "repo-deep-tests": {
        "rust-properties": (
            rust_properties.locks_proptest,
            "no tracked Cargo.lock locks proptest",
        ),
    },
}


def refine(
    workflow: str, reasons: dict[str, str], root: Path, tracked: list[str]
) -> dict[str, str]:
    """Skip each content-read job where its task would find nothing.

    Returns:
        The reasons, a job its predicate refuses skipped.

    """
    reasons = dict(reasons)
    for job, (holds, empty) in REFINES.get(workflow, {}).items():
        if reasons.get(job, "").startswith("applies") and not holds(root, tracked):
            reasons[job] = f"skipped: {empty}"
    return reasons


class Args(argparse.Namespace):
    """The command line, typed where argparse writes it."""

    root: Path
    table: Path
    workflow: str


def main(argv: list[str] | None = None) -> int:
    """Print one output per job of the workflow.

    Returns:
        0, or 1 when the table cannot be read or has no such workflow,
        or a matrix's list cannot be read.

    """
    parser = argparse.ArgumentParser(prog="audit_legs.py")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--table", type=Path, default=TABLE)
    parser.add_argument("workflow")
    args = parser.parse_args(argv, namespace=Args())
    try:
        table = load_table(args.table)
    except decoded.UndecodableError as err:
        findings.finding("", str(err), annotate=True)
        return 1
    rows = table.get(args.workflow)
    if rows is None:
        findings.finding(
            "", f"{args.table} has no table for {args.workflow}", annotate=True
        )
        return 1
    tracked = gate_tools.tracked_files(args.root)
    try:
        reasons, lists = matrices(
            args.workflow, applies(rows, args.root, tracked), args.root, tracked
        )
        reasons = refine(args.workflow, reasons, args.root, tracked)
    except decoded.UndecodableError as err:
        findings.finding("", str(err), annotate=True)
        return 1
    for job, why in reasons.items():
        print(f"audit-legs: {job}: {why}", file=sys.stderr)
        print(f"{job}={'true' if why.startswith('applies') else 'false'}")
    for output, entries in lists.items():
        print(f"{output}={json.dumps(entries, separators=(',', ':'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
