#!/usr/bin/env python3
"""Table tests for the coverage tasks' Python leg (#1331).

The canon is a Python codebase, and the coverage machinery could not
measure it: every `coverage:*` task skipped unless `Cargo.toml` or
`go.mod` existed. The Python leg runs the repository's own `test` task
under coverage.py and prints `python <percent>` and `python-branches
<percent>`, and the floor is a minimum every leg must hold.

The bodies under test are the tasks' own `run` strings, read out of
mise/config.toml with decoded.load_toml and run by bash in a planted
git tree, not copies of them, and they call the real
`mise/coverage-measure.sh`. Only the measuring tools are stubs
(`coverage`, `go`, and `mise`'s read of the test task), each
answering with a planted number, so a row states the measurement and
asserts what the gate does with it.

Each row asserts the message, not just the exit status: a planted row
scored on exit alone can pass for the wrong reason.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TypedDict, Unpack, override

import decoded
import gate_cache_key
import hermetic
import workflow_yaml

CANON = Path(__file__).resolve().parent.parent
BELT = CANON / "mise"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")

CONFIG = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))

# A floor the derived-state law accepts: measured 82.0 minus the band.
FLOOR = """\
# band: 2
# measured: 82.0
# derived: v1.0.0
80.0
"""

# The measuring tools. Each answers only what the leg asks of it, with
# the number the row planted in its environment.
COVERAGE = """\
#!/usr/bin/env bash
set -euo pipefail
out=""
cmd=$1
while [[ $# -gt 0 ]]; do
  [[ $1 == -o ]] && out=$2
  shift
done
case "${cmd}" in
  debug)
    echo "  coverage_module: /stub/site/coverage/__init__.py"
    echo "       executable: ${STUB_PYTHON}"
    ;;
  combine) ;;
  json)
    printf '{"totals": {"percent_statements_covered": %s,' "${STUB_PY}" > "${out}"
    printf ' "percent_branches_covered": %s}}\\n' "${STUB_BR}" >> "${out}"
    ;;
  xml) [[ -n ${STUB_NO_XML:-} ]] || echo "<coverage/>" > "${out}" ;;
  *) exit 1 ;;
esac
"""

GO = """\
#!/usr/bin/env bash
out=""
for arg in "$@"; do
  case "${arg}" in -coverprofile=* | -o=*) out=${arg#*=} ;; *) ;; esac
done
case "$1 ${2:-}" in
  "test "*)
    # The profile `go test -coverprofile` writes (a tree with no test
    # task), unless the row plants a run that wrote none.
    [[ -z ${STUB_GO_NO_PROFILE:-} ]] || exit 0
    [[ -z ${out} ]] || echo "mode: atomic" > "${out}"
    ;;
  "tool covdata")
    # The merged profile of the run's counters (#1651).
    [[ -z ${STUB_GO_NO_PROFILE:-} ]] || exit 0
    echo "mode: atomic" > "${out}"
    ;;
  "tool cover") printf 'total:\\t(statements)\\t%s%%\\n' "${STUB_GO}" ;;
  *) exit 1 ;;
esac
"""

MISE = """\
#!/usr/bin/env bash
# With STUB_FIXTURE, the captured mise answers (#1608): `tasks info`
# from the capture, `test` being the task STUB_ROOT names, its probe
# directory the planted tree; `tasks ls` the capture's names.
if [[ -n ${STUB_FIXTURE:-} ]]; then
  case "$1 $2" in
    "tasks info")
      n=$3
      [[ ${n} != test ]] || n=${STUB_ROOT}
      exec jq -e --arg n "${n}" --arg d "${STUB_DIR}" \\
        '."tasks info --json"[$n] // empty | .dir |= sub("<probe>"; $d)' \\
        "${STUB_FIXTURE}"
      ;;
    "tasks ls")
      exec jq '[."tasks ls --hidden --json"[] | {name: .}]' "${STUB_FIXTURE}"
      ;;
    *) exit 1 ;;
  esac
fi
# `tasks info <name> --json`: the planted test task, or a planted
# dependency of it beside that file as task.<name>.json.
if [[ "$1 $2" == "tasks info" ]]; then
  f=${STUB_TASK}
  [[ $3 == test ]] || f="${STUB_TASK%.json}.$3.json"
  if [[ -f ${f} ]]; then
    cat "${f}"
    exit 0
  fi
