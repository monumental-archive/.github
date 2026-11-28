"""Measure a repository's PL/pgSQL statement and branch coverage.

Org canon (#1547). The `plpgsql` leg of `coverage-measure.sh`: the belt
delivers this reader, the repository supplies the database. The figures
are plpgsql_check's own (`plpgsql_coverage_statements` and
`plpgsql_coverage_branches`), read from a test database that preloads
the extension and runs with its profiler on. The extension is loaded
only by a test stack, never by a published image's own start.

The database lives only for the length of the suite, so the reading is
taken inside it: the repository's test teardown runs

    "${ORG_PYTHON}" "${ORG_BELT_DIR}/plpgsql_coverage.py" collect <conninfo>

before it stops the database, when ORG_PLPGSQL_COVERAGE names the
directory to write into. A suite may use any number of databases, each
torn down with its own call (#2163). Postgres is reached through the
client the repository names: `psql` on PATH unless the call names
another after `--` (#2114),

    collect <conninfo> -- <client> [<argument>...]

whose words are run as they are, an argument vector and never a shell
line, with the helper's own `psql` arguments after them. The belt pins
no client. Two subcommands:

  collect <conninfo> [-- <client> [<argument>...]]
                         the teardown's call. Writes this database's
                         report, a JSON record of the profiler's state
                         and of every PL/pgSQL function's reading, as a
                         file of its own in the directory
                         ORG_PLPGSQL_COVERAGE names: created exclusively
                         under a fresh name, so concurrent teardowns
                         never collide and none replaces another's. It
                         judges nothing: a teardown that failed here
                         would fail the suite and lose the reason, so
                         every refusal is `totals`'s, by name.
  totals <label> <report>
                         reads every report in the directory (or the
                         one report file) and merges them. Prints
                         `plpgsql <percent>` and `plpgsql-branches
                         <percent>` (or `plpgsql-branches not-held`)
                         on stdout, the per-function figures on stderr,
                         or refuses by name. Reports with no PL/pgSQL
                         function print no totals and exit 0.

The same call asks plpgsql_check's static check about every listed
function (#1605): `plpgsql_check_function_tb` with every optional check
on and fatal errors reported rather than stopping at the first, a
trigger function once per table its triggers sit on. Each reading
carries its findings (line, level, message); `totals` reds every
finding, whatever its level, by function and line, before it judges the
floor, and the decoder refuses a reading with no findings answer: a
report from a helper that did not ask is not a clean result. The one
suppression is the extension's own per-function option,
`-- @plpgsql_check_options: <check> = false`: the helper reads each
function's source as the database holds it (`pg_proc.prosrc`) and
records every options line that turns a check off; `totals` names every
one on every run and reds one with no comment line directly above it
saying why.

The run is not this helper's: the repository's `test` command runs
through `mise/measured`, which sets ORG_PLPGSQL_COVERAGE; the leg then
calls `totals` on the reports the teardowns wrote.

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
`plpgsql_profiler_function_statements_tb`.

Several databases merge exactly (#2163). Each report records, per
function, the ids of the statements that ran (`exec_stmts > 0`) and a
digest of the function's source; `totals` merges the reports by the
function's signature (its oid differs per database) and takes the union
of the ids: a statement is covered if any database ran it. One
signature with two bodies cannot be merged by id and is refused by
name. After the merge, a function none of whose statements ran in any
database is red by name, whatever the total (the owner's ruling of
2026-11-27): the merged measurement names the untested function itself.

`plpgsql-branches` merges nothing: plpgsql_check exposes no branch
membership (no release through v2.11.1 says which branch a statement
belongs to), keys its shared profile by database, and offers no profile
merge, so no exact branch figure over two databases exists (#2163's
probes). With one report the leg is held as below. With more than one
it is printed `plpgsql-branches not-held`, which coverage:check and the
derivation read as a leg not held this run with its floor left as it
stands, and the log names why (#2169, which holds it again once
plpgsql_check exposes branch membership) and prints the highest
fraction any one database read per function, labelled as the lower
bound it is. Rejected (#2163): the highest fraction as the enforced
figure, which undercounts whenever two databases took different
branches; deriving branches from `parent_stmtid` and `block_num`,
which cannot tell an ELSE from an ELSIF and see no empty branch; and
holding the leg red until upstream changes.

With one report, `plpgsql-branches` is STATEMENT-WEIGHTED: the
extension computes its branch count internally and returns only the
fraction, so each function's branch fraction is
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

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING

from belt import findings
from belt.decoded import UndecodableError, as_array, as_object, load_json
from belt.typed_std import group

if TYPE_CHECKING:
    from collections.abc import Iterable

NAME = "plpgsql-coverage"
# The leg a report measures: the subject of its findings.
LEG = "plpgsql"
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
SELECT p.oid, p.oid::regprocedure,
       coalesce((SELECT string_agg(DISTINCT t.tgrelid::text, ' ')
                   FROM pg_trigger t
                  WHERE t.tgfoid = p.oid AND NOT t.tgisinternal), '')
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

# The ids of one function's statements that ran (#2163): what merges
# across databases, by the function's signature.
RAN_SQL = """\
SELECT stmtid
  FROM plpgsql_profiler_function_statements_tb({oid}::regprocedure)
 WHERE exec_stmts > 0
 ORDER BY stmtid"""

# One function's static check (#1605): plpgsql_check_function_tb with
# every optional check on, and fatal errors reported rather than ending
# the check at the first. A trigger function is checked against each
# table a trigger attaches it to, since its NEW and OLD are that
# table's rows; relid 0 checks any other function. The message is base64
# so a newline in it cannot split the row psql prints.
LINT_SQL = """\
SELECT coalesce(lineno::text, ''), level,
       replace(encode(convert_to(message, 'UTF8'), 'base64'), E'\\n', '')
  FROM plpgsql_check_function_tb({oid}::regprocedure, {relid}::regclass,
                                 fatal_errors => false,
                                 other_warnings => true,
                                 performance_warnings => true,
                                 extra_warnings => true,
                                 security_warnings => true,
                                 compatibility_warnings => true)"""

# One function's source, base64 so its newlines survive psql's rows.
SOURCE_SQL = (
    "SELECT replace(encode(convert_to(prosrc, 'UTF8'), 'base64'), E'\\n', '')"
    " FROM pg_proc WHERE oid = {oid}"
)

# The extension's own per-function option (#1605): the one suppression
# allowed, read from the function's source as the database holds it.
OPTIONS = re.compile(r"--\s*@plpgsql_check_options:\s*(?P<options>.*)")
SETTING = re.compile(r"(?P<name>\w+)\s*=\s*(?P<value>\w+)")

OID = re.compile(r"[0-9]+")
SETTINGS = 2  # the profiler and the preload list
LISTING = 3  # an oid, a signature, and the tables its triggers sit on
FINDING = 3  # a line, a level, a message
PLACES = Decimal("0.01")
HUNDRED = Decimal(100)
# A report file's name, created exclusively in the reports directory.
REPORT_PREFIX = "database-"
REPORT_SUFFIX = ".json"
# What a leg prints for a figure no measurement can give exactly this
# run (#2163): coverage_floor.py holds nothing and leaves the floor.
NOT_HELD = "not-held"
TRACKING = "#2169"


@dataclass(frozen=True)
class Finding:
    """One static-check finding: where, how severe, and what."""

    line: int | None
    level: str
    message: str


@dataclass(frozen=True)
class Suppression:
    """One `@plpgsql_check_options` line that turns a check off.

    `reason` is the comment line directly above it, or None when there
    is none: a suppression without its reason is red.
    """

    line: int
    options: str
    reason: str | None


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
    # The ids of the statements that ran, and the sha256 of the source
    # they number (#2163): what merges across databases.
    ran: tuple[int, ...]
    source: str
    # What plpgsql_check's static check reported (#1605); empty is clean.
    findings: tuple[Finding, ...] = ()
    # The function's own options that turn a check off (#1605).
    suppressions: tuple[Suppression, ...] = ()


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


# The client when the call names none: the repository's psql on PATH.
PSQL = ("psql",)


class NotAReportError(Exception):
    """The report file does not decode to a coverage report."""


def psql(client: tuple[str, ...], conninfo: str, sql: str) -> list[list[str]]:
    """Run one statement through the client and return its rows, tab-split.

    `client` is the command line the call named, or PSQL; psql's own
    arguments are appended to it as words, never joined into a string.

    Returns:
        One list of fields per output row.

    Raises:
        ReadError: psql exited non-zero.

    """
    # The repository's own client: the belt pins none.
    done = subprocess.run(
        [
            *client,
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


def lint_function(
    client: tuple[str, ...], conninfo: str, oid: str, relids: list[str]
) -> tuple[Finding, ...]:
    """Ask plpgsql_check's static check about one function.

    Returns:
        Every distinct finding, across each table the function's
        triggers sit on (or none).

    Raises:
        ReadError: psql failed, or printed a row of the wrong shape.

    """
    found: list[Finding] = []
    for relid in relids or ["0"]:
        if not OID.fullmatch(relid):
            msg = f"its trigger table oid '{relid}' is not a number"
            raise ReadError(msg)
        for row in psql(client, conninfo, LINT_SQL.format(oid=oid, relid=relid)):
            if len(row) != FINDING:
                msg = f"unreadable finding: {row!r}"
                raise ReadError(msg)
            line, level, encoded = row
            try:
                message = base64.b64decode(encoded, validate=True).decode("utf-8")
            except ValueError as exc:
                msg = f"unreadable finding: {row!r}"
                raise ReadError(msg) from exc
            finding = Finding(int(line) if line.isdecimal() else None, level, message)
            if finding not in found:
                found.append(finding)
    return tuple(found)


def suppresses(options: str) -> bool:
    """Say whether an options line turns any check off.

    Returns:
        True when it sets a check false, or `without_warnings` true; a
        line that only turns checks on suppresses nothing.

    """
    for setting in SETTING.finditer(options):
        name, value = group(setting, "name") or "", group(setting, "value") or ""
        on = value.lower() in {"true", "on", "yes", "1"}
        if on == (name.lower() == "without_warnings"):
            return True
    return False


def suppressions_in(source: str) -> tuple[Suppression, ...]:
    """Find every options line in a function's source that suppresses.

    Returns:
        Each with its line (as plpgsql_check numbers the source) and the
        reason the comment line directly above it gives, if any.

    """
    lines = source.splitlines()
    found: list[Suppression] = []
    for index, raw in enumerate(lines):
        found_options = OPTIONS.search(raw)
        options = group(found_options, "options") if found_options else None
        if options is None or not suppresses(options):
            continue
        above = lines[index - 1].strip() if index else ""
        said = above.removeprefix("--").strip() if above.startswith("--") else ""
        reason = said if said and OPTIONS.search(above) is None else None
        found.append(Suppression(index + 1, options.strip(), reason))
    return tuple(found)


def read_source(client: tuple[str, ...], conninfo: str, oid: str) -> str:
    """Read one function's source as the database holds it.

    Returns:
        The source text.

    Raises:
        ReadError: psql failed, or printed something not one source.

    """
    rows = psql(client, conninfo, SOURCE_SQL.format(oid=oid))
    try:
        (encoded,) = rows[0]
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (IndexError, ValueError) as exc:
        msg = f"unreadable source: {rows!r}"
        raise ReadError(msg) from exc


def read_function(
    client: tuple[str, ...],
    conninfo: str,
    oid: str,
    signature: str,
    relids: list[str],
) -> Reading | Unreadable:
    """Read one function's figures, or the reason they cannot be read.

    Returns:
        The function's reading, or why it is unreadable.

    """
    if not OID.fullmatch(oid):
        return Unreadable(signature, f"its oid '{oid}' is not a number")
    try:
        rows = psql(client, conninfo, READ_SQL.format(oid=oid))
        ran_rows = psql(client, conninfo, RAN_SQL.format(oid=oid))
        findings = lint_function(client, conninfo, oid, relids)
        source = read_source(client, conninfo, oid)
    except ReadError as exc:
        return Unreadable(signature, str(exc))
    try:
        statements, branches, count, executed, branching = rows[0]
        figures = (
            Decimal(statements),
            Decimal(branches),
            int(count),
            int(executed),
            int(branching),
        )
    except IndexError, ValueError, InvalidOperation:
        return Unreadable(signature, f"unreadable figures: {rows!r}")
    if any(len(row) != 1 or not OID.fullmatch(row[0]) for row in ran_rows):
        return Unreadable(signature, f"unreadable statement ids: {ran_rows!r}")
    return Reading(
        function=signature,
        statements=figures[0],
        branches=figures[1],
        statement_count=figures[2],
        executed=figures[3],
        branch_free=figures[1] == 1 and figures[4] == 0,
        ran=tuple(int(row[0]) for row in ran_rows),
        source=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        findings=findings,
        suppressions=suppressions_in(source),
    )


def gather(client: tuple[str, ...], conninfo: str) -> Report | Failed:
    """Read the settings and every function from the database.

    Returns:
        The report, or the failure that stopped the collection.

    """
    try:
        settings = psql(client, conninfo, META_SQL)
        psql(client, conninfo, CREATE_SQL)
        listed = psql(client, conninfo, LIST_SQL)
    except FileNotFoundError:
        # One line: a finding escapes a newline, so the remedy follows a `;`.
        return Failed(
            f"{client[0]} is not on PATH (the repository's own client); "
            "pass the client after --, for example:"
            " collect <conninfo> -- docker exec -i <container> psql"
        )
    except ReadError as exc:
        return Failed(str(exc))
    if len(settings) != 1 or len(settings[0]) != SETTINGS:
        return Failed(f"unreadable settings from the database: {settings!r}")
    functions: list[Reading | Unreadable] = []
    for row in listed:
        if len(row) != LISTING:
            return Failed(f"unreadable function listing: {row!r}")
        functions.append(
            read_function(client, conninfo, row[0], row[1], row[2].split())
        )
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
                    "ran": list(f.ran),
                    "source": f.source,
                    "findings": [
                        {"line": g.line, "level": g.level, "message": g.message}
                        for g in f.findings
                    ],
                    "suppressions": [
                        {"line": u.line, "options": u.options, "reason": u.reason}
                        for u in f.suppressions
                    ],
                })
        document = {
            "profiler": report.profiler,
            "preload": report.preload,
            "functions": entries,
        }
    return json.dumps(document, indent=2) + "\n"


def collect(conninfo: str, client: tuple[str, ...] = PSQL) -> int:
    """Write the report the teardown owes, beside every other database's.

    The file is created exclusively under a fresh name in the directory
    ORG_PLPGSQL_COVERAGE names, so two teardowns running at once write
    two reports and neither replaces the other (#2163).

    Returns:
        0 once the report is written; 1 when there is nowhere to write it.

    """
    target = os.environ.get(ENV, "")
    if not target:
        findings.finding(
            "", f"{ENV} is not set — the leg sets it; call collect only then"
        )
        return 1
    text = encode(gather(client, conninfo))
    directory = Path(target)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        findings.finding(
            "", f"{ENV} names {target}, which is not a directory of reports"
        )
        return 1
    handle, _ = tempfile.mkstemp(
        prefix=REPORT_PREFIX, suffix=REPORT_SUFFIX, dir=directory
    )
    with os.fdopen(handle, "w", encoding="utf-8") as report:
        report.write(text)
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


def decode_suppression(value: object, where: str) -> Suppression:
    """Decode one recorded suppression.

    Returns:
        The suppression.

    Raises:
        NotAReportError: the entry is not one.

    """
    entry = as_object(value, where)
    line = entry.get("line")
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        msg = f"{where}: 'line' is not a line number"
        raise NotAReportError(msg)
    reason = entry.get("reason")
    if reason is not None and not isinstance(reason, str):
        msg = f"{where}: 'reason' is not a string"
        raise NotAReportError(msg)
    return Suppression(line, text_field(entry, "options", where), reason)


def decode_finding(value: object, where: str) -> Finding:
    """Decode one static-check finding.

    Returns:
        The finding.

    Raises:
        NotAReportError: the entry is not a finding.

    """
    entry = as_object(value, where)
    line = entry.get("line")
    if line is not None and (isinstance(line, bool) or not isinstance(line, int)):
        msg = f"{where}: 'line' is not a line number"
        raise NotAReportError(msg)
    return Finding(
        line, text_field(entry, "level", where), text_field(entry, "message", where)
    )


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
    # A reading without the static check's answer is not a clean result:
    # the helper that wrote it did not ask (#1605).
    findings = as_array(entry.get("findings"), f"{where}: 'findings'")
    suppressed = as_array(entry.get("suppressions"), f"{where}: 'suppressions'")
    reading = Reading(
        function=function,
        statements=fraction_field(entry, "statements", where),
        branches=fraction_field(entry, "branches", where),
        statement_count=count_field(entry, "statement_count", where),
        executed=count_field(entry, "executed", where),
        branch_free=branch_free,
        ran=ran_field(entry, where),
        source=text_field(entry, "source", where),
        findings=tuple(
            decode_finding(item, f"{where} finding {index}")
            for index, item in enumerate(findings)
        ),
        suppressions=tuple(
            decode_suppression(item, f"{where} suppression {index}")
            for index, item in enumerate(suppressed)
        ),
    )
    # The ids are the executed statements, one each, among the counted.
    if len(reading.ran) != reading.executed or any(
        i > reading.statement_count for i in reading.ran
    ):
        msg = (
            f"{where}: 'ran' does not name 'executed' statements"
            " from 1 to 'statement_count'"
        )
        raise NotAReportError(msg)
    return reading


def ran_field(document: dict[str, object], where: str) -> tuple[int, ...]:
    """Read the ids of the statements that ran: distinct, ascending, from 1.

    Returns:
        The ids.

    Raises:
        NotAReportError: the field is not such a list.

    """
    value = document.get("ran")
    ids = value if isinstance(value, list) else None
    if ids is None or any(
        isinstance(i, bool) or not isinstance(i, int) or i < 1 for i in ids
    ):
        msg = f"{where}: 'ran' is not a list of statement ids"
        raise NotAReportError(msg)
    found = tuple(i for i in ids if isinstance(i, int))
    if list(found) != sorted(set(found)):
        msg = f"{where}: 'ran' is not a list of statement ids"
        raise NotAReportError(msg)
    return found


def decode(text: str) -> Report | Failed:
    """Decode a report file: the one place its JSON is read.

    Returns:
        The typed report.

    Raises:
        NotAReportError: what about the file is not a coverage report.

    """
    try:
        decoded = load_json(text)
    except UndecodableError as exc:
        msg = f"not JSON ({exc})"
        raise NotAReportError(msg) from exc
    # A wrong shape the shared readers refuse is this report's refusal.
    try:
        document = as_object(decoded, "the document")
        if "error" in document:
            return Failed(text_field(document, "error", "the document"))
        listed = as_array(document.get("functions"), "the document: 'functions'")
        functions = tuple(
            decode_function(item, f"function {index}")
            for index, item in enumerate(listed)
        )
    except UndecodableError as exc:
        raise NotAReportError(str(exc)) from exc
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


def unmeasured(report: Report | Failed) -> list[str]:
    """Name every reason the report's figures are not a measurement.

    Returns:
        The refusal lines: a failed collection, a profiler off or not
        preloaded, and every function the extension could not read.

    """
    if isinstance(report, Failed):
        return [f"the teardown's collection failed: {report.error}"]
    lines = []
    if report.profiler != "on":
        lines.append(
            f"plpgsql_check.profiler is '{report.profiler}' —"
            " with the profiler off every function reads 0%, which is not a"
            " measurement; start the test database with"
            " -c plpgsql_check.profiler=on"
        )
    if "plpgsql_check" not in [p.strip() for p in report.preload.split(",")]:
        lines.append(
            f"plpgsql_check is not in shared_preload_libraries"
            f" ('{report.preload}') — the profile is per session and"
            " the teardown sees none of the suite's; preload plpgsql,plpgsql_check"
        )
    unread = [f for f in report.functions if isinstance(f, Unreadable)]
    lines.extend(f"plpgsql_check cannot read {f.function}: {f.error}" for f in unread)
    if unread:
        lines.append(
            f"{len(unread)} function(s) unreadable — a function the"
            " extension cannot read is never dropped from the total"
        )
    return lines


def unclean(report: Report) -> list[str]:
    """Name every static-check finding and unexplained suppression (#1605).

    Returns:
        The refusal lines, each a finding about the plpgsql leg.

    """
    # Judged before the floor: every optional check is on so that what it
    # reports is held, so every finding is red, whatever its level.
    found = [
        (f.function, g)
        for f in report.functions
        if isinstance(f, Reading)
        for g in f.findings
    ]
    lines = [
        f"{function}: line {'-' if g.line is None else g.line}: {g.level}: {g.message}"
        for function, g in found
    ]
    if found:
        lines.append(
            f"{len(found)} static-check finding(s) — plpgsql_check"
            " reads each as a body that cannot do what it says"
        )
    # A suppression is allowed only with its reason (#1605): the comment
    # line directly above it.
    lines.extend(
        f"{f.function}: line {u.line}: suppression `{u.options}` has no"
        " reason — the comment line directly above it says why"
        for f in report.functions
        if isinstance(f, Reading)
        for u in f.suppressions
        if u.reason is None
    )
    return lines


def refusals(report: Report | Failed) -> list[str]:
    """Name every reason the report cannot become a measurement.

    Returns:
        The refusal lines, empty when the report is a measurement: each a
        finding about the plpgsql leg.

    """
    lines = unmeasured(report)
    if isinstance(report, Report):
        lines.extend(unclean(report))
    return lines


def suppressed_lines(label: str, report: Report) -> list[str]:
    """Name every suppression that carries its reason, on every run.

    Returns:
        One line per suppression, so none is invisible.

    """
    return [
        f"{label}: plpgsql: {f.function}: line {u.line}: suppresses"
        f" `{u.options}` — {u.reason}"
        for f in report.functions
        if isinstance(f, Reading)
        for u in f.suppressions
        if u.reason is not None
    ]


@dataclass(frozen=True)
class Merged:
    """One function across every database that holds it (#2163)."""

    function: str
    statement_count: int
    # The union of the statement ids any database ran.
    ran: frozenset[int]
    # Each database's reading, in report order.
    readings: tuple[Reading, ...]

    @property
    def executed(self) -> int:
        """Count the statements any database ran."""
        return len(self.ran)

    @property
    def branch_free(self) -> bool:
        """Say whether any database read the function as branch-free."""
        return any(r.branch_free for r in self.readings)

    @property
    def highest_branches(self) -> Decimal:
        """Name the highest branch fraction one database read: a lower bound."""
        return max(r.branches for r in self.readings)


def merge(reports: list[Report]) -> tuple[list[Merged], list[str]]:
    """Merge the readings by signature, statements by id (#2163).

    Returns:
        Every function in first-seen order, and the refusal for each
        signature whose body differs between databases (its statement
        ids number different statements, so they cannot be merged).

    """
    grouped: dict[str, list[Reading]] = {}
    for report in reports:
        for f in report.functions:
            if isinstance(f, Reading):
                grouped.setdefault(f.function, []).append(f)
    merged: list[Merged] = []
    lines: list[str] = []
    for function, readings in grouped.items():
        first = readings[0]
        if any(
            (r.source, r.statement_count) != (first.source, first.statement_count)
            for r in readings
        ):
            lines.append(
                f"{function}: its body differs between the databases merged —"
                " statement ids are merged over one body only; build every"
                " test database from the same schema"
            )
            continue
        ran = frozenset(i for r in readings for i in r.ran)
        merged.append(Merged(function, first.statement_count, ran, tuple(readings)))
    return merged, lines


def unrun(merged: list[Merged], databases: int) -> list[str]:
    """Name every function no database ran (the owner's ruling, 2026-11-27).

    Returns:
        One refusal per function none of whose statements ran.

    """
    where = "the database" if databases == 1 else f"any of the {databases} databases"
    return [
        f"{m.function}: none of its {m.statement_count} statements ran in"
        f" {where} measured — a function no test runs is red, whatever"
        " the total"
        for m in merged
        if not m.ran
    ]


def reports_at(path: Path) -> list[Path]:
    """List the reports a path holds: a directory's, or the one file.

    Returns:
        The report files, sorted by name; empty when there is none.

    """
    if path.is_dir():
        return sorted(
            p for p in path.iterdir() if p.suffix == REPORT_SUFFIX and p.is_file()
        )
    return [path] if path.is_file() else []


def read_reports(paths: list[Path]) -> list[Report | Failed] | None:
    """Decode every report, naming each that is not one.

    Returns:
        The decoded reports, or None once every bad file is named.

    """
    decoded: list[Report | Failed] = []
    bad = False
    for path in paths:
        which = "the report" if len(paths) == 1 else f"the report {path.name}"
        try:
            decoded.append(decode(path.read_text(encoding="utf-8")))
        except NotAReportError as exc:
            findings.finding(LEG, f"{which} is not a coverage report: {exc}")
            bad = True
    return None if bad else decoded


def one_database(label: str, readings: list[Reading]) -> Decimal | None:
    """Print one database's per-function figures, as the leg always has.

    Returns:
        The statement-weighted branch total, or None with no branch.

    """
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
    return branch_total(readings)


def many_databases(label: str, merged: list[Merged]) -> Decimal | None:
    """Print the merged per-function figures, branches as lower bounds.

    Returns:
        The statement-weighted total of each function's highest branch
        fraction (a lower bound, never held), or None with no branch.

    """
    for m in merged:
        branches = (
            "none (left out of the branch total)"
            if m.branch_free
            else f"at least {percent(m.highest_branches)}%"
            " (the highest one database read)"
        )
        print(
            f"{label}: plpgsql: {m.function}:"
            f" statements {m.executed} of {m.statement_count}"
            f" ({percent(Decimal(m.executed) / Decimal(m.statement_count))}%),"
            f" branches {branches}",
            file=sys.stderr,
        )
    branching = [m for m in merged if not m.branch_free]
    weight = sum(m.statement_count for m in branching)
    if weight == 0:
        return None
    covered = sum(
        (m.highest_branches * m.statement_count for m in branching), Decimal(0)
    )
    return covered / weight


def once(lines: Iterable[str]) -> list[str]:
    """Keep each line once, in the order first given.

    Returns:
        The lines: what every database repeats is named once.

    """
    seen: set[str] = set()
    kept: list[str] = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            kept.append(line)
    return kept


def judged(decoded: list[Report | Failed]) -> tuple[list[Merged], list[str]]:
    """Merge the reports, after every reason they cannot be merged.

    Returns:
        The merged functions (none once there is a refusal), and every
        refusal, each named once however many databases repeat it.

    """
    reports = [r for r in decoded if isinstance(r, Report)]
    lines = [x for r in decoded for x in unmeasured(r)]
    lines.extend(x for r in reports for x in unclean(r))
    merged: list[Merged] = []
    if not lines:
        merged, differ = merge(reports)
        lines.extend(differ)
        if not differ:
            lines.extend(unrun(merged, len(reports)))
    lines = once(lines)
    return ([] if lines else merged), lines


def branch_words(branched: Decimal | None, databases: int) -> str:
    """Say what the branch leg is this run, for the totals line.

    Returns:
        The words after `branches`.

    """
    if branched is None:
        return "none: no function has a branch"
    if databases == 1:
        return f"{percent(branched)}% statement-weighted"
    return (
        f"not held; at least {percent(branched)}% statement-weighted,"
        " a lower bound from the highest fraction one database read"
        " per function"
    )


def print_totals(label: str, reports: list[Report], merged: list[Merged]) -> None:
    """Print the per-function figures, the totals and the two legs."""
    databases = len(reports)
    if databases == 1:
        branched = one_database(
            label, [f for f in reports[0].functions if isinstance(f, Reading)]
        )
    else:
        branched = many_databases(label, merged)
    executed = sum(m.executed for m in merged)
    statements = sum(m.statement_count for m in merged)
    stmt_pct = (
        percent(Decimal(executed) / Decimal(statements)) if statements else "0.00"
    )
    across = "" if databases == 1 else f" across {databases} databases"
    print(
        f"{label}: plpgsql: total statements {executed} of {statements}"
        f" ({stmt_pct}%){across}, branches {branch_words(branched, databases)}",
        file=sys.stderr,
    )
    if branched is not None and databases > 1:
        print(
            f"{label}: plpgsql: plpgsql-branches: not held, {databases}"
            " databases; plpgsql_check cannot merge branches across"
            f" databases ({TRACKING})",
            file=sys.stderr,
        )
    print(f"plpgsql {stmt_pct}")
    if branched is not None:
        held = percent(branched) if databases == 1 else NOT_HELD
        print(f"plpgsql-branches {held}")


def totals(label: str, report_path: str) -> int:
    """Print the two legs from every report, merged, or refuse by name.

    Returns:
        0 with the totals printed (or none, with no PL/pgSQL function);
        1 when no report was written or one is refused.

    """
    paths = reports_at(Path(report_path))
    if not paths:
        findings.finding(
            LEG,
            "the test task wrote no report — its teardown"
            f" must call belt plpgsql-coverage collect while {ENV} is set",
        )
        return 1
    decoded = read_reports(paths)
    if decoded is None:
        return 1
    reports = [r for r in decoded if isinstance(r, Report)]
    for line in once(x for r in reports for x in suppressed_lines(label, r)):
        print(line, file=sys.stderr)
    merged, lines = judged(decoded)
    for line in lines:
        findings.finding(LEG, line)
    if lines:
        return 1
    if not merged:
        print(
            f"{label}: plpgsql: the database holds no PL/pgSQL function",
            file=sys.stderr,
        )
        return 0
    print_totals(label, reports, merged)
    return 0


def main(argv: list[str]) -> int:
    """Dispatch a subcommand.

    Returns:
        The subcommand's status, or 2 on a usage error.

    """
    match argv:
        case ["collect", conninfo] | ["collect", conninfo, "--"]:
            return collect(conninfo)
        case ["collect", conninfo, "--", *client]:
            return collect(conninfo, tuple(client))
        case ["totals", label, report]:
            return totals(label, report)
        case _:
            findings.finding(
                "",
                f"usage: {NAME} collect <conninfo> [-- <client> [<argument>...]]"
                " | totals <label> <report>",
            )
            return 2
