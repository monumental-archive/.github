#!/usr/bin/env python3
"""Measure a repository's PL/pgSQL statement and branch coverage.

Org canon (#1547). The `plpgsql` leg of `coverage-measure.sh`: the belt
delivers this reader, the repository supplies the database. The figures
are plpgsql_check's own (`plpgsql_coverage_statements` and
`plpgsql_coverage_branches`), read from a test database that preloads
the extension and runs with its profiler on. The extension belongs to
the repository's test stack, never to a published image.

The database lives only for the length of the suite, so the reading is
taken inside it: the repository's test teardown runs

    "${ORG_PYTHON}" "${ORG_BELT_DIR}/plpgsql-coverage.py" collect <conninfo>

before it stops the database, when ORG_PLPGSQL_COVERAGE names the file
to write. Postgres is reached through the repository's own `psql`,
already needed to run its tests; the belt pins no client. Three
subcommands:

  collect <conninfo>     the teardown's call. Writes the report, a JSON
                         record of the profiler's state and of every
                         PL/pgSQL function's reading, to the file
                         ORG_PLPGSQL_COVERAGE names. It judges nothing:
                         a teardown that failed here would fail the
                         suite and lose the reason, so every refusal is
                         `totals`'s, by name.
  totals <label> <file>  reads a report. Prints `plpgsql <percent>` and
                         `plpgsql-branches <percent>` on stdout, the
                         per-function figures on stderr, or refuses by
                         name. A report with no PL/pgSQL function prints
                         no totals and exits 0.
  measure <label>        the leg: runs the repository's `test` task, and
                         every task it depends on, with
                         ORG_PLPGSQL_COVERAGE set, then `totals`.

The functions read are every PL/pgSQL function outside the system
schemas that no extension owns: postgis alone owns 22 PL/pgSQL
functions in a non-system schema (measured on the proving repository,
2026-11-19), and they are not the repository's code.

Three readings decide the refusals (measured at plpgsql_check 2.10.16):

- The functions return a fraction from 0 to 1. Reported here as a
  percentage, truncated to two places so a figure is never rounded up
  past a floor it did not reach.
- With the profiler off, every function reads 0 for statements, called
  or not. Off is indistinguishable from untested by the figures, so the
  setting itself is read and an off profiler is refused by name, never
  measured as 0%. The same holds for a profiler not in
  shared_preload_libraries: its profile is per session, and the
  teardown's session sees none of the suite's.
- A function the extension cannot read is listed with the error and
  reds the leg. It is never dropped from the total.

`plpgsql` is exact: executed statements over all statements, where
the statements are the denominator of plpgsql_check's own statement
figure, the visible ones, exactly the rows of
`plpgsql_profiler_function_statements_tb`. `plpgsql-branches` is
STATEMENT-WEIGHTED: the extension computes its branch count internally
and returns only the fraction, so each function's branch fraction is
weighted by its statement count. A branch-free function reads 1 by the
extension's rule, ran or not, and is left out of the branch total; it
is recognised as fraction 1 with no IF, CASE or loop statement. A
function whose only branches are exception handlers, every one taken,
is read as branch-free too: left out, it never raises the total.
Rejected (#1547 ruling): an unweighted mean, the lowest function's
figure, and parsing the function source for a branch count, which
would be a second PL/pgSQL parser. The report lists every function's
figures beside the totals, so the totals can be checked by hand.
"""

import json
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path

NAME = "plpgsql-coverage"
ENV = "ORG_PLPGSQL_COVERAGE"

# Settings, read in the teardown's own session.
META_SQL = (
    "SELECT coalesce(current_setting('plpgsql_check.profiler', true), ''),"
    " current_setting('shared_preload_libraries')"
)

CREATE_SQL = "CREATE EXTENSION IF NOT EXISTS plpgsql_check"

# Every PL/pgSQL function the repository wrote: outside the system
# schemas, and owned by no extension (pg_depend deptype 'e').
LIST_SQL = """\
SELECT p.oid, p.oid::regprocedure
  FROM pg_proc p
  JOIN pg_language l ON l.oid = p.prolang
  JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE l.lanname = 'plpgsql'
   AND n.nspname NOT IN ('pg_catalog', 'information_schema')
   AND n.nspname NOT LIKE 'pg\\_%'
   AND NOT EXISTS (
     SELECT FROM pg_depend d
      WHERE d.classid = 'pg_proc'::regclass
        AND d.objid = p.oid
        AND d.deptype = 'e')
 ORDER BY 2"""

# One function's figures: the extension's two fractions, then over its
# visible statements (the statement fraction's own denominator) the
# count, the executed count, and how many open a branch. Statement
# types are plpgsql's own names; every loop's starts LOOP, WHILE or FOR.
READ_SQL = """\
SELECT plpgsql_coverage_statements({oid}::regprocedure),
       plpgsql_coverage_branches({oid}::regprocedure),
       s.statements, s.executed, s.branching
  FROM (SELECT count(*) AS statements,
               count(*) FILTER (WHERE exec_stmts > 0) AS executed,
               count(*) FILTER (WHERE stmtname IN ('IF', 'CASE', 'LOOP', 'WHILE')
                                   OR stmtname LIKE 'FOR%') AS branching
          FROM plpgsql_profiler_function_statements_tb({oid}::regprocedure)) s"""