fi
exit 1
"""

# The repository's own vitest, installed in node_modules: writes the
# json-summary and lcov reports its provider would, with planted totals.
VITEST = """\
#!/usr/bin/env bash
set -euo pipefail
dir=""
for arg in "$@"; do
  case "${arg}" in --coverage.reportsDirectory=*) dir=${arg#*=} ;; esac
done
echo "vitest suite ran" >&2
echo "vitest args: $*" >&2
[[ -n ${dir} ]] || exit 0
lines='{"total": {"lines": {"pct": %s}, "branches": {"pct": %s}}}\\n'
printf "${lines}" "${STUB_TS}" "${STUB_TS_BR}" > "${dir}/coverage-summary.json"
echo "TN:" > "${dir}/lcov.info"
"""

# `mise tasks info --json` for every task of a probe holding each way
# mise 2026.11.7 documents for one task to run another (#1608), captured
# from task-references/probe.toml.
TASK_REFERENCES = BELT / "testdata" / "task-references" / "tasks-info.json"

# The head of every refusal of the test task's vitest command.
HEAD_MSG = (
    "coverage:check: typescript: the leg measures the test task's own vitest command"
)

# Lockfile entries, in pnpm's form, by what the row plants.
PNPM_VITEST = "  vitest@4.1.12:\n    resolution: {integrity: stub}\n"
PNPM_V8 = "  '@vitest/coverage-v8@4.1.12':\n    resolution: {integrity: stub}\n"


def plant_typescript(tree: Path, runner: str) -> list[str]:
    """Plant a JavaScript tree whose runner is `runner`.

    `vitest` pins the v8 provider beside it; `vitest-bare` pins vitest
    alone; any other name is declared in package.json with no vitest.

    Returns:
        The paths to track.

    """
    if runner.startswith("vitest"):
        manifest = {"devDependencies": {"vitest": "4.1.12"}}
        lock = "lockfileVersion: '9.0'\npackages:\n" + PNPM_VITEST
        if runner == "vitest":
            lock += PNPM_V8
        (tree / "pnpm-lock.yaml").write_text(lock, encoding="utf-8")
        bin_dir = tree / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "vitest").write_text(VITEST, encoding="utf-8")
        (bin_dir / "vitest").chmod(0o755)
        tracked = ["package.json", "pnpm-lock.yaml"]
    else:
        manifest = {"devDependencies": {runner: "1.0.0"}}
        tracked = ["package.json"]
    (tree / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tracked


def upload_line(tree: Path, *names: str, label: str = "coverage:check") -> str:
    """Render the upload line naming reports in the row's directory.

    Returns:
        The line, with each name under the ORG_COVERAGE_REPORTS the
        planted run was given: `reports` beside the tree.

    """
    paths = ",".join(str(tree.parent / "reports" / name) for name in names)
    return f"{label}: upload {paths}\n"


def reports_env(tmp: Path, *, reports_dir: bool | str) -> dict[str, str]:
    """Name the row's ORG_COVERAGE_REPORTS, or leave it unset.

    Returns:
        The variable to add: `reports` beside the tree for True, the
        given path for a string, nothing for False.

    """
    if not reports_dir:
        return {}
    named = tmp / "reports" if reports_dir is True else Path(reports_dir)
    return {"ORG_COVERAGE_REPORTS": str(named)}


def init_tree(
    tree: Path, env: dict[str, str], tracked: list[str], *, commit: bool
) -> None:
    """Make the planted tree a repository tracking `tracked`.

    `commit` commits it, so `git status` reads what the run changed.
    """
    subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
    if tracked:
        subprocess.run([GIT, "add", *tracked], cwd=tree, env=env, check=True)
    if commit:
        subprocess.run(
            [GIT, "-c", "commit.gpgsign=false", "commit", "-qm", "planted"],
            cwd=tree,
            env=env,
            check=True,
        )


def captured_env(captured_test: str | None, tree: Path) -> dict[str, str]:
    """Point the mise stub at the task-references capture, if asked.

    Returns:
        The stub's variables, or none when no captured task is `test`.

    """
    if captured_test is None:
        return {}
    return {
        "STUB_FIXTURE": str(TASK_REFERENCES),
        "STUB_ROOT": captured_test,
        "STUB_DIR": str(tree),
    }


def measuring_step(task: str) -> str:
    """Read the step of a coverage task that measures (#1667).

    `coverage:adopt` is a run list: create the floor, `test` by
    reference, then the measurement, which is the step these rows run.

    Returns:
        The task's script, or its run list's last one.

    """
    belt = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(belt["tasks"], "the belt's [tasks]")
    run = decoded.as_object(tasks[task], task)["run"]
    if isinstance(run, str):
        return run
    steps = decoded.as_array(run, f"{task}'s run")
    return decoded.as_str(steps[-1], f"{task}'s last step")


def plant_run(run: Path, *, python: bool = True) -> None:
    """Leave a run's directory as `measured` leaves it (#1667, #1651).

    The suite ran through `measured`, which wrote the run's rc file and
    coverage.py's data, and each Go test process its counters; the stub
    `coverage` and `go` read none of them, but the legs find them.
    `python` False leaves out coverage.py's data: a Python suite that ran
    outside `measured`.
    """
    (run / "go").mkdir(parents=True)
    (run / "go" / "covcounters.stub").write_text("", encoding="utf-8")
    if python:
        (run / "python").mkdir()
        (run / "coverage.rc").write_text("[run]\n", encoding="utf-8")
        (run / "python" / ".coverage.stub").write_text("", encoding="utf-8")


def run_task(  # ruff: ignore[too-many-arguments] -- one keyword per planted fact
    task: str,
    *,
    py: str = "90.0",
    branches: str = "85.0",
    go: str | None = None,
    floor: bool = True,
    test_task: bool = True,
    python: bool = True,
    typescript: str | None = None,
    ts: str = "88.0",
    ts_branches: str = "81.0",
    test_run: str | None = None,
    depends: dict[str, str] | None = None,
    go_profile: bool = True,
    captured_test: str | None = None,
    reports_dir: bool | str = True,
    commit: bool = False,
    measured: bool = True,
    python_measured: bool = True,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run one coverage task's body in a planted tree with stub tools.

    `captured_test` names the captured task-references task mise
    answers as `test`, every other task as captured. `reports_dir`
    names `reports`, beside the tree and not yet made, as
    ORG_COVERAGE_REPORTS, as the gate job names one; False leaves the
    variable unset, as on a developer's machine; a path names that.
    `commit` commits the planted tree, so `git status` reads what the
    run changed. `measured` leaves the run's directory as a suite run
    through `measured` does (#1667); False, as one that ran outside it;
    `python_measured` False, with Go's counters and no Python data.

    Returns:
        The finished process, and a snapshot directory holding the
        tree's files as the task left them.

    """
    tmp = Path(tempfile.mkdtemp())
    tree = tmp / "tree"
    stubs = tmp / "stubs"
    tree.mkdir()
    stubs.mkdir()
    for name, text in (("coverage", COVERAGE), ("go", GO), ("mise", MISE)):
        (stubs / name).write_text(text, encoding="utf-8")
        (stubs / name).chmod(0o755)
    (stubs / "python3").symlink_to(sys.executable)
    task_json = tmp / "task.json"
    if test_run is None and typescript is not None:
        # A vitest tree's test task runs vitest (#1357 item 2); the python
        # leg runs the same line, so it is the stub binary, not pnpm.
        test_run = "node_modules/.bin/vitest run"
    for name, run in (depends or {}).items():
        answer: dict[str, object] = {"dir": str(tree), "run": [run]}
        (tmp / f"task.{name}.json").write_text(json.dumps(answer), encoding="utf-8")
    if test_task:
        test_answer: dict[str, object] = {
            "dir": str(tree),
            "run": [test_run or "echo suite ran"],
            "depends": sorted(depends or {}),
        }
        task_json.write_text(json.dumps(test_answer), encoding="utf-8")

    tracked = []
    if python:
        (tree / "helper.py").write_text("print('hi')\n", encoding="utf-8")
        tracked.append("helper.py")
    if go is not None:
        (tree / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
        tracked.append("go.mod")
    if typescript is not None:
        tracked += plant_typescript(tree, typescript)
    if floor:
        (tree / ".coverage-floor").write_text(FLOOR, encoding="utf-8")
        tracked.append(".coverage-floor")

    env = hermetic.env()
    env |= {
        "ORG_BELT_DIR": str(BELT),
        "PATH": f"{stubs}{os.pathsep}{env.get('PATH', '')}",
        "STUB_PYTHON": sys.executable,
        "STUB_PY": py,
        "STUB_BR": branches,
        "STUB_GO": go or "",
        "STUB_GO_NO_PROFILE": "" if go_profile else "1",
        "STUB_TASK": str(task_json),
        "STUB_TS": ts,
        "STUB_TS_BR": ts_branches,
    }
    env |= captured_env(captured_test, tree)
    env |= reports_env(tmp, reports_dir=reports_dir)
    env["ORG_COVERAGE_RUN"] = str(tmp / "run")
    if measured:
        plant_run(tmp / "run", python=python_measured)
    init_tree(tree, env, tracked, commit=commit)
    done = subprocess.run(
        [BASH, "-euo", "pipefail", "-c", measuring_step(task)],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return done, tmp


class VitestPlant(TypedDict, total=False):
    """`run_task`'s keyword parameters but the three `refused` sets."""

    py: str
    branches: str
    go: str | None
    floor: bool
    test_task: bool
    ts: str
    ts_branches: str
    depends: dict[str, str] | None
    go_profile: bool
    captured_test: str | None
    reports_dir: bool | str
    commit: bool
    measured: bool
    python_measured: bool


class Plant(VitestPlant, total=False):
    """The facts a row may plant: every `run_task` keyword parameter."""

    python: bool
    typescript: str | None
    test_run: str | None


class CoverageTaskCase(unittest.TestCase):
    """Runs a task and removes the planted tree afterwards."""

    def run_task(
        self, task: str, **plant: Unpack[Plant]
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        """Run a task and schedule its tree for removal.

        Returns:
            The finished process and the planted tree.

        """
        done, tmp = run_task(task, **plant)
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        return done, tmp / "tree"


class CheckRows(CoverageTaskCase):
    """`coverage:check` over the Python leg, alone and beside Go."""

    def test_python_only_tree_measures_and_holds(self) -> None:
        """Both Python legs print, and each holds the floor by name."""
        done, tree = self.run_task("coverage:check")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:check: python leg holds the floor 80.0 (90.0%)\n"
            "coverage:check: python-branches leg holds the floor 80.0 (85.0%)\n"
            + upload_line(tree, "coverage.xml"),
            done.stdout,
        )
        # The check reads the run `measured` left and runs no suite (#1667).
        self.assertNotIn("suite ran", done.stderr)

    def test_python_run_outside_measured_reports_no_figure(self) -> None:
        """A suite that ran outside `measured`: no python figure, by name."""
        done, _ = self.run_task("coverage:check", go="91.0", python_measured=False)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn(
            "coverage:check: python: the test run measured no Python — its test"
            ' command runs\n  through "${ORG_BELT_DIR}/measured" (#1667),'
            " or no suite ran under mise\n",
            done.stderr,
        )
        self.assertNotIn("python", done.stdout)

    def test_branches_below_the_floor_red_by_name(self) -> None:
        """Statements holding does not carry the branch leg."""
        done, _ = self.run_task("coverage:check", branches="79.9")
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "coverage:check: python-branches: 79.9% is below the derived floor 80.0%\n",
            done.stderr,
        )

    def test_statements_below_the_floor_red_by_name(self) -> None:
        """The line leg reds before the branch leg is read."""
        done, _ = self.run_task("coverage:check", py="70.0")
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "coverage:check: python: 70.0% is below the derived floor 80.0%\n",
            done.stderr,
        )

    def test_no_floor_skips_clean(self) -> None:
        """A repository that tracks Python but adopted no floor owes nothing."""
        done, _ = self.run_task("coverage:check", floor=False)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("coverage:check: no .coverage-floor, skipped\n", done.stdout)
        self.assertNotIn("suite ran", done.stderr)

    def test_python_without_a_test_task_skips_clean(self) -> None:
        """Tracked Python with no suite has no corpus to hold a floor over."""
        done, _ = self.run_task("coverage:check", test_task=False)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:check: no measurable language (go.mod, Cargo.toml,"
            " tracked *.py with a test task, package.json with vitest, or"
            " a floor recording its legs),"
            " skipped\n",
            done.stdout,
        )

    def test_mixed_go_and_python_prints_every_leg(self) -> None:
        """A mixed tree measures each language it tracks."""
        done, tree = self.run_task("coverage:check", go="93.0")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:check: go leg holds the floor 80.0 (93.0%)\n"
            "coverage:check: python leg holds the floor 80.0 (90.0%)\n"
            "coverage:check: python-branches leg holds the floor 80.0 (85.0%)\n"
            + upload_line(tree, "coverage.txt", "coverage.xml"),
            done.stdout,
        )

    def test_mixed_tree_every_leg_must_hold(self) -> None:
        """Python holding does not carry a Go leg below the floor."""
        done, _ = self.run_task("coverage:check", go="75.0")
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "coverage:check: go: 75.0% is below the derived floor 80.0%\n",
            done.stderr,
        )


