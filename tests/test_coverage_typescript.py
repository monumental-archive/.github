"""The coverage tasks over a TypeScript tree (#1357).

Split out of test_coverage_tasks.py so the gate's test shards can run
it beside the rest (#2049); the planted trees, stubs and the task
runner are that file's, imported from it.

TypeScriptRows: the run `measured` made of the tree's own vitest, read
and never repeated (#2105), its coverage provider, and every leg
printed beside Python's.
"""

from tests import support
from tests.test_coverage_tasks import (
    MEASURED_VITEST,
    run_in_tree,
    upload_line,
    vitest_runs,
)

# The stub vitest's count when the suite never ran.
NO_RUNS: list[str] = []
# Each typescript leg a floor records, lost from the run (#1666, #2143).
LOST_TYPESCRIPT = "".join(
    f"coverage:check: the floor was derived with {leg} and this run measured"
    " none\n  the typescript leg needs a root package.json whose lockfile"
    " pins vitest and a coverage provider, and a `test` task that runs"
    f" vitest; or record the leg's removal on purpose: `# dropped: {leg}"
    " <reason>` in .coverage-floor\n"
    for leg in ("typescript", "typescript-branches")
)


# `coverage:check` over the TypeScript leg (#1357).
def test_a_vitest_tree_measures_and_holds(env: support.Env) -> None:
    """The run's reports are read: both legs print, and no vitest runs."""
    done, tree = run_in_tree(
        "coverage:check", python=False, typescript="vitest", env=env
    )
    assert done.returncode == 0, done.stderr
    assert (
        "coverage:check: typescript leg holds its floor 80.0 (88.0%)\n"
        "coverage:check: typescript-branches leg holds its floor 80.0 (81.0%)\n"
        + upload_line(tree, "lcov-typescript.info")
    ) == done.stdout
    assert vitest_runs(tree) == NO_RUNS


def test_no_report_with_the_leg_recorded_reds_naming_measured(env: support.Env) -> None:
    """A floor that records the leg and a run with no report: red (#2105)."""
    done, tree = run_in_tree(
        "coverage:check",
        python=False,
        typescript="vitest",
        measured=False,
        legs="typescript typescript-branches",
        env=env,
    )
    assert done.returncode == 1, done.stderr
    assert (
        "coverage:check: typescript: the test run measured no TypeScript —\n"
        '  name vitest on the measured command line: "measured pnpm exec'
        ' vitest run"\n'
    ) in done.stderr
    assert vitest_runs(tree) == NO_RUNS


def test_test_then_check_runs_the_suite_once(env: support.Env) -> None:
    """`test` through `measured`, then the check: one vitest run (#2105)."""
    done, tree = run_in_tree(
        "coverage:check",
        python=False,
        typescript="vitest",
        measured=False,
        run_test=True,
        env=env,
    )
    assert done.returncode == 0, done.stderr
    assert (
        "coverage:check: typescript leg holds its floor 80.0 (88.0%)\n" in done.stdout
    )
    runs = vitest_runs(tree)
    assert len(runs) == 1, runs
    assert "--coverage.enabled=true" in runs[0]


def test_branches_below_the_floor_red_by_name(env: support.Env) -> None:
    """Lines holding does not carry the branch leg."""
    done, _ = run_in_tree(
        "coverage:check", python=False, typescript="vitest", ts_branches="79.5", env=env
    )
    assert done.returncode == 1
    assert (
        "coverage:check: typescript-branches: 79.5% is below its derived floor 80.0%\n"
    ) in done.stderr


def test_lines_below_the_floor_red_by_name(env: support.Env) -> None:
    """The line leg reds before the branch leg is read."""
    done, _ = run_in_tree(
        "coverage:check", python=False, typescript="vitest", ts="60.0", env=env
    )
    assert done.returncode == 1
    assert (
        "coverage:check: typescript: 60.0% is below its derived floor 80.0%\n"
        in done.stderr
    )