PLACES = Decimal("0.01")
HUNDRED = Decimal(100)


class ReadError(Exception):
    """A read failed (psql, or mise for a task); the message names it."""


def psql(conninfo: str, sql: str) -> list[list[str]]:
    """Run one statement and return its rows, tab-split.

    Returns:
        One list of fields per output row.

    Raises:
        ReadError: psql exited non-zero.

    """
    # The repository's own psql, by name on PATH: the belt pins no client.
    # ruff: ignore[subprocess-without-shell-equals-true, start-process-with-partial-path]
    done = subprocess.run(
        [
            "psql",
            "-X",
            "-q",
            "-A",
            "-t",
            "-F",
            "\t",
            "-v",
            "ON_ERROR_STOP=1",
            "-d",
            conninfo,
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise ReadError(done.stderr.strip() or f"psql exited {done.returncode}")
    return [line.split("\t") for line in done.stdout.splitlines() if line]


def read_function(conninfo: str, oid: str, signature: str) -> dict:
    """Read one function's figures, or the reason they cannot be read.

    Returns:
        The function's report entry.

    """
    entry: dict = {"function": signature}
    try:
        rows = psql(conninfo, READ_SQL.format(oid=oid))
        statements, branches, count, executed, branching = rows[0]
        entry |= {
            "statements": float(statements),
            "branches": float(branches),
            "statement_count": int(count),
            "executed": int(executed),
            "branch_free": Decimal(branches) == 1 and int(branching) == 0,
        }
    except ReadError as exc:
        entry["error"] = str(exc)
    except IndexError, ValueError, InvalidOperation:
        entry["error"] = f"unreadable figures: {rows!r}"
    return entry


def collect(conninfo: str) -> int:
    """Write the report the teardown owes.

    Returns:
        0 once the report is written; 1 when there is nowhere to write it.

    """
    target = os.environ.get(ENV, "")
    if not target:
        print(
            f"{NAME}: {ENV} is not set — the leg sets it; call collect only then",
            file=sys.stderr,
        )
        return 1
    report: dict = {}
    try:
        profiler, preload = psql(conninfo, META_SQL)[0]
        report |= {"profiler": profiler, "preload": preload}
        psql(conninfo, CREATE_SQL)
        listed = psql(conninfo, LIST_SQL)
        report["functions"] = [
            read_function(conninfo, oid, signature) for oid, signature in listed
        ]
    except FileNotFoundError:
        report["error"] = "psql is not on PATH (the repository's own client)"
    except ReadError as exc:
        report["error"] = str(exc)
    except ValueError:
        report["error"] = "unreadable settings from the database"
    Path(target).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


def percent(value: Decimal) -> str:
    """Format a fraction as a percentage, truncated to two places.

    Returns:
        The percentage without a sign.

    """
    return str((value * HUNDRED).quantize(PLACES, rounding=ROUND_DOWN))


def statement_total(functions: list[dict]) -> tuple[int, int]:
    """Count covered statements over all statements: the exact figure.

    Returns:
        (executed, statements) summed over every function.

    """
    executed = sum(f["executed"] for f in functions)
    return executed, sum(f["statement_count"] for f in functions)


def branch_total(functions: list[dict]) -> Decimal | None:
    """Combine the branch fractions, statement-weighted.

    plpgsql_check returns each function's branch fraction without the
    branch count behind it, so each fraction is weighted by its
    function's statement count. A branch-free function (fraction 1 and
    no IF, CASE or loop) is left out: the extension reads 1 for it
    whether or not it ran, and counting it would raise the total
    without one branch being taken.

    Returns:
        The weighted fraction, or None when no function has a branch.

    """
    branching = [f for f in functions if not f["branch_free"]]
    weight = sum(f["statement_count"] for f in branching)
    if weight == 0:
        return None
    covered = sum(Decimal(str(f["branches"])) * f["statement_count"] for f in branching)
    return covered / weight


def refusals(label: str, report: dict) -> list[str]:
    """Name every reason the report cannot become a measurement.

    Returns:
        The refusal lines, empty when the report is a measurement.

    """
    head = f"{label}: plpgsql:"
    if "error" in report:
        return [f"{head} the teardown's collection failed: {report['error']}"]
    lines = []
    if report.get("profiler") != "on":
        lines.append(
            f"{head} plpgsql_check.profiler is '{report.get('profiler', '')}' —"
            " with the profiler off every function reads 0%, which is not a"
            " measurement; start the test database with"
            " -c plpgsql_check.profiler=on"
        )
    preload = [p.strip() for p in str(report.get("preload", "")).split(",")]
    if "plpgsql_check" not in preload:
        lines.append(
            f"{head} plpgsql_check is not in shared_preload_libraries"
            f" ('{report.get('preload', '')}') — the profile is per session and"
            " the teardown sees none of the suite's; preload plpgsql,plpgsql_check"
        )
    unread = [f for f in report.get("functions", []) if "error" in f]
    lines.extend(
        f"{head} plpgsql_check cannot read {f['function']}: {f['error']}"
        for f in unread
    )
    if unread:
        lines.append(
            f"{head} {len(unread)} function(s) unreadable — a function the"
            " extension cannot read is never dropped from the total"
        )
    return lines


def totals(label: str, report_path: str) -> int:
    """Print the two legs from a report, or refuse by name.

    Returns:
        0 with the totals printed (or none, with no PL/pgSQL function);
        1 when the report is missing or refused.

    """
    path = Path(report_path)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(
            f"{label}: plpgsql: the test task wrote no report — its teardown"
            f" must call plpgsql-coverage.py collect while {ENV} is set",
            file=sys.stderr,
        )
        return 1
    except ValueError as exc:
        print(f"{label}: plpgsql: the report is not JSON: {exc}", file=sys.stderr)
        return 1
    lines = refusals(label, report)
    if lines:
        print("\n".join(lines), file=sys.stderr)
        return 1
    functions = report.get("functions", [])
    for f in functions:
        branches = (
            "none (left out of the branch total)"
            if f["branch_free"]
            else f"{percent(Decimal(str(f['branches'])))}%"
        )
        print(
            f"{label}: plpgsql: {f['function']}:"
            f" statements {f['executed']} of {f['statement_count']}"
            f" ({percent(Decimal(str(f['statements'])))}%),"
            f" branches {branches}",
            file=sys.stderr,
        )
    if not functions:
        print(
            f"{label}: plpgsql: the database holds no PL/pgSQL function",
            file=sys.stderr,
        )
        return 0
    executed, statements = statement_total(functions)
    branched = branch_total(functions)
    stmt_pct = (
        percent(Decimal(executed) / Decimal(statements)) if statements else "0.00"
    )
    print(
        f"{label}: plpgsql: total statements {executed} of {statements}"
        f" ({stmt_pct}%), branches "
        + (
            "none: no function has a branch"
            if branched is None
            else f"{percent(branched)}% statement-weighted"
        ),
        file=sys.stderr,
    )
    print(f"plpgsql {stmt_pct}")
    if branched is not None:
        print(f"plpgsql-branches {percent(branched)}")
    return 0


def task_info(name: str) -> dict:
    """Read one task as mise resolved it, read-only (never a nested run).

    Returns:
        The task's `mise tasks info --json` object.

    Raises:
        ReadError: mise could not answer for the task (named).

    """
    # ruff: ignore[subprocess-without-shell-equals-true, start-process-with-partial-path]
    done = subprocess.run(
        ["mise", "tasks", "info", name, "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        msg = f"cannot read task '{name}' with mise tasks info"
        raise ReadError(msg)
    return json.loads(done.stdout)


def scripts(name: str, seen: set[str]) -> list[tuple[str, str]]:
    """List a task's run scripts after those of every task it depends on.

    Returns:
        (directory, script) pairs in the order they run.

    Raises:
        ReadError: a task that cannot be read, or a run entry that is
            not a script.

    """
    if name in seen:
        return []
    seen.add(name)
    info = task_info(name)
    out: list[tuple[str, str]] = []
    for dep in info.get("depends") or []:
        dep_name = dep if isinstance(dep, str) else dep.get("task", "")
        out += scripts(dep_name, seen)
    for run in info.get("run") or []:
        if not isinstance(run, str):
            msg = f"task '{name}' has a run entry that is not a script"
            raise ReadError(msg)
        out.append((info.get("dir") or str(Path.cwd()), run))
    return out


def measure(label: str) -> int:
    """Run the test task with the report path set, then read it.

    Returns:
        `totals`'s status, or 1 when the suite cannot run or fails.

    """
    try:
        steps = scripts("test", set())
    except ReadError as exc:
        print(f"{label}: plpgsql: {exc}", file=sys.stderr)
        return 1
    if not steps:
        print(
            f"{label}: plpgsql: the test task runs nothing — the leg measures"
            " the repository's own suite",
            file=sys.stderr,
        )
        return 1
    with tempfile.TemporaryDirectory() as work:
        report = str(Path(work) / "plpgsql-coverage.json")
        env = os.environ | {ENV: report}
        for directory, script in steps:
            # ruff: ignore[subprocess-without-shell-equals-true, start-process-with-partial-path]
            done = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", script],
                cwd=directory,
                env=env,
                stdout=sys.stderr,
                check=False,
            )
            if done.returncode != 0:
                print(
                    f"{label}: plpgsql: the test task failed under measurement —"
                    " a suite that does not pass cannot measure a floor",
                    file=sys.stderr,
                )
                return 1
        return totals(label, report)


def main(argv: list[str]) -> int:
    """Dispatch a subcommand.

    Returns:
        The subcommand's status, or 2 on a usage error.

    """
    match argv:
        case ["collect", conninfo]:
            return collect(conninfo)
        case ["totals", label, report]:
            return totals(label, report)
        case ["measure", label]:
            return measure(label)
        case _:
            print(
                f"usage: {NAME} collect <conninfo> | totals <label> <report>"
                " | measure <label>",
                file=sys.stderr,
            )
            return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