class TypeScriptRows(CoverageTaskCase):
    """`coverage:check` over the TypeScript leg (#1357)."""

    def test_a_vitest_tree_measures_and_holds(self) -> None:
        """Both TypeScript legs print, and each holds the floor by name."""
        done, tree = self.run_task("coverage:check", python=False, typescript="vitest")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:check: typescript leg holds the floor 80.0 (88.0%)\n"
            "coverage:check: typescript-branches leg holds the floor 80.0 (81.0%)\n"
            + upload_line(tree, "lcov-typescript.info"),
            done.stdout,
        )
        self.assertIn("vitest suite ran", done.stderr)

    def test_branches_below_the_floor_red_by_name(self) -> None:
        """Lines holding does not carry the branch leg."""
        done, _ = self.run_task(
            "coverage:check", python=False, typescript="vitest", ts_branches="79.5"
        )
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "coverage:check: typescript-branches: 79.5% is below the derived"
            " floor 80.0%\n",
            done.stderr,
        )

    def test_lines_below_the_floor_red_by_name(self) -> None:
        """The line leg reds before the branch leg is read."""
        done, _ = self.run_task(
            "coverage:check", python=False, typescript="vitest", ts="60.0"
        )
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "coverage:check: typescript: 60.0% is below the derived floor 80.0%\n",
            done.stderr,
        )

    def test_no_floor_skips_clean(self) -> None:
        """A vitest tree with no floor owes nothing, and runs nothing."""
        done, _ = self.run_task(
            "coverage:check", python=False, typescript="vitest", floor=False
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("coverage:check: no .coverage-floor, skipped\n", done.stdout)
        self.assertNotIn("vitest suite ran", done.stderr)

    def test_an_unsupported_runner_is_named_and_skipped(self) -> None:
        """A jest tree is named, never guessed at or measured as nothing."""
        done, _ = self.run_task("coverage:check", python=False, typescript="jest")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn(
            "coverage:check: typescript: jest is not a runner the leg supports"
            " (vitest), skipped\n",
            done.stderr,
        )
        self.assertEqual(
            "coverage:check: no measurable language (go.mod, Cargo.toml,"
            " tracked *.py with a test task, package.json with vitest, or"
            " a floor recording its legs),"
            " skipped\n",
            done.stdout,
        )

    def test_vitest_without_a_provider_reds_with_the_remedy(self) -> None:
        """The belt pins no provider; the lockfile must."""
        done, _ = self.run_task(
            "coverage:check", python=False, typescript="vitest-bare"
        )
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "coverage:check: vitest runs the suite but the lockfile pins no"
            " coverage provider —\n"
            "  add @vitest/coverage-v8 to devDependencies at vitest's own version\n",
            done.stderr,
        )

    def test_mixed_python_and_typescript_prints_every_leg(self) -> None:
        """A mixed tree measures each language it tracks."""
        done, tree = self.run_task("coverage:check", typescript="vitest")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:check: python leg holds the floor 80.0 (90.0%)\n"
            "coverage:check: python-branches leg holds the floor 80.0 (85.0%)\n"
            "coverage:check: typescript leg holds the floor 80.0 (88.0%)\n"
            "coverage:check: typescript-branches leg holds the floor 80.0 (81.0%)\n"
            + upload_line(tree, "lcov-typescript.info", "coverage.xml"),
            done.stdout,
        )

    def test_the_test_task_selection_is_the_measured_selection(self) -> None:
        """A `test` task narrower than vitest's config is measured as written.

        #1357 item 2: the leg runs the repository's own test command, so a
        task running `vitest run tests/unit` is measured over tests/unit,
        not over every file the vitest config would select.
        """
        done, _ = self.run_task(
            "coverage:check",
            python=False,
            typescript="vitest",
            test_run="vitest run tests/unit",
        )
        self.assertEqual(0, done.returncode, done.stderr)
        runs = [
            ln.removeprefix("vitest args: ").split()
            for ln in done.stderr.splitlines()
            if ln.startswith("vitest args: ")
        ]
        self.assertEqual(1, len(runs), done.stderr)
        self.assertIn("tests/unit", runs[0])
        self.assertIn("--coverage.enabled=true", runs[0])

    def test_a_depended_on_vitest_task_is_followed(self) -> None:
        """The test task's `depends` are read: vitest may live in one."""
        done, _ = self.run_task(
            "coverage:check",
            python=False,
            typescript="vitest",
            test_run="echo test: unit tests green",
            depends={"test:unit": "pnpm exec vitest run tests/unit"},
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn(
            "vitest args: run tests/unit --coverage.enabled=true", done.stderr
        )

    def test_a_vitest_tree_without_a_test_task_skips_clean(self) -> None:
        """No test task, no suite the gate runs: nothing to measure."""
        done, _ = self.run_task(
            "coverage:check", python=False, typescript="vitest", test_task=False
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("no measurable language", done.stdout)
        self.assertNotIn("vitest suite ran", done.stderr)

    def refused(self, run: str, message: str, **plant: Unpack[VitestPlant]) -> None:
        """Assert a test task whose vitest command is refused, by message."""
        done, _ = self.run_task(
            "coverage:check",
            python=False,
            typescript="vitest",
            test_run=run,
            **plant,
        )
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertIn(message, done.stderr)
        self.assertNotIn("vitest suite ran", done.stderr)

    def test_a_test_task_without_vitest_is_refused(self) -> None:
        """A `pnpm test` behind a package.json script is not read through."""
        self.refused(
            "pnpm test", HEAD_MSG + ", and the test task and its depends run none —\n"
        )

    def test_two_vitest_commands_are_refused(self) -> None:
        """The leg measures one suite."""
        self.refused(
            "vitest run tests/a",
            HEAD_MSG + ", and the test task and its depends run 2 —\n",
            depends={"test:more": "vitest run tests/b"},
        )

    def test_a_shell_expansion_is_refused(self) -> None:
        """Arguments only the shell can resolve are not guessed at."""
        self.refused(
            "vitest run ${FILES}",
            "  test: a shell expansion in `vitest run ${FILES}`\n",
        )

    def test_a_compound_line_is_refused(self) -> None:
        """A `cd` before vitest is a directory the leg would not honour."""
        self.refused(
            "cd web && vitest run",
            "  test: vitest inside a compound line, `cd web && vitest run`\n",
        )

    def test_an_unknown_launcher_is_refused(self) -> None:
        """Only the package managers' plain launchers are read."""
        self.refused(
            "bunx vitest run",
            "  test: vitest behind a command the leg does not read,"
            " `bunx vitest run`\n",
        )

    def test_a_subcommand_other_than_run_is_refused(self) -> None:
        """`vitest watch` is not a suite run."""
        self.refused("vitest watch", HEAD_MSG + ", and `vitest watch` is not a run —\n")

    def test_a_coverage_flag_in_the_task_is_refused(self) -> None:
        """The leg owns the coverage flags."""
        self.refused(
            "vitest run --coverage.enabled=false",
            HEAD_MSG + ", and it sets coverage itself —\n",
        )

    def test_check_writes_the_typescript_lcov(self) -> None:
        """The upload is the measured run's own lcov, off lcov.info."""
        done, tree = self.run_task("coverage:check", python=False, typescript="vitest")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(
            done.stdout.endswith(upload_line(tree, "lcov-typescript.info")),
            done.stdout,
        )
        reports = tree.parent / "reports"
        self.assertEqual("TN:\n", (reports / "lcov-typescript.info").read_text("utf-8"))
        self.assertFalse((reports / "lcov.info").exists())


class TaskReferenceRows(CoverageTaskCase):
    """The leg follows every task the test task runs to vitest (#1608).

    mise answers from the task-references capture: its own `tasks info
    --json` for a probe holding each documented way one task runs
    another.
    """

    def captured(self, root: str) -> subprocess.CompletedProcess[str]:
        """Run the typescript leg with the captured task as `test`.

        Returns:
            The finished process.

        """
        done, _ = self.run_task(
            "coverage:check", python=False, typescript="vitest", captured_test=root
        )
        return done

    def found_through(self, root: str) -> None:
        """Assert the captured task's vitest is reached and measured."""
        done = self.captured(root)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn(
            "vitest args: run tests/unit --coverage.enabled=true", done.stderr
        )

    def test_vitest_through_a_run_task_entry_is_found(self) -> None:
        """`run = [{ task = "unit" }, …]` runs unit, where vitest is (#1608)."""
        self.found_through("run-task")

    def test_vitest_through_a_run_tasks_entry_is_found(self) -> None:
        """`run = [{ tasks = ["lint", "unit"] }]` runs unit too (#1608)."""
        self.found_through("run-tasks")

    def test_vitest_through_depends_post_is_found(self) -> None:
        """`depends_post = ["unit"]` runs unit after the task (#1608)."""
        self.found_through("post-name")

    def test_every_captured_reference_form_is_followed(self) -> None:
        """Each form that runs a task as written reaches unit (#1608).

        A depends name and lone string, a table depends_post, and a run
        inherited through `extends`. Patterns are test_task_walk.py's:
        the probe's matches run no vitest.
        """
        for root in ("depends-name", "depends-string", "post-table", "extends"):
            with self.subTest(root=root):
                self.found_through(root)

    def test_a_depends_with_arguments_is_refused_by_name(self) -> None:
        """A depends with arguments: mise reports an array (#1608).

        On main the jq walk died on it with jq's own error, naming no
        task. The measured run cannot pass the arguments, so the walk
        refuses the task that asks for them.
        """
        done = self.captured("depends-args")
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertIn(
            "coverage:check: typescript: task 'test' runs 'unit' with arguments,"
            " which the measured run cannot pass\n",
            done.stderr,
        )
        self.assertNotIn("vitest suite ran", done.stderr)

    def test_every_form_that_passes_something_is_refused(self) -> None:
        """An env prefix, a table with args, a `{ task }` with args."""
        for root, what in (
            ("depends-array", "arguments"),
            ("depends-env", "environment"),
            ("depends-table", "arguments"),
            ("run-task-args", "arguments"),
        ):
            with self.subTest(root=root):
                done = self.captured(root)
                self.assertEqual(1, done.returncode, done.stderr)
                self.assertIn(f"runs 'unit' with {what},", done.stderr)

    def test_a_cycle_ends_and_refuses_by_name(self) -> None:
        """cycle-a depends on cycle-b, which runs cycle-a: no vitest."""
        done = self.captured("cycle-a")
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertIn(
            HEAD_MSG + ", and the test task and its depends run none —\n"
            "  tasks walked: test, cycle-b, cycle-a\n",
            done.stderr,
        )
        self.assertNotIn("vitest suite ran", done.stderr)

    def test_no_vitest_anywhere_names_the_tasks_walked(self) -> None:
        """A depends and a `{ task }` entry are walked, and named."""
        done = self.captured("none")
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertIn(
            HEAD_MSG + ", and the test task and its depends run none —\n"
            "  tasks walked: test, lint, run-string\n",
            done.stderr,
        )
        self.assertNotIn("vitest suite ran", done.stderr)

    def test_wait_for_is_not_followed(self) -> None:
        """`wait_for` adds no task to the run, so its vitest is not the suite."""
        done = self.captured("wait-for")
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertIn("  tasks walked: test\n", done.stderr)


def upload_step() -> str:
    """Read the upload job's "List the reports" `run:` value from ci.yml.

    Returns:
        The value, as the runner's bash receives it.

    """
    ci = workflow_yaml.document(CANON / ".github/workflows/ci.yml")
    (run,) = [
        s["run"]
        for s in workflow_yaml.steps(workflow_yaml.jobs(ci)["coverage-upload"])
        if s.get("name") == "List the reports"
    ]
    return decoded.as_str(run, "run")


def run_upload_step(
    reports: list[str], *, has_token: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run the lifted step beside the planted reports.

    Returns:
        The finished step, its GITHUB_OUTPUT appended to stdout.

    """
    with tempfile.TemporaryDirectory() as tmp:
        for name in reports:
            (Path(tmp) / name).write_text("report\n", encoding="utf-8")
        out = Path(tmp) / "outputs"
        env = hermetic.env(
            PATH=os.environ.get("PATH", ""),
            HAS_TOKEN="true" if has_token else "false",
            GITHUB_OUTPUT=str(out),
        )
        done = subprocess.run(
            [BASH, "-e", "-c", upload_step()],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        done.stdout += out.read_text("utf-8") if out.exists() else ""
        return done


class UploadStep(unittest.TestCase):
    """ci.yml's upload job hands Codecov what the gate's artifact holds.

    The job measures nothing (#1528): it downloads the reports the gate's
    coverage:check wrote and lists them, in coverage:check's order. These
    rows run the lifted step in a directory holding the planted reports.
    """

    def test_every_report_the_gate_wrote_is_named_and_uploaded(self) -> None:
        """A mixed tree uploads each report, in coverage:check's order."""
        done = run_upload_step(["coverage.xml", "lcov.info", "coverage.txt"])
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "upload lcov.info\\,coverage.txt\\,coverage.xml\n"
            "files=lcov.info,coverage.txt,coverage.xml\n",
            done.stdout,
        )

    def test_a_python_only_tree_uploads(self) -> None:
        """The step once skipped any tree without Cargo.toml or go.mod."""
        done = run_upload_step(["coverage.xml"])
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("upload coverage.xml\nfiles=coverage.xml\n", done.stdout)

    def test_no_token_skips_and_says_why(self) -> None:
        """No token: nothing is uploaded, and the step says why."""
        done = run_upload_step(["coverage.xml"], has_token=False)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "::notice::no token; badge upload skipped\nfiles=\n",
            done.stdout,
        )

    def test_no_report_skips_by_name(self) -> None:
        """Nothing came down: skipped by name, never an upload of nothing.

        The job reads no gate output (#1740), so a tree without a floor,
        whose gate wrote no report, reaches this step with none.
        """
        done = run_upload_step(["unrelated.txt"])
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "::notice::no coverage report came down from the gate;"
            " badge upload skipped\nfiles=\n",
            done.stdout,
        )


class AdoptRows(CoverageTaskCase):
    """The write-mode half reads the same measurement."""

    def test_adopt_derives_from_the_weakest_python_leg(self) -> None:
        """Branches below statements set the floor."""
        done, tree = self.run_task("coverage:adopt", floor=False, branches="83.47")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage-floor: adopted .coverage-floor at 81.4 (measured 83.4)\n",
            done.stdout,
        )
        self.assertTrue((tree / ".coverage-floor").is_file())

    def test_adopt_without_a_measurable_language_measures_nothing(self) -> None:
        """No suite, nothing to adopt."""
        done, _ = self.run_task("coverage:adopt", floor=False, test_task=False)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:adopt: no measurable language (go.mod, Cargo.toml,"
            " tracked *.py with a test task, package.json with vitest, or"
            " a floor recording its legs),"
            " nothing to measure\n",
            done.stdout,
        )


