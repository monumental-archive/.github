"""Table tests for the PL/pgSQL coverage leg (#1547).

Two tables. The helper's rows run `mise/plpgsql_coverage.py collect`
against a stub `psql` that answers with planted figures, then `totals`
over the report it wrote, and assert the exact messages. The leg's rows
run `coverage:check`'s own `run` string, read out of mise/config.toml,
in a planted git tree whose `test` task calls the real helper in its
teardown position, so a row states the database's figures and asserts
what the gate does with them.

The leg searches no file (#1666): which legs a floor holds is the
floor's own `legs:` record, and a report written by the suite's
teardown is the plpgsql leg's answer. So the clause is written whole
here, and the rows plant it where a search would have switched the leg
on.
"""

import base64
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Protocol

import pytest

from belt import coverage_floor as cf
from belt import plpgsql_coverage as pc
from belt.decoded import as_array, as_object, as_str, load_json, load_toml
from belt.typed_pytest import fixture
from tests import support, test_coverage_tasks

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

# More client calls than the collect's setup alone makes; the reports two
# collects write; and the databases the real-server row tears down.
SETUP_CALLS = 3
COLLECTS = 2
DATABASES = 2

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "leg"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


CANON = support.CANON
BELT = CANON / "mise"
BASH = support.tool("bash")
GIT = support.tool("git")
CONFIG = load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
TASKS = as_object(CONFIG["tasks"], "the belt's [tasks]")

CLAUSE = "LANGUAGE plpgsql"

PRELOAD = "pg_stat_statements,plpgsql,plpgsql_check"

# The repository's psql, answering each statement the helper sends with
# what the row planted in STUB_DIR.
PSQL = """\
#!/usr/bin/env bash
sql="${*: -1}"
case "${sql}" in
  *"CREATE EXTENSION"*)
    if [[ -n ${STUB_NO_EXTENSION:-} ]]; then
      echo "${STUB_NO_EXTENSION}" >&2
      exit 3
    fi
    ;;
  *current_setting*)
    if [[ -n ${STUB_META_FAIL:-} ]]; then
      echo "${STUB_META_FAIL}" >&2
      exit 2
    fi
    if [[ -n ${STUB_META:-} ]]; then
      echo "${STUB_META}"
    else
      printf '%s\\t%s\\n' "${STUB_PROFILER}" "${STUB_PRELOAD}"
    fi
    ;;
  *lanname*)
    cat "${STUB_DIR}/functions"
    # An extension's own functions answer any listing that keeps them.
    [[ ${sql} == *"d.deptype = 'e'"* ]] || cat "${STUB_DIR}/extension"
    ;;
  *prosrc*)
    # A function's source, base64 on one line, as the helper asks.
    oid=${sql##*oid = }
    planted="${STUB_DIR}/source.${oid}"
    if [[ -f ${planted} ]]; then
      base64 < "${planted}" | tr -d '\n'
    else
      printf 'BEGIN RETURN 1; END' | base64 | tr -d '\n'
    fi
    echo
    ;;
  *plpgsql_check_function_tb*)
    # The static check: the findings planted for this oid and table, or
    # none (a clean function); every call is logged for the rows.
    oid=${sql#*plpgsql_check_function_tb(}
    oid=${oid%%::*}
    relid=${sql#*::regprocedure, }
    relid=${relid%%::*}
    echo "${oid} ${relid}" >> "${STUB_DIR}/linted"
    if [[ -f ${STUB_DIR}/lint.${oid}.err ]]; then
      cat "${STUB_DIR}/lint.${oid}.err" >&2
      exit 3
    fi
    planted="${STUB_DIR}/lint.${oid}.${relid}"
    [[ ! -f ${planted} ]] || cat "${planted}"
    ;;
  *"ORDER BY stmtid"*)
    # The ids of the statements that ran (#2163): those planted for the
    # oid, else the first <executed> of its planted figures.
    oid=${sql#*plpgsql_profiler_function_statements_tb(}
    oid=${oid%%::*}
    if [[ -f ${STUB_DIR}/ran.${oid} ]]; then
      cat "${STUB_DIR}/ran.${oid}"
    else
      read -r _ _ _ executed _ < "${STUB_DIR}/read.${oid}" || true
      if [[ ${executed:-} =~ ^[0-9]+$ ]]; then
        for ((i = 1; i <= executed; i++)); do echo "${i}"; done
      fi
    fi
    ;;
  *plpgsql_coverage_statements*)
    oid=${sql#*plpgsql_coverage_statements(}
    oid=${oid%%::*}
    if [[ -f ${STUB_DIR}/read.${oid}.err ]]; then
      cat "${STUB_DIR}/read.${oid}.err" >&2
      exit 3
    fi
    cat "${STUB_DIR}/read.${oid}"
    ;;
  *) exit 2 ;;
esac
"""

# `mise tasks info test --json`: the planted test task, or nothing.
MISE = """\
#!/usr/bin/env bash
if [[ "$1 $2 $3" == "tasks info test" && -f ${STUB_TASK} ]]; then
  cat "${STUB_TASK}"
  exit 0
fi
exit 1
"""


# A reading, as psql prints it: the statement and branch fractions,
# then statements, executed statements, and statements that branch.
# Two functions: 4 of 5 statements and every branch of its IF, then 5
# of 10 statements and half the branches. Statements: 9 of 15, 60.00%.
# Branches, statement-weighted: (1 x 5 + 0.5 x 10)/15 = 66.66%,
# truncated, never rounded up.
TWO = {
    "101": ("public.guard()", "0.8\t1\t5\t4\t1"),
    "102": ("public.stamp(integer)", "0.5\t0.5\t10\t5\t2"),
}

GUARD = (
    "{label}: plpgsql: public.guard(): statements 4 of 5 (80.00%), branches 100.00%\n"
)
STAMP = (
    "{label}: plpgsql: public.stamp(integer): statements 5 of 10 (50.00%),"
    " branches 50.00%\n"
)
TWO_REPORT = (
    GUARD
    + STAMP
    + "{label}: plpgsql: total statements 9 of 15 (60.00%), branches 66.66%"
    " statement-weighted\n"
)

# A source digest, as a report records one (#2163).
SOURCE = "0" * 64

# A branch-free function: no IF, CASE or loop, and the extension's 1.
TOUCH = ("public.touch()", "1\t1\t2\t2\t0")
TOUCH_LINE = (
    "{label}: plpgsql: public.touch(): statements 2 of 2 (100.00%),"
    " branches none (left out of the branch total)\n"
)


