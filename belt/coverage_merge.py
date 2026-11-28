"""Gather every gate row's measurement into one run directory (#2160).

    belt coverage-merge <rows-dir> <run-dir>

A split test leg measures each task, and each shard of a task, on a
runner of its own: every row keeps its run directory (`measured` wrote
into it, ORG_GATE_COVERAGE_RUN) as an artifact that holds it as
`coverage-run-<index>/`, and the gate's coverage job merges every one
flat into <rows-dir>, so each row is a directory of its own there for
one row or many (#2178). This
makes of them the one run directory coverage:check reads, so the floor
is judged once, over the data every row kept, as if one runner had run
the whole suite:

    python      every row's `python/.coverage*` data file, side by side
                (`parallel = true`; `coverage combine` reads them all),
                and the rows' one rc file. Every row measures at the
                same paths, so the rc files are the same file.
    go          every row's `go/` counter and meta files, side by side:
                `go tool covdata` merges them.
    typescript  every row's `typescript/lcov.info`, merged line by line
                and branch by branch (a hit on any row is a hit), with
                the summary the leg reads computed from the merge as
                istanbul computes its own percentages.
    rust        every row's `rust-lcov.info` (each row renders its own
                with `cargo llvm-cov report`, rust-export, because its
                profiles need its own binaries), merged the same way into
                `rust-lcov.info`, which the rust leg reads in place of a
                report of profiles.
    plpgsql     every row's reports, side by side in one
                `plpgsql-coverage/` directory, one file per test
                database (#2163's shape, whose `totals` merges them):
                a row's own directory's files keep their names, and a
                row's single `plpgsql-coverage.json` goes in as
                `<row>.json`. Where one row wrote the single file and
                nothing else did, it is also kept as that file, for a
                reader of the single report; two such reports are never
                one file, so a reader of one reports nothing and the
                floor's record reds the leg that reported nothing.

A file two rows wrote under one name with different contents (other
than the merged ones) is red by name: nothing is overwritten unseen.

stdlib only.
"""

import argparse
import filecmp
import json
import math
import shutil
from pathlib import Path

from belt import findings

type Lcov = dict[str, tuple[dict[int, int], dict[tuple[str, str, str], int]]]


def read_lcov(text: str) -> Lcov:
    """Read an lcov tracefile's line and branch records.

    Returns:
        Per source file: hits per line, and hits per branch (line, block,
        branch); a branch never taken counts 0.

    """
    out: Lcov = {}
    lines: dict[int, int] = {}
    branches: dict[tuple[str, str, str], int] = {}
    source = ""
    for raw in text.splitlines():
        record, _, value = raw.partition(":")
        if record == "SF":
            source = value
            lines, branches = out.setdefault(source, ({}, {}))
        elif record == "DA" and source:
            number, hits = value.split(",")[:2]
            lines[int(number)] = lines.get(int(number), 0) + int(float(hits))
        elif record == "BRDA" and source:
            line, block, branch, taken = value.split(",")[:4]
            count = 0 if taken == "-" else int(float(taken))
            key = (line, block, branch)
            branches[key] = branches.get(key, 0) + count
        elif record == "end_of_record":
            source = ""
    return out


def merge_lcov(into: Lcov, more: Lcov) -> None:
    """Add one tracefile's hits to another's: a hit on either is a hit."""
    for source, (lines, branches) in more.items():
        have_lines, have_branches = into.setdefault(source, ({}, {}))
        for number, hits in lines.items():
            have_lines[number] = have_lines.get(number, 0) + hits
        for key, hits in branches.items():
            have_branches[key] = have_branches.get(key, 0) + hits


def branch_order(item: tuple[tuple[str, str, str], int]) -> tuple[int, str, str]:
    """Order a branch record by its line, as a number, then block and branch.

    Returns:
        The sort key.

    """
    (line, block, branch), _ = item
    return int(line), block, branch


def write_lcov(data: Lcov) -> str:
    """Write a tracefile, its LF/LH/BRF/BRH counted from its records.

    Returns:
        The tracefile's text.

    """
    out: list[str] = []
    for source in sorted(data):
        lines, branches = data[source]
        ordered = sorted(branches.items(), key=branch_order)
        out.extend([
            f"SF:{source}",
            *(f"BRDA:{ln},{block},{br},{hits}" for (ln, block, br), hits in ordered),
            f"BRF:{len(branches)}",
            f"BRH:{sum(1 for h in branches.values() if h)}",
            *(f"DA:{n},{lines[n]}" for n in sorted(lines)),
            f"LF:{len(lines)}",
            f"LH:{sum(1 for h in lines.values() if h)}",
            "end_of_record",
        ])
    return "".join(f"{line}\n" for line in out)