class CheckUploadLine(CoverageTaskCase):
    """coverage:check names the reports this run wrote (#1528).

    The badge job uploads what the gate measured instead of measuring
    again, so the gate's run must write each leg's report and say which.
    """

    def test_the_line_lists_the_report_the_run_wrote(self) -> None:
        """The Cobertura report is rendered from the leg's own run."""
        done, tree = self.run_task("coverage:check")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(
            done.stdout.endswith(upload_line(tree, "coverage.xml")),
            done.stdout,
        )
        self.assertEqual(
            "<coverage/>\n",
            (tree.parent / "reports" / "coverage.xml").read_text("utf-8"),
        )


class CheckLeavesTheTree(CoverageTaskCase):
    """A coverage run leaves the working tree as it found it (#1628).

    The reports were written into the tree, where nothing ignores them,
    and a local run followed by `git add -A` committed them.
    """

    @staticmethod
    def status(tree: Path) -> str:
        """Read the planted tree's `git status --porcelain`.

        Returns:
            The status, empty for a tree as it was planted.

        """
        return subprocess.run(
            [GIT, "status", "--porcelain"],
            cwd=tree,
            env=hermetic.env(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def named(self, stdout: str) -> list[Path]:
        """Read the paths the upload line names.

        Returns:
            Each path, in the line's order.

        """
        line = stdout.splitlines()[-1]
        self.assertTrue(line.startswith("coverage:check: upload "), stdout)
        return [Path(p) for p in line.split(" upload ", 1)[1].split(",")]

    def test_a_named_directory_holds_every_report_and_the_tree_is_clean(
        self,
    ) -> None:
        """The gate's shape: the files the line names exist, outside it."""
        done, tree = self.run_task("coverage:check", go="93.0", commit=True)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("", self.status(tree))
        named = self.named(done.stdout)
        reports = [
            tree.parent / "reports" / n for n in ("coverage.txt", "coverage.xml")
        ]
        self.assertEqual(reports, named)
        for path in named:
            self.assertTrue(path.is_file(), path)

    def test_the_figures_the_check_judged_are_kept_beside_the_reports(
        self,
    ) -> None:
        """coverage-figures.txt: the leg lines, for the release job (#1622)."""
        done, tree = self.run_task("coverage:check", go="93.0", commit=True)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "go 93.0\npython 90.0\npython-branches 85.0\n",
            (tree.parent / "reports" / "coverage-figures.txt").read_text(
                encoding="utf-8"
            ),
        )
        self.assertNotIn("coverage-figures.txt", done.stdout)

    def test_unset_writes_a_scratch_directory_and_removes_it(self) -> None:
        """A developer's run: the tree is clean and the scratch is gone."""
        done, tree = self.run_task(
            "coverage:check", go="93.0", reports_dir=False, commit=True
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("", self.status(tree))
        named = self.named(done.stdout)
        files: list[str] = ["coverage.txt", "coverage.xml"]
        names = [p.name for p in named]
        self.assertEqual(files, names)
        scratch = named[0].parent
        self.assertNotEqual(tree, scratch)
        self.assertNotIn(tree, scratch.parents)
        self.assertFalse(scratch.exists(), scratch)

    def test_a_run_that_wrote_no_report_is_refused(self) -> None:
        """A leg that ran and left no report is refused by the task."""
        # The Go stub measures but writes no profile, so the Go leg ran
        # and nothing exists to upload.
        done, _ = self.run_task(
            "coverage:check", go="90.0", python=False, go_profile=False
        )
        self.assertEqual(1, done.returncode, done.stdout)
        self.assertIn(
            "coverage:check: the legs measured wrote no report — nothing for"
            " the badge to upload",
            done.stderr,
        )
        self.assertNotIn("upload ", done.stdout)

    def test_no_floor_lists_nothing(self) -> None:
        """No floor: skipped, and no upload line for the badge to read."""
        done, _ = self.run_task("coverage:check", floor=False)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertNotIn("upload", done.stdout)

    def test_report_task_is_gone(self) -> None:
        """The badge's second measurement no longer exists to drift."""
        self.assertNotIn(
            "coverage:report", decoded.as_object(CONFIG["tasks"], "the belt's [tasks]")
        )


# The Rust leg's tools: rustup asserting llvm-tools, and cargo answering
# the workspace read (no pgrx crate) and the measurement.
RUSTUP = """\
#!/usr/bin/env bash
echo "llvm-tools-stub"
"""

# llvm-tools first, then a listing past a pipe buffer: a probe that stops
# at the first line closes the pipe on a rustup still writing (#1637).
RUSTUP_LONG = """\
#!/usr/bin/env bash
echo "llvm-tools-stub"
head -c 200000 /dev/zero | tr '\\0' '\\n'
"""

# A stand-in cargo, enough to follow the Rust leg (#1653). It logs each
# call to STUB_DIR/cargo.log; answers `metadata` with the planted tree as
# workspace root and target (STUB_METADATA, a file, replaces the
# packages, `<root>` read as the root); `llvm-cov show-env` as
# cargo-llvm-cov 0.9.1 does, the profile path under the CARGO_TARGET_DIR
# it is given; `test` writes one
# profile there when instrumented, or fails with STUB_CARGO_FAIL; and
# `llvm-cov report` writes lcov for each `file:found:hit` in STUB_FILES
# whose absolute path no `--ignore-filename-regex` matches, as the real
# report leaves such a file out.
CARGO = """\
#!/usr/bin/env bash
echo "$*" >> "${STUB_DIR}/cargo.log"
out=""
ignore=()
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[i]}" in
    --output-path) out=${args[i + 1]} ;;
    --ignore-filename-regex) ignore+=("${args[i + 1]}") ;;
    *) ;;
  esac
done
case "$1 ${2:-}" in
  "metadata --no-deps")
    packages='{"packages": [], "workspace_members": []}'
    if [[ -n ${STUB_METADATA:-} ]]; then
      packages=$(sed "s|<root>|${PWD}|g" "${STUB_METADATA}")
    fi
    jq -c --arg root "${PWD}" \\
      '. + {target_directory: ($root + "/target"), workspace_root: $root}' \\
      <<< "${packages}"
    ;;
  "llvm-cov show-env")
    echo "export LLVM_PROFILE_FILE='${CARGO_TARGET_DIR}/tree-%p-%8m.profraw'"
    echo "export RUSTC_WRAPPER=/stub/cargo-llvm-cov"
    echo "export CARGO_LLVM_COV_TARGET_DIR='${CARGO_TARGET_DIR}'"
    ;;
  "llvm-cov clean") rm -f "${CARGO_TARGET_DIR}"/*.profraw ;;
  "llvm-cov report")
    : > "${out}"
    for record in ${STUB_FILES}; do
      IFS=: read -r file found hit <<< "${record}"
      for one in ${ignore[@]+"${ignore[@]}"}; do
        [[ ! ${PWD}/${file} =~ ${one} ]] || continue 2
      done
      printf 'SF:%s\\nLF:%s\\nLH:%s\\nend_of_record\\n' \\
        "${PWD}/${file}" "${found}" "${hit}" >> "${out}"
    done
    ;;
  "test "*)
    [[ -z ${STUB_CARGO_FAIL:-} ]] || exit 101
    if [[ -n ${LLVM_PROFILE_FILE:-} ]]; then
      mkdir -p "$(dirname "${LLVM_PROFILE_FILE}")"
      : > "$(dirname "${LLVM_PROFILE_FILE}")/tree-$$-1.profraw"
    fi
    ;;
  *) exit 1 ;;
esac
"""


# What a row expects when the stand-in cargo was not called so, typed so
# the comparison carries no Any.
NO_CALLS: list[str] = []

# The rust surface's refusal of the removed COVERAGE_EXCLUDE (#1653).
COVERAGE_EXCLUDE_REFUSAL = (
    "coverage:check: COVERAGE_EXCLUDE is set ({named}) — it is removed (#1653):\n"
    "  the rust leg measures what the test task's own command builds, so the"
    " command\n"
    "  says what it leaves out (cargo test --workspace --exclude <member>);"
    " delete\n"
    "  the variable from mise.toml's [env] (a pgrx extension needs no line:"
    " test:pgrx\n"
    "  measures it)\n"
)


# The directory digest of the stand-in `rustc` the Rust rows run beside
# (#1877), under the `stable` toolchain they set.
STABLE = gate_cache_key.directory_digest(hermetic.rustc_verbose("stable"))


def plant_rust_run(tree: Path, run: Path) -> None:
    """Leave what a measured cargo test leaves: the run's marker, one profile."""
    (run / "rust").mkdir(parents=True)
    profiles = tree / "target" / f"llvm-cov-target-{STABLE}"
    profiles.mkdir(parents=True)
    (profiles / "tree-1-1.profraw").write_text("", encoding="utf-8")


class MeasureSelectors(unittest.TestCase):
    """Every selector the script accepts measures its leg."""

    def test_the_rust_selector_measures_the_rust_leg(self) -> None:
        """`rust` is accepted, so it must not measure nothing and pass."""
        self.assert_rust_leg_measured(RUSTUP)

    def test_llvm_tools_ahead_of_a_long_listing_is_found(self) -> None:
        """`llvm-tools…` first, then 200 kB: the leg measures (#1637).

        On main the probe stopped at the first line and the leg reported
        the component missing.
        """
        self.assert_rust_leg_measured(RUSTUP_LONG)

    def assert_rust_leg_measured(self, rustup: str) -> None:
        """Run the `rust` selector with this rustup and require its number."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            stubs = Path(tmp) / "stubs"
            tree.mkdir()
            stubs.mkdir()
            for name, text in (("rustup", rustup), ("cargo", CARGO)):
                (stubs / name).write_text(text, encoding="utf-8")
                (stubs / name).chmod(0o755)
            hermetic.rustc(stubs)
            (tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
            plant_rust_run(tree, Path(tmp) / "run")
            env = hermetic.env()
            env |= {
                "ORG_BELT_DIR": str(BELT),
                "ORG_COVERAGE_RUN": str(Path(tmp) / "run"),
                "RUSTUP_TOOLCHAIN": "stable",
                "PATH": f"{stubs}{os.pathsep}{env.get('PATH', '')}",
                "STUB_DIR": tmp,
                "STUB_FILES": "a.rs:13:8 b.rs:0:0",
            }
            env["ORG_COVERAGE_REPORTS"] = str(Path(tmp) / "reports")
            done = subprocess.run(
                [BASH, str(BELT / "coverage-measure.sh"), "coverage:test", "rust"],
                cwd=tree,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            written = (Path(tmp) / "reports" / "lcov.info").is_file()
        self.assertEqual(0, done.returncode, done.stderr)
        # 8 of 13 lines, summed over every file: the report it wrote.
        self.assertEqual("rust 61.53846153846154\n", done.stdout)
        self.assertTrue(written)

    def test_a_go_and_rust_tree_measures_both_legs(self) -> None:
        """Every tracked language runs its leg, not the first one (#471).

        A mixed repository once took the Rust leg alone and reported the
        floor held over a Go module nothing had measured.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            stubs = Path(tmp) / "stubs"
            tree.mkdir()
            stubs.mkdir()
            for name, text in (("rustup", RUSTUP), ("cargo", CARGO), ("go", GO)):
                (stubs / name).write_text(text, encoding="utf-8")
                (stubs / name).chmod(0o755)
            hermetic.rustc(stubs)
            (tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
            (tree / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
            plant_rust_run(tree, Path(tmp) / "run")
            env = hermetic.env()
            env |= {
                "ORG_BELT_DIR": str(BELT),
                "ORG_COVERAGE_RUN": str(Path(tmp) / "run"),
                "RUSTUP_TOOLCHAIN": "stable",
                "PATH": f"{stubs}{os.pathsep}{env.get('PATH', '')}",
                "STUB_DIR": tmp,
                "STUB_FILES": "a.rs:200:123",
                "STUB_GO": "93.0",
            }
            done = subprocess.run(
                [BASH, str(BELT / "coverage-measure.sh"), "coverage:test", "all"],
                cwd=tree,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("go 93.0\nrust 61.5\n", done.stdout)


class RustLegGuards(unittest.TestCase):
    """The rust leg's own guards, each by its message (#1653)."""

    @staticmethod
    def leg(
        tmp: str,
        *,
        marker: bool = True,
        profile: bool = True,
        metadata: dict[str, object] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run the `rust` selector over a planted run and stand-in cargo.

        `marker` leaves this run's `measured` marker, `profile` one
        profile in the measurement's directory; `metadata` the packages
        `cargo metadata` reports.

        Returns:
            The finished process and the stand-in cargo's calls.

        """
        base = Path(tmp)
        tree, stubs, run = base / "tree", base / "stubs", base / "run"
        tree.mkdir()
        stubs.mkdir()
        for name, text in (("rustup", RUSTUP), ("cargo", CARGO)):
            (stubs / name).write_text(text, encoding="utf-8")
            (stubs / name).chmod(0o755)
        hermetic.rustc(stubs)
        (tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        plant_rust_run(tree, run)
        if not marker:
            (run / "rust").rmdir()
        if not profile:
            profiles = tree / "target" / f"llvm-cov-target-{STABLE}"
            (profiles / "tree-1-1.profraw").unlink()
        env = hermetic.env()
        env |= {
            "ORG_BELT_DIR": str(BELT),
            "ORG_COVERAGE_RUN": str(run),
            "ORG_COVERAGE_REPORTS": str(base / "reports"),
            "RUSTUP_TOOLCHAIN": "stable",
            "PATH": f"{stubs}{os.pathsep}{env.get('PATH', '')}",
            "STUB_DIR": tmp,
            "STUB_FILES": "src/lib.rs:10:0 crates/core/src/lib.rs:10:9",
        }
        if metadata is not None:
            (base / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            env["STUB_METADATA"] = str(base / "metadata.json")
        done = subprocess.run(
            [BASH, str(BELT / "coverage-measure.sh"), "coverage:check", "rust"],
            cwd=tree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        log = base / "cargo.log"
        return done, log.read_text("utf-8").splitlines() if log.exists() else []

    NO_RUN = (
        "coverage:check: the test task ran no measured Rust — its test command"
        ' runs\n  through "${ORG_BELT_DIR}/measured" (#1653), or no suite ran'
        " under mise\n"
    )

    def test_an_earlier_runs_profiles_without_this_runs_marker_are_refused(
        self,
    ) -> None:
        """Profiles on disk, but `measured` made no marker in this run."""
        with tempfile.TemporaryDirectory() as tmp:
            done, calls = self.leg(tmp, marker=False)
            self.assertEqual(1, done.returncode, done.stderr)
            self.assertEqual("", done.stdout)
            self.assertIn(self.NO_RUN, done.stderr)
            reports = [c for c in calls if "report" in c]
            self.assertEqual(NO_CALLS, reports)

    def test_this_runs_marker_without_a_profile_is_refused(self) -> None:
        """`measured` ran, the suite wrote nothing: the same refusal."""
        with tempfile.TemporaryDirectory() as tmp:
            done, calls = self.leg(tmp, profile=False)
            self.assertEqual(1, done.returncode, done.stderr)
            self.assertEqual("", done.stdout)
            self.assertIn(self.NO_RUN, done.stderr)
            reports = [c for c in calls if "report" in c]
            self.assertEqual(NO_CALLS, reports)

    def test_a_root_package_extension_leaves_its_own_targets(self) -> None:
        """The extension at the workspace root: its targets go, a member stays."""
        root_ext = {
            "packages": [
                {
                    "id": "path+file://<root>#ext@1.0.0",
                    "name": "ext",
                    "version": "1.0.0",
                    "publish": None,
                    "dependencies": [{"name": "pgrx"}],
                    "features": {"default": [], "pg18": ["pgrx/pg18"]},
                    "targets": [{"kind": ["lib"], "crate_types": ["lib"]}],
                    "manifest_path": "<root>/Cargo.toml",
                },
            ],
            "workspace_members": ["path+file://<root>#ext@1.0.0"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            done, calls = self.leg(tmp, metadata=root_ext)
            self.assertEqual(0, done.returncode, done.stderr)
            # crates/core's 9 of 10; the root's src/lib.rs (none hit) left out.
            self.assertEqual("rust 90\n", done.stdout)
            (report,) = [c for c in calls if "llvm-cov report" in c]
            self.assertRegex(
                report,
                r"^llvm-cov report --ignore-filename-regex"
                r" \^/\S+/\(src\|tests\|benches\|examples\)/\|\^/\S+/build\\\.rs\$ ",
            )


class ReportsSelector(CoverageTaskCase):
    """`reports` names what a run wrote, and refuses a run of nothing (#1528)."""

    @staticmethod
    def reports(tree: Path) -> subprocess.CompletedProcess[str]:
        """Run the `reports` selector in a planted tree.

        Returns:
            The finished process.

        """
        env = hermetic.env()
        env |= {
            "ORG_BELT_DIR": str(BELT),
            "ORG_COVERAGE_REPORTS": str(tree.parent / "reports"),
        }
        return subprocess.run(
            [BASH, str(BELT / "coverage-measure.sh"), "coverage:test", "reports"],
            cwd=tree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_legs_print_then_the_line_names_each_report(self) -> None:
        """The legs' lines, then the reports in the badge job's order."""
        done, tree = self.run_task("coverage:check", go="93.0")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(
            done.stdout.endswith(
                "python-branches leg holds the floor 80.0 (85.0%)\n"
                + upload_line(tree, "coverage.txt", "coverage.xml")
            ),
            done.stdout,
        )

    def test_a_stale_report_in_a_named_directory_is_not_listed(self) -> None:
        """An earlier run's report is cleared: the list is this run's."""
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "lcov.info"
            stale.write_text("stale\n", encoding="utf-8")
            done, _ = self.run_task("coverage:check", reports_dir=tmp)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertTrue(
                done.stdout.endswith(
                    f"coverage:check: upload {Path(tmp) / 'coverage.xml'}\n"
                ),
                done.stdout,
            )
            self.assertFalse(stale.exists())

    def test_a_run_that_measured_nothing_is_refused(self) -> None:
        """No leg lines: refused before any report is read."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            tree.mkdir()
            (Path(tmp) / "reports").mkdir()
            (Path(tmp) / "reports" / "coverage.xml").write_text("x\n", "utf-8")
            subprocess.run(
                [GIT, "init", "-q"], cwd=tree, env=hermetic.env(), check=True
            )
            done = self.reports(tree)
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertIn(
            "coverage:test: no leg was measured — a check that reads nothing"
            " must not pass",
            done.stderr,
        )


# The refusal the python leg prints when coverage recorded a file that
# no longer resolves, with the planted child's file named.
NO_SOURCE_MSG = (
    "coverage:test: coverage recorded 'helper.py', which no file in the tree"
    " matches —\n"
    "  a test ran a Python child under measurement from a directory other than"
    " the\n"
    "  repository root, and its files were recorded relative to that directory;\n"
    "  start the child from the repository root\n"
)


def run_python_leg(test_run: str) -> tuple[subprocess.CompletedProcess[str], bool]:
    """Measure a planted tree's python leg with the real coverage.py.

    The tree tracks `sub/helper.py` and an empty floor. `test_run` runs
    first through the real `measured`, as the repository's test task
    would (#1667), then the leg reads the run it measured.

    Returns:
        The finished measurement, and whether it wrote coverage.xml.

    """
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "tree"
        stubs = Path(tmp) / "stubs"
        (tree / "sub").mkdir(parents=True)
        stubs.mkdir()
        (stubs / "mise").write_text(MISE, encoding="utf-8")
        (stubs / "mise").chmod(0o755)
        (tree / "sub" / "helper.py").write_text("print('hi')\n", encoding="utf-8")
        task = Path(tmp) / "task.json"
        answer: dict[str, object] = {"dir": str(tree), "run": [test_run]}
        task.write_text(json.dumps(answer), encoding="utf-8")
        (tree / ".coverage-floor").write_text("", encoding="utf-8")
        env = hermetic.env()
        env |= {
            "ORG_BELT_DIR": str(BELT),
            "PATH": f"{stubs}{os.pathsep}{env.get('PATH', '')}",
            "STUB_TASK": str(task),
            "ORG_COVERAGE_REPORTS": str(Path(tmp) / "reports"),
            "ORG_COVERAGE_RUN": str(Path(tmp) / "run"),
        }
        for args in (["init", "-q"], ["add", "sub/helper.py"]):
            subprocess.run([GIT, *args], cwd=tree, env=env, check=True)
        subprocess.run(
            [str(BELT / "measured"), BASH, "-c", test_run],
            cwd=tree,
            env=env,
            capture_output=True,
            check=True,
        )
        done = subprocess.run(
            [BASH, str(BELT / "coverage-measure.sh"), "coverage:test", "python"],
            cwd=tree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return done, (Path(tmp) / "reports" / "coverage.xml").is_file()


class ChildFromAnotherDirectory(unittest.TestCase):
    """A child measured from a subdirectory is refused by name (#1528).

    Under `relative_files` a child records its files relative to its own
    directory, so a child started in `sub/` records `helper.py`, and the
    report cannot find it. The gate renders coverage.xml on every run
    since #1528, where it rendered only the JSON before.
    """

    @classmethod
    @override
    def setUpClass(cls) -> None:
        """Assert the tool first, as the belt's tasks assert theirs.

        The rows run the python leg with the real coverage.py the belt
        pins, not a stub: what they hold is coverage's own reading of a
        child started from another directory (#1528).
        """
        hermetic.tool("coverage")

    def test_a_child_run_from_a_subdirectory_is_red_by_name(self) -> None:
        """The belt's words, the recorded file, and the remedy."""
        done, wrote = run_python_leg("cd sub && python3 helper.py")
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertIn(NO_SOURCE_MSG, done.stderr)
        self.assertEqual("", done.stdout)
        self.assertFalse(wrote)

    def test_the_same_child_from_the_root_renders_the_report(self) -> None:
        """Control: started from the root, it measures and renders."""
        done, wrote = run_python_leg("python3 sub/helper.py")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("python 100.0\npython-branches 100.0\n", done.stdout)
        self.assertTrue(wrote)


class GoLegRows(CoverageTaskCase):
    """The go leg reads the test task's own run, or says it measures itself."""

    def test_no_test_task_measures_go_test_itself_by_name(self) -> None:
        """go.mod and no test task: today's command, named."""
        done, _ = self.run_task(
            "coverage:check", go="93.0", python=False, test_task=False
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn(
            "coverage:check: go: no test task: measuring `go test ./...`", done.stderr
        )
        self.assertIn(
            "coverage:check: go leg holds the floor 80.0 (93.0%)", done.stdout
        )

    def test_a_run_with_no_counters_is_refused_by_name(self) -> None:
        """A test task whose run measured no Go: refused, no figure."""
        done, _ = self.run_task(
            "coverage:check", go="93.0", python=False, measured=False
        )
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "coverage:check: the test task ran no measured Go — its test command"
            ' runs\n  through "${ORG_BELT_DIR}/measured" (#1651), or no suite ran'
            " under mise\n",
            done.stderr,
        )
        self.assertNotIn("go leg holds", done.stdout)


# A Go toolchain for the rows below, enough to follow the mechanism: `go
# test` records the GOFLAGS it was given, then starts a planted test
# binary through the `-exec` program GOFLAGS names, with the leading
# arguments go 1.27.4 hands one; `tool covdata` writes the merged
# profile, `tool cover` the planted total. STUB_GO_FAIL fails the suite
# after it ran.
GO_EXEC = """\
#!/usr/bin/env bash
out=""
for arg in "$@"; do
  case "${arg}" in -o=*) out=${arg#*=} ;; *) ;; esac
done
case "$1 ${2:-}" in
  "test "*)
    echo "${GOFLAGS:-}" >> "${STUB_DIR}/goflags.log"
    program=""
    for flag in ${GOFLAGS:-}; do
      case "${flag}" in -exec=*) program=${flag#*=} ;; *) ;; esac
    done
    ${program} "${STUB_DIR}/pkg.test" -test.paniconexit0 \\
      "-test.gocoverdir=${STUB_DIR}/go-build" -test.timeout=10m0s
    [[ -z ${STUB_GO_FAIL:-} ]] || exit 1
    ;;
  "tool covdata") echo "mode: atomic" > "${out}" ;;
  "tool cover") printf 'total:\\t(statements)\\t%s%%\\n' "${STUB_GO}" ;;
  *) exit 1 ;;
esac
"""
# A test binary: its counters land in the last -test.gocoverdir it is
# given, as Go's flag package takes the last.
PKG_TEST = """\
#!/usr/bin/env bash
dir=""
for arg in "$@"; do
  case "${arg}" in -test.gocoverdir=*) dir=${arg#*=} ;; *) ;; esac
done
mkdir -p "${dir}"
: > "${dir}/covcounters.stub.$$"
: > "${dir}/covmeta.stub"
"""


class GoOneRun(unittest.TestCase):
    """coverage:check under a real mise reads the Go suite's one run (#1651)."""

    def plant(self, base: Path, *, command: str) -> Path:
        """Plant a Go-shaped tree whose test task sets GOFLAGS and counts runs.

        Returns:
            The tree.

        """
        tree = base / "tree"
        tree.mkdir()
        stubs = base / "stubs"
        stubs.mkdir()
        for path, text in ((stubs / "go", GO_EXEC), (base / "pkg.test", PKG_TEST)):
            path.write_text(text, encoding="utf-8")
            path.chmod(0o755)
        (tree / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
        (tree / ".coverage-floor").write_text(FLOOR, encoding="utf-8")
        (tree / "mise.toml").write_text(
            '[task_config]\nshell = "bash -euo pipefail -c"\n'
            '[tasks.test]\nenv = { GOFLAGS = "-trimpath" }\n'
            f"run = '''\necho ran >> ../counter\n{command}\n'''\n",
            encoding="utf-8",
        )
        env = hermetic.env()
        for argv in ([GIT, "init", "-q"], [GIT, "add", "-A"]):
            subprocess.run(argv, cwd=tree, env=env, check=True)
        self.stubs = stubs
        return tree

    def check(self, tree: Path, **given: str) -> subprocess.CompletedProcess[str]:
        """Run `mise run coverage:check` in the tree, the belt global.

        Returns:
            The finished run.

        """
        base = tree.parent
        config = base / "config"
        config.mkdir()
        (base / "tmp").mkdir()
        env = hermetic.env(
            MISE_CONFIG_DIR=str(config),
            XDG_CONFIG_HOME=str(config),
            MISE_GLOBAL_CONFIG_FILE=str(BELT / "config.toml"),
            MISE_TRUSTED_CONFIG_PATHS=f"{base}:{BELT.parent}",
            MISE_OFFLINE="1",
            MISE_YES="1",
            MISE_AUTO_INSTALL="0",
            MISE_TASK_RUN_AUTO_INSTALL="0",
            TMPDIR=str(base / "tmp"),
            STUB_DIR=str(base),
            STUB_GO="91.0",
            **given,
        )
        env["PATH"] = f"{self.stubs}{os.pathsep}{env['PATH']}"
        return subprocess.run(
            [hermetic.tool("mise"), "run", "coverage:check"],
            cwd=tree,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )

    MEASURED = '"${ORG_BELT_DIR}/measured" go test -race ./...'

    def test_the_task_runs_once_measured_with_its_own_goflags(self) -> None:
        """Counter 1, the go figure held, the task's GOFLAGS kept and extended."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = self.plant(Path(tmp), command=self.MEASURED)
            done = self.check(tree)
            out = done.stdout + done.stderr
            self.assertEqual(0, done.returncode, out)
            self.assertEqual("ran\n", (Path(tmp) / "counter").read_text("utf-8"))
            self.assertIn("coverage:check: go leg holds the floor 80.0 (91.0%)", out)
            self.assertEqual(
                "-trimpath -cover -coverpkg=./... -covermode=atomic"
                f" -exec={BELT}/go-test-exec\n",
                (Path(tmp) / "goflags.log").read_text("utf-8"),
            )
            counters = list((Path(tmp) / "tmp").glob("org-coverage-*/go/covcounters.*"))
            self.assertEqual(1, len(counters), out)

    def test_two_go_test_runs_in_one_command_both_land(self) -> None:
        """Each `go test` writes counters of its own: nothing is overwritten."""
        with tempfile.TemporaryDirectory() as tmp:
            command = (
                '"${ORG_BELT_DIR}/measured" bash -c'
                " 'go test ./a/... && go test ./b/...'"
            )
            tree = self.plant(Path(tmp), command=command)
            done = self.check(tree)
            out = done.stdout + done.stderr
            self.assertEqual(0, done.returncode, out)
            counters = list((Path(tmp) / "tmp").glob("org-coverage-*/go/covcounters.*"))
            self.assertEqual(2, len(counters), out)

    def test_a_failing_suite_holds_no_figure_though_counters_exist(self) -> None:
        """The suite's status gates the figure: `test` red, nothing read."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = self.plant(Path(tmp), command=self.MEASURED)
            done = self.check(tree, STUB_GO_FAIL="1")
            out = done.stdout + done.stderr
            self.assertNotEqual(0, done.returncode, out)
            self.assertNotIn("go leg holds", out)
            self.assertIn("[test] ERROR task failed", out)
            counters = list((Path(tmp) / "tmp").glob("org-coverage-*/go/covcounters.*"))
            self.assertEqual(1, len(counters), out)

    def test_the_tasks_own_coverprofile_is_refused_by_name(self) -> None:
        """A -coverprofile of its own on `go test`: refused before it runs."""
        with tempfile.TemporaryDirectory() as tmp:
            command = self.MEASURED + " -coverprofile=own.out"
            tree = self.plant(Path(tmp), command=command)
            done = self.check(tree)
            out = done.stdout + done.stderr
            self.assertNotEqual(0, done.returncode, out)
            self.assertIn(
                "measured: -coverprofile=own.out on its go test, which would"
                " measure Go a second way",
                out,
            )
            self.assertFalse((Path(tmp) / "goflags.log").exists())


# A workspace with a pgrx extension, as `cargo metadata` reports it: a
# plain member and `ext`, which depends on pgrx and declares a major.
PGRX_METADATA = {
    "packages": [
        {
            "id": f"path+file://<root>/crates/{name}#{name}@1.0.0",
            "name": name,
            "version": "1.0.0",
            "publish": None,
            "dependencies": [{"name": "pgrx"}] if pgrx else [],
            "features": {"default": [], "pg18": ["pgrx/pg18"]} if pgrx else {},
            "targets": [{"kind": ["lib"], "crate_types": ["lib"]}],
            "manifest_path": f"<root>/crates/{name}/Cargo.toml",
        }
        for name, pgrx in (("core", False), ("ext", True))
    ],
    "workspace_members": [
        f"path+file://<root>/crates/{name}#{name}@1.0.0" for name in ("core", "ext")
    ],
}
# The repository's test task, as the rows plant it: it counts its runs.
RUST_TASK = """\
[task_config]
shell = "bash -euo pipefail -c"
[tasks.test]
run = '''
echo ran >> ../counter
{command}
'''
"""


class RustOneRun(unittest.TestCase):
    """coverage:check under a real mise reads the Rust suite's one run (#1653)."""

    MEASURED = '"${ORG_BELT_DIR}/measured" cargo test --workspace --all-features'

    def plant(self, base: Path, *, command: str) -> Path:
        """Plant a Rust-shaped tree whose test task counts its runs.

        Returns:
            The tree.

        """
        tree = base / "tree"
        tree.mkdir()
        stubs = base / "stubs"
        stubs.mkdir()
        for name, text in (("cargo", CARGO), ("rustup", RUSTUP)):
            (stubs / name).write_text(text, encoding="utf-8")
            (stubs / name).chmod(0o755)
        hermetic.rustc(stubs)
        (tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        (tree / ".coverage-floor").write_text(FLOOR, encoding="utf-8")
        (tree / "mise.toml").write_text(
            RUST_TASK.format(command=command), encoding="utf-8"
        )
        env = hermetic.env()
        for argv in ([GIT, "init", "-q"], [GIT, "add", "-A"]):
            subprocess.run(argv, cwd=tree, env=env, check=True)
        self.stubs = stubs
        return tree

    def check(self, tree: Path, **given: str) -> subprocess.CompletedProcess[str]:
        """Run `mise run coverage:check` in the tree, the belt global.

        Returns:
            The finished run.

        """
        base = tree.parent
        config = base / "config"
        config.mkdir()
        (base / "tmp").mkdir()
        env = hermetic.env(
            MISE_CONFIG_DIR=str(config),
            XDG_CONFIG_HOME=str(config),
            MISE_GLOBAL_CONFIG_FILE=str(BELT / "config.toml"),
            MISE_TRUSTED_CONFIG_PATHS=f"{base}:{BELT.parent}",
            MISE_OFFLINE="1",
            MISE_YES="1",
            MISE_AUTO_INSTALL="0",
            MISE_TASK_RUN_AUTO_INSTALL="0",
            TMPDIR=str(base / "tmp"),
            STUB_DIR=str(base),
            RUSTUP_TOOLCHAIN="stable",
            **({"STUB_FILES": "crates/core/src/lib.rs:20:18"} | given),
        )
        env["PATH"] = f"{self.stubs}{os.pathsep}{env['PATH']}"
        return subprocess.run(
            [hermetic.tool("mise"), "run", "coverage:check"],
            cwd=tree,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )

    @staticmethod
    def calls(base: Path) -> list[str]:
        """Read the stand-in cargo's log.

        Returns:
            Each call's arguments, in order.

        """
        log = base / "cargo.log"
        return log.read_text("utf-8").splitlines() if log.exists() else []

    def test_the_task_runs_once_and_the_leg_only_reports(self) -> None:
        """Counter 1, one `cargo test`, the leg's `report`, and no build."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = self.plant(Path(tmp), command=self.MEASURED)
            done = self.check(tree)
            out = done.stdout + done.stderr
            self.assertEqual(0, done.returncode, out)
            self.assertEqual("ran\n", (Path(tmp) / "counter").read_text("utf-8"))
            self.assertIn("coverage:check: rust leg holds the floor 80.0 (90%)", out)
            calls = self.calls(Path(tmp))
            suites = [c for c in calls if c.startswith("test")]
            once: list[str] = ["test --workspace --all-features"]
            self.assertEqual(once, suites)
            (report,) = [c for c in calls if c.startswith("llvm-cov report")]
            self.assertRegex(
                report, r"^llvm-cov report --lcov --output-path /\S+/lcov\.info$"
            )
            # Every llvm-cov call is the environment, the clean or the
            # report: none builds or runs a suite of the leg's own.
            builds = [
                c
                for c in calls
                if c.startswith("llvm-cov")
                and c.split()[1] not in {"show-env", "clean", "report"}
            ]
            self.assertEqual(NO_CALLS, builds)

    def test_a_test_command_outside_measured_is_red_naming_the_prefix(self) -> None:
        """No profile from this run: refused by name, no figure."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = self.plant(Path(tmp), command="cargo test --workspace")
            done = self.check(tree)
            out = done.stdout + done.stderr
            self.assertNotEqual(0, done.returncode, out)
            self.assertIn(
                "coverage:check: the test task ran no measured Rust — its test"
                ' command runs\n  through "${ORG_BELT_DIR}/measured" (#1653), or'
                " no suite ran under mise\n",
                done.stderr,
            )
            self.assertNotIn("rust leg holds", out)
            reports = [c for c in self.calls(Path(tmp)) if "report" in c]
            self.assertEqual(NO_CALLS, reports)

    def test_a_failed_test_leaves_coverage_check_not_run(self) -> None:
        """The suite's status gates the figure: `test` red, no report read."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = self.plant(Path(tmp), command=self.MEASURED)
            done = self.check(tree, STUB_CARGO_FAIL="1")
            out = done.stdout + done.stderr
            self.assertNotEqual(0, done.returncode, out)
            self.assertIn("[test] ERROR task failed", out)
            self.assertNotIn("rust leg holds", out)
            reports = [c for c in self.calls(Path(tmp)) if "report" in c]
            self.assertEqual(NO_CALLS, reports)

    def test_a_pgrx_crate_the_command_built_is_absent_from_the_figure(
        self,
    ) -> None:
        """The extension's file leaves the report by path; the figure is core's."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = self.plant(Path(tmp), command=self.MEASURED)
            metadata = Path(tmp) / "metadata.json"
            metadata.write_text(json.dumps(PGRX_METADATA), encoding="utf-8")
            done = self.check(
                tree,
                STUB_METADATA=str(metadata),
                STUB_FILES="crates/core/src/lib.rs:20:18 crates/ext/src/lib.rs:80:0",
            )
            out = done.stdout + done.stderr
            self.assertEqual(0, done.returncode, out)
            # 18 of 20: the extension's 80 unhit lines are not counted.
            self.assertIn("coverage:check: rust leg holds the floor 80.0 (90%)", out)
            self.assertIn(
                "coverage:check: pgrx-pg18: measured and held by test:pgrx", out
            )
            (report,) = [c for c in self.calls(Path(tmp)) if "llvm-cov report" in c]
            self.assertRegex(
                report,
                r"^llvm-cov report --ignore-filename-regex \^/\S+/crates/ext/ --lcov ",
            )

    def test_coverage_exclude_set_is_red_by_name(self) -> None:
        """The removed variable, set: refused with its remedy, no figure."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = self.plant(Path(tmp), command=self.MEASURED)
            done = self.check(tree, COVERAGE_EXCLUDE="lab-wasm")
            out = done.stdout + done.stderr
            self.assertNotEqual(0, done.returncode, out)
            self.assertIn(
                COVERAGE_EXCLUDE_REFUSAL.format(named="lab-wasm"), done.stderr
            )
            self.assertNotIn("rust leg holds", out)


if __name__ == "__main__":
    unittest.main()
