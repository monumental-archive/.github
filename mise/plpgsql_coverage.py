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

    "${ORG_PYTHON}" "${ORG_BELT_DIR}/plpgsql_coverage.py" collect <conninfo>

before it stops the database, when ORG_PLPGSQL_COVERAGE names the file
to write. Postgres is reached through the repository's own `psql`,
already needed to run its tests; the belt pins no client. Two
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

The run is not this helper's: `coverage-measure.sh` walks the `test`
task and its depends once, for every leg that reads that run, and sets
ORG_PLPGSQL_COVERAGE in it when the plpgsql leg is selected; the leg
then calls `totals` on the report the teardown wrote.

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
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import cast

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

OID = re.compile(r"[0-9]+")
SETTINGS = 2  # the profiler and the preload list
LISTING = 2  # an oid and a signature
PLACES = Decimal("0.01")
HUNDRED = Decimal(100)


@dataclass(frozen=True)
class Reading:
    """One function the extension read.

    The fractions are Decimals parsed from the text psql printed, never
    floats: a figure must never be rounded up past a floor on its way
    through.
    """

    function: str
    statements: Decimal
    branches: Decimal
    statement_count: int
    executed: int
    branch_free: bool


@dataclass(frozen=True)
class Unreadable:
    """One function the extension could not read, with the reason."""

    function: str
    error: str


@dataclass(frozen=True)
class Report:
    """A collection that reached the database."""

    profiler: str
    preload: str
    functions: tuple[Reading | Unreadable, ...]


@dataclass(frozen=True)
class Failed:
    """A collection that could not read the database at all."""

    error: str


class ReadError(Exception):
    """psql exited non-zero; the message is its stderr."""


class NotAReportError(Exception):
    """The report file does not decode to a coverage report."""


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


def read_function(conninfo: str, oid: str, signature: str) -> Reading | Unreadable:
    """Read one function's figures, or the reason they cannot be read.

    Returns:
        The function's reading, or why it is unreadable.

    """
    if not OID.fullmatch(oid):
        return Unreadable(signature, f"its oid '{oid}' is not a number")
    try:
        rows = psql(conninfo, READ_SQL.format(oid=oid))
    except ReadError as exc:
        return Unreadable(signature, str(exc))
    try:
        statements, branches, count, executed, branching = rows[0]
        return Reading(
            function=signature,
            statements=Decimal(statements),
            branches=Decimal(branches),
            statement_count=int(count),
            executed=int(executed),
            branch_free=Decimal(branches) == 1 and int(branching) == 0,
        )
    except IndexError, ValueError, InvalidOperation:
        return Unreadable(signature, f"unreadable figures: {rows!r}")


def gather(conninfo: str) -> Report | Failed:
    """Read the settings and every function from the database.

    Returns:
        The report, or the failure that stopped the collection.

    """
    try:
        settings = psql(conninfo, META_SQL)
        psql(conninfo, CREATE_SQL)
        listed = psql(conninfo, LIST_SQL)
    except FileNotFoundError:
        return Failed("psql is not on PATH (the repository's own client)")
    except ReadError as exc:
        return Failed(str(exc))
    if len(settings) != 1 or len(settings[0]) != SETTINGS:
        return Failed(f"unreadable settings from the database: {settings!r}")
    functions: list[Reading | Unreadable] = []
    for row in listed:
        if len(row) != LISTING:
            return Failed(f"unreadable function listing: {row!r}")
        functions.append(read_function(conninfo, row[0], row[1]))
    return Report(settings[0][0], settings[0][1], tuple(functions))


def encode(report: Report | Failed) -> str:
    """Write a report in the helper's own wire format.

    Returns:
        The JSON text; fractions as the decimal text psql printed.

    """
    if isinstance(report, Failed):
        document: dict[str, object] = {"error": report.error}
    else:
        entries: list[dict[str, object]] = []
        for f in report.functions:
            if isinstance(f, Unreadable):
                entries.append({"function": f.function, "error": f.error})
            else:
                entries.append({
                    "function": f.function,
                    "statements": str(f.statements),
                    "branches": str(f.branches),
                    "statement_count": f.statement_count,
                    "executed": f.executed,
                    "branch_free": f.branch_free,
                })
        document = {
            "profiler": report.profiler,
            "preload": report.preload,
            "functions": entries,
        }
    return json.dumps(document, indent=2) + "\n"


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
    Path(target).write_text(encode(gather(conninfo)), encoding="utf-8")
    return 0


def text_field(document: dict[str, object], key: str, where: str) -> str:
    """Read one string field.

    Returns:
        The field.

    Raises:
        NotAReportError: the field is absent or not a string.

    """
    value = document.get(key)
    if not isinstance(value, str):
        msg = f"{where}: '{key}' is not a string"
        raise NotAReportError(msg)
    return value


def count_field(document: dict[str, object], key: str, where: str) -> int:
    """Read one non-negative integer field (never a boolean).

    Returns:
        The field.

    Raises:
        NotAReportError: the field is absent, not an integer, or negative.

    """
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        msg = f"{where}: '{key}' is not a count"
        raise NotAReportError(msg)
    return value


def fraction_field(document: dict[str, object], key: str, where: str) -> Decimal:
    """Read one fraction, written as decimal text, between 0 and 1.

    Returns:
        The fraction.

    Raises:
        NotAReportError: the field is not decimal text from 0 to 1.

    """
    text = text_field(document, key, where)
    try:
        value = Decimal(text)
    except InvalidOperation:
        value = Decimal(-1)
    if not value.is_finite() or not 0 <= value <= 1:
        msg = f"{where}: '{key}' is not a fraction from 0 to 1"
        raise NotAReportError(msg)
    return value