def percent(covered: int, total: int) -> float:
    """Compute a percentage as istanbul does: floored to two places.

    Returns:
        100 for an empty total, else covered/total floored at 0.01.

    """
    if total == 0:
        return 100.0
    return math.floor(10000 * covered / total) / 100


def summary(data: Lcov) -> dict[str, object]:
    """Total a merged tracefile as vitest's json-summary totals one.

    Returns:
        `{"total": {"lines": …, "branches": …}}` with counts and pct.

    """
    found = sum(len(lines) for lines, _ in data.values())
    hit = sum(1 for lines, _ in data.values() for h in lines.values() if h)
    branches = sum(len(b) for _, b in data.values())
    taken = sum(1 for _, b in data.values() for h in b.values() if h)
    return {
        "total": {
            "lines": {"total": found, "covered": hit, "pct": percent(hit, found)},
            "branches": {
                "total": branches,
                "covered": taken,
                "pct": percent(taken, branches),
            },
        }
    }


def place(source: Path, target: Path, red: list[str]) -> None:
    """Copy one file in, unless the same name already holds other bytes."""
    if target.exists():
        if not filecmp.cmp(source, target, shallow=False):
            red.append(f"{target.name}: two rows wrote it with different contents")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def gather(row: Path, run: Path, red: list[str]) -> None:
    """Copy one row's side-by-side files in: the rc file, Python and Go data."""
    if (row / "coverage.rc").is_file():
        place(row / "coverage.rc", run / "coverage.rc", red)
    for sub in ("python", "go"):
        files = sorted((row / sub).glob("*")) if (row / sub).is_dir() else []
        for file in (f for f in files if f.is_file()):
            place(file, run / sub / file.name, red)
    if (row / "rust").is_dir():
        (run / "rust").mkdir(exist_ok=True)


def merged(rows: list[Path], report: str) -> Lcov | None:
    """Merge every row's tracefile at one path.

    Returns:
        The merged tracefile, or None when no row wrote one.

    """
    found = [row / report for row in rows if (row / report).is_file()]
    if not found:
        return None
    data: Lcov = {}
    for path in found:
        merge_lcov(data, read_lcov(path.read_text("utf-8")))
    return data


def plpgsql(rows: list[Path], run: Path, red: list[str]) -> None:
    """Gather every row's PL/pgSQL reports into one directory."""
    singles = [row / "plpgsql-coverage.json" for row in rows]
    singles = [p for p in singles if p.is_file()]
    into = run / "plpgsql-coverage"
    for row in rows:
        mine = row / "plpgsql-coverage"
        for file in sorted(mine.iterdir()) if mine.is_dir() else []:
            if file.is_file():
                place(file, into / file.name, red)
    for single in singles:
        place(single, into / f"{single.parent.name}.json", red)
    if len(singles) == 1 and not any(
        (row / "plpgsql-coverage").is_dir() for row in rows
    ):
        place(singles[0], run / "plpgsql-coverage.json", red)


def merge(rows: list[Path], run: Path) -> list[str]:
    """Gather the rows' run directories into one.

    Returns:
        One line per defect; empty when the run directory is whole.

    """
    red: list[str] = []
    run.mkdir(parents=True, exist_ok=True)
    for row in rows:
        gather(row, run, red)
    typescript = merged(rows, "typescript/lcov.info")
    if typescript is not None:
        (run / "typescript").mkdir(exist_ok=True)
        (run / "typescript" / "lcov.info").write_text(write_lcov(typescript), "utf-8")
        (run / "typescript" / "coverage-summary.json").write_text(
            json.dumps(summary(typescript), sort_keys=True), "utf-8"
        )
    rust = merged(rows, "rust-lcov.info")
    if rust is not None:
        (run / "rust").mkdir(exist_ok=True)
        (run / "rust-lcov.info").write_text(write_lcov(rust), "utf-8")
    plpgsql(rows, run, red)
    return red


class Args(argparse.Namespace):
    """The command's arguments, typed as parsed."""

    rows: str
    run: str


def main(argv: list[str]) -> int:
    """Gather the rows.

    Returns:
        0, 1 after naming each defect, 2 for a malformed command line.

    """
    parser = argparse.ArgumentParser(
        prog="belt coverage-merge", description="Gather the rows"
    )
    parser.add_argument("rows", metavar="ROWS_DIR")
    parser.add_argument("run", metavar="RUN_DIR")
    args = parser.parse_args(argv, namespace=Args())
    rows_dir, run = Path(args.rows), Path(args.run)
    rows = (
        sorted(p for p in rows_dir.iterdir() if p.is_dir()) if rows_dir.is_dir() else []
    )
    red = merge(rows, run)
    for line in red:
        findings.finding("", line)
    if red:
        return 1
    print(f"coverage_merge: {len(rows)} row(s) gathered into {run}")
    return 0
