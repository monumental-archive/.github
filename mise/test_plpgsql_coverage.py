#!/usr/bin/env python3
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

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import base64
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

import hermetic
import plpgsql_coverage as pc
import test_coverage_tasks
from decoded import as_array, as_object, as_str, load_json, load_toml

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "leg"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


CANON = Path(__file__).resolve().parent.parent
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

# A floor the derived-state law accepts: measured 82.0 minus the band.
FLOOR = "# band: 2\n# measured: 82.0\n# derived: v1.0.0\n80.0\n"

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
    argv: list[str], env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    """Run a command and capture its output as text.

    Returns:
        The finished process.

    """
    return subprocess.run(
        argv, cwd=cwd, env=env, capture_output=True, text=True, check=False
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
        mock.patch.dict(os.environ, env, clear=True),
        contextlib.chdir(cwd),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        status = pc.main(argv)
    return Done(status, out.getvalue(), err.getvalue())


def measured_run(
    run_dir: Path, env: dict[str, str], tree: Path, *, python: bool, collects: bool
) -> None:
    """Leave a run's directory as a suite run through `measured` does (#1667).

    `python` plants the rc file and coverage.py's data the stub
    `coverage` reads; `collects` runs the suite's teardown, the helper
    called in-process (#1711), with the ORG_PLPGSQL_COVERAGE `measured`
    names in the run.

    Raises:
        AssertionError: the teardown failed; its output is the message.

    """
    run_dir.mkdir(parents=True)
    if python:
        (run_dir / "python").mkdir()
        (run_dir / "coverage.rc").write_text("[run]\n", encoding="utf-8")
        (run_dir / "python" / ".coverage.stub").write_text("", encoding="utf-8")
    if collects:
        report = {"ORG_PLPGSQL_COVERAGE": str(run_dir / "plpgsql-coverage.json")}
        done = call(["collect", "postgres://stub"], env | report, tree)
        if done.returncode != 0:
            raise AssertionError(done.stderr)


class Collecting(unittest.TestCase):
    """Collects from a planted database and reads the report."""

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
        The answers directory and the report stay on the row as
        `self.answers` and `self.report`.

        Returns:
            The collect and the totals processes.

        """
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        stubs, answers = plant_stubs(tmp, functions, unreadable)
        if listing is not None:
            (answers / "functions").write_text(listing, encoding="utf-8")
        for key, rows in (lint or {}).items():
            (answers / f"lint.{key}").write_text(rows, encoding="utf-8")
        for oid, source in (sources or {}).items():
            (answers / f"source.{oid}").write_text(source, encoding="utf-8")
        report = tmp / "report.json"
        self.answers, self.report = answers, report
        env = stub_env(stubs, answers, ORG_PLPGSQL_COVERAGE=str(report), **planted)
        collected = call(["collect", "postgres://stub"], env, tmp)
        read = call(["totals", "leg", str(report)], env, tmp)
        return collected, read


class HelperRows(Collecting):
    """`collect` against a stub psql, then `totals` over its report."""

    def test_two_functions_weighted_into_the_totals(self) -> None:
        """Each figure is weighted by its function's statement count."""
        collected, read = self.measure(TWO)
        self.assertEqual(0, collected.returncode, collected.stderr)
        self.assertEqual(0, read.returncode, read.stderr)
        self.assertEqual("plpgsql 60.00\nplpgsql-branches 66.66\n", read.stdout)
        self.assertEqual(TWO_REPORT.format(label="leg"), read.stderr)

    def test_a_branch_free_function_is_left_out_of_the_branch_total(self) -> None:
        """It counts toward statements; the branch total is unchanged."""
        collected, read = self.measure(TWO | {"103": TOUCH})
        self.assertEqual(0, collected.returncode, collected.stderr)
        self.assertEqual(0, read.returncode, read.stderr)
        self.assertEqual("plpgsql 64.70\nplpgsql-branches 66.66\n", read.stdout)
        self.assertEqual(
            (
                GUARD
                + STAMP
                + TOUCH_LINE
                + "{label}: plpgsql: total statements 11 of 17 (64.70%),"
                " branches 66.66% statement-weighted\n"
            ).format(label="leg"),
            read.stderr,
        )

    def test_no_branch_anywhere_prints_no_branch_leg(self) -> None:
        """A branch total over no branch would be a number about nothing."""
        collected, read = self.measure({"103": TOUCH})
        self.assertEqual(0, collected.returncode, collected.stderr)
        self.assertEqual(0, read.returncode, read.stderr)
        self.assertEqual("plpgsql 100.00\n", read.stdout)
        self.assertEqual(
            TOUCH_LINE.format(label="leg")
            + "leg: plpgsql: total statements 2 of 2 (100.00%), branches none:"
            " no function has a branch\n",
            read.stderr,
        )

    def test_extension_members_are_not_listed(self) -> None:
        """An extension's own PL/pgSQL (postgis's) never enters the total."""
        collected, read = self.measure(TWO)
        self.assertEqual(0, collected.returncode, collected.stderr)
        self.assertNotIn("postgis_owned", read.stderr)
        self.assertEqual("plpgsql 60.00\nplpgsql-branches 66.66\n", read.stdout)

    def test_an_unreadable_function_is_listed_and_red(self) -> None:
        """The function stays in the report, named, and nothing is totalled."""
        collected, read = self.measure(
            TWO, unreadable={"102": "ERROR:  stub: cache lookup failed"}
        )
        self.assertEqual(0, collected.returncode, collected.stderr)
        self.assertEqual(1, read.returncode)
        self.assertEqual("", read.stdout)
        self.assertEqual(
            "leg: plpgsql: plpgsql_check cannot read public.stamp(integer):"
            " ERROR:  stub: cache lookup failed\n"
            "leg: plpgsql: 1 function(s) unreadable — a function the extension"
            " cannot read is never dropped from the total\n",
            read.stderr,
        )

    def test_an_oid_that_is_not_a_number_is_never_formatted(self) -> None:
        """A listing row whose oid is not digits is unreadable, by name."""
        odd = {"7; DROP TABLE t": ("public.odd()", "1\t1\t1\t1\t0")}
        collected, read = self.measure(odd)
        self.assertEqual(0, collected.returncode, collected.stderr)
        self.assertEqual(1, read.returncode)
        self.assertEqual(
            "leg: plpgsql: plpgsql_check cannot read public.odd(): its oid"
            " '7; DROP TABLE t' is not a number\n"
            "leg: plpgsql: 1 function(s) unreadable — a function the extension"
            " cannot read is never dropped from the total\n",
            read.stderr,
        )

    def test_no_plpgsql_function_prints_no_totals(self) -> None:
        """A database with no PL/pgSQL function has nothing to total."""
        collected, read = self.measure({})
        self.assertEqual(0, collected.returncode, collected.stderr)
        self.assertEqual(0, read.returncode, read.stderr)
        self.assertEqual("", read.stdout)
        self.assertEqual(
            "leg: plpgsql: the database holds no PL/pgSQL function\n", read.stderr
        )

    def test_the_profiler_off_is_red_by_name_never_zero(self) -> None:
        """Off reads 0% for every function; the setting is refused instead."""
        off = {"101": ("public.guard()", "0\t0\t5\t0\t1")}
        collected, read = self.measure(off, STUB_PROFILER="off")
        self.assertEqual(0, collected.returncode, collected.stderr)
        self.assertEqual(1, read.returncode)
        self.assertEqual("", read.stdout)
        self.assertEqual(
            "leg: plpgsql: plpgsql_check.profiler is 'off' — with the profiler"
            " off every function reads 0%, which is not a measurement; start"
            " the test database with -c plpgsql_check.profiler=on\n",
            read.stderr,
        )

    def test_a_profiler_not_preloaded_is_red_by_name(self) -> None:
        """A session-local profile cannot reach the teardown's session."""
        collected, read = self.measure(TWO, STUB_PRELOAD="pg_stat_statements")
        self.assertEqual(0, collected.returncode, collected.stderr)
        self.assertEqual(1, read.returncode)
        self.assertEqual(
            "leg: plpgsql: plpgsql_check is not in shared_preload_libraries"
            " ('pg_stat_statements') — the profile is per session and the"
            " teardown sees none of the suite's; preload plpgsql,plpgsql_check\n",
            read.stderr,
        )

    def test_a_missing_report_names_the_teardown(self) -> None:
        """A suite whose teardown never called collect is refused."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        read = call(
            ["totals", "leg", str(tmp / "none.json")],
            hermetic.env(MISE_TASK_NAME=TASK),
            tmp,
        )
        self.assertEqual(1, read.returncode)
        self.assertEqual(
            "leg: plpgsql: the test task wrote no report — its teardown must"
            " call plpgsql_coverage.py collect while ORG_PLPGSQL_COVERAGE is set\n",
            read.stderr,
        )

    def test_a_file_that_is_not_a_report_is_refused_by_what(self) -> None:
        """The one decoder names what about the file is wrong."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
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
        self.assertEqual(1, read.returncode)
        self.assertEqual("", read.stdout)
        self.assertEqual(
            "leg: plpgsql: the report is not a coverage report: function 0:"
            " 'statements' is not a string\n",
            read.stderr,
        )

    def test_collect_without_a_report_path_is_refused(self) -> None:
        """The teardown calls collect only when the leg set the path."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        stubs, answers = plant_stubs(tmp, TWO)
        done = call(
            ["collect", "postgres://stub"],
            stub_env(stubs, answers),
            tmp,
        )
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            f"{TASK}: ORG_PLPGSQL_COVERAGE is not set — the leg sets"
            " it; call collect only then\n",
            done.stderr,
        )


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


class LintRows(Collecting):
    """plpgsql_check's static check, in the same report (#1605)."""

    def test_a_report_with_no_findings_is_green(self) -> None:
        """Every function asked, none reported: the totals print."""
        collected, read = self.measure(TWO)
        self.assertEqual(0, collected.returncode, collected.stderr)
        self.assertEqual(0, read.returncode, read.stderr)
        self.assertEqual("plpgsql 60.00\nplpgsql-branches 66.66\n", read.stdout)
        document = as_object(load_json(self.report.read_text("utf-8")), "report")
        functions = as_array(document.get("functions"), "functions")
        findings = [as_object(f, "function").get("findings") for f in functions]
        none: list[object] = [[], []]
        self.assertEqual(none, findings)
        self.assertEqual("101 0\n102 0\n", (self.answers / "linted").read_text("utf-8"))

    def test_an_error_is_red_by_function_and_line(self) -> None:
        """A column that does not exist: the leg is red before the floor."""
        message = 'column "no_such_column" does not exist'
        _, read = self.measure(TWO, lint={"102.0": finding("2", "error", message)})
        self.assertEqual(1, read.returncode)
        self.assertEqual("", read.stdout)
        self.assertEqual(
            LINT_RED.format(
                function="public.stamp(integer)",
                line=2,
                level="error",
                message=message,
                count=1,
            ),
            read.stderr,
        )

    def test_a_warning_is_red(self) -> None:
        """Unreachable code, an extra warning, is red like an error."""
        _, read = self.measure(
            TWO, lint={"101.0": finding("14", "warning extra", "unreachable code")}
        )
        self.assertEqual(1, read.returncode)
        self.assertEqual(
            LINT_RED.format(
                function="public.guard()",
                line=14,
                level="warning extra",
                message="unreachable code",
                count=1,
            ),
            read.stderr,
        )

    def test_a_finding_with_no_line_is_named_without_one(self) -> None:
        """A routine-wide finding (volatility) has no line."""
        message = "routine is marked as VOLATILE, should be IMMUTABLE"
        _, read = self.measure(TWO, lint={"101.0": finding("", "performance", message)})
        self.assertEqual(1, read.returncode)
        self.assertEqual(
            LINT_RED.format(
                function="public.guard()",
                line="-",
                level="performance",
                message=message,
                count=1,
            ),
            read.stderr,
        )

    def test_a_trigger_function_is_checked_against_each_table(self) -> None:
        """Its NEW and OLD are each table's rows; a shared finding is one."""
        listing = "101\tpublic.guard()\t5001 5002\n102\tpublic.stamp(integer)\t\n"
        row = finding("14", "warning extra", "unreachable code")
        _, read = self.measure(
            TWO, listing=listing, lint={"101.5001": row, "101.5002": row}
        )
        self.assertEqual(
            "101 5001\n101 5002\n102 0\n",
            (self.answers / "linted").read_text("utf-8"),
        )
        self.assertEqual(
            LINT_RED.format(
                function="public.guard()",
                line=14,
                level="warning extra",
                message="unreachable code",
                count=1,
            ),
            read.stderr,
        )

    def test_an_extension_member_is_never_asked(self) -> None:
        """An extension's PL/pgSQL (postgis's) is not the repository's to check."""
        self.measure(TWO)
        self.assertNotIn("900", (self.answers / "linted").read_text("utf-8"))

    def test_the_extension_absent_is_refused_by_name(self) -> None:
        """No plpgsql_check in the test stack: the collection fails, named."""
        why = 'ERROR:  extension "plpgsql_check" is not available'
        collected, read = self.measure(TWO, STUB_NO_EXTENSION=why)
        self.assertEqual(0, collected.returncode, collected.stderr)
        self.assertEqual(1, read.returncode)
        self.assertEqual(
            f"leg: plpgsql: the teardown's collection failed: {why}\n", read.stderr
        )

    def test_a_reading_without_its_lint_answer_is_refused(self) -> None:
        """A report from a helper that did not ask is not a clean result."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
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
        self.assertEqual(1, read.returncode)
        self.assertEqual(
            "leg: plpgsql: the report is not a coverage report: function 0:"
            " 'findings' is not a list\n",
            read.stderr,
        )


REASON = "-- the RAISE ends the guard, so the RETURN after it never runs"
OPTIONS_OFF = "-- @plpgsql_check_options: extra_warnings = false"
BODY = "\nBEGIN\n  RAISE EXCEPTION 'no';\n  RETURN NULL;\nEND\n"


class SuppressionRows(Collecting):
    """The extension's own option, its reason read from the source (#1605)."""

    def test_a_suppression_with_its_reason_is_green_and_printed(self) -> None:
        """Every suppression is named on every run, with why."""
        source = f"\n{REASON}\n{OPTIONS_OFF}{BODY}"
        _, read = self.measure(TWO, sources={"101": source})
        self.assertEqual(0, read.returncode, read.stderr)
        self.assertIn(
            "leg: plpgsql: public.guard(): line 3: suppresses"
            " `extra_warnings = false` — the RAISE ends the guard, so the"
            " RETURN after it never runs\n",
            read.stderr,
        )
        self.assertEqual("plpgsql 60.00\nplpgsql-branches 66.66\n", read.stdout)

    def test_a_suppression_without_a_reason_is_red(self) -> None:
        """No comment above, an empty one, or another options line."""
        for name, above, line in (
            ("nothing above", "", 1),
            ("an empty comment", "--", 2),
            (
                "another options line",
                "-- @plpgsql_check_options: fatal_errors = true",
                2,
            ),
        ):
            with self.subTest(name):
                source = (f"{above}\n" if above else "") + OPTIONS_OFF + BODY
                _, read = self.measure(TWO, sources={"101": source})
                self.assertEqual(1, read.returncode)
                self.assertEqual("", read.stdout)
                self.assertEqual(
                    f"leg: plpgsql: public.guard(): line {line}: suppression"
                    " `extra_warnings = false` has no reason — the comment line"
                    " directly above it says why\n",
                    read.stderr,
                )

    def test_an_options_line_that_only_turns_checks_on_is_no_suppression(
        self,
    ) -> None:
        """Turning a check on needs no reason and is not listed."""
        source = "\n-- @plpgsql_check_options: performance_warnings = true" + BODY
        _, read = self.measure(TWO, sources={"101": source})
        self.assertEqual(0, read.returncode, read.stderr)
        self.assertNotIn("suppress", read.stderr)

    def test_without_warnings_is_a_suppression(self) -> None:
        """Its `true` turns every warning off."""
        source = "\n-- @plpgsql_check_options: without_warnings = true" + BODY
        _, read = self.measure(TWO, sources={"101": source})
        self.assertEqual(1, read.returncode)
        self.assertIn(
            "suppression `without_warnings = true` has no reason", read.stderr
        )


class RefusalRows(Collecting):
    """Every way a collection or a report file is refused, by name."""

    def collection_failed(self, read: Done, why: str) -> None:
        """Assert the leg refuses a failed collection, naming why."""
        self.assertEqual(1, read.returncode)
        self.assertEqual("", read.stdout)
        self.assertEqual(
            f"leg: plpgsql: the teardown's collection failed: {why}\n", read.stderr
        )

    def test_no_psql_on_path_fails_the_collection_by_name(self) -> None:
        """The client is the repository's; without it nothing is read."""
        empty = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        collected, read = self.measure(TWO, PATH=str(empty))
        self.assertEqual(0, collected.returncode, collected.stderr)
        self.collection_failed(
            read, "psql is not on PATH (the repository's own client)"
        )

    def test_a_database_psql_cannot_reach_fails_by_its_error(self) -> None:
        """The message psql printed is the reason, verbatim."""
        why = 'FATAL:  database "stub" does not exist'
        collected, read = self.measure(TWO, STUB_META_FAIL=why)
        self.assertEqual(0, collected.returncode, collected.stderr)
        self.collection_failed(read, why)

    def test_unreadable_settings_fail_the_collection(self) -> None:
        """Two settings or nothing: one field is not the profiler and preload."""
        collected, read = self.measure(TWO, STUB_META="on")
        self.assertEqual(0, collected.returncode, collected.stderr)
        self.collection_failed(read, "unreadable settings from the database: [['on']]")

    def test_an_unreadable_listing_row_fails_the_collection(self) -> None:
        """A listing row without its signature is not guessed at."""
        collected, read = self.measure(TWO, listing="101\n")
        self.assertEqual(0, collected.returncode, collected.stderr)
        self.collection_failed(read, "unreadable function listing: ['101']")

    def test_figures_that_are_not_numbers_are_unreadable(self) -> None:
        """A reading psql printed in the wrong shape is listed, red."""
        odd = {"101": ("public.guard()", "x\ty")}
        collected, read = self.measure(odd)
        self.assertEqual(0, collected.returncode, collected.stderr)
        self.assertEqual(1, read.returncode)
        self.assertEqual(
            "leg: plpgsql: plpgsql_check cannot read public.guard():"
            " unreadable figures: [['x', 'y']]\n"
            "leg: plpgsql: 1 function(s) unreadable — a function the extension"
            " cannot read is never dropped from the total\n",
            read.stderr,
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
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
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
        ]
        for document, what in rows:
            with self.subTest(what=what):
                read = self.read_planted(document)
                self.assertEqual(1, read.returncode)
                self.assertEqual("", read.stdout)
                self.assertEqual(
                    f"leg: plpgsql: the report is not a coverage report: {what}\n",
                    read.stderr,
                )

    def test_a_usage_error_names_both_subcommands(self) -> None:
        """No subcommand, or an unknown one, is exit 2 with the usage."""
        for argv in ([], ["measure", "leg"]):
            with self.subTest(argv=argv):
                done = run(
                    [sys.executable, str(HELPER), *argv],
                    hermetic.env(MISE_TASK_NAME=TASK),
                    CANON,
                )
                self.assertEqual(2, done.returncode)
                self.assertEqual(
                    f"{TASK}: usage: plpgsql-coverage collect <conninfo>"
                    " | totals <label> <report>\n",
                    done.stderr,
                )


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


class LegRows(unittest.TestCase):
    """`coverage:check` over the plpgsql leg, decided by the floor's record."""

    def check(
        self,
        functions: dict[str, tuple[str, str]],
        *,
        legs: str,
        files: dict[str, str],
        collects: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run coverage:check's body in a planted tree.

        `legs` is the floor's `legs:` record, `files` the tracked files
        beside it, and `collects` whether the suite's teardown calls the
        helper. A tracked `*.py` brings the python leg in, measured by
        the stub coverage.py.

        Returns:
            The finished task.

        """
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        stubs, answers = plant_stubs(tmp, functions)
        (stubs / "coverage").write_text(COVERAGE, encoding="utf-8")
        (stubs / "coverage").chmod(0o755)
        (stubs / "python3").symlink_to(sys.executable)
        tree = tmp / "tree"
        tree.mkdir()
        for name, text in files.items():
            (tree / name).write_text(text, encoding="utf-8")
        floor = FLOOR.replace("80.0\n", f"# legs: {legs}\n80.0\n")
        (tree / ".coverage-floor").write_text(floor, encoding="utf-8")
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
        )
        check = as_object(TASKS["coverage:check"], "coverage:check")
        script = as_str(check["run"], "coverage:check's run")
        as_mise = hermetic.task_run("coverage:check", script)
        return run(as_mise.argv, env | as_mise.env, tree)

    PYTHON_HELD = (
        "coverage:check: python leg holds the floor 80.0 (90.0%)\n"
        "coverage:check: python-branches leg holds the floor 80.0 (85.0%)\n"
    )

    def assert_held(self, held: str, stdout: str) -> None:
        """Assert the hold lines, then the one upload: the Python report (#1628).

        The reports are written outside the tree, so the path is the
        measurement's scratch directory; its file name is the leg's.
        """
        head, _, upload = stdout.rstrip("\n").rpartition("\n")
        self.assertEqual(held, head + "\n")
        self.assertRegex(upload, r"^coverage:check: upload /\S+/coverage\.xml$")

    def test_the_clause_in_files_switches_nothing_on(self) -> None:
        """No search: a README, a .py and a .sql naming the clause (#1666)."""
        done = self.check(
            TWO,
            legs="python python-branches",
            files=MENTIONS,
            collects=False,
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assert_held(self.PYTHON_HELD, done.stdout)
        self.assertNotIn("plpgsql", done.stdout + done.stderr)

    def test_a_recorded_leg_is_held_with_no_sql_tracked(self) -> None:
        """The report is the answer: no .sql file is needed to find it."""
        done = self.check(
            HELD,
            legs="python python-branches plpgsql plpgsql-branches",
            files={"helper.py": "print('hi')\n"},
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assert_held(
            self.PYTHON_HELD
            + "coverage:check: plpgsql leg holds the floor 80.0 (95.00%)\n"
            "coverage:check: plpgsql-branches leg holds the floor 80.0 (90.00%)\n",
            done.stdout,
        )

    def test_below_the_floor_is_red_by_name(self) -> None:
        """Statements at 60% red the plpgsql leg by its name."""
        done = self.check(
            TWO, legs="plpgsql plpgsql-branches", files={"schema.sql": "-- sql\n"}
        )
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "coverage:check: plpgsql: 60.00% is below the derived floor 80.0%\n",
            done.stderr,
        )

    def test_a_record_whose_every_leg_reports_nothing_names_them(self) -> None:
        """Every recorded leg lost: each named, never the generic refusal."""
        done = self.check(
            TWO,
            legs="python python-branches",
            files={"README.md": "no python left\n"},
            collects=False,
        )
        self.assertEqual(1, done.returncode)
        remedy = (
            "  the python leg needs tracked *.py and a `test` task whose test"
            ' command runs through "${{ORG_BELT_DIR}}/measured"; or record'
            " the leg's removal on purpose: `# dropped: {leg} <reason>` in"
            " .coverage-floor\n"
        )
        self.assertEqual(
            "coverage:check: the floor was derived with python and this run"
            " measured none\n"
            + remedy.format(leg="python")
            + "coverage:check: the floor was derived with python-branches and"
            " this run measured none\n" + remedy.format(leg="python-branches"),
            done.stderr.removeprefix("suite ran\n"),
        )
        self.assertNotIn("no leg was measured", done.stderr)
        self.assertEqual("", done.stdout)

    def test_a_recorded_leg_that_reports_nothing_is_red_by_name(self) -> None:
        """The teardown stopped collecting: the leg is named, never dropped."""
        done = self.check(
            TWO,
            legs="python python-branches plpgsql plpgsql-branches",
            files={"helper.py": "print('hi')\n"},
            collects=False,
        )
        self.assertEqual(1, done.returncode)
        remedy = (
            "  the plpgsql leg needs a test command run through"
            ' "${{ORG_BELT_DIR}}/measured" and its teardown to call'
            " plpgsql_coverage.py collect while ORG_PLPGSQL_COVERAGE is set;"
            " or record the leg's removal on purpose:"
            " `# dropped: {leg} <reason>` in .coverage-floor\n"
        )
        self.assertIn(
            "coverage:check: the floor was derived with plpgsql and this run"
            " measured none\n" + remedy.format(leg="plpgsql"),
            done.stderr,
        )
        self.assertIn(
            "coverage:check: the floor was derived with plpgsql-branches and"
            " this run measured none\n" + remedy.format(leg="plpgsql-branches"),
            done.stderr,
        )


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


class ReadsTheRunRows(unittest.TestCase):
    """Both legs read the one run `measured` left, and run no suite (#1667)."""

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
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
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
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "python 90.0\npython-branches 85.0\n"
            "plpgsql 60.00\nplpgsql-branches 66.66\n",
            done.stdout,
        )
        self.assertNotIn("suite ran", done.stderr)

    def test_plpgsql_alone_reads_only_its_report(self) -> None:
        """The plpgsql selector prints its own figures and no python one."""
        done, _ = self.measure("plpgsql")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("plpgsql 60.00\nplpgsql-branches 66.66\n", done.stdout)

    def test_only_formatless_legs_pass_by_name(self) -> None:
        """No report is owed where no leg measured has a format."""
        done, _ = self.measure("reports", python=False)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "plpgsql 60.00\nplpgsql-branches 66.66\n"
            "leg: upload none — every leg measured has no upload format"
            " (plpgsql, plpgsql-branches)\n",
            done.stdout,
        )
        self.assertIn(
            "leg: plpgsql, plpgsql-branches: no upload format,"
            " not counted as a report writer\n",
            done.stderr,
        )

    def test_a_leg_with_a_format_that_wrote_nothing_is_refused(self) -> None:
        """A formatless leg beside it does not excuse the missing report."""
        done, _ = self.measure("reports", no_xml=True)
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertIn(
            "leg: the legs measured wrote no report — nothing for the badge"
            " to upload\n",
            done.stderr,
        )

    def test_a_formatless_leg_beside_a_written_report(self) -> None:
        """The written report uploads; the formatless legs are named."""
        done, tree = self.measure("reports")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(
            done.stdout.endswith(
                f"leg: upload {tree.parent / 'reports' / 'coverage.xml'}\n"
            ),
            done.stdout,
        )
        self.assertIn(
            "leg: plpgsql, plpgsql-branches: no upload format,"
            " not counted as a report writer\n",
            done.stderr,
        )


if __name__ == "__main__":
    unittest.main()