def as_object(value: object, where: str) -> dict[str, object]:
    """Narrow a decoded JSON value to an object with string keys.

    Returns:
        The object.

    Raises:
        NotAReportError: the value is not a JSON object.

    """
    if not isinstance(value, dict):
        msg = f"{where} is not an object"
        raise NotAReportError(msg)
    # JSON object keys are strings; the values are read field by field.
    return cast("dict[str, object]", value)


def as_list(value: object, where: str) -> list[object]:
    """Narrow a decoded JSON value to a list.

    Returns:
        The list; its items are read one by one.

    Raises:
        NotAReportError: the value is not a JSON array.

    """
    if not isinstance(value, list):
        msg = f"{where} is not a list"
        raise NotAReportError(msg)
    return cast("list[object]", value)


def decode_function(value: object, where: str) -> Reading | Unreadable:
    """Decode one function entry, in one of its two shapes.

    Returns:
        The reading, or the unreadable function.

    Raises:
        NotAReportError: the entry is neither shape.

    """
    entry = as_object(value, where)
    function = text_field(entry, "function", where)
    if "error" in entry:
        return Unreadable(function, text_field(entry, "error", where))
    branch_free = entry.get("branch_free")
    if not isinstance(branch_free, bool):
        msg = f"{where}: 'branch_free' is not a boolean"
        raise NotAReportError(msg)
    return Reading(
        function=function,
        statements=fraction_field(entry, "statements", where),
        branches=fraction_field(entry, "branches", where),
        statement_count=count_field(entry, "statement_count", where),
        executed=count_field(entry, "executed", where),
        branch_free=branch_free,
    )


def decode(text: str) -> Report | Failed:
    """Decode a report file: the one place its JSON is read.

    Returns:
        The typed report.

    Raises:
        NotAReportError: what about the file is not a coverage report.

    """
    try:
        decoded: object = json.loads(text)
    except ValueError as exc:
        msg = f"not JSON ({exc})"
        raise NotAReportError(msg) from exc
    document = as_object(decoded, "the document")
    if "error" in document:
        return Failed(text_field(document, "error", "the document"))
    listed = as_list(document.get("functions"), "the document: 'functions'")
    functions = tuple(
        decode_function(item, f"function {index}") for index, item in enumerate(listed)
    )
    return Report(
        profiler=text_field(document, "profiler", "the document"),
        preload=text_field(document, "preload", "the document"),
        functions=functions,
    )


def percent(value: Decimal) -> str:
    """Format a fraction as a percentage, truncated to two places.

    Returns:
        The percentage without a sign.

    """
    return str((value * HUNDRED).quantize(PLACES, rounding=ROUND_DOWN))


def branch_total(readings: list[Reading]) -> Decimal | None:
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
    branching = [f for f in readings if not f.branch_free]
    weight = sum(f.statement_count for f in branching)
    if weight == 0:
        return None
    covered = sum((f.branches * f.statement_count for f in branching), Decimal(0))
    return covered / weight


def refusals(label: str, report: Report | Failed) -> list[str]:
    """Name every reason the report cannot become a measurement.

    Returns:
        The refusal lines, empty when the report is a measurement.

    """
    head = f"{label}: plpgsql:"
    if isinstance(report, Failed):
        return [f"{head} the teardown's collection failed: {report.error}"]
    lines = []
    if report.profiler != "on":
        lines.append(
            f"{head} plpgsql_check.profiler is '{report.profiler}' —"
            " with the profiler off every function reads 0%, which is not a"
            " measurement; start the test database with"
            " -c plpgsql_check.profiler=on"
        )
    if "plpgsql_check" not in [p.strip() for p in report.preload.split(",")]:
        lines.append(
            f"{head} plpgsql_check is not in shared_preload_libraries"
            f" ('{report.preload}') — the profile is per session and"
            " the teardown sees none of the suite's; preload plpgsql,plpgsql_check"
        )
    unread = [f for f in report.functions if isinstance(f, Unreadable)]
    lines.extend(
        f"{head} plpgsql_check cannot read {f.function}: {f.error}" for f in unread
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
    try:
        report = decode(Path(report_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(
            f"{label}: plpgsql: the test task wrote no report — its teardown"
            f" must call plpgsql_coverage.py collect while {ENV} is set",
            file=sys.stderr,
        )
        return 1
    except NotAReportError as exc:
        print(
            f"{label}: plpgsql: the report is not a coverage report: {exc}",
            file=sys.stderr,
        )
        return 1
    lines = refusals(label, report)
    if lines or isinstance(report, Failed):
        print("\n".join(lines), file=sys.stderr)
        return 1
    readings = [f for f in report.functions if isinstance(f, Reading)]
    for f in readings:
        branches = (
            "none (left out of the branch total)"
            if f.branch_free
            else f"{percent(f.branches)}%"
        )
        print(
            f"{label}: plpgsql: {f.function}:"
            f" statements {f.executed} of {f.statement_count}"
            f" ({percent(f.statements)}%),"
            f" branches {branches}",
            file=sys.stderr,
        )
    if not readings:
        print(
            f"{label}: plpgsql: the database holds no PL/pgSQL function",
            file=sys.stderr,
        )
        return 0
    executed = sum(f.executed for f in readings)
    statements = sum(f.statement_count for f in readings)
    branched = branch_total(readings)
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
        case _:
            print(
                f"usage: {NAME} collect <conninfo> | totals <label> <report>",
                file=sys.stderr,
            )
            return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