def plant_stubs(
    tmp: Path,
    functions: dict[str, tuple[str, str]],
    unreadable: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    """Write the stub tools and the database's planted answers.

    Returns:
        The stub directory and the answer directory.

    """
    stubs = tmp / "stubs"
    answers = tmp / "answers"
    stubs.mkdir()
    answers.mkdir()
    for name, text in (("psql", PSQL), ("mise", MISE)):
        (stubs / name).write_text(text, encoding="utf-8")
        (stubs / name).chmod(0o755)
    listing = "".join(f"{oid}\t{sig}\t\n" for oid, (sig, _) in functions.items())
    (answers / "functions").write_text(listing, encoding="utf-8")
    # postgis owns PL/pgSQL functions in a non-system schema; uncovered.
    (answers / "extension").write_text(
        "900\tpublic.postgis_owned()\t\n", encoding="utf-8"
    )
    (answers / "read.900").write_text("0\t0\t40\t0\t3\n", encoding="utf-8")
    for oid, (_, figures) in functions.items():
        (answers / f"read.{oid}").write_text(f"{figures}\n", encoding="utf-8")
    for oid, error in (unreadable or {}).items():
        (answers / f"read.{oid}.err").write_text(f"{error}\n", encoding="utf-8")
    return stubs, answers


def stub_env(
    stubs: Path, answers: Path, *, env: support.Env, **planted: str
) -> dict[str, str]:
    """Build a row's environment: the stubs first on PATH.

    Returns:
        The environment.

    """
    environ = env(MISE_TASK_NAME=TASK)
    return (
        environ
        | {
            "PATH": f"{stubs}{os.pathsep}{environ.get('PATH', '')}",
            "ORG_PYTHON": sys.executable,
            "ORG_BELT_DIR": str(BELT),
            "STUB_DIR": str(answers),
            "STUB_PROFILER": "on",
            "STUB_PRELOAD": PRELOAD,
        }
        | planted
    )


def run(
    argv: list[str], env: dict[str, str], cwd: Path, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a command and capture its output as text.

    Returns:
        The finished process.

    """
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def as_the_child(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], cwd: Path
) -> None:
    """Give the helper's in-process call the environment a child had (#1711).

    The environment a script row handed the child is this process's for
    the call, cleared and set, so the helper starts the stub psql it
    finds on that PATH, as the child did.
    """
    for name in list(os.environ):
        monkeypatch.delenv(name)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(cwd)


class Teardown(NamedTuple):
    """Whether the suite's teardown collects, and over how many databases."""

    collects: bool = True
    databases: int = 1


# The suite every row has unless it names another: it collects once.
COLLECTS_ONCE = Teardown()


def measured_run(
    tmp: Path,
    env: dict[str, str],
    *,
    python: bool,
    teardown: Teardown,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave `tmp`'s run directory as a suite run through `measured` does (#1667).

    `python` plants the rc file and coverage.py's data the stub
    `coverage` reads; `teardown.collects` runs the suite's teardown in
    `tmp`'s tree, the helper called in-process (#1711), with the
    ORG_PLPGSQL_COVERAGE `measured` names in the run, once for each of
    `teardown.databases` (#2163).

    """
    run_dir, tree = tmp / "run", tmp / "tree"
    run_dir.mkdir(parents=True)
    if python:
        (run_dir / "python").mkdir()
        (run_dir / "coverage.rc").write_text("[run]\n", encoding="utf-8")
        (run_dir / "python" / ".coverage.stub").write_text("", encoding="utf-8")
    for _ in range(teardown.databases if teardown.collects else 0):
        report = {"ORG_PLPGSQL_COVERAGE": str(run_dir / "plpgsql-coverage")}
        as_the_child(monkeypatch, env | report, tree)
        assert pc.main(COLLECT) == 0


# The teardown's call, naming no client.
COLLECT = ["collect", "postgres://stub"]


@dataclass(frozen=True)
class Database:
    """What a row's stub database holds and answers.

    `listing` replaces the function listing psql prints, verbatim;
    `lint` plants the static check's rows, keyed `<oid>.<relid>`;
    `sources` a function's body by oid; `env` the stub's own knobs.
    """

    functions: dict[str, tuple[str, str]]
    unreadable: dict[str, str] = field(default_factory=dict[str, str])
    listing: str | None = None
    lint: dict[str, str] = field(default_factory=dict[str, str])
    sources: dict[str, str] = field(default_factory=dict[str, str])
    env: dict[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True)
class Planted:
    """A planted stub database: its answers and where reports go."""

    answers: Path
    reports: Path

    def report(self) -> Path:
        """Name the one report a collection wrote.

        Returns:
            The report's path.

        """
        (written,) = self.reports.glob("*.json")
        return written


@fixture
def plant(
    tmp_path: Path, env: support.Env, monkeypatch: pytest.MonkeyPatch
) -> Callable[[Database], Planted]:
    """Plant a stub database and set the teardown's environment.

    Returns:
        A function that plants one database, in a directory of its own,
        and makes this process's environment and directory the child's.

    """

    def planting(database: Database) -> Planted:
        tmp = Path(tempfile.mkdtemp(dir=tmp_path))
        stubs, answers = plant_stubs(tmp, database.functions, database.unreadable)
        if database.listing is not None:
            (answers / "functions").write_text(database.listing, encoding="utf-8")
        for key, rows in database.lint.items():
            (answers / f"lint.{key}").write_text(rows, encoding="utf-8")
        for oid, source in database.sources.items():
            (answers / f"source.{oid}").write_text(source, encoding="utf-8")
        reports = tmp / "reports"
        environ = stub_env(
            stubs,
            answers,
            **({"ORG_PLPGSQL_COVERAGE": str(reports)} | database.env),
            env=env,
        )
        as_the_child(monkeypatch, environ, tmp)
        return Planted(answers, reports)

    return planting


def finding(line: str, level: str, message: str) -> str:
    """Write one static-check row as psql prints it.

    Returns:
        The tab-separated row, the message base64 as the helper asks.

    """
    encoded = base64.b64encode(message.encode("utf-8")).decode("ascii")
    return f"{line}\t{level}\t{encoded}\n"


LINT_RED = (
    "leg: plpgsql: {function}: line {line}: {level}: {message}\n"
    "leg: plpgsql: {count} static-check finding(s) — plpgsql_check reads each"
    " as a body that cannot do what it says\n"
)

REASON = "-- the RAISE ends the guard, so the RETURN after it never runs"
OPTIONS_OFF = "-- @plpgsql_check_options: extra_warnings = false"
BODY = "\nBEGIN\n  RAISE EXCEPTION 'no';\n  RETURN NULL;\nEND\n"

OWN = "(the repository's own client)"
REMEDY = (
    "pass the client after --, for example:"
    " collect <conninfo> -- docker exec -i <container> psql"
)

UNREADABLE = (
    "leg: plpgsql: 1 function(s) unreadable — a function the extension"
    " cannot read is never dropped from the total\n"
)
FAILED = "leg: plpgsql: the teardown's collection failed: {why}\n"
NO_REPORT = (
    "leg: plpgsql: the test task wrote no report — its teardown must"
    " call belt plpgsql-coverage collect while ORG_PLPGSQL_COVERAGE is set\n"
)
TOTALLED = "plpgsql 60.00\nplpgsql-branches 66.66\n"
NO_REASON = (
    "leg: plpgsql: public.guard(): line {line}: suppression"
    " `extra_warnings = false` has no reason — the comment line"
    " directly above it says why\n"
)
EXTENSION_ABSENT = 'ERROR:  extension "plpgsql_check" is not available'
UNREACHED = 'FATAL:  database "stub" does not exist'
MISSING_CLIENT = "/nonexistent/client"


class Collected(NamedTuple):
    """A row: the database, the teardown's argv, then what `totals` says."""

    name: str
    database: Database
    collect: list[str]
    status: int
    stdout: str
    stderr: str


class Merged(NamedTuple):
    """A row: the database, its planted answers, the teardowns, the totals."""

    name: str
    database: Database
    answers: dict[str, str]
    collects: int
    status: int
    stdout: str
    stderr: str


# Each row: the database, the teardown's argv, then what `totals` over
# the report it wrote returns, prints and says.
COLLECTED = [
    Collected(
        "two functions weighted, an extension's own left out",
        Database(TWO),
        COLLECT,
        0,
        TOTALLED,
        TWO_REPORT.format(label="leg"),
    ),
    Collected(
        "a branch-free function left out of the branch total",
        Database(TWO | {"103": TOUCH}),
        COLLECT,
        0,
        "plpgsql 64.70\nplpgsql-branches 66.66\n",
        (
            GUARD
            + STAMP
            + TOUCH_LINE
            + "{label}: plpgsql: total statements 11 of 17 (64.70%),"
            " branches 66.66% statement-weighted\n"
        ).format(label="leg"),
    ),
    Collected(
        "no branch anywhere prints no branch leg",
        Database({"103": TOUCH}),
        COLLECT,
        0,
        "plpgsql 100.00\n",
        TOUCH_LINE.format(label="leg")
        + "leg: plpgsql: total statements 2 of 2 (100.00%), branches none:"
        " no function has a branch\n",
    ),
    Collected(
        "an unreadable function is listed and red",
        Database(TWO, unreadable={"102": "ERROR:  stub: cache lookup failed"}),
        COLLECT,
        1,
        "",
        "leg: plpgsql: plpgsql_check cannot read public.stamp(integer):"
        " ERROR:  stub: cache lookup failed\n" + UNREADABLE,
    ),
    Collected(
        "an oid that is not a number is never formatted",
        Database({"7; DROP TABLE t": ("public.odd()", "1\t1\t1\t1\t0")}),
        COLLECT,
        1,
        "",
        "leg: plpgsql: plpgsql_check cannot read public.odd(): its oid"
        " '7; DROP TABLE t' is not a number\n" + UNREADABLE,
    ),
    Collected(
        "figures that are not numbers are unreadable",
        Database({"101": ("public.guard()", "x\ty")}),
        COLLECT,
        1,
        "",
        "leg: plpgsql: plpgsql_check cannot read public.guard():"
        " unreadable figures: [['x', 'y']]\n" + UNREADABLE,
    ),
    Collected(
        "no PL/pgSQL function prints no totals",
        Database({}),
        COLLECT,
        0,
        "",
        "leg: plpgsql: the database holds no PL/pgSQL function\n",
    ),
    Collected(
        "the profiler off is red by name, never zero",
        Database(
            {"101": ("public.guard()", "0\t0\t5\t0\t1")}, env={"STUB_PROFILER": "off"}
        ),
        COLLECT,
        1,
        "",
        "leg: plpgsql: plpgsql_check.profiler is 'off' — with the profiler"
        " off every function reads 0%, which is not a measurement; start"
        " the test database with -c plpgsql_check.profiler=on\n",
    ),
    Collected(
        "a profiler not preloaded is red by name",
        Database(TWO, env={"STUB_PRELOAD": "pg_stat_statements"}),
        COLLECT,
        1,
        "",
        "leg: plpgsql: plpgsql_check is not in shared_preload_libraries"
        " ('pg_stat_statements') — the profile is per session and the"
        " teardown sees none of the suite's; preload plpgsql,plpgsql_check\n",
    ),
    Collected(
        "an error is red by function and line",
        Database(
            TWO,
            lint={
                "102.0": finding("2", "error", 'column "no_such_column" does not exist')
            },
        ),
        COLLECT,
        1,
        "",
        LINT_RED.format(
            function="public.stamp(integer)",
            line=2,
            level="error",
            message='column "no_such_column" does not exist',
            count=1,
        ),
    ),
    Collected(
        "a warning is red",
        Database(
            TWO, lint={"101.0": finding("14", "warning extra", "unreachable code")}
        ),
        COLLECT,
        1,
        "",
        LINT_RED.format(
            function="public.guard()",
            line=14,
            level="warning extra",
            message="unreachable code",
            count=1,
        ),
    ),
    Collected(
        "a routine-wide finding is named without a line",
        Database(
            TWO,
            lint={
                "101.0": finding(
                    "",
                    "performance",
                    "routine is marked as VOLATILE, should be IMMUTABLE",
                )
            },
        ),
        COLLECT,
        1,
        "",
        LINT_RED.format(
            function="public.guard()",
            line="-",
            level="performance",
            message="routine is marked as VOLATILE, should be IMMUTABLE",
            count=1,
        ),
    ),
    Collected(
        "the extension absent is refused by name",
        Database(TWO, env={"STUB_NO_EXTENSION": EXTENSION_ABSENT}),
        COLLECT,
        1,
        "",
        FAILED.format(why=EXTENSION_ABSENT),
    ),
    Collected(
        "a suppression with its reason is green and printed",
        Database(TWO, sources={"101": f"\n{REASON}\n{OPTIONS_OFF}{BODY}"}),
        COLLECT,
        0,
        TOTALLED,
        "leg: plpgsql: public.guard(): line 3: suppresses"
        " `extra_warnings = false` — the RAISE ends the guard, so the"
        " RETURN after it never runs\n" + TWO_REPORT.format(label="leg"),
    ),
    Collected(
        "a suppression with nothing above has no reason",
        Database(TWO, sources={"101": OPTIONS_OFF + BODY}),
        COLLECT,
        1,
        "",
        NO_REASON.format(line=1),
    ),
    Collected(
        "a suppression under an empty comment has no reason",
        Database(TWO, sources={"101": f"--\n{OPTIONS_OFF}{BODY}"}),
        COLLECT,
        1,
        "",
        NO_REASON.format(line=2),
    ),
    Collected(
        "a suppression under another options line has no reason",
        Database(
            TWO,
            sources={
                "101": "-- @plpgsql_check_options: fatal_errors = true\n"
                + OPTIONS_OFF
                + BODY
            },
        ),
        COLLECT,
        1,
        "",
        NO_REASON.format(line=2),
    ),
    Collected(
        "an options line that only turns checks on is no suppression",
        Database(
            TWO,
            sources={
                "101": "\n-- @plpgsql_check_options: performance_warnings = true" + BODY
            },
        ),
        COLLECT,
        0,
        TOTALLED,
        TWO_REPORT.format(label="leg"),
    ),
    Collected(
        "without_warnings is a suppression",
        Database(
            TWO,
            sources={
                "101": "\n-- @plpgsql_check_options: without_warnings = true" + BODY
            },
        ),
        COLLECT,
        1,
        "",
        "leg: plpgsql: public.guard(): line 2: suppression"
        " `without_warnings = true` has no reason — the comment line"
        " directly above it says why\n",
    ),
    Collected(
        "a client that cannot start names the remedy",
        Database(TWO),
        [*COLLECT, "--", MISSING_CLIENT, "exec"],
        1,
        "",
        FAILED.format(why=f"{MISSING_CLIENT} is not on PATH {OWN}; {REMEDY}"),
    ),
    Collected(
        "a database psql cannot reach fails by its error",
        Database(TWO, env={"STUB_META_FAIL": UNREACHED}),
        COLLECT,
        1,
        "",
        FAILED.format(why=UNREACHED),
    ),
    Collected(
        "unreadable settings fail the collection",
        Database(TWO, env={"STUB_META": "on"}),
        COLLECT,
        1,
        "",
        FAILED.format(why="unreadable settings from the database: [['on']]"),
    ),
    Collected(
        "an unreadable listing row fails the collection",
        Database(TWO, listing="101\n"),
        COLLECT,
        1,
        "",
        FAILED.format(why="unreadable function listing: ['101']"),
    ),
]


@pytest.mark.parametrize("row", COLLECTED, ids=[row.name for row in COLLECTED])
def test_collect_then_totals(
    plant: Callable[[Database], Planted],
    capfd: pytest.CaptureFixture[str],
    row: Collected,
) -> None:
    """The teardown collects; `totals` over its report says exactly this."""
    planted = plant(row.database)
    collected = pc.main(row.collect)
    assert (collected, *capfd.readouterr()) == (0, "", "")
    status = pc.main(["totals", "leg", str(planted.reports)])
    out, err = capfd.readouterr()
    assert (status, out, err) == (row.status, row.stdout, row.stderr)


def test_no_psql_on_path_fails_the_collection_by_name(
    plant: Callable[[Database], Planted],
    capfd: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """The client is the repository's; without it nothing is read."""
    empty = tmp_path / "empty"
    empty.mkdir()
    planted = plant(Database(TWO, env={"PATH": str(empty)}))
    assert pc.main(COLLECT) == 0
    status = pc.main(["totals", "leg", str(planted.reports)])
    out, err = capfd.readouterr()
    why = f"psql is not on PATH {OWN}; {REMEDY}"
    assert (status, out, err) == (1, "", FAILED.format(why=why))


def test_a_clean_report_records_no_finding_and_asks_each_function(
    plant: Callable[[Database], Planted],
) -> None:
    """Every function asked, none reported; an extension's never asked."""
    planted = plant(Database(TWO))
    assert pc.main(COLLECT) == 0
    document = as_object(load_json(planted.report().read_text("utf-8")), "report")
    functions = as_array(document.get("functions"), "functions")
    findings = [as_object(f, "function").get("findings") for f in functions]
    none: list[object] = [[], []]
    assert none == findings
    assert (planted.answers / "linted").read_text("utf-8") == "101 0\n102 0\n"


def test_a_trigger_function_is_checked_against_each_table(
    plant: Callable[[Database], Planted],
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Its NEW and OLD are each table's rows; a shared finding is one."""
    row = finding("14", "warning extra", "unreachable code")
    planted = plant(
        Database(
            TWO,
            listing="101\tpublic.guard()\t5001 5002\n102\tpublic.stamp(integer)\t\n",
            lint={"101.5001": row, "101.5002": row},
        )
    )
    assert pc.main(COLLECT) == 0
    status = pc.main(["totals", "leg", str(planted.reports)])
    out, err = capfd.readouterr()
    linted = (planted.answers / "linted").read_text("utf-8")
    assert linted == "101 5001\n101 5002\n102 0\n"
    red = LINT_RED.format(
        function="public.guard()",
        line=14,
        level="warning extra",
        message="unreachable code",
        count=1,
    )
    assert (status, out, err) == (1, "", red)


# A client the call names (#2114): logs the two words it was handed
# before the helper's own, then runs the stub psql with the rest.
CLIENT = """\
#!/usr/bin/env bash
printf '[%s][%s][%s]\\n' "$1" "$2" "$3" >> "${STUB_DIR}/client"
shift 2
exec "${STUB_PSQL}" "$@"
"""


@pytest.mark.parametrize("named", [COLLECT, [*COLLECT, "--"]])
def test_no_client_named_runs_psql_on_path(
    plant: Callable[[Database], Planted],
    capfd: pytest.CaptureFixture[str],
    named: list[str],
) -> None:
    """`collect <conninfo>`, with or without a bare `--`, runs psql."""
    planted = plant(Database(TWO))
    collected = pc.main(named)
    assert (collected, *capfd.readouterr()) == (0, "", "")
    status = pc.main(["totals", "leg", str(planted.reports)])
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, TOTALLED, TWO_REPORT.format(label="leg"))
    assert (planted.answers / "linted").is_file()


def test_a_named_client_is_run_with_its_words_first(
    plant: Callable[[Database], Planted],
    capfd: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """The words run as an argv, unjoined, then the helper's own.

    The client and the stub psql are planted outside PATH.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    for name, text in (("client", CLIENT), ("psql", PSQL)):
        (elsewhere / name).write_text(text, encoding="utf-8")
        (elsewhere / name).chmod(0o755)
    planted = plant(
        Database(
            TWO,
            env={
                "PATH": f"{elsewhere / 'empty'}{os.pathsep}/usr/bin{os.pathsep}/bin",
                "STUB_PSQL": str(elsewhere / "psql"),
            },
        )
    )
    collected = pc.main([*COLLECT, "--", str(elsewhere / "client"), "a b", "c"])
    assert (collected, *capfd.readouterr()) == (0, "", "")
    assert planted.report().is_file()
    calls = (planted.answers / "client").read_text(encoding="utf-8").splitlines()
    assert len(calls) > SETUP_CALLS
    every: list[str] = ["[a b][c][-X]"] * len(calls)
    assert every == calls


def test_collect_without_a_report_path_is_refused(
    plant: Callable[[Database], Planted],
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The teardown calls collect only when the leg set the path."""
    plant(Database(TWO, env={"ORG_PLPGSQL_COVERAGE": ""}))
    status = pc.main(COLLECT)
    out, err = capfd.readouterr()
    refused = (
        f"{TASK}: ORG_PLPGSQL_COVERAGE is not set — the leg sets"
        " it; call collect only then\n"
    )
    assert (status, out, err) == (1, "", refused)


@fixture
def in_the_task(
    tmp_path: Path, env: support.Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the row as the task, in its scratch directory."""
    as_the_child(monkeypatch, env(MISE_TASK_NAME=TASK), tmp_path)


GOOD = {
    "function": "public.guard()",
    "statements": "0.8",
    "branches": "1",
    "statement_count": 5,
    "executed": 4,
    "branch_free": False,
    "ran": [1, 2, 3, 4],
    "source": SOURCE,
    "findings": [],
    "suppressions": [],
}


def report(*entries: object) -> dict[str, object]:
    """Wrap function entries in a report document.

    Returns:
        The document.

    """
    return {"profiler": "on", "preload": PRELOAD, "functions": list(entries)}


def without(key: str) -> dict[str, object]:
    """Copy GOOD without one key.

    Returns:
        The entry.

    """
    return {k: v for k, v in GOOD.items() if k != key}


NOT_A_REPORT = "leg: plpgsql: the report is not a coverage report: {what}\n"
UNNAMED = (
    "function 0: 'ran' does not name 'executed' statements from 1 to 'statement_count'"
)


def as_json(document: object) -> str:
    """Write a planted report's text.

    Returns:
        The document as JSON.

    """
    return json.dumps(document)


# Each row: the report file's text, then what `totals` says of it.
PLANTED: list[tuple[str, str]] = [
    (as_json({"error": "boom"}), FAILED.format(why="boom")),
    (
        "[",
        NOT_A_REPORT.format(
            what="not JSON (Expecting value: line 1 column 2 (char 1))"
        ),
    ),
    ("[]", NOT_A_REPORT.format(what="the document is not an object")),
    (
        as_json({"error": 3}),
        NOT_A_REPORT.format(what="the document: 'error' is not a string"),
    ),
    (
        as_json({"profiler": "on", "preload": PRELOAD, "functions": {}}),
        NOT_A_REPORT.format(what="the document: 'functions' is not a list"),
    ),
    (
        as_json({"preload": PRELOAD, "functions": []}),
        NOT_A_REPORT.format(what="the document: 'profiler' is not a string"),
    ),
    (as_json(report(1)), NOT_A_REPORT.format(what="function 0 is not an object")),
    (
        as_json(report(GOOD | {"statements": 0.8})),
        NOT_A_REPORT.format(what="function 0: 'statements' is not a string"),
    ),
    (
        as_json(report(without("findings"))),
        NOT_A_REPORT.format(what="function 0: 'findings' is not a list"),
    ),
    (
        as_json(report(GOOD | {"branch_free": "no"})),
        NOT_A_REPORT.format(what="function 0: 'branch_free' is not a boolean"),
    ),
    (
        as_json(report(GOOD | {"statement_count": -1})),
        NOT_A_REPORT.format(what="function 0: 'statement_count' is not a count"),
    ),
    (
        as_json(report(GOOD | {"executed": True})),
        NOT_A_REPORT.format(what="function 0: 'executed' is not a count"),
    ),
    (
        as_json(report(GOOD | {"statements": "1.5"})),
        NOT_A_REPORT.format(
            what="function 0: 'statements' is not a fraction from 0 to 1"
        ),
    ),
    (
        as_json(report(GOOD | {"branches": "abc"})),
        NOT_A_REPORT.format(
            what="function 0: 'branches' is not a fraction from 0 to 1"
        ),
    ),
    (
        as_json(report(GOOD, GOOD | {"statements": "NaN"})),
        NOT_A_REPORT.format(
            what="function 1: 'statements' is not a fraction from 0 to 1"
        ),
    ),
    (
        as_json(report({"function": "public.guard()", "error": 7})),
        NOT_A_REPORT.format(what="function 0: 'error' is not a string"),
    ),
    *(
        (
            as_json(report(entry)),
            NOT_A_REPORT.format(
                what="function 0: 'ran' is not a list of statement ids"
            ),
        )
        for entry in (
            without("ran"),
            GOOD | {"ran": [1, True, 3, 4]},
            GOOD | {"ran": [2, 1, 3, 4]},
            GOOD | {"ran": [0, 1, 2, 3]},
        )
    ),
    (as_json(report(GOOD | {"ran": [1, 2, 3]})), NOT_A_REPORT.format(what=UNNAMED)),
    (
        as_json(report(GOOD | {"ran": [1, 2, 3, 6]})),
        NOT_A_REPORT.format(what=UNNAMED),
    ),
    (
        as_json(report(without("source"))),
        NOT_A_REPORT.format(what="function 0: 'source' is not a string"),
    ),
]


@pytest.mark.usefixtures("in_the_task")
@pytest.mark.parametrize(("text", "stderr"), PLANTED)
def test_totals_refuses_a_report_by_what_is_wrong(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], text: str, stderr: str
) -> None:
    """A failed collection, or a file the one decoder refuses, by name."""
    planted = tmp_path / "report.json"
    planted.write_text(text, encoding="utf-8")
    status = pc.main(["totals", "leg", str(planted)])
    out, err = capfd.readouterr()
    assert (status, out, err) == (1, "", stderr)


@pytest.mark.usefixtures("in_the_task")
def test_a_missing_report_names_the_teardown(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """A suite whose teardown never called collect is refused."""
    status = pc.main(["totals", "leg", str(tmp_path / "none.json")])
    out, err = capfd.readouterr()
    assert (status, out, err) == (1, "", NO_REPORT)


@pytest.mark.usefixtures("in_the_task")
@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param([], "the following arguments are required: mode", id="none"),
        pytest.param(
            ["measure", "leg"],
            "argument mode: invalid choice: 'measure'"
            " (choose from 'collect', 'totals')",
            id="unknown",
        ),
    ],
)
def test_a_usage_error_names_both_subcommands(
    argv: list[str], error: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """No subcommand, or an unknown one: argparse's exit 2 and its error."""
    with pytest.raises(SystemExit) as ended:
        pc.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt plpgsql-coverage "), err
    assert err.splitlines()[-1] == f"belt plpgsql-coverage: error: {error}"


# The clause, whole, in three tracked files of three kinds.
MENTIONS = {
    "README.md": f"Functions are declared with `{CLAUSE}`.\n",
    "helper.py": f"# {CLAUSE}\nprint('hi')\n",
    "schema.sql": f"CREATE FUNCTION f() RETURNS trigger {CLAUSE} AS $$ $$;\n",
}

HELD = {
    "101": ("public.guard()", "0.9\t1\t10\t9\t1"),
    "102": ("public.stamp(integer)", "1\t0.8\t10\t10\t1"),
}

# One function: 19 of 20 statements, 88% of its branches (#2143).
STRONG = {"101": ("public.guard()", "0.95\t0.88\t20\t19\t1")}

PYTHON_HELD = (
    "coverage:check: python leg holds its floor 80.0 (90.0%)\n"
    "coverage:check: python-branches leg holds its floor 80.0 (85.0%)\n"
)


@dataclass(frozen=True)
class Leg:
    """coverage:check's body run in a planted tree, and the floor it read."""

    done: subprocess.CompletedProcess[str]
    floor: Path
    planted: str


class RunsLeg(Protocol):
    """Lay out and run one `coverage:check`; `run_leg` says how."""

    def __call__(
        self,
        functions: dict[str, tuple[str, str]],
        *,
        legs: str,
        files: dict[str, str],
        measured: dict[str, str] | None = None,
        teardown: Teardown = COLLECTS_ONCE,
    ) -> Leg:
        """Plant the tree, the floor and the run the suite left, then check."""
        ...


@fixture
def run_leg(
    tmp_path: Path, env: support.Env, monkeypatch: pytest.MonkeyPatch
) -> RunsLeg:
    """Lay out `coverage:check` over the plpgsql leg, decided by the floor's record.

    Returns:
        A function that plants one tree and its run, and returns the leg.

    """

    def laying_out(
        functions: dict[str, tuple[str, str]],
        *,
        legs: str,
        files: dict[str, str],
        measured: dict[str, str] | None = None,
        teardown: Teardown = COLLECTS_ONCE,
    ) -> Leg:
        """Plant the tree, the floor and the run the suite left.

        `legs` is the floor's `legs:` record, each leg at 80.0 unless
        `measured` names the figure its floor was derived from (#2143),
        `files` the tracked files beside it, and `teardown` whether the
        suite's teardown calls the helper, and over how many databases
        (#2163). A tracked `*.py` brings the python leg in,
        measured by the stub coverage.py.

        Returns:
            The finished check and the floor it read.

        """
        tmp = Path(tempfile.mkdtemp(dir=tmp_path))
        stubs, answers = plant_stubs(tmp, functions)
        (stubs / "coverage").write_text(COVERAGE, encoding="utf-8")
        (stubs / "coverage").chmod(0o755)
        (stubs / "python3").symlink_to(sys.executable)
        test_coverage_tasks.plant_pytest_venv(stubs)
        tree = tmp / "tree"
        tree.mkdir()
        for name, text in files.items():
            (tree / name).write_text(text, encoding="utf-8")
        figures = dict.fromkeys(legs.split(), "82.0") | (measured or {})
        floor = cf.render(
            {leg: cf.fresh(Decimal(figure)) for leg, figure in figures.items()},
            "v1.0.0",
        )
        (tree / ".coverage-floor").write_text(floor, encoding="utf-8")
        # The test task, for the read of it the python leg makes; the
        # check never runs it.
        task = tmp / "task.json"
        suite: dict[str, object] = {"dir": str(tree), "run": ["echo suite ran >&2"]}
        task.write_text(json.dumps(suite), "utf-8")
        environ = stub_env(
            stubs,
            answers,
            STUB_TASK=str(task),
            STUB_PYTHON=sys.executable,
            STUB_PY="90.0",
            STUB_BR="85.0",
            ORG_COVERAGE_RUN=str(tmp / "run"),
            env=env,
        )
        run([GIT, "init", "-q"], environ, tree)
        run([GIT, "add", *files, ".coverage-floor"], environ, tree)
        measured_run(
            tmp,
            environ,
            python=any(name.endswith(".py") for name in files),
            teardown=teardown,
            monkeypatch=monkeypatch,
        )
        check = as_object(TASKS["coverage:check"], "coverage:check")
        script = as_str(check["run"], "coverage:check's run")
        as_mise = support.task_run("coverage:check", script)
        done = run(as_mise.argv, environ | as_mise.env, tree)
        return Leg(done, tree / ".coverage-floor", floor)

    return laying_out


def python_remedy(leg: str) -> str:
    """Name the remedy coverage:check prints for a Python leg it lost.

    Returns:
        The remedy's line.

    """
    return (
        "  the python leg needs tracked *.py and a `test` task whose test"
        ' command runs through "${ORG_BELT_DIR}/measured"; or record'
        f" the leg's removal on purpose: `# dropped: {leg} <reason>` in"
        " .coverage-floor\n"
    )


def plpgsql_remedy(leg: str) -> str:
    """Name the remedy coverage:check prints for a PL/pgSQL leg it lost.

    Returns:
        The remedy's line.

    """
    return (
        "  the plpgsql leg needs a test command run through"
        ' "${ORG_BELT_DIR}/measured" and its teardown to call'
        " belt plpgsql-coverage collect while ORG_PLPGSQL_COVERAGE is set;"
        " or record the leg's removal on purpose:"
        f" `# dropped: {leg} <reason>` in .coverage-floor\n"
    )


def assert_held(held: str, stdout: str) -> None:
    """Assert the hold lines, then the one upload: the Python report (#1628).

    The reports are written outside the tree, so the path is the
    measurement's scratch directory; its file name is the leg's.
    """
    head, _, upload = stdout.rstrip("\n").rpartition("\n")
    assert held == head + "\n"
    assert re.search(r"^coverage:check: upload /\S+/coverage\.xml$", upload)


def test_the_clause_in_files_switches_nothing_on(
    run_leg: RunsLeg,
) -> None:
    """No search: a README, a .py and a .sql naming the clause (#1666)."""
    leg = run_leg(
        TWO,
        legs="python python-branches",
        files=MENTIONS,
        teardown=Teardown(collects=False),
    )
    done = leg.done
    assert done.returncode == 0, done.stderr
    assert_held(PYTHON_HELD, done.stdout)
    assert "plpgsql" not in done.stdout + done.stderr


def test_a_recorded_leg_is_held_with_no_sql_tracked(
    run_leg: RunsLeg,
) -> None:
    """The report is the answer: no .sql file is needed to find it."""
    leg = run_leg(
        HELD,
        legs="python python-branches plpgsql plpgsql-branches",
        files={"helper.py": "print('hi')\n"},
    )
    done = leg.done
    assert done.returncode == 0, done.stderr
    assert_held(
        PYTHON_HELD + "coverage:check: plpgsql leg holds its floor 80.0 (95.00%)\n"
        "coverage:check: plpgsql-branches leg holds its floor 80.0 (90.00%)\n",
        done.stdout,
    )


def test_below_the_floor_is_red_by_name(run_leg: RunsLeg) -> None:
    """Statements at 60% red the plpgsql leg by its name."""
    leg = run_leg(
        TWO, legs="plpgsql plpgsql-branches", files={"schema.sql": "-- sql\n"}
    )
    done = leg.done
    assert done.returncode == 1
    assert (
        "coverage:check: plpgsql: 60.00% is below its derived floor 80.0%\n"
        in done.stderr
    )


def test_a_leg_below_its_own_floor_above_a_weaker_leg_is_red(
    run_leg: RunsLeg,
) -> None:
    """plpgsql-branches at 88%: above python-branches, below its own 90.

    Every leg is held to a floor of its own (#2143). Held to the
    weakest leg's floor, the shape this replaced, the branch figure
    stood eight points above it and the gate was green.
    """
    leg = run_leg(
        STRONG,
        legs="python python-branches plpgsql plpgsql-branches",
        files={"helper.py": "print('hi')\n"},
        measured={"plpgsql": "95.0", "plpgsql-branches": "95.0"},
    )
    done = leg.done
    assert done.returncode == 1, done.stderr
    assert (
        done.stderr.splitlines()[-1]
        == "coverage:check: plpgsql-branches: 88.00% is below its derived floor 90%"
    )
    assert "python" not in done.stderr
    assert done.stdout == (
        PYTHON_HELD + "coverage:check: plpgsql leg holds its floor 90 (95.00%)\n"
    )


def test_a_record_whose_every_leg_reports_nothing_names_them(
    run_leg: RunsLeg,
) -> None:
    """Every recorded leg lost: each named, never the generic refusal."""
    leg = run_leg(
        TWO,
        legs="python python-branches",
        files={"README.md": "no python left\n"},
        teardown=Teardown(collects=False),
    )
    done = leg.done
    assert done.returncode == 1
    assert (
        "coverage:check: the floor was derived with python and this run"
        " measured none\n"
        + python_remedy("python")
        + "coverage:check: the floor was derived with python-branches and"
        " this run measured none\n" + python_remedy("python-branches")
    ) == done.stderr.removeprefix("suite ran\n")
    assert "no leg was measured" not in done.stderr
    assert not done.stdout


def test_a_recorded_leg_that_reports_nothing_is_red_by_name(
    run_leg: RunsLeg,
) -> None:
    """The teardown stopped collecting: the leg is named, never dropped."""
    leg = run_leg(
        TWO,
        legs="python python-branches plpgsql plpgsql-branches",
        files={"helper.py": "print('hi')\n"},
        teardown=Teardown(collects=False),
    )
    done = leg.done
    assert done.returncode == 1
    assert (
        "coverage:check: the floor was derived with plpgsql and this run"
        " measured none\n" + plpgsql_remedy("plpgsql")
    ) in done.stderr
    assert (
        "coverage:check: the floor was derived with plpgsql-branches and"
        " this run measured none\n" + plpgsql_remedy("plpgsql-branches")
    ) in done.stderr


def test_two_databases_leave_the_branch_floor_as_it_stands(
    run_leg: RunsLeg,
) -> None:
    """Not held over two databases: named, and the floor file unchanged."""
    leg = run_leg(
        HELD,
        legs="python python-branches plpgsql plpgsql-branches",
        files={"helper.py": "print('hi')\n"},
        teardown=Teardown(databases=2),
    )
    done = leg.done
    assert done.returncode == 0, done.stderr
    assert_held(
        PYTHON_HELD + "coverage:check: plpgsql leg holds its floor 80.0 (95.00%)\n"
        "coverage:check: plpgsql-branches leg not held: this run cannot"
        " measure it exactly (the measurement says why); its floor 80.0"
        " stands\n",
        done.stdout,
    )
    assert (
        "coverage:check: plpgsql: plpgsql-branches: not held, 2 databases;"
        " plpgsql_check cannot merge branches across databases (#2169)\n"
    ) in done.stderr
    assert leg.planted == leg.floor.read_text(encoding="utf-8")


# The coverage.py stub the python leg's rows use, read from its suite.
COVERAGE = str(test_coverage_tasks.COVERAGE)

# `mise tasks info <name> --json`, for any task planted in STUB_TASKS.
MISE_TASKS = """\
#!/usr/bin/env bash
f="${STUB_TASKS}/$3.json"
if [[ "$1 $2" == "tasks info" && -f ${f} ]]; then
  cat "${f}"
  exit 0
fi
exit 1
"""


def counter(tree: Path) -> int:
    """Read how many times the counting script ran.

    Returns:
        The number of lines the script appended.

    """
    return len((tree.parent / "counter").read_text("utf-8").splitlines())


class RunsMeasure(Protocol):
    """Lay out and run one coverage-measure.sh; `run_measure` says how."""

    def __call__(
        self, selector: str = "all", *, python: bool = True, no_xml: bool = False
    ) -> subprocess.CompletedProcess[str]:
        """Plant the run, python and PL/pgSQL, then measure."""
        ...


@fixture
def run_measure(
    tmp_path: Path, env: support.Env, monkeypatch: pytest.MonkeyPatch
) -> RunsMeasure:
    """Both legs read the one run `measured` left, and run no suite (#1667).

    Returns:
        A function that plants one run and returns the script that read it.

    """

    def laying_out(
        selector: str = "all",
        *,
        python: bool = True,
        no_xml: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Lay out coverage-measure.sh over a planted run, python and PL/pgSQL.

        One run per row, in the row's own tmp_path: reports go to
        `reports` there, beside the tree (#1628); `no_xml` has the python
        leg write none.

        Returns:
            The finished script.

        """
        tmp = tmp_path
        stubs, answers = plant_stubs(tmp, TWO)
        for name, text in (("coverage", COVERAGE), ("mise", MISE_TASKS)):
            (stubs / name).write_text(text, encoding="utf-8")
            (stubs / name).chmod(0o755)
        (stubs / "python3").symlink_to(sys.executable)
        test_coverage_tasks.plant_pytest_venv(stubs)
        tree = tmp / "tree"
        tree.mkdir()
        defs = tmp / "tasks"
        defs.mkdir()
        suite: dict[str, object] = {"dir": str(tree), "run": ["echo suite ran >&2"]}
        (defs / "test.json").write_text(json.dumps(suite), "utf-8")
        tracked = ["schema.sql"]
        (tree / "schema.sql").write_text(f"CREATE FUNCTION f() {CLAUSE};\n", "utf-8")
        if python:
            (tree / "helper.py").write_text("print('hi')\n", encoding="utf-8")
            tracked.append("helper.py")
        environ = stub_env(
            stubs,
            answers,
            STUB_TASKS=str(defs),
            ORG_COVERAGE_DISCOVER="1",
            STUB_PYTHON=sys.executable,
            STUB_PY="90.0",
            STUB_BR="85.0",
            STUB_NO_XML="1" if no_xml else "",
            ORG_COVERAGE_REPORTS=str(tmp / "reports"),
            ORG_COVERAGE_RUN=str(tmp / "run"),
            env=env,
        )
        run([GIT, "init", "-q"], environ, tree)
        run([GIT, "add", *tracked], environ, tree)
        measured_run(
            tmp,
            environ,
            python=python,
            teardown=COLLECTS_ONCE,
            monkeypatch=monkeypatch,
        )
        script = str(BELT / "coverage-measure.sh")
        return run([BASH, script, "leg", selector], environ, tree)

    return laying_out


def test_both_legs_read_one_run_under_all(
    run_measure: RunsMeasure,
) -> None:
    """Python and PL/pgSQL print their figures, and no suite runs."""
    done = run_measure()
    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        "python 90.0\npython-branches 85.0\nplpgsql 60.00\nplpgsql-branches 66.66\n"
    )
    assert "suite ran" not in done.stderr


def test_plpgsql_alone_reads_only_its_report(
    run_measure: RunsMeasure,
) -> None:
    """The plpgsql selector prints its own figures and no python one."""
    done = run_measure("plpgsql")
    assert done.returncode == 0, done.stderr
    assert done.stdout == "plpgsql 60.00\nplpgsql-branches 66.66\n"


def test_only_formatless_legs_pass_by_name(
    run_measure: RunsMeasure,
) -> None:
    """No report is owed where no leg measured has a format."""
    done = run_measure("reports", python=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        "plpgsql 60.00\nplpgsql-branches 66.66\n"
        "leg: upload none — every leg measured has no upload format"
        " (plpgsql, plpgsql-branches)\n"
    )
    assert (
        "leg: plpgsql, plpgsql-branches: no upload format,"
        " not counted as a report writer\n"
    ) in done.stderr


def test_a_leg_with_a_format_that_wrote_nothing_is_refused(
    run_measure: RunsMeasure,
) -> None:
    """A formatless leg beside it does not excuse the missing report."""
    done = run_measure("reports", no_xml=True)
    assert done.returncode == 1
    assert not done.stdout
    assert (
        "leg: the legs measured wrote no report — nothing for the badge to upload\n"
    ) in done.stderr


def test_a_formatless_leg_beside_a_written_report(
    run_measure: RunsMeasure, tmp_path: Path
) -> None:
    """The written report uploads; the formatless legs are named."""
    done = run_measure("reports")
    assert done.returncode == 0, done.stderr
    assert done.stdout.endswith(
        f"leg: upload {tmp_path / 'reports' / 'coverage.xml'}\n"
    ), done.stdout
    assert (
        "leg: plpgsql, plpgsql-branches: no upload format,"
        " not counted as a report writer\n"
    ) in done.stderr


NOT_HELD_LINE = (
    "leg: plpgsql: plpgsql-branches: not held, {n} databases; plpgsql_check"
    " cannot merge branches across databases (#2169)\n"
)


def plant_ran(answers: Path, ran: dict[str, str]) -> None:
    """Plant the statement ids one database ran, by oid, replacing the last."""
    for oid in TWO:
        (answers / f"ran.{oid}").unlink(missing_ok=True)
    for oid, ids in ran.items():
        (answers / f"ran.{oid}").write_text(ids, encoding="utf-8")


@fixture
def merging(plant: Callable[[Database], Planted]) -> Planted:
    """Plant the stub database each of several teardowns collects from (#2163).

    Returns:
        The planted database; no report is written yet.

    """
    return plant(Database(TWO))


def test_each_collect_writes_a_report_of_its_own(merging: Planted) -> None:
    """Two teardowns, two files: neither replaces the other."""
    assert pc.main(COLLECT) == 0
    assert pc.main(COLLECT) == 0
    written = sorted(p.name for p in merging.reports.iterdir())
    assert len(written) == COLLECTS, written
    for name in written:
        assert re.search(r"^database-.+\.json$", name)


def test_statements_merge_as_the_union_of_what_ran(
    merging: Planted, capfd: pytest.CaptureFixture[str]
) -> None:
    """Half of stamp's statements in each database: all ten, merged."""
    plant_ran(merging.answers, {"102": "1\n2\n3\n4\n5\n"})
    assert pc.main(COLLECT) == 0
    plant_ran(merging.answers, {"102": "6\n7\n8\n9\n10\n"})
    assert pc.main(COLLECT) == 0
    status = pc.main(["totals", "leg", str(merging.reports)])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == "plpgsql 93.33\nplpgsql-branches not-held\n"
    assert (
        "leg: plpgsql: public.guard(): statements 4 of 5 (80.00%),"
        " branches at least 100.00% (the highest one database read)\n"
        "leg: plpgsql: public.stamp(integer): statements 10 of 10"
        " (100.00%), branches at least 50.00% (the highest one database"
        " read)\n"
        "leg: plpgsql: total statements 14 of 15 (93.33%) across 2"
        " databases, branches not held; at least 66.66%"
        " statement-weighted, a lower bound from the highest fraction"
        " one database read per function\n" + NOT_HELD_LINE.format(n=2)
    ) == err


# Each row: the database, the answers planted in it, how many
# teardowns collect from it, then what `totals` over their reports says.
MERGED = [
    Merged(
        "no branch anywhere prints no branch leg over two",
        Database({"103": TOUCH}),
        {},
        2,
        0,
        "plpgsql 100.00\n",
        TOUCH_LINE.format(label="leg")
        + "leg: plpgsql: total statements 2 of 2 (100.00%) across 2"
        " databases, branches none: no function has a branch\n",
    ),
    Merged(
        "a function no database ran is red by name",
        Database({"101": TWO["101"], "104": ("public.idle()", "0\t0\t3\t0\t0")}),
        {},
        2,
        1,
        "",
        "leg: plpgsql: public.idle(): none of its 3 statements ran in any"
        " of the 2 databases measured — a function no test runs is red,"
        " whatever the total\n",
    ),
    Merged(
        "one database names a function it never ran",
        Database(TWO | {"102": ("public.stamp(integer)", "0\t0\t10\t0\t2")}),
        {},
        1,
        1,
        "",
        "leg: plpgsql: public.stamp(integer): none of its 10 statements ran"
        " in the database measured — a function no test runs is red,"
        " whatever the total\n",
    ),
    Merged(
        "refusals every database repeats are named once",
        Database(TWO, env={"STUB_PROFILER": "off"}),
        {},
        2,
        1,
        "",
        "leg: plpgsql: plpgsql_check.profiler is 'off' — with the profiler"
        " off every function reads 0%, which is not a measurement; start"
        " the test database with -c plpgsql_check.profiler=on\n",
    ),
    Merged(
        "statement ids psql printed wrong are unreadable",
        Database(TWO),
        {"ran.101": "1\nx\n"},
        1,
        1,
        "",
        "leg: plpgsql: plpgsql_check cannot read public.guard():"
        " unreadable statement ids: [['1'], ['x']]\n" + UNREADABLE,
    ),
]


@pytest.mark.parametrize("row", MERGED, ids=[row.name for row in MERGED])
def test_collects_then_totals(
    plant: Callable[[Database], Planted],
    capfd: pytest.CaptureFixture[str],
    row: Merged,
) -> None:
    """Each teardown collects; `totals` over every report says exactly this."""
    planted = plant(row.database)
    for name, text in row.answers.items():
        (planted.answers / name).write_text(text, encoding="utf-8")
    for _ in range(row.collects):
        collected = pc.main(COLLECT)
        assert (collected, *capfd.readouterr()) == (0, "", "")
    status = pc.main(["totals", "leg", str(planted.reports)])
    out, err = capfd.readouterr()
    assert (status, out, err) == (row.status, row.stdout, row.stderr)


def test_one_signature_with_two_bodies_is_red_by_name(
    merging: Planted, capfd: pytest.CaptureFixture[str]
) -> None:
    """Statement ids number one body; two cannot be merged by id."""
    assert pc.main(COLLECT) == 0
    (merging.answers / "source.101").write_text("BEGIN RETURN 2; END", "utf-8")
    assert pc.main(COLLECT) == 0
    status = pc.main(["totals", "leg", str(merging.reports)])
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        (
            "leg: plpgsql: public.guard(): its body differs between the"
            " databases merged — statement ids are merged over one body only;"
            " build every test database from the same schema\n"
        ),
    )


def test_a_function_one_database_ran_is_covered(
    merging: Planted, capfd: pytest.CaptureFixture[str]
) -> None:
    """Run in one database and absent from the other: merged, green."""
    assert pc.main(COLLECT) == 0
    (merging.answers / "functions").write_text(
        "101\tpublic.guard()\t\n", encoding="utf-8"
    )
    assert pc.main(COLLECT) == 0
    status = pc.main(["totals", "leg", str(merging.reports)])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == "plpgsql 60.00\nplpgsql-branches not-held\n"


def test_a_report_that_is_not_one_is_named_by_its_file(
    merging: Planted, capfd: pytest.CaptureFixture[str]
) -> None:
    """Among several, the bad file is named."""
    assert pc.main(COLLECT) == 0
    (merging.reports / "database-planted.json").write_text("[", encoding="utf-8")
    status = pc.main(["totals", "leg", str(merging.reports)])
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        (
            "leg: plpgsql: the report database-planted.json is not a coverage"
            " report: not JSON (Expecting value: line 1 column 2 (char 1))\n"
        ),
    )


def test_an_empty_directory_is_no_report(
    merging: Planted, capfd: pytest.CaptureFixture[str]
) -> None:
    """A directory no teardown wrote into holds no measurement."""
    merging.reports.mkdir()
    status = pc.main(["totals", "leg", str(merging.reports)])
    out, err = capfd.readouterr()
    assert (status, out, err) == (1, "", NO_REPORT)


def test_a_report_path_that_is_a_file_is_refused_at_collect(
    merging: Planted,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The variable names a directory; a file there is not replaced."""
    merging.reports.write_text("kept\n", encoding="utf-8")
    status = pc.main(COLLECT)
    out, err = capfd.readouterr()
    refused = (
        f"{TASK}: ORG_PLPGSQL_COVERAGE names {merging.reports}, which is not a"
        " directory of reports\n"
    )
    assert (status, out, err) == (1, "", refused)
    assert merging.reports.read_text(encoding="utf-8") == "kept\n"


# The database the real rows start (#2163): the org's image, digest-pinned.
IMAGE_PIN = CANON / "docker" / "plpgsql-coverage-image.toml"
# The image's own preloads, then the extension's, and the profiler on.
POSTGRES = (
    "postgres",
    "-c",
    "shared_preload_libraries=pg_stat_statements,pgaudit,plpgsql,plpgsql_check",
    "-c",
    "plpgsql_check.profiler=on",
)
# Seconds the database may take to start, once the image is pulled.
READY = 120

# The fixture schema every real database is built from: one function
# with two branches, clean under every static check plpgsql_check has.
FIXTURE = """\
CREATE FUNCTION public.sign(n integer) RETURNS integer
  LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  IF n > 0 THEN
    RETURN 1;
  ELSE
    RETURN -1;
  END IF;
END
$$;
"""
# A second function, for the database set that never runs it.
IDLE = """\
CREATE FUNCTION public.idle() RETURNS integer
  LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  RETURN 0;
END
$$;
"""


def pinned_image() -> str:
    """Read the image the real rows run on.

    Returns:
        The image reference, tag and digest.

    """
    pin = as_object(load_toml(IMAGE_PIN.read_text(encoding="utf-8")), "the pin")
    return as_str(pin["image"], "the pin's image")


# The leg on a real Postgres with plpgsql_check (#2163).
#
# One container from the digest-pinned org image serves every row;
# each row makes databases of its own, so no row reads another's
# profile (plpgsql_check keys its profile by database).


@dataclass(frozen=True)
class Server:
    """The container a module's real-database rows share."""

    docker: str
    container: str
    make_env: support.Env

    def environment(self) -> dict[str, str]:
        """Build the environment the container's clients run in.

        Returns:
            The `env` fixture's, with docker reachable.

        """
        return self.make_env(tools=("docker",), MISE_TASK_NAME=TASK)

    def client(self) -> list[str]:
        """Name the client a container-hosted suite's teardown names (#2114).

        Returns:
            The client's words.

        """
        return [
            self.docker,
            *("exec", "-i", self.container, "psql"),
            *("-h", "127.0.0.1", "-U", "postgres"),
        ]

    def sql(self, database: str, text: str) -> str:
        """Run SQL in one database, as the suite does.

        Returns:
            What psql printed.

        """
        done = subprocess.run(
            [
                *self.client(),
                *("-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1"),
                *("-d", database),
            ],
            input=text,
            env=self.environment(),
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout

    def database(self, *schema: str) -> str:
        """Make a database of the row's from the fixture schema.

        Returns:
            Its name.

        """
        name = f"row_{os.urandom(6).hex()}"
        self.sql("postgres", f"CREATE DATABASE {name};")
        self.sql(name, "".join(schema or (FIXTURE,)))
        return name

    def ready(self) -> None:
        """Wait until the database takes connections over TCP.

        Raises:
            AssertionError: it took no connection in time.

        """
        # Over TCP: the image's first start serves its socket only while
        # it initialises, then restarts; the TCP listener is the last one.
        for _ in range(READY * 2):
            ready = subprocess.run(
                [
                    self.docker,
                    "exec",
                    self.container,
                    "pg_isready",
                    "-q",
                    *("-h", "127.0.0.1", "-U", "postgres"),
                ],
                env=self.environment(),
                capture_output=True,
                check=False,
            )
            if ready.returncode == 0:
                return
            time.sleep(0.5)
        msg = f"the database took no connection within {READY} s"
        raise AssertionError(msg)

    def remove(self) -> None:
        """Remove the container and its databases with it."""
        subprocess.run(
            [self.docker, "rm", "--force", self.container],
            env=self.environment(),
            capture_output=True,
            check=False,
        )


@fixture(scope="module")
def server(env: support.Env) -> Iterator[Server]:
    """Start the database, wait until it takes connections, remove it after.

    Yields:
        The server, once it takes connections.

    Raises:
        AssertionError: it did not start, or took no connection in
            time; docker's own words are the message.

    """
    docker = support.tool("docker")
    container = f"plpgsql-coverage-{os.getpid()}-{os.urandom(4).hex()}"
    environ = env(tools=("docker",), MISE_TASK_NAME=TASK)
    started = subprocess.run(
        [
            docker,
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--env",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            pinned_image(),
            *POSTGRES,
        ],
        env=environ,
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        msg = f"the database did not start: {started.stderr}"
        raise AssertionError(msg)
    up = Server(docker, container, env)
    try:
        up.ready()
        yield up
    finally:
        up.remove()


@fixture
def served(server: Server, monkeypatch: pytest.MonkeyPatch) -> Server:
    """Run the row as the teardown does, against the real database.

    Returns:
        The server.

    """
    as_the_child(monkeypatch, server.environment(), CANON)
    return server


def teardown(server: Server, database: str) -> list[str]:
    """Name the teardown's collect from one real database.

    Returns:
        The argv.

    """
    return ["collect", f"dbname={database}", "--", *server.client()]


def two_databases(server: Server) -> tuple[str, str]:
    """Run a different branch of sign in each of two databases.

    Returns:
        The two databases.

    """
    ahead, behind = server.database(), server.database()
    server.sql(ahead, "SELECT public.sign(1);")
    server.sql(behind, "SELECT public.sign(-1);")
    return ahead, behind


def test_two_databases_merge_to_one_database_running_both(
    served: Server,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Each branch in its own database reads as one database running both."""
    both = served.database()
    served.sql(both, "SELECT public.sign(1); SELECT public.sign(-1);")
    alone, two = tmp_path / "alone", tmp_path / "two"
    monkeypatch.setenv("ORG_PLPGSQL_COVERAGE", str(alone))
    assert pc.main(teardown(served, both)) == 0
    monkeypatch.setenv("ORG_PLPGSQL_COVERAGE", str(two))
    for database in two_databases(served):
        assert pc.main(teardown(served, database)) == 0
    capfd.readouterr()
    single = pc.main(["totals", "leg", str(alone)])
    single_out, single_err = capfd.readouterr()
    merged = pc.main(["totals", "leg", str(two)])
    out, err = capfd.readouterr()
    assert (single, merged) == (0, 0), single_err + err
    assert single_out.splitlines()[0] == out.splitlines()[0] == "plpgsql 100.00"
    assert (
        "leg: plpgsql: sign(integer): statements 4 of 4 (100.00%),"
        " branches at least 50.00% (the highest one database read)\n"
    ) in err


def test_two_databases_leave_the_branch_floor_untouched(
    served: Server,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Not held, by name and count; coverage:check changes no floor line."""
    reports = tmp_path / "reports"
    monkeypatch.setenv("ORG_PLPGSQL_COVERAGE", str(reports))
    for database in two_databases(served):
        assert pc.main(teardown(served, database)) == 0
    status = pc.main(["totals", "leg", str(reports)])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == "plpgsql 100.00\nplpgsql-branches not-held\n"
    assert NOT_HELD_LINE.format(n=2) in err
    floor = tmp_path / ".coverage-floor"
    # Each leg on its own line (#2143): the branch line apart from the
    # statement one, so the row shows which line stands.
    text = cf.render(
        {
            "plpgsql": cf.fresh(Decimal("82.0")),
            "plpgsql-branches": cf.fresh(Decimal("60.0")),
        },
        "v1.0.0",
    )
    floor.write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(out))
    status = cf.main(["hold", "--label", "coverage:check", "--job", "gate"])
    out, err = capfd.readouterr()
    assert (status, err) == (0, "")
    assert out == (
        "coverage:check: plpgsql leg holds its floor 80.0 (100.00%)\n"
        "coverage:check: plpgsql-branches leg not held: this run cannot"
        " measure it exactly (the measurement says why); its floor 58.0"
        " stands\n"
    )
    assert text == floor.read_text(encoding="utf-8")


def test_one_database_holds_the_branch_leg_as_now(
    served: Server,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """One report: plpgsql_check's own branch figure, held."""
    one = served.database()
    served.sql(one, "SELECT public.sign(1);")
    own = served.sql(
        one,
        "CREATE EXTENSION IF NOT EXISTS plpgsql_check;"
        " SELECT plpgsql_coverage_branches('public.sign(integer)');",
    )
    reports = tmp_path / "reports"
    monkeypatch.setenv("ORG_PLPGSQL_COVERAGE", str(reports))
    assert pc.main(teardown(served, one)) == 0
    status = pc.main(["totals", "leg", str(reports)])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert own == "0.5\n"
    assert out == "plpgsql 75.00\nplpgsql-branches 50.00\n"
    assert "not held" not in err


def test_two_concurrent_collects_leave_two_reports(
    served: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Teardowns at the same moment write a report each."""
    ahead, behind = served.database(), served.database()
    reports = tmp_path / "reports"
    monkeypatch.setenv("ORG_PLPGSQL_COVERAGE", str(reports))
    client = tuple(served.client())
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(
            pool.map(
                lambda name: pc.collect(f"dbname={name}", client),
                (ahead, behind),
            )
        )
    assert statuses == [0, 0]
    assert len(list(reports.glob("database-*.json"))) == DATABASES


def test_a_function_no_database_runs_is_red_by_name(
    served: Server,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Idle is in both databases and run in neither.

    regprocedure names a function on the search path without its
    schema, as the listing prints it.
    """
    ahead = served.database(FIXTURE, IDLE)
    behind = served.database(FIXTURE, IDLE)
    served.sql(ahead, "SELECT public.sign(1);")
    served.sql(behind, "SELECT public.sign(-1);")
    reports = tmp_path / "reports"
    monkeypatch.setenv("ORG_PLPGSQL_COVERAGE", str(reports))
    for database in (ahead, behind):
        assert pc.main(teardown(served, database)) == 0
    status = pc.main(["totals", "leg", str(reports)])
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        (
            "leg: plpgsql: idle(): none of its 2 statements ran in any"
            " of the 2 databases measured — a function no test runs is red,"
            " whatever the total\n"
        ),
    )