def test_no_floor_skips_clean(env: support.Env) -> None:
    """A vitest tree with no floor owes nothing: `test` runs unmeasured."""
    done, tree = run_in_tree(
        "coverage:check",
        python=False,
        typescript="vitest",
        floor=False,
        measured=False,
        run_test=True,
        env=env,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == "coverage:check: no .coverage-floor, skipped\n"
    unmeasured: list[str] = ["run"]
    assert unmeasured == vitest_runs(tree)


def test_an_unsupported_runner_reds_its_recorded_legs(env: support.Env) -> None:
    """A jest tree's recorded typescript legs are red, never skipped.

    Every floor records its legs (#2143), so each is red by name with
    the remedy naming vitest; `coverage:adopt`'s probe names the
    runner itself.
    """
    done, _ = run_in_tree("coverage:check", python=False, typescript="jest", env=env)
    assert (done.returncode, done.stdout, done.stderr) == (1, "", LOST_TYPESCRIPT)


def test_vitest_without_a_provider_reds_with_the_remedy(env: support.Env) -> None:
    """The belt pins no provider; the lockfile must."""
    done, _ = run_in_tree(
        "coverage:check", python=False, typescript="vitest-bare", env=env
    )
    assert done.returncode == 1
    assert (
        "coverage:check: vitest runs the suite but the lockfile pins no"
        " coverage provider —\n"
        "  add @vitest/coverage-v8 to devDependencies at vitest's own version\n"
    ) in done.stderr


def test_mixed_python_and_typescript_prints_every_leg(env: support.Env) -> None:
    """A mixed tree measures each language it tracks."""
    done, tree = run_in_tree("coverage:check", typescript="vitest", env=env)
    assert done.returncode == 0, done.stderr
    assert (
        "coverage:check: python leg holds its floor 80.0 (90.0%)\n"
        "coverage:check: python-branches leg holds its floor 80.0 (85.0%)\n"
        "coverage:check: typescript leg holds its floor 80.0 (88.0%)\n"
        "coverage:check: typescript-branches leg holds its floor 80.0 (81.0%)\n"
        + upload_line(tree, "lcov-typescript.info", "coverage.xml")
    ) == done.stdout


def test_the_test_task_selection_is_the_measured_selection(env: support.Env) -> None:
    """A `test` command narrower than vitest's config is measured as written.

    #1357 item 2: the run measured is the repository's own test
    command, so `vitest run tests/unit` is measured over tests/unit,
    not over every file the vitest config would select.
    """
    done, tree = run_in_tree(
        "coverage:check",
        python=False,
        typescript="vitest",
        test_run=MEASURED_VITEST + " tests/unit",
        measured=False,
        run_test=True,
        env=env,
    )
    assert done.returncode == 0, done.stderr
    runs = [run.split() for run in vitest_runs(tree)]
    assert len(runs) == 1, runs
    selection: list[str] = ["run", "tests/unit"]
    assert selection == runs[0][:2]
    assert "--coverage.enabled=true" in runs[0]


def test_a_vitest_tree_without_a_test_task_reds_its_legs(env: support.Env) -> None:
    """No test task, no suite the gate runs: each recorded leg named."""
    done, _ = run_in_tree(
        "coverage:check", python=False, typescript="vitest", test_task=False, env=env
    )
    assert (done.returncode, done.stderr) == (1, LOST_TYPESCRIPT)


def test_a_coverage_flag_in_the_test_command_is_refused(env: support.Env) -> None:
    """The leg owns the coverage flags: `test` reds before vitest runs."""
    done, tree = run_in_tree(
        "coverage:check",
        python=False,
        typescript="vitest",
        test_run=MEASURED_VITEST + " --coverage.enabled=false",
        measured=False,
        run_test=True,
        env=env,
    )
    assert done.returncode == 1, done.stderr
    assert "measured: --coverage.enabled=false is on the vitest run" in done.stderr
    assert vitest_runs(tree) == NO_RUNS


def test_check_writes_the_typescript_lcov(env: support.Env) -> None:
    """The upload is the measured run's own lcov, off lcov.info."""
    done, tree = run_in_tree(
        "coverage:check", python=False, typescript="vitest", env=env
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.endswith(upload_line(tree, "lcov-typescript.info")), done.stdout
    reports = tree.parent / "reports"
    assert (reports / "lcov-typescript.info").read_text("utf-8") == "TN:\n"
    assert not (reports / "lcov.info").exists()
