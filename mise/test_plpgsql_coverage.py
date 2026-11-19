#!/usr/bin/env python3
"""Table tests for the PL/pgSQL coverage leg (#1547).

Two tables. The helper's rows run `mise/plpgsql-coverage.py collect`
against a stub `psql` that answers with planted figures, then `totals`
over the report it wrote, and assert the exact messages. The leg's rows
run `coverage:check`'s own `run` string, read out of mise/config.toml,
in a planted git tree whose `test` task calls the real helper in its
teardown position, so a row states the database's figures and asserts
what the gate does with them.

The canon's own tracked files never carry a PL/pgSQL LANGUAGE clause:
the leg would apply to the canon itself. The planted clause is built
from two halves here for that reason.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import json
import os
import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace

hermetic = SimpleNamespace(
    **runpy.run_path(str(Path(__file__).resolve().with_name("hermetic.py")))
)

CANON = Path(__file__).resolve().parent.parent
BELT = CANON / "mise"
HELPER = BELT / "plpgsql-coverage.py"
BASH = shutil.which("bash")
GIT = shutil.which("git")
CONFIG = tomllib.loads((BELT / "config.toml").read_text(encoding="utf-8"))

# Never written whole in a tracked canon file (see the docstring).
CLAUSE = "LANGUAGE " + "plpgsql"

PRELOAD = "pg_stat_statements,plpgsql,plpgsql_check"

# The repository's psql, answering each statement the helper sends with
# what the row planted in STUB_DIR.
PSQL = """\
#!/usr/bin/env bash
sql="${*: -1}"
case "${sql}" in
  *"CREATE EXTENSION"*) exit 0 ;;
  *current_setting*) printf '%s\\t%s\\n' "${STUB_PROFILER}" "${STUB_PRELOAD}" ;;
  *lanname*)
    cat "${STUB_DIR}/functions"
    # An extension's own functions answer any listing that keeps them.
    [[ ${sql} == *"d.deptype = 'e'"* ]] || cat "${STUB_DIR}/extension"
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
    listing = "".join(f"{oid}\t{sig}\n" for oid, (sig, _) in functions.items())
    (answers / "functions").write_text(listing, encoding="utf-8")
    # postgis owns PL/pgSQL functions in a non-system schema; uncovered.
    (answers / "extension").write_text(
        "900\tpublic.postgis_owned()\n", encoding="utf-8"
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
    env = hermetic.env()
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


def run(argv: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a command and capture its output as text.

    Returns:
        The finished process.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    return subprocess.run(
        argv, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )


class HelperRows(unittest.TestCase):
    """`collect` against a stub psql, then `totals` over its report."""

    def measure(
        self,
        functions: dict[str, tuple[str, str]],
        unreadable: dict[str, str] | None = None,
        **planted: str,
    ) -> tuple[subprocess.CompletedProcess, subprocess.CompletedProcess]:
        """Collect a report from the planted database and read it.

        Returns:
            The collect and the totals processes.

        """
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        stubs, answers = plant_stubs(tmp, functions, unreadable)
        report = tmp / "report.json"
        env = stub_env(stubs, answers, ORG_PLPGSQL_COVERAGE=str(report), **planted)
        py = sys.executable
        collected = run([py, str(HELPER), "collect", "postgres://stub"], env, tmp)
        read = run([py, str(HELPER), "totals", "leg", str(report)], env, tmp)
        return collected, read

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
        read = run(
            [sys.executable, str(HELPER), "totals", "leg", str(tmp / "none.json")],
            hermetic.env(),
            tmp,
        )
        self.assertEqual(1, read.returncode)
        self.assertEqual(
            "leg: plpgsql: the test task wrote no report — its teardown must"
            " call plpgsql-coverage.py collect while ORG_PLPGSQL_COVERAGE is set\n",
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
        }
        document = {"profiler": "on", "preload": PRELOAD, "functions": [entry]}
        planted.write_text(json.dumps(document), encoding="utf-8")
        read = run(
            [sys.executable, str(HELPER), "totals", "leg", str(planted)],
            hermetic.env(),
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
        done = run(
            [sys.executable, str(HELPER), "collect", "postgres://stub"],
            stub_env(stubs, answers),
            tmp,
        )
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            "plpgsql-coverage: ORG_PLPGSQL_COVERAGE is not set — the leg sets"
            " it; call collect only then\n",
            done.stderr,
        )


class LegRows(unittest.TestCase):
    """`coverage:check` over the plpgsql leg, in a planted tree."""

    def check(
        self,
        functions: dict[str, tuple[str, str]],
        *,
        clause: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run coverage:check's body; the test task's teardown collects.

        Returns:
            The finished task.

        """
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        stubs, answers = plant_stubs(tmp, functions)
        tree = tmp / "tree"
        tree.mkdir()
        body = f"CREATE FUNCTION f() RETURNS trigger {CLAUSE} AS $$ $$;\n"
        if not clause:
            body = "CREATE TABLE t (id integer);\n"
        (tree / "schema.sql").write_text(body, encoding="utf-8")
        (tree / ".coverage-floor").write_text(FLOOR, encoding="utf-8")
        teardown = '"${ORG_PYTHON}" "${ORG_BELT_DIR}/plpgsql-coverage.py"'
        task = tmp / "task.json"
        task.write_text(
            json.dumps({
                "dir": str(tree),
                "run": ["echo suite ran >&2", f"{teardown} collect postgres://stub"],
            }),
            encoding="utf-8",
        )
        env = stub_env(stubs, answers, STUB_TASK=str(task))
        run([GIT, "init", "-q"], env, tree)
        run([GIT, "add", "schema.sql", ".coverage-floor"], env, tree)
        script = CONFIG["tasks"]["coverage:check"]["run"]
        return run([BASH, "-euo", "pipefail", "-c", script], env, tree)

    def test_the_floor_is_held(self) -> None:
        """Both PL/pgSQL legs print and each holds the floor by name."""
        held = {
            "101": ("public.guard()", "0.9\t1\t10\t9\t1"),
            "102": ("public.stamp(integer)", "1\t0.8\t10\t10\t1"),
        }
        done = self.check(held)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:check: plpgsql leg holds the floor 80.0 (95.00%)\n"
            "coverage:check: plpgsql-branches leg holds the floor 80.0 (90.00%)\n"
            "coverage:check: upload none — every leg measured has no upload"
            " format (plpgsql, plpgsql-branches)\n",
            done.stdout,
        )
        self.assertIn("suite ran", done.stderr)

    def test_below_the_floor_is_red_by_name(self) -> None:
        """Statements at 60% red the plpgsql leg by its name."""
        done = self.check(TWO)
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "coverage:check: plpgsql: 60.00% is below the derived floor 80.0%\n",
            done.stderr,
        )

    def test_no_tracked_plpgsql_is_skipped_clean(self) -> None:
        """Tracked SQL with no PL/pgSQL function skips the leg by name."""
        done = self.check(TWO, clause=False)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:check: no measurable language (go.mod, Cargo.toml,"
            " tracked *.py with a test task, package.json with vitest, or"
            " tracked PL/pgSQL with a test task), skipped\n",
            done.stdout,
        )
        self.assertNotIn("suite ran", done.stderr)


class ReportRows(unittest.TestCase):
    """The report step names the formatless legs, never counts them."""

    def reports(
        self, measured: str, files: tuple[str, ...] = ()
    ) -> subprocess.CompletedProcess:
        """Run `coverage-measure.sh <label> reports` over planted legs.

        Returns:
            The finished step.

        """
        tree = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tree, ignore_errors=True)
        env = hermetic.env() | {"ORG_PYTHON": sys.executable}
        run([GIT, "init", "-q"], env, tree)
        for name in files:
            (tree / name).write_text("TN:\n", encoding="utf-8")
        script = str(BELT / "coverage-measure.sh")
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [BASH, script, "leg", "reports"],
            cwd=tree,
            env=env,
            input=measured,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_only_formatless_legs_pass_by_name(self) -> None:
        """No report is owed where no leg measured has a format."""
        done = self.reports("plpgsql 95.00\nplpgsql-branches 90.00\n")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "leg: upload none — every leg measured has no upload format"
            " (plpgsql, plpgsql-branches)\n",
            done.stdout,
        )
        self.assertEqual(
            "leg: plpgsql, plpgsql-branches: no upload format,"
            " not counted as a report writer\n",
            done.stderr,
        )

    def test_a_leg_with_a_format_that_wrote_nothing_is_refused(self) -> None:
        """A formatless leg beside it does not excuse the missing report."""
        done = self.reports("typescript 88.0\nplpgsql 95.00\n")
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertIn(
            "leg: the legs measured wrote no report — nothing for the badge"
            " to upload\n",
            done.stderr,
        )

    def test_a_formatless_leg_beside_a_written_report(self) -> None:
        """The written report uploads; the formatless leg is named."""
        done = self.reports(
            "typescript 88.0\nplpgsql 95.00\n", files=("lcov-typescript.info",)
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("leg: upload lcov-typescript.info\n", done.stdout)
        self.assertEqual(
            "leg: plpgsql: no upload format, not counted as a report writer\n",
            done.stderr,
        )


if __name__ == "__main__":
    unittest.main()
