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

pytest, run by `mise run test`.py."""

import base64
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, override

import pytest

from belt import coverage_floor as cf
from belt import plpgsql_coverage as pc
from belt.decoded import as_array, as_object, as_str, load_json, load_toml
from tests import hermetic, test_coverage_tasks

if TYPE_CHECKING:
    from collections.abc import Callable

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "leg"


@pytest.fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


CANON = hermetic.CANON
BELT = CANON / "mise"
HELPER = BELT / "plpgsql_coverage.py"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
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


def stub_env(stubs: Path, answers: Path, **planted: str) -> dict[str, str]:
    """Build a row's environment: the stubs first on PATH.

    Returns:
        The environment.

    """
    env = hermetic.env(MISE_TASK_NAME=TASK)
    return (
        env
        | {
            "PATH": f"{stubs}{os.pathsep}{env.get('PATH', '')}",
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


@dataclass(frozen=True)
class Done:
    """What an in-process call to the helper returned and wrote."""

    returncode: int
    stdout: str
    stderr: str


def call(argv: list[str], env: dict[str, str], cwd: Path) -> Done:
    """Call the helper's `main` in this process (#1711).

    The environment a script row handed the child is this process's for
    the call (`mock.patch.dict`, cleared and restored), so the helper
    starts the stub psql it finds on that PATH, as the child did.

    Returns:
        The status `main` returned and what it wrote to each stream.

    """
    out, err = io.StringIO(), io.StringIO()
    with (
        pytest.MonkeyPatch.context() as patched,
        contextlib.chdir(cwd),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        hermetic.patch_env(patched, env, clear=True)
        status = pc.main(argv)
    return Done(status, out.getvalue(), err.getvalue())


def measured_run(  # ruff: ignore[too-many-arguments] -- one keyword per part of a run
    run_dir: Path,
    env: dict[str, str],
    tree: Path,
    *,
    python: bool,
    collects: bool,
    databases: int = 1,
) -> None:
    """Leave a run's directory as a suite run through `measured` does (#1667).

    `python` plants the rc file and coverage.py's data the stub
    `coverage` reads; `collects` runs the suite's teardown, the helper
    called in-process (#1711), with the ORG_PLPGSQL_COVERAGE `measured`
    names in the run, once for each of `databases` (#2163).

    Raises:
        AssertionError: the teardown failed; its output is the message.

    """
    run_dir.mkdir(parents=True)
    if python:
        (run_dir / "python").mkdir()
        (run_dir / "coverage.rc").write_text("[run]\n", encoding="utf-8")
        (run_dir / "python" / ".coverage.stub").write_text("", encoding="utf-8")
    for _ in range(databases if collects else 0):
        report = {"ORG_PLPGSQL_COVERAGE": str(run_dir / "plpgsql-coverage")}
        done = call(["collect", "postgres://stub"], env | report, tree)
        if done.returncode != 0:
            raise AssertionError(done.stderr)


@pytest.mark.usefixtures("_stack")
class TestCollecting:
    """Collects from a planted database and reads the report."""
    
    @pytest.fixture
    def _stack(self, request: pytest.FixtureRequest) -> None:
        """Hold the row's cleanups, undone last first after it ends."""
        self.stack = contextlib.ExitStack()
        request.addfinalizer(self.stack.close)

    # The words the call names after `--`; None names none (#2114).
    client: list[str] | None = None

    def measure(
        self,
        functions: dict[str, tuple[str, str]],
        unreadable: dict[str, str] | None = None,
        listing: str | None = None,
        lint: dict[str, str] | None = None,
        sources: dict[str, str] | None = None,
        **planted: str,
    ) -> tuple[Done, Done]:
        """Collect a report from the planted database and read it.

        `listing` replaces the function listing psql prints, verbatim;
        `lint` plants the static check's rows, keyed `<oid>.<relid>`.
        `self.client` is the words the call names after `--`.
        The answers directory and the report stay on the row as
        `self.answers` and `self.report`.

        Returns:
            The collect and the totals processes.

        """
        tmp = Path(tempfile.mkdtemp())
        self.stack.callback(shutil.rmtree, tmp, ignore_errors=True)
        stubs, answers = plant_stubs(tmp, functions, unreadable)
        if listing is not None:
            (answers / "functions").write_text(listing, encoding="utf-8")
        for key, rows in (lint or {}).items():
            (answers / f"lint.{key}").write_text(rows, encoding="utf-8")
        for oid, source in (sources or {}).items():
            (answers / f"source.{oid}").write_text(source, encoding="utf-8")
        reports = tmp / "reports"
        env = stub_env(stubs, answers, ORG_PLPGSQL_COVERAGE=str(reports), **planted)
        named = [] if self.client is None else ["--", *self.client]
        collected = call(["collect", "postgres://stub", *named], env, tmp)
        read = call(["totals", "leg", str(reports)], env, tmp)
        # The one report the collection wrote, for the rows that read it.
        written = sorted(reports.glob("*.json")) if reports.is_dir() else []
        self.answers = answers
        self.report = written[0] if len(written) == 1 else reports
        return collected, read


@pytest.mark.usefixtures("_stack")
class TestHelperRows(TestCollecting):
    """`collect` against a stub psql, then `totals` over its report."""
    
    @pytest.fixture
    def _stack(self, request: pytest.FixtureRequest) -> None:
        """Hold the row's cleanups, undone last first after it ends."""
        self.stack = contextlib.ExitStack()
        request.addfinalizer(self.stack.close)

    def test_two_functions_weighted_into_the_totals(self) -> None:
        """Each figure is weighted by its function's statement count."""
        collected, read = self.measure(TWO)
        assert collected.returncode == 0, collected.stderr
        assert read.returncode == 0, read.stderr
        assert read.stdout == "plpgsql 60.00\nplpgsql-branches 66.66\n"
        assert TWO_REPORT.format(label="leg") == read.stderr

    def test_a_branch_free_function_is_left_out_of_the_branch_total(self) -> None:
        """It counts toward statements; the branch total is unchanged."""
        collected, read = self.measure(TWO | {"103": TOUCH})
        assert collected.returncode == 0, collected.stderr
        assert read.returncode == 0, read.stderr
        assert read.stdout == "plpgsql 64.70\nplpgsql-branches 66.66\n"
        assert (
            GUARD
            + STAMP
            + TOUCH_LINE
            + "{label}: plpgsql: total statements 11 of 17 (64.70%),"
            " branches 66.66% statement-weighted\n"
        ).format(label="leg") == read.stderr

    def test_no_branch_anywhere_prints_no_branch_leg(self) -> None:
        """A branch total over no branch would be a number about nothing."""
        collected, read = self.measure({"103": TOUCH})
        assert collected.returncode == 0, collected.stderr
        assert read.returncode == 0, read.stderr
        assert read.stdout == "plpgsql 100.00\n"
        assert (
            TOUCH_LINE.format(label="leg")
            + "leg: plpgsql: total statements 2 of 2 (100.00%), branches none:"
            " no function has a branch\n"
        ) == read.stderr

    def test_extension_members_are_not_listed(self) -> None:
        """An extension's own PL/pgSQL (postgis's) never enters the total."""
        collected, read = self.measure(TWO)
        assert collected.returncode == 0, collected.stderr
        assert "postgis_owned" not in read.stderr
        assert read.stdout == "plpgsql 60.00\nplpgsql-branches 66.66\n"

    def test_an_unreadable_function_is_listed_and_red(self) -> None:
        """The function stays in the report, named, and nothing is totalled."""
        collected, read = self.measure(
            TWO, unreadable={"102": "ERROR:  stub: cache lookup failed"}
        )
        assert collected.returncode == 0, collected.stderr
        assert read.returncode == 1
        assert read.stdout == ""
        assert read.stderr == (
            "leg: plpgsql: plpgsql_check cannot read public.stamp(integer):"
            " ERROR:  stub: cache lookup failed\n"
            "leg: plpgsql: 1 function(s) unreadable — a function the extension"
            " cannot read is never dropped from the total\n"
        )

    def test_an_oid_that_is_not_a_number_is_never_formatted(self) -> None:
        """A listing row whose oid is not digits is unreadable, by name."""
        odd = {"7; DROP TABLE t": ("public.odd()", "1\t1\t1\t1\t0")}
        collected, read = self.measure(odd)
        assert collected.returncode == 0, collected.stderr
        assert read.returncode == 1
        assert read.stderr == (
            "leg: plpgsql: plpgsql_check cannot read public.odd(): its oid"
            " '7; DROP TABLE t' is not a number\n"
            "leg: plpgsql: 1 function(s) unreadable — a function the extension"
            " cannot read is never dropped from the total\n"
        )

    def test_no_plpgsql_function_prints_no_totals(self) -> None:
        """A database with no PL/pgSQL function has nothing to total."""
        collected, read = self.measure({})
        assert collected.returncode == 0, collected.stderr
        assert read.returncode == 0, read.stderr
        assert read.stdout == ""
        assert read.stderr == "leg: plpgsql: the database holds no PL/pgSQL function\n"

    def test_the_profiler_off_is_red_by_name_never_zero(self) -> None:
        """Off reads 0% for every function; the setting is refused instead."""
        off = {"101": ("public.guard()", "0\t0\t5\t0\t1")}
        collected, read = self.measure(off, STUB_PROFILER="off")
        assert collected.returncode == 0, collected.stderr
        assert read.returncode == 1
        assert read.stdout == ""
        assert read.stderr == (
            "leg: plpgsql: plpgsql_check.profiler is 'off' — with the profiler"
            " off every function reads 0%, which is not a measurement; start"
            " the test database with -c plpgsql_check.profiler=on\n"
        )

    def test_a_profiler_not_preloaded_is_red_by_name(self) -> None:
        """A session-local profile cannot reach the teardown's session."""
        collected, read = self.measure(TWO, STUB_PRELOAD="pg_stat_statements")
        assert collected.returncode == 0, collected.stderr
        assert read.returncode == 1
        assert read.stderr == (
            "leg: plpgsql: plpgsql_check is not in shared_preload_libraries"
            " ('pg_stat_statements') — the profile is per session and the"
            " teardown sees none of the suite's; preload plpgsql,plpgsql_check\n"
        )

    def test_a_missing_report_names_the_teardown(self) -> None:
        """A suite whose teardown never called collect is refused."""
        tmp = Path(tempfile.mkdtemp())
        self.stack.callback(shutil.rmtree, tmp, ignore_errors=True)
        read = call(
            ["totals", "leg", str(tmp / "none.json")],
            hermetic.env(MISE_TASK_NAME=TASK),
            tmp,
        )
        assert read.returncode == 1
        assert read.stderr == (
            "leg: plpgsql: the test task wrote no report — its teardown must"
            " call belt plpgsql-coverage collect while ORG_PLPGSQL_COVERAGE is set\n"
        )

    def test_a_file_that_is_not_a_report_is_refused_by_what(self) -> None:
        """The one decoder names what about the file is wrong."""
        tmp = Path(tempfile.mkdtemp())
        self.stack.callback(shutil.rmtree, tmp, ignore_errors=True)
        planted = tmp / "report.json"
        entry = {
            "function": "public.guard()",
            "statements": 0.8,
            "branches": "1",
            "statement_count": 5,
            "executed": 4,
            "branch_free": False,
            "findings": [],
            "suppressions": [],
        }
        document = {"profiler": "on", "preload": PRELOAD, "functions": [entry]}
        planted.write_text(json.dumps(document), encoding="utf-8")
        read = call(
            ["totals", "leg", str(planted)],
            hermetic.env(MISE_TASK_NAME=TASK),
            tmp,
        )
        assert read.returncode == 1
        assert read.stdout == ""
        assert read.stderr == (
            "leg: plpgsql: the report is not a coverage report: function 0:"
            " 'statements' is not a string\n"
        )

    def test_collect_without_a_report_path_is_refused(self) -> None:
        """The teardown calls collect only when the leg set the path."""
        tmp = Path(tempfile.mkdtemp())
        self.stack.callback(shutil.rmtree, tmp, ignore_errors=True)
        stubs, answers = plant_stubs(tmp, TWO)
        done = call(
            ["collect", "postgres://stub"],
            stub_env(stubs, answers),
            tmp,
        )
        assert done.returncode == 1
        assert (
            f"{TASK}: ORG_PLPGSQL_COVERAGE is not set — the leg sets"
            " it; call collect only then\n"
        ) == done.stderr


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


@pytest.mark.usefixtures("_stack")
class TestLintRows(TestCollecting):
    """plpgsql_check's static check, in the same report (#1605)."""
    
    @pytest.fixture
    def _stack(self, request: pytest.FixtureRequest) -> None:
        """Hold the row's cleanups, undone last first after it ends."""
        self.stack = contextlib.ExitStack()
        request.addfinalizer(self.stack.close)

    def test_a_report_with_no_findings_is_green(self) -> None:
        """Every function asked, none reported: the totals print."""
        collected, read = self.measure(TWO)
        assert collected.returncode == 0, collected.stderr
        assert read.returncode == 0, read.stderr
        assert read.stdout == "plpgsql 60.00\nplpgsql-branches 66.66\n"
        document = as_object(load_json(self.report.read_text("utf-8")), "report")
        functions = as_array(document.get("functions"), "functions")
        findings = [as_object(f, "function").get("findings") for f in functions]
        none: list[object] = [[], []]
        assert none == findings
        assert (self.answers / "linted").read_text("utf-8") == "101 0\n102 0\n"

    def test_an_error_is_red_by_function_and_line(self) -> None:
        """A column that does not exist: the leg is red before the floor."""
        message = 'column "no_such_column" does not exist'
        _, read = self.measure(TWO, lint={"102.0": finding("2", "error", message)})
        assert read.returncode == 1
        assert read.stdout == ""
        assert (
            LINT_RED.format(
                function="public.stamp(integer)",
                line=2,
                level="error",
                message=message,
                count=1,
            )
            == read.stderr
        )

    def test_a_warning_is_red(self) -> None:
        """Unreachable code, an extra warning, is red like an error."""
        _, read = self.measure(
            TWO, lint={"101.0": finding("14", "warning extra", "unreachable code")}
        )
        assert read.returncode == 1
        assert (
            LINT_RED.format(
                function="public.guard()",
                line=14,
                level="warning extra",
                message="unreachable code",
                count=1,
            )
            == read.stderr
        )

    def test_a_finding_with_no_line_is_named_without_one(self) -> None:
        """A routine-wide finding (volatility) has no line."""
        message = "routine is marked as VOLATILE, should be IMMUTABLE"
        _, read = self.measure(TWO, lint={"101.0": finding("", "performance", message)})
        assert read.returncode == 1
        assert (
            LINT_RED.format(
                function="public.guard()",
                line="-",
                level="performance",
                message=message,
                count=1,
            )
            == read.stderr
        )

    def test_a_trigger_function_is_checked_against_each_table(self) -> None:
        """Its NEW and OLD are each table's rows; a shared finding is one."""
        listing = "101\tpublic.guard()\t5001 5002\n102\tpublic.stamp(integer)\t\n"
        row = finding("14", "warning extra", "unreachable code")
        _, read = self.measure(
            TWO, listing=listing, lint={"101.5001": row, "101.5002": row}
        )
        assert (self.answers / "linted").read_text(
            "utf-8"
        ) == "101 5001\n101 5002\n102 0\n"
        assert (
            LINT_RED.format(
                function="public.guard()",
                line=14,
                level="warning extra",
                message="unreachable code",
                count=1,
            )
            == read.stderr
        )

    def test_an_extension_member_is_never_asked(self) -> None:
        """An extension's PL/pgSQL (postgis's) is not the repository's to check."""
        self.measure(TWO)
        assert "900" not in (self.answers / "linted").read_text("utf-8")

    def test_the_extension_absent_is_refused_by_name(self) -> None:
        """No plpgsql_check in the test stack: the collection fails, named."""
        why = 'ERROR:  extension "plpgsql_check" is not available'
        collected, read = self.measure(TWO, STUB_NO_EXTENSION=why)
        assert collected.returncode == 0, collected.stderr
        assert read.returncode == 1
        assert f"leg: plpgsql: the teardown's collection failed: {why}\n" == read.stderr

    def test_a_reading_without_its_lint_answer_is_refused(self) -> None:
        """A report from a helper that did not ask is not a clean result."""
        tmp = Path(tempfile.mkdtemp())
        self.stack.callback(shutil.rmtree, tmp, ignore_errors=True)
        entry = {
            "function": "public.guard()",
            "statements": "0.8",
            "branches": "1",
            "statement_count": 5,
            "executed": 4,
            "branch_free": False,
        }
        planted = tmp / "report.json"
        document = {"profiler": "on", "preload": PRELOAD, "functions": [entry]}
        planted.write_text(json.dumps(document), encoding="utf-8")
        read = call(
            ["totals", "leg", str(planted)],
            hermetic.env(MISE_TASK_NAME=TASK),
            tmp,
        )
        assert read.returncode == 1
        assert read.stderr == (
            "leg: plpgsql: the report is not a coverage report: function 0:"
            " 'findings' is not a list\n"
        )


REASON = "-- the RAISE ends the guard, so the RETURN after it never runs"
OPTIONS_OFF = "-- @plpgsql_check_options: extra_warnings = false"
BODY = "\nBEGIN\n  RAISE EXCEPTION 'no';\n  RETURN NULL;\nEND\n"


class TestSuppressionRows(TestCollecting):
    """The extension's own option, its reason read from the source (#1605)."""

    def test_a_suppression_with_its_reason_is_green_and_printed(self) -> None:
        """Every suppression is named on every run, with why."""
        source = f"\n{REASON}\n{OPTIONS_OFF}{BODY}"
        _, read = self.measure(TWO, sources={"101": source})
        assert read.returncode == 0, read.stderr
        assert (
            "leg: plpgsql: public.guard(): line 3: suppresses"
            " `extra_warnings = false` — the RAISE ends the guard, so the"
            " RETURN after it never runs\n"
        ) in read.stderr
        assert read.stdout == "plpgsql 60.00\nplpgsql-branches 66.66\n"

    @pytest.mark.parametrize(
        "name,above,line",
        (
            ("nothing above", "", 1),
            ("an empty comment", "--", 2),
            (
                "another options line",
                "-- @plpgsql_check_options: fatal_errors = true",
                2,
            ),
        ),
    )
    def test_a_suppression_without_a_reason_is_red(
        self, name: object, above: object, line: object
    ) -> None:
        """No comment above, an empty one, or another options line."""
        source = (f"{above}\n" if above else "") + OPTIONS_OFF + BODY
        _, read = self.measure(TWO, sources={"101": source})
        assert read.returncode == 1
        assert read.stdout == ""
        assert (
            f"leg: plpgsql: public.guard(): line {line}: suppression"
            " `extra_warnings = false` has no reason — the comment line"
            " directly above it says why\n"
        ) == read.stderr

    def test_an_options_line_that_only_turns_checks_on_is_no_suppression(
        self,
    ) -> None:
        """Turning a check on needs no reason and is not listed."""
        source = "\n-- @plpgsql_check_options: performance_warnings = true" + BODY
        _, read = self.measure(TWO, sources={"101": source})
        assert read.returncode == 0, read.stderr
        assert "suppress" not in read.stderr

    def test_without_warnings_is_a_suppression(self) -> None:
        """Its `true` turns every warning off."""
        source = "\n-- @plpgsql_check_options: without_warnings = true" + BODY
        _, read = self.measure(TWO, sources={"101": source})
        assert read.returncode == 1
        assert "suppression `without_warnings = true` has no reason" in read.stderr


# A client the call names (#2114): logs the two words it was handed
# before the helper's own, then runs the stub psql with the rest.
CLIENT = """\
#!/usr/bin/env bash
printf '[%s][%s][%s]\\n' "$1" "$2" "$3" >> "${STUB_DIR}/client"
shift 2
exec "${STUB_PSQL}" "$@"
"""

OWN = "(the repository's own client)"
REMEDY = (
    "pass the client after --, for example:"
    " collect <conninfo> -- docker exec -i <container> psql"
)


@pytest.mark.usefixtures("_stack")
class TestClientRows(TestCollecting):
    """The client is psql on PATH unless the call names another."""
    
    @pytest.fixture
    def _stack(self, request: pytest.FixtureRequest) -> None:
        """Hold the row's cleanups, undone last first after it ends."""
        self.stack = contextlib.ExitStack()
        request.addfinalizer(self.stack.close)

    def named_client(self) -> list[str]:
        """Plant CLIENT outside PATH; the stub psql is out of PATH too.

        Returns:
            The client's path.

        """
        tmp = Path(tempfile.mkdtemp())
        self.stack.callback(shutil.rmtree, tmp, ignore_errors=True)
        client = tmp / "client"
        client.write_text(CLIENT, encoding="utf-8")
        client.chmod(0o755)
        psql = tmp / "psql"
        psql.write_text(PSQL, encoding="utf-8")
        psql.chmod(0o755)
        self.stub_psql = str(psql)
        return [str(client)]

    def test_no_client_named_runs_psql_on_path(self) -> None:
        """`collect <conninfo>`, with or without a bare `--`, runs psql."""
        clients: tuple[list[str] | None, ...] = (None, [])
        for client in clients:
            self.client = client
            collected, read = self.measure(TWO)
            assert collected.returncode == 0, collected.stderr
            assert read.returncode == 0, read.stderr
            assert (self.answers / "linted").is_file()

    def test_a_named_client_is_run_with_its_words_first(self) -> None:
        """The words run as an argv, unjoined, then the helper's own."""
        empty = Path(tempfile.mkdtemp())
        self.stack.callback(shutil.rmtree, empty, ignore_errors=True)
        self.client = [*self.named_client(), "a b", "c"]
        collected, read = self.measure(
            TWO,
            PATH=f"{empty}{os.pathsep}/usr/bin{os.pathsep}/bin",
            STUB_PSQL=self.stub_psql,
        )
        assert collected.returncode == 0, collected.stderr
        assert read.returncode == 0, read.stderr
        assert self.report.is_file()
        calls = (self.answers / "client").read_text(encoding="utf-8").splitlines()
        assert len(calls) > 3
        every: list[str] = ["[a b][c][-X]"] * len(calls)
        assert every == calls

    def test_a_client_that_cannot_start_names_the_remedy(self) -> None:
        """A named client that does not exist is refused, with the remedy."""
        missing = "/nonexistent/client"
        self.client = [missing, "exec"]
        collected, read = self.measure(TWO)
        assert collected.returncode == 0, collected.stderr
        assert read.returncode == 1
        assert (
            "leg: plpgsql: the teardown's collection failed:"
            f" {missing} is not on PATH {OWN}; {REMEDY}\n"
        ) == read.stderr


@pytest.mark.usefixtures("_stack")
class TestRefusalRows(TestCollecting):
    """Every way a collection or a report file is refused, by name."""
    
    @pytest.fixture
    def _stack(self, request: pytest.FixtureRequest) -> None:
        """Hold the row's cleanups, undone last first after it ends."""
        self.stack = contextlib.ExitStack()
        request.addfinalizer(self.stack.close)

    def collection_failed(self, read: Done, why: str) -> None:
        """Assert the leg refuses a failed collection, naming why."""
        assert read.returncode == 1
        assert read.stdout == ""
        assert f"leg: plpgsql: the teardown's collection failed: {why}\n" == read.stderr

    def test_no_psql_on_path_fails_the_collection_by_name(self) -> None:
        """The client is the repository's; without it nothing is read."""
        empty = Path(tempfile.mkdtemp())
        self.stack.callback(shutil.rmtree, empty, ignore_errors=True)
        collected, read = self.measure(TWO, PATH=str(empty))
        assert collected.returncode == 0, collected.stderr
        self.collection_failed(read, f"psql is not on PATH {OWN}; {REMEDY}")

    def test_a_database_psql_cannot_reach_fails_by_its_error(self) -> None:
        """The message psql printed is the reason, verbatim."""
        why = 'FATAL:  database "stub" does not exist'
        collected, read = self.measure(TWO, STUB_META_FAIL=why)
        assert collected.returncode == 0, collected.stderr
        self.collection_failed(read, why)

    def test_unreadable_settings_fail_the_collection(self) -> None:
        """Two settings or nothing: one field is not the profiler and preload."""
        collected, read = self.measure(TWO, STUB_META="on")
        assert collected.returncode == 0, collected.stderr
        self.collection_failed(read, "unreadable settings from the database: [['on']]")

    def test_an_unreadable_listing_row_fails_the_collection(self) -> None:
        """A listing row without its signature is not guessed at."""
        collected, read = self.measure(TWO, listing="101\n")
        assert collected.returncode == 0, collected.stderr
        self.collection_failed(read, "unreadable function listing: ['101']")

    def test_figures_that_are_not_numbers_are_unreadable(self) -> None:
        """A reading psql printed in the wrong shape is listed, red."""
        odd = {"101": ("public.guard()", "x\ty")}
        collected, read = self.measure(odd)
        assert collected.returncode == 0, collected.stderr
        assert read.returncode == 1
        assert read.stderr == (
            "leg: plpgsql: plpgsql_check cannot read public.guard():"
            " unreadable figures: [['x', 'y']]\n"
            "leg: plpgsql: 1 function(s) unreadable — a function the extension"
            " cannot read is never dropped from the total\n"
        )

    def test_a_failed_collection_in_the_file_is_refused(self) -> None:
        """The failure shape decodes, and the leg refuses with its reason."""
        self.collection_failed(self.read_planted({"error": "boom"}), "boom")

    def read_planted(self, document: object) -> Done:
        """Run `totals` over a planted report file.

        Returns:
            The finished process.

        """
        tmp = Path(tempfile.mkdtemp())
        self.stack.callback(shutil.rmtree, tmp, ignore_errors=True)
        planted = tmp / "report.json"
        text = document if isinstance(document, str) else json.dumps(document)
        planted.write_text(text, encoding="utf-8")
        return call(
            ["totals", "leg", str(planted)],
            hermetic.env(MISE_TASK_NAME=TASK),
            tmp,
        )

    def test_the_decoder_names_what_is_not_a_report(self) -> None:
        """Every shape the one decoder refuses, by what is wrong with it."""
        good = {
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
            return {"profiler": "on", "preload": PRELOAD, "functions": list(entries)}

        rows: list[tuple[object, str]] = [
            ("[", "not JSON (Expecting value: line 1 column 2 (char 1))"),
            ([], "the document is not an object"),
            ({"error": 3}, "the document: 'error' is not a string"),
            (
                {"profiler": "on", "preload": PRELOAD, "functions": {}},
                "the document: 'functions' is not a list",
            ),
            (
                {"preload": PRELOAD, "functions": []},
                "the document: 'profiler' is not a string",
            ),
            (report(1), "function 0 is not an object"),
            (
                report(good | {"branch_free": "no"}),
                "function 0: 'branch_free' is not a boolean",
            ),
            (
                report(good | {"statement_count": -1}),
                "function 0: 'statement_count' is not a count",
            ),
            (
                report(good | {"executed": True}),
                "function 0: 'executed' is not a count",
            ),
            (
                report(good | {"statements": "1.5"}),
                "function 0: 'statements' is not a fraction from 0 to 1",
            ),
            (
                report(good | {"branches": "abc"}),
                "function 0: 'branches' is not a fraction from 0 to 1",
            ),
            (
                report(good, good | {"statements": "NaN"}),
                "function 1: 'statements' is not a fraction from 0 to 1",
            ),
            (
                report({"function": "public.guard()", "error": 7}),
                "function 0: 'error' is not a string",
            ),
            (
                report({k: v for k, v in good.items() if k != "ran"}),
                "function 0: 'ran' is not a list of statement ids",
            ),
            (
                report(good | {"ran": [1, True, 3, 4]}),
                "function 0: 'ran' is not a list of statement ids",
            ),
            (
                report(good | {"ran": [2, 1, 3, 4]}),
                "function 0: 'ran' is not a list of statement ids",
            ),
            (
                report(good | {"ran": [0, 1, 2, 3]}),
                "function 0: 'ran' is not a list of statement ids",
            ),
            (
                report(good | {"ran": [1, 2, 3]}),
                (
                    "function 0: 'ran' does not name 'executed' statements"
                    " from 1 to 'statement_count'"
                ),
            ),
            (
                report(good | {"ran": [1, 2, 3, 6]}),
                (
                    "function 0: 'ran' does not name 'executed' statements"
                    " from 1 to 'statement_count'"
                ),
            ),
            (
                report({k: v for k, v in good.items() if k != "source"}),
                "function 0: 'source' is not a string",
            ),
        ]
        for document, what in rows:
            read = self.read_planted(document)
            assert read.returncode == 1
            assert read.stdout == ""
            assert (
                f"leg: plpgsql: the report is not a coverage report: {what}\n"
                == read.stderr
            )

    @pytest.mark.parametrize("argv", ([], ["measure", "leg"]))
    def test_a_usage_error_names_both_subcommands(self, argv: object) -> None:
        """No subcommand, or an unknown one, is exit 2 with the usage."""
        done = run(
            [sys.executable, str(HELPER), *argv],
            hermetic.env(MISE_TASK_NAME=TASK),
            CANON,
        )
        assert done.returncode == 2
        assert (
            f"{TASK}: usage: plpgsql-coverage collect <conninfo>"
            " [-- <client> [<argument>...]] | totals <label> <report>\n"
        ) == done.stderr


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


@pytest.mark.usefixtures("_stack")
class TestLegRows:
    """`coverage:check` over the plpgsql leg, decided by the floor's record."""
    
    @pytest.fixture
    def _stack(self, request: pytest.FixtureRequest) -> None:
        """Hold the row's cleanups, undone last first after it ends."""
        self.stack = contextlib.ExitStack()
        request.addfinalizer(self.stack.close)

    def check(  # ruff: ignore[too-many-arguments] -- one keyword per part of a run
        self,
        functions: dict[str, tuple[str, str]],
        *,
        legs: str,
        files: dict[str, str],
        collects: bool = True,
        measured: dict[str, str] | None = None,
        databases: int = 1,
    ) -> subprocess.CompletedProcess[str]:
        """Run coverage:check's body in a planted tree.

        `legs` is the floor's `legs:` record, each leg at 80.0 unless
        `measured` names the figure its floor was derived from (#2143),
        `files` the tracked files beside it, and `collects` whether the
        suite's teardown calls the helper, once per database of
        `databases` (#2163). A tracked `*.py` brings the python leg in,
        measured by the stub coverage.py. The floor file stays on the
        row as `self.floor`.

        Returns:
            The finished task.

        """
        tmp = Path(tempfile.mkdtemp())
        self.stack.callback(shutil.rmtree, tmp, ignore_errors=True)
        stubs, answers = plant_stubs(tmp, functions)
        (stubs / "coverage").write_text(COVERAGE, encoding="utf-8")
        (stubs / "coverage").chmod(0o755)
        (stubs / "python3").symlink_to(sys.executable)
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
        self.floor, self.planted = tree / ".coverage-floor", floor
        # The test task, for the read of it the python leg makes; the
        # check never runs it.
        task = tmp / "task.json"
        suite: dict[str, object] = {"dir": str(tree), "run": ["echo suite ran >&2"]}
        task.write_text(json.dumps(suite), "utf-8")
        env = stub_env(
            stubs,
            answers,
            STUB_TASK=str(task),
            STUB_PYTHON=sys.executable,
            STUB_PY="90.0",
            STUB_BR="85.0",
            ORG_COVERAGE_RUN=str(tmp / "run"),
        )
        run([GIT, "init", "-q"], env, tree)
        run([GIT, "add", *files, ".coverage-floor"], env, tree)
        measured_run(
            tmp / "run",
            env,
            tree,
            python=any(name.endswith(".py") for name in files),
            collects=collects,
            databases=databases,
        )
        check = as_object(TASKS["coverage:check"], "coverage:check")
        script = as_str(check["run"], "coverage:check's run")
        as_mise = hermetic.task_run("coverage:check", script)
        return run(as_mise.argv, env | as_mise.env, tree)

    PYTHON_HELD = (
        "coverage:check: python leg holds its floor 80.0 (90.0%)\n"
        "coverage:check: python-branches leg holds its floor 80.0 (85.0%)\n"
    )

    def assert_held(self, held: str, stdout: str) -> None:
        """Assert the hold lines, then the one upload: the Python report (#1628).

        The reports are written outside the tree, so the path is the
        measurement's scratch directory; its file name is the leg's.
        """
        head, _, upload = stdout.rstrip("\n").rpartition("\n")
        assert held == head + "\n"
        assert re.search(r"^coverage:check: upload /\S+/coverage\.xml$", upload)

    def test_the_clause_in_files_switches_nothing_on(self) -> None:
        """No search: a README, a .py and a .sql naming the clause (#1666)."""
        done = self.check(
            TWO,
            legs="python python-branches",
            files=MENTIONS,
            collects=False,
        )
        assert done.returncode == 0, done.stderr
        self.assert_held(self.PYTHON_HELD, done.stdout)
        assert "plpgsql" not in done.stdout + done.stderr

    def test_a_recorded_leg_is_held_with_no_sql_tracked(self) -> None:
        """The report is the answer: no .sql file is needed to find it."""
        done = self.check(
            HELD,
            legs="python python-branches plpgsql plpgsql-branches",
            files={"helper.py": "print('hi')\n"},
        )
        assert done.returncode == 0, done.stderr
        self.assert_held(
            self.PYTHON_HELD
            + "coverage:check: plpgsql leg holds its floor 80.0 (95.00%)\n"
            "coverage:check: plpgsql-branches leg holds its floor 80.0 (90.00%)\n",
            done.stdout,
        )

    def test_below_the_floor_is_red_by_name(self) -> None:
        """Statements at 60% red the plpgsql leg by its name."""
        done = self.check(
            TWO, legs="plpgsql plpgsql-branches", files={"schema.sql": "-- sql\n"}
        )
        assert done.returncode == 1
        assert (
            "coverage:check: plpgsql: 60.00% is below its derived floor 80.0%\n"
            in done.stderr
        )

    def test_a_leg_below_its_own_floor_above_a_weaker_leg_is_red(self) -> None:
        """plpgsql-branches at 88%: above python-branches, below its own 90.

        Every leg is held to a floor of its own (#2143). Held to the
        weakest leg's floor, the shape this replaced, the branch figure
        stood eight points above it and the gate was green.
        """
        done = self.check(
            STRONG,
            legs="python python-branches plpgsql plpgsql-branches",
            files={"helper.py": "print('hi')\n"},
            measured={"plpgsql": "95.0", "plpgsql-branches": "95.0"},
        )
        assert done.returncode == 1, done.stderr
        assert (
            done.stderr.splitlines()[-1]
            == "coverage:check: plpgsql-branches: 88.00% is below its derived floor 90%"
        )
        assert "python" not in done.stderr
        assert done.stdout == (
            self.PYTHON_HELD
            + "coverage:check: plpgsql leg holds its floor 90 (95.00%)\n"
        )

    def test_a_record_whose_every_leg_reports_nothing_names_them(self) -> None:
        """Every recorded leg lost: each named, never the generic refusal."""
        done = self.check(
            TWO,
            legs="python python-branches",
            files={"README.md": "no python left\n"},
            collects=False,
        )
        assert done.returncode == 1
        remedy = (
            "  the python leg needs tracked *.py and a `test` task whose test"
            ' command runs through "${{ORG_BELT_DIR}}/measured"; or record'
            " the leg's removal on purpose: `# dropped: {leg} <reason>` in"
            " .coverage-floor\n"
        )
        assert (
            "coverage:check: the floor was derived with python and this run"
            " measured none\n"
            + remedy.format(leg="python")
            + "coverage:check: the floor was derived with python-branches and"
            " this run measured none\n" + remedy.format(leg="python-branches")
        ) == done.stderr.removeprefix("suite ran\n")
        assert "no leg was measured" not in done.stderr
        assert done.stdout == ""

    def test_a_recorded_leg_that_reports_nothing_is_red_by_name(self) -> None:
        """The teardown stopped collecting: the leg is named, never dropped."""
        done = self.check(
            TWO,
            legs="python python-branches plpgsql plpgsql-branches",
            files={"helper.py": "print('hi')\n"},
            collects=False,
        )
        assert done.returncode == 1
        remedy = (
            "  the plpgsql leg needs a test command run through"
            ' "${{ORG_BELT_DIR}}/measured" and its teardown to call'
            " belt plpgsql-coverage collect while ORG_PLPGSQL_COVERAGE is set;"
            " or record the leg's removal on purpose:"
            " `# dropped: {leg} <reason>` in .coverage-floor\n"
        )
        assert (
            "coverage:check: the floor was derived with plpgsql and this run"
            " measured none\n" + remedy.format(leg="plpgsql")
        ) in done.stderr
        assert (
            "coverage:check: the floor was derived with plpgsql-branches and"
            " this run measured none\n" + remedy.format(leg="plpgsql-branches")
        ) in done.stderr

    def test_two_databases_leave_the_branch_floor_as_it_stands(self) -> None:
        """Not held over two databases: named, and the floor file unchanged."""
        done = self.check(
            HELD,
            legs="python python-branches plpgsql plpgsql-branches",
            files={"helper.py": "print('hi')\n"},
            databases=2,
        )
        assert done.returncode == 0, done.stderr
        self.assert_held(
            self.PYTHON_HELD
            + "coverage:check: plpgsql leg holds its floor 80.0 (95.00%)\n"
            "coverage:check: plpgsql-branches leg not held: this run cannot"
            " measure it exactly (the measurement says why); its floor 80.0"
            " stands\n",
            done.stdout,
        )
        assert (
            "coverage:check: plpgsql: plpgsql-branches: not held, 2 databases;"
            " plpgsql_check cannot merge branches across databases (#2169)\n"
        ) in done.stderr
        assert self.planted == self.floor.read_text(encoding="utf-8")


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


@pytest.mark.usefixtures("_stack")
class TestReadsTheRunRows:
    """Both legs read the one run `measured` left, and run no suite (#1667)."""
    
    @pytest.fixture
    def _stack(self, request: pytest.FixtureRequest) -> None:
        """Hold the row's cleanups, undone last first after it ends."""
        self.stack = contextlib.ExitStack()
        request.addfinalizer(self.stack.close)

    def measure(
        self,
        selector: str = "all",
        *,
        python: bool = True,
        no_xml: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        """Run coverage-measure.sh over a planted run, python and PL/pgSQL.

        Reports go to `reports` beside the tree (#1628); `no_xml` has the
        python leg write none.

        Returns:
            The finished script and the planted tree.

        """
        tmp = Path(tempfile.mkdtemp())
        self.stack.callback(shutil.rmtree, tmp, ignore_errors=True)
        stubs, answers = plant_stubs(tmp, TWO)
        for name, text in (("coverage", COVERAGE), ("mise", MISE_TASKS)):
            (stubs / name).write_text(text, encoding="utf-8")
            (stubs / name).chmod(0o755)
        (stubs / "python3").symlink_to(sys.executable)
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
        env = stub_env(
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
        )
        run([GIT, "init", "-q"], env, tree)
        run([GIT, "add", *tracked], env, tree)
        measured_run(tmp / "run", env, tree, python=python, collects=True)
        script = str(BELT / "coverage-measure.sh")
        return run([BASH, script, "leg", selector], env, tree), tree

    def test_both_legs_read_one_run_under_all(self) -> None:
        """Python and PL/pgSQL print their figures, and no suite runs."""
        done, _ = self.measure()
        assert done.returncode == 0, done.stderr
        assert done.stdout == (
            "python 90.0\npython-branches 85.0\nplpgsql 60.00\nplpgsql-branches 66.66\n"
        )
        assert "suite ran" not in done.stderr

    def test_plpgsql_alone_reads_only_its_report(self) -> None:
        """The plpgsql selector prints its own figures and no python one."""
        done, _ = self.measure("plpgsql")
        assert done.returncode == 0, done.stderr
        assert done.stdout == "plpgsql 60.00\nplpgsql-branches 66.66\n"

    def test_only_formatless_legs_pass_by_name(self) -> None:
        """No report is owed where no leg measured has a format."""
        done, _ = self.measure("reports", python=False)
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

    def test_a_leg_with_a_format_that_wrote_nothing_is_refused(self) -> None:
        """A formatless leg beside it does not excuse the missing report."""
        done, _ = self.measure("reports", no_xml=True)
        assert done.returncode == 1
        assert done.stdout == ""
        assert (
            "leg: the legs measured wrote no report — nothing for the badge to upload\n"
        ) in done.stderr

    def test_a_formatless_leg_beside_a_written_report(self) -> None:
        """The written report uploads; the formatless legs are named."""
        done, tree = self.measure("reports")
        assert done.returncode == 0, done.stderr
        assert done.stdout.endswith(
            f"leg: upload {tree.parent / 'reports' / 'coverage.xml'}\n"
        ), done.stdout
        assert (
            "leg: plpgsql, plpgsql-branches: no upload format,"
            " not counted as a report writer\n"
        ) in done.stderr


NOT_HELD_LINE = (
    "leg: plpgsql: plpgsql-branches: not held, {n} databases; plpgsql_check"
    " cannot merge branches across databases (#2169)\n"
)


@pytest.mark.usefixtures("_set_up_test_merge_rows")
class TestMergeRows:
    """Several databases' reports, merged by signature (#2163), on the stub."""

    @pytest.fixture
    def _set_up_test_merge_rows(self, tmp_path: Path) -> None:
        """Plant the stub database and an empty reports directory."""
        self.tmp = tmp_path
        self.stubs, self.answers = plant_stubs(self.tmp, TWO)
        self.reports = self.tmp / "reports"
        self.env = stub_env(
            self.stubs, self.answers, ORG_PLPGSQL_COVERAGE=str(self.reports)
        )

    def collect(self, ran: dict[str, str] | None = None) -> None:
        """Tear one database down: plant what it ran, then collect it."""
        for oid in TWO:
            (self.answers / f"ran.{oid}").unlink(missing_ok=True)
        for oid, ids in (ran or {}).items():
            (self.answers / f"ran.{oid}").write_text(ids, encoding="utf-8")
        done = call(["collect", "postgres://stub"], self.env, self.tmp)
        assert done.returncode == 0, done.stderr

    def totals(self) -> Done:
        """Read every report the teardowns wrote.

        Returns:
            What `totals` returned and printed.

        """
        return call(["totals", "leg", str(self.reports)], self.env, self.tmp)

    def test_each_collect_writes_a_report_of_its_own(self) -> None:
        """Two teardowns, two files: neither replaces the other."""
        self.collect()
        self.collect()
        written = sorted(p.name for p in self.reports.iterdir())
        assert len(written) == 2, written
        for name in written:
            assert re.search(r"^database-.+\.json$", name)

    def test_statements_merge_as_the_union_of_what_ran(self) -> None:
        """Half of stamp's statements in each database: all ten, merged."""
        self.collect({"102": "1\n2\n3\n4\n5\n"})
        self.collect({"102": "6\n7\n8\n9\n10\n"})
        read = self.totals()
        assert read.returncode == 0, read.stderr
        assert read.stdout == "plpgsql 93.33\nplpgsql-branches not-held\n"
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
        ) == read.stderr

    def test_no_branch_anywhere_prints_no_branch_leg_over_two(self) -> None:
        """Branch-free functions only: nothing to hold or leave."""
        (self.answers / "functions").write_text(
            "103\tpublic.touch()\t\n", encoding="utf-8"
        )
        (self.answers / "read.103").write_text(f"{TOUCH[1]}\n", encoding="utf-8")
        self.collect()
        self.collect()
        read = self.totals()
        assert read.returncode == 0, read.stderr
        assert read.stdout == "plpgsql 100.00\n"
        assert (
            TOUCH_LINE.format(label="leg")
            + "leg: plpgsql: total statements 2 of 2 (100.00%) across 2"
            " databases, branches none: no function has a branch\n"
        ) == read.stderr

    def test_one_signature_with_two_bodies_is_red_by_name(self) -> None:
        """Statement ids number one body; two cannot be merged by id."""
        self.collect()
        (self.answers / "source.101").write_text("BEGIN RETURN 2; END", "utf-8")
        self.collect()
        read = self.totals()
        assert read.returncode == 1
        assert read.stdout == ""
        assert read.stderr == (
            "leg: plpgsql: public.guard(): its body differs between the"
            " databases merged — statement ids are merged over one body only;"
            " build every test database from the same schema\n"
        )

    def test_a_function_no_database_ran_is_red_by_name(self) -> None:
        """Present in both, run in neither: named, whatever the total."""
        (self.answers / "functions").write_text(
            "101\tpublic.guard()\t\n104\tpublic.idle()\t\n", encoding="utf-8"
        )
        (self.answers / "read.104").write_text("0\t0\t3\t0\t0\n", encoding="utf-8")
        self.collect()
        self.collect()
        read = self.totals()
        assert read.returncode == 1
        assert read.stdout == ""
        assert read.stderr == (
            "leg: plpgsql: public.idle(): none of its 3 statements ran in any"
            " of the 2 databases measured — a function no test runs is red,"
            " whatever the total\n"
        )

    def test_a_function_one_database_ran_is_covered(self) -> None:
        """Run in one database and absent from the other: merged, green."""
        self.collect()
        (self.answers / "functions").write_text(
            "101\tpublic.guard()\t\n", encoding="utf-8"
        )
        self.collect()
        read = self.totals()
        assert read.returncode == 0, read.stderr
        assert read.stdout == "plpgsql 60.00\nplpgsql-branches not-held\n"

    def test_one_database_names_a_function_it_never_ran(self) -> None:
        """The same rule over a single report."""
        (self.answers / "read.102").write_text("0\t0\t10\t0\t2\n", encoding="utf-8")
        self.collect()
        read = self.totals()
        assert read.returncode == 1
        assert read.stderr == (
            "leg: plpgsql: public.stamp(integer): none of its 10 statements ran"
            " in the database measured — a function no test runs is red,"
            " whatever the total\n"
        )

    def test_refusals_repeated_by_every_database_are_named_once(self) -> None:
        """Two databases with the profiler off: one line, not two."""
        self.env["STUB_PROFILER"] = "off"
        self.collect()
        self.collect()
        read = self.totals()
        assert read.returncode == 1
        assert read.stderr == (
            "leg: plpgsql: plpgsql_check.profiler is 'off' — with the profiler"
            " off every function reads 0%, which is not a measurement; start"
            " the test database with -c plpgsql_check.profiler=on\n"
        )

    def test_a_report_that_is_not_one_is_named_by_its_file(self) -> None:
        """Among several, the bad file is named."""
        self.collect()
        (self.reports / "database-planted.json").write_text("[", encoding="utf-8")
        read = self.totals()
        assert read.returncode == 1
        assert read.stderr == (
            "leg: plpgsql: the report database-planted.json is not a coverage"
            " report: not JSON (Expecting value: line 1 column 2 (char 1))\n"
        )

    def test_an_empty_directory_is_no_report(self) -> None:
        """A directory no teardown wrote into holds no measurement."""
        self.reports.mkdir()
        read = self.totals()
        assert read.returncode == 1
        assert read.stderr == (
            "leg: plpgsql: the test task wrote no report — its teardown must"
            " call belt plpgsql-coverage collect while ORG_PLPGSQL_COVERAGE is set\n"
        )

    def test_a_report_path_that_is_a_file_is_refused_at_collect(self) -> None:
        """The variable names a directory; a file there is not replaced."""
        self.reports.write_text("kept\n", encoding="utf-8")
        done = call(["collect", "postgres://stub"], self.env, self.tmp)
        assert done.returncode == 1
        assert (
            f"{TASK}: ORG_PLPGSQL_COVERAGE names {self.reports}, which is not a"
            " directory of reports\n"
        ) == done.stderr
        assert self.reports.read_text(encoding="utf-8") == "kept\n"

    def test_statement_ids_psql_printed_wrong_are_unreadable(self) -> None:
        """An id that is not a number is listed, red, never guessed."""
        self.collect({"101": "1\nx\n"})
        read = self.totals()
        assert read.returncode == 1
        assert (
            "leg: plpgsql: plpgsql_check cannot read public.guard():"
            " unreadable statement ids: [['1'], ['x']]\n"
        ) in read.stderr


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


@pytest.mark.usefixtures("_stack")
class TestRealDatabaseRows:
    """The leg on a real Postgres with plpgsql_check (#2163).

    One container from the digest-pinned org image serves every row;
    each row makes databases of its own, so no row reads another's
    profile (plpgsql_check keys its profile by database).
    """
    
    @pytest.fixture
    def _stack(self, request: pytest.FixtureRequest) -> None:
        """Hold the row's cleanups, undone last first after it ends."""
        self.stack = contextlib.ExitStack()
        request.addfinalizer(self.stack.close)

    container = ""
    docker = ""
    env: ClassVar[dict[str, str]]

    @classmethod
    @override
    def setUpClass(cls) -> None:
        """Start the database and wait until it takes connections.

        Raises:
            AssertionError: it did not start, or took no connection in
                time; docker's own words are the message.

        """
        cls.docker = hermetic.tool("docker")
        cls.container = f"plpgsql-coverage-{os.getpid()}-{os.urandom(4).hex()}"
        cls.env = hermetic.env(tools=("docker",), MISE_TASK_NAME=TASK)
        started = subprocess.run(
            [
                cls.docker,
                "run",
                "--detach",
                "--rm",
                "--name",
                cls.container,
                "--env",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                pinned_image(),
                *POSTGRES,
            ],
            env=cls.env,
            capture_output=True,
            text=True,
            check=False,
        )
        if started.returncode != 0:
            msg = f"the database did not start: {started.stderr}"
            raise AssertionError(msg)
        cls.addClassCleanup(cls.remove)
        # Over TCP: the image's first start serves its socket only while
        # it initialises, then restarts; the TCP listener is the last one.
        for _ in range(READY * 2):
            ready = subprocess.run(
                [
                    cls.docker,
                    "exec",
                    cls.container,
                    "pg_isready",
                    "-q",
                    *("-h", "127.0.0.1", "-U", "postgres"),
                ],
                env=cls.env,
                capture_output=True,
                check=False,
            )
            if ready.returncode == 0:
                return
            time.sleep(0.5)
        msg = f"the database took no connection within {READY} s"
        raise AssertionError(msg)

    @classmethod
    def remove(cls) -> None:
        """Remove the container and its databases with it."""
        subprocess.run(
            [cls.docker, "rm", "--force", cls.container],
            env=cls.env,
            capture_output=True,
            check=False,
        )

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
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout

    def database(self, *schema: str) -> str:
        """Make a database of this row's from the fixture schema.

        Returns:
            Its name.

        """
        name = f"row_{os.urandom(6).hex()}"
        self.sql("postgres", f"CREATE DATABASE {name};")
        self.sql(name, "".join(schema or (FIXTURE,)))
        return name

    def collect(self, reports: Path, *databases: str) -> None:
        """Tear each database down: the teardown's own call, in turn."""
        for database in databases:
            done = call(
                ["collect", f"dbname={database}", "--", *self.client()],
                self.env | {"ORG_PLPGSQL_COVERAGE": str(reports)},
                CANON,
            )
            assert done.returncode == 0, done.stderr

    def totals(self, reports: Path) -> Done:
        """Read the reports.

        Returns:
            What `totals` returned and printed.

        """
        return call(["totals", "leg", str(reports)], self.env, CANON)

    def scratch(self) -> Path:
        """Make a directory this row removes.

        Returns:
            Its path.

        """
        tmp = Path(tempfile.mkdtemp())
        self.stack.callback(shutil.rmtree, tmp, ignore_errors=True)
        return tmp

    def two_databases(self) -> Path:
        """Run a different branch of sign in each of two databases.

        Returns:
            The directory their two reports are in.

        """
        ahead, behind = self.database(), self.database()
        self.sql(ahead, "SELECT public.sign(1);")
        self.sql(behind, "SELECT public.sign(-1);")
        reports = self.scratch() / "reports"
        self.collect(reports, ahead, behind)
        return reports

    def test_two_databases_merge_to_one_database_running_both(self) -> None:
        """Each branch in its own database reads as one database running both."""
        both = self.database()
        self.sql(both, "SELECT public.sign(1); SELECT public.sign(-1);")
        alone = self.scratch() / "reports"
        self.collect(alone, both)
        merged, single = self.totals(self.two_databases()), self.totals(alone)
        assert merged.returncode == 0, merged.stderr
        assert single.returncode == 0, single.stderr
        assert single.stdout.splitlines()[0] == merged.stdout.splitlines()[0]
        assert merged.stdout.splitlines()[0] == "plpgsql 100.00"
        assert (
            "leg: plpgsql: sign(integer): statements 4 of 4 (100.00%),"
            " branches at least 50.00% (the highest one database read)\n"
        ) in merged.stderr

    def test_two_databases_leave_the_branch_floor_untouched(self) -> None:
        """Not held, by name and count; coverage:check changes no floor line."""
        read = self.totals(self.two_databases())
        assert read.returncode == 0, read.stderr
        assert read.stdout == "plpgsql 100.00\nplpgsql-branches not-held\n"
        assert NOT_HELD_LINE.format(n=2) in read.stderr
        tree = self.scratch()
        floor = tree / ".coverage-floor"
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
        held = run(
            [
                sys.executable,
                str(BELT / "coverage_floor.py"),
                *("hold", "--label", "coverage:check", "--job", "gate"),
            ],
            self.env | {"ORG_PYTHON": sys.executable},
            tree,
            stdin=read.stdout,
        )
        assert held.returncode == 0, held.stderr
        assert held.stdout == (
            "coverage:check: plpgsql leg holds its floor 80.0 (100.00%)\n"
            "coverage:check: plpgsql-branches leg not held: this run cannot"
            " measure it exactly (the measurement says why); its floor 58.0"
            " stands\n"
        )
        assert text == floor.read_text(encoding="utf-8")

    def test_one_database_holds_the_branch_leg_as_now(self) -> None:
        """One report: plpgsql_check's own branch figure, held."""
        one = self.database()
        self.sql(one, "SELECT public.sign(1);")
        own = self.sql(
            one,
            "CREATE EXTENSION IF NOT EXISTS plpgsql_check;"
            " SELECT plpgsql_coverage_branches('public.sign(integer)');",
        )
        reports = self.scratch() / "reports"
        self.collect(reports, one)
        read = self.totals(reports)
        assert read.returncode == 0, read.stderr
        assert own == "0.5\n"
        assert read.stdout == "plpgsql 75.00\nplpgsql-branches 50.00\n"
        assert "not held" not in read.stderr

    def test_two_concurrent_collects_leave_two_reports(self) -> None:
        """Teardowns at the same moment write a report each."""
        ahead, behind = self.database(), self.database()
        reports = self.scratch() / "reports"
        env = self.env | {"ORG_PLPGSQL_COVERAGE": str(reports)}
        started = [
            subprocess.Popen(
                [
                    sys.executable,
                    *(str(HELPER), "collect", f"dbname={name}", "--"),
                    *self.client(),
                ],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for name in (ahead, behind)
        ]
        for process in started:
            _, err = process.communicate()
            assert process.returncode == 0, err
        assert len(list(reports.glob("database-*.json"))) == 2

    def test_a_function_no_database_runs_is_red_by_name(self) -> None:
        """Idle is in both databases and run in neither.

        regprocedure names a function on the search path without its
        schema, as the listing prints it.
        """
        ahead = self.database(FIXTURE, IDLE)
        behind = self.database(FIXTURE, IDLE)
        self.sql(ahead, "SELECT public.sign(1);")
        self.sql(behind, "SELECT public.sign(-1);")
        reports = self.scratch() / "reports"
        self.collect(reports, ahead, behind)
        read = self.totals(reports)
        assert read.returncode == 1
        assert read.stdout == ""
        assert read.stderr == (
            "leg: plpgsql: idle(): none of its 2 statements ran in any"
            " of the 2 databases measured — a function no test runs is red,"
            " whatever the total\n"
        )
