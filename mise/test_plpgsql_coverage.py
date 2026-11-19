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


class Collecting(unittest.TestCase):
    """Collects from a planted database and reads the report."""

    def measure(
        self,
        functions: dict[str, tuple[str, str]],
        unreadable: dict[str, str] | None = None,
        listing: str | None = None,
        **planted: str,
    ) -> tuple[subprocess.CompletedProcess, subprocess.CompletedProcess]:
        """Collect a report from the planted database and read it.

        `listing` replaces the function listing psql prints, verbatim.

        Returns:
            The collect and the totals processes.

        """
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        stubs, answers = plant_stubs(tmp, functions, unreadable)
        if listing is not None:
            (answers / "functions").write_text(listing, encoding="utf-8")
        report = tmp / "report.json"
        env = stub_env(stubs, answers, ORG_PLPGSQL_COVERAGE=str(report), **planted)
        py = sys.executable
        collected = run([py, str(HELPER), "collect", "postgres://stub"], env, tmp)
        read = run([py, str(HELPER), "totals", "leg", str(report)], env, tmp)
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


class RefusalRows(Collecting):
    """Every way a collection or a report file is refused, by name."""

    def collection_failed(self, read: subprocess.CompletedProcess, why: str) -> None:
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

    def read_planted(self, document: object) -> subprocess.CompletedProcess:
        """Run `totals` over a planted report file.

        Returns:
            The finished process.

        """
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        planted = tmp / "report.json"
        text = document if isinstance(document, str) else json.dumps(document)
        planted.write_text(text, encoding="utf-8")
        return run(
            [sys.executable, str(HELPER), "totals", "leg", str(planted)],
            hermetic.env(),
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
                done = run([sys.executable, str(HELPER), *argv], hermetic.env(), CANON)
                self.assertEqual(2, done.returncode)
                self.assertEqual(
                    "usage: plpgsql-coverage collect <conninfo>"
                    " | totals <label> <report>\n",
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


# The coverage.py stub the python leg's rows use, read from its suite.
COVERAGE = str(
    runpy.run_path(str(Path(__file__).resolve().with_name("test_coverage_tasks.py")))[
        "COVERAGE"
    ]
)

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


class RunOnceRows(unittest.TestCase):
    """The test task and its depends run once, for every leg that reads it."""

    def measure(
        self,
        tasks: dict[str, dict[str, object]],
        selector: str = "all",
        *,
        python: bool = True,
        no_xml: bool = False,
    ) -> tuple[subprocess.CompletedProcess, Path]:
        """Run coverage-measure.sh over planted tasks, python and PL/pgSQL.

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
        (tree / "sub").mkdir(parents=True)
        defs = tmp / "tasks"
        defs.mkdir()
        tracked = ["schema.sql"]
        (tree / "schema.sql").write_text(f"CREATE FUNCTION f() {CLAUSE};\n", "utf-8")
        if python:
            (tree / "helper.py").write_text("print('hi')\n", encoding="utf-8")
            tracked.append("helper.py")
        for name, task in tasks.items():
            planted = {"dir": str(tree)} | task
            if task.get("dir") == "sub":
                planted["dir"] = str(tree / "sub")
            (defs / f"{name}.json").write_text(json.dumps(planted), "utf-8")
        env = stub_env(
            stubs,
            answers,
            STUB_TASKS=str(defs),
            STUB_PYTHON=sys.executable,
            STUB_PY="90.0",
            STUB_BR="85.0",
            STUB_NO_XML="1" if no_xml else "",
            ORG_COVERAGE_REPORTS=str(tmp / "reports"),
        )
        run([GIT, "init", "-q"], env, tree)
        run([GIT, "add", *tracked], env, tree)
        script = str(BELT / "coverage-measure.sh")
        return run([BASH, script, "leg", selector], env, tree), tree

    COLLECT = '"${ORG_PYTHON}" "${ORG_BELT_DIR}/plpgsql-coverage.py" collect stub'
    COUNT = 'echo ran >> "${STUB_TASKS}/../counter"'

    def test_both_legs_read_one_run_under_all(self) -> None:
        """Python and PL/pgSQL print their figures; the suite ran once."""
        done, tree = self.measure({
            "test": {"run": [self.COLLECT], "depends": ["test:unit"]},
            "test:unit": {"run": [self.COUNT]},
        })
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "python 90.0\npython-branches 85.0\n"
            "plpgsql 60.00\nplpgsql-branches 66.66\n",
            done.stdout,
        )
        self.assertEqual(1, counter(tree))

    def test_plpgsql_alone_runs_once_without_python(self) -> None:
        """The plpgsql selector sets only its own variable."""
        marker = 'echo "${COVERAGE_PROCESS_START:-unset}" > "${STUB_TASKS}/../py"'
        done, tree = self.measure(
            {
                "test": {"run": [self.COLLECT], "depends": ["test:unit"]},
                "test:unit": {"run": [self.COUNT, marker]},
            },
            "plpgsql",
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("plpgsql 60.00\nplpgsql-branches 66.66\n", done.stdout)
        self.assertEqual(1, counter(tree))
        # Whatever the row inherited (the canon's own coverage:check
        # measures this suite with the variable set): the plpgsql
        # selector leaves it as it found it.
        inherited = os.environ.get("COVERAGE_PROCESS_START", "unset")
        self.assertEqual(f"{inherited}\n", (tree.parent / "py").read_text("utf-8"))

    def test_python_measures_what_only_a_depend_runs(self) -> None:
        """The depend's script runs under the python leg's measurement."""
        marker = 'echo "${COVERAGE_PROCESS_START:-unset}" > "${STUB_TASKS}/../py"'
        done, tree = self.measure(
            {
                "test": {"run": ["echo suite in a depend"], "depends": ["test:unit"]},
                "test:unit": {"run": [marker]},
            },
            "python",
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("python 90.0\npython-branches 85.0\n", done.stdout)
        recorded = (tree.parent / "py").read_text("utf-8").strip()
        self.assertTrue(recorded.endswith("/rc"), recorded)
        # The leg's own rc, never one the row inherited.
        self.assertNotEqual(os.environ.get("COVERAGE_PROCESS_START"), recorded)

    def test_a_shared_depend_runs_once(self) -> None:
        """Two tasks depending on one run it once, before both."""
        done, tree = self.measure(
            {
                "test": {"run": [self.COLLECT], "depends": ["a", "b"]},
                "a": {"run": ["echo a >> ${STUB_TASKS}/../order"], "depends": ["c"]},
                "b": {"run": ["echo b >> ${STUB_TASKS}/../order"], "depends": ["c"]},
                "c": {"run": [self.COUNT, "echo c >> ${STUB_TASKS}/../order"]},
            },
            python=False,
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(1, counter(tree))
        self.assertEqual("c\na\nb\n", (tree.parent / "order").read_text("utf-8"))

    def test_a_script_runs_from_its_own_tasks_dir(self) -> None:
        """A depend with a `dir` of its own runs there, not in the root."""
        where = 'pwd -P > "${STUB_TASKS}/../where"'
        done, tree = self.measure(
            {
                "test": {"run": [self.COLLECT], "depends": ["test:unit"]},
                "test:unit": {"run": [where], "dir": "sub"},
            },
            python=False,
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            str((tree / "sub").resolve()) + "\n",
            (tree.parent / "where").read_text("utf-8"),
        )

    def test_only_formatless_legs_pass_by_name(self) -> None:
        """No report is owed where no leg measured has a format."""
        done, _ = self.measure(
            {"test": {"run": [self.COLLECT]}}, "reports", python=False
        )
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
        done, _ = self.measure(
            {"test": {"run": [self.COLLECT]}}, "reports", no_xml=True
        )
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertIn(
            "leg: the legs measured wrote no report — nothing for the badge"
            " to upload\n",
            done.stderr,
        )

    def test_a_formatless_leg_beside_a_written_report(self) -> None:
        """The written report uploads; the formatless legs are named."""
        done, tree = self.measure({"test": {"run": [self.COLLECT]}}, "reports")
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

    def test_a_suite_that_runs_nothing_is_refused(self) -> None:
        """Neither the task nor its depends carries a script."""
        done, _ = self.measure(
            {"test": {"run": [], "depends": ["x"]}, "x": {}}, python=False
        )
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertEqual(
            "leg: the test task and its depends run nothing — the legs measure\n"
            "  the repository's own suite and cannot measure a task that runs"
            " nothing\n",
            done.stderr,
        )


if __name__ == "__main__":
    unittest.main()
