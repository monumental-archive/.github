#!/usr/bin/env python3
"""Table tests for the coverage tasks' Python leg (#1331).

The canon is a Python codebase, and the coverage machinery could not
measure it: every `coverage:*` task skipped unless `Cargo.toml` or
`go.mod` existed. The Python leg runs the repository's own `test` task
under coverage.py and prints `python <percent>` and `python-branches
<percent>`, and the floor is a minimum every leg must hold.

The bodies under test are the tasks' own `run` strings, read out of
mise/config.toml with tomllib and run by bash in a planted git tree,
not copies of them, and they call the real `mise/coverage-measure.sh`.
Only the measuring tools are stubs (`coverage`, `go`, and `mise`'s
read of the test task), each answering with a planted number, so a row
states the measurement and asserts what the gate does with it.

Each row asserts the message, not just the exit status: a planted row
scored on exit alone can pass for the wrong reason.

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
BASH = shutil.which("bash")
GIT = shutil.which("git")

CONFIG = tomllib.loads((BELT / "config.toml").read_text(encoding="utf-8"))

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
  xml) echo "<coverage/>" > "${out}" ;;
  *) exit 1 ;;
esac
"""

GO = """\
#!/usr/bin/env bash
case "$1" in
  test)
    # The profile `go test -coverprofile` writes, unless the row plants
    # a run that wrote none.
    [[ -z ${STUB_GO_NO_PROFILE:-} ]] || exit 0
    for arg in "$@"; do
      [[ ${arg} != -coverprofile=* ]] || echo "mode: atomic" > "${arg#*=}"
    done
    ;;
  tool) printf 'total:\\t(statements)\\t%s%%\\n' "${STUB_GO}" ;;
  *) exit 1 ;;
esac
"""

MISE = """\
#!/usr/bin/env bash
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


def plant_leftovers(tree: Path, leftovers: dict[str, bool]) -> list[str]:
    """Plant report files as an earlier run would leave them.

    Returns:
        The names to track: those the row marks as the repository's own.

    """
    for name in leftovers:
        (tree / name).write_text("leftover\n", encoding="utf-8")
    return [name for name, is_tracked in leftovers.items() if is_tracked]


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
    leftovers: dict[str, bool] | None = None,
    go_profile: bool = True,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run one coverage task's body in a planted tree with stub tools.

    `leftovers` plants report files as an earlier run would leave them:
    name to whether the repository tracks it. Each holds "leftover".

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
        (tmp / f"task.{name}.json").write_text(
            json.dumps({"dir": str(tree), "run": [run]}), encoding="utf-8"
        )
    if test_task:
        task_json.write_text(
            json.dumps({
                "dir": str(tree),
                "run": [test_run or "echo suite ran"],
                "depends": sorted(depends or {}),
            }),
            encoding="utf-8",
        )

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
    tracked += plant_leftovers(tree, leftovers or {})

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
    subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [GIT, "init", "-q"], cwd=tree, env=env, check=True
    )
    if tracked:
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [GIT, "add", *tracked], cwd=tree, env=env, check=True
        )
    done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [BASH, "-euo", "pipefail", "-c", CONFIG["tasks"][task]["run"]],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return done, tmp


class CoverageTaskCase(unittest.TestCase):
    """Runs a task and removes the planted tree afterwards."""

    def run_task(
        self, task: str, **plant: str | bool | None
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        """Run a task and schedule its tree for removal.

        Returns:
            The finished process and the planted tree.

        """
        done, tmp = run_task(task, **plant)  # type: ignore[arg-type]
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        return done, tmp / "tree"


class CheckRows(CoverageTaskCase):
    """`coverage:check` over the Python leg, alone and beside Go."""

    def test_python_only_tree_measures_and_holds(self) -> None:
        """Both Python legs print, and each holds the floor by name."""
        done, _ = self.run_task("coverage:check")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:check: python leg holds the floor 80.0 (90.0%)\n"
            "coverage:check: python-branches leg holds the floor 80.0 (85.0%)\n"
            "coverage:check: upload coverage.xml\n",
            done.stdout,
        )
        self.assertIn("suite ran", done.stderr)

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
            " tracked *.py with a test task, or package.json with vitest),"
            " skipped\n",
            done.stdout,
        )

    def test_mixed_go_and_python_prints_every_leg(self) -> None:
        """A mixed tree measures each language it tracks."""
        done, _ = self.run_task("coverage:check", go="93.0")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:check: go leg holds the floor 80.0 (93.0%)\n"
            "coverage:check: python leg holds the floor 80.0 (90.0%)\n"
            "coverage:check: python-branches leg holds the floor 80.0 (85.0%)\n"
            "coverage:check: upload coverage.txt,coverage.xml\n",
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
        done, _ = self.run_task("coverage:check", python=False, typescript="vitest")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:check: typescript leg holds the floor 80.0 (88.0%)\n"
            "coverage:check: typescript-branches leg holds the floor 80.0 (81.0%)\n"
            "coverage:check: upload lcov-typescript.info\n",
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
            " tracked *.py with a test task, or package.json with vitest),"
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
        done, _ = self.run_task("coverage:check", typescript="vitest")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:check: python leg holds the floor 80.0 (90.0%)\n"
            "coverage:check: python-branches leg holds the floor 80.0 (85.0%)\n"
            "coverage:check: typescript leg holds the floor 80.0 (88.0%)\n"
            "coverage:check: typescript-branches leg holds the floor 80.0 (81.0%)\n"
            "coverage:check: upload lcov-typescript.info,coverage.xml\n",
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

    def refused(self, run: str, message: str, **plant: object) -> None:
        """Assert a test task whose vitest command is refused, by message."""
        done, _ = self.run_task(
            "coverage:check",
            python=False,
            typescript="vitest",
            test_run=run,
            **plant,  # type: ignore[arg-type]
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
            done.stdout.endswith("coverage:check: upload lcov-typescript.info\n"),
            done.stdout,
        )
        self.assertEqual("TN:\n", (tree / "lcov-typescript.info").read_text("utf-8"))
        self.assertFalse((tree / "lcov.info").exists())


def upload_step() -> str:
    """Read the upload job's "List the reports" run block from ci.yml.

    Returns:
        The block, dedented, as the runner's bash receives it.

    """
    lines = (CANON / ".github/workflows/ci.yml").read_text("utf-8").splitlines()
    at = lines.index("      - name: List the reports")
    at = lines.index("        run: |", at) + 1
    body = []
    for line in lines[at:]:
        if line.strip() and not line.startswith(" " * 10):
            break
        body.append(line[10:])
    return "\n".join(body) + "\n"


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
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HAS_TOKEN": "true" if has_token else "false",
            "GITHUB_OUTPUT": str(out),
        }
        # ruff: ignore[subprocess-without-shell-equals-true]
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
            "upload lcov.info,coverage.txt,coverage.xml\n"
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

    def test_an_empty_artifact_is_refused(self) -> None:
        """An artifact with no report is red, never an upload of nothing."""
        done = run_upload_step(["unrelated.txt"])
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "::error::the gate's coverage-reports artifact holds no report",
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
            " tracked *.py with a test task, or package.json with vitest),"
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
            done.stdout.endswith("coverage:check: upload coverage.xml\n"),
            done.stdout,
        )
        self.assertEqual("<coverage/>\n", (tree / "coverage.xml").read_text("utf-8"))

    def test_an_untracked_leftover_is_not_listed(self) -> None:
        """A report an earlier run left behind is removed, never uploaded."""
        done, tree = self.run_task(
            "coverage:check", leftovers={"lcov.info": False, "coverage.txt": False}
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(
            done.stdout.endswith("coverage:check: upload coverage.xml\n"),
            done.stdout,
        )
        self.assertFalse((tree / "lcov.info").exists())
        self.assertFalse((tree / "coverage.txt").exists())

    def test_a_tracked_file_of_the_same_name_is_left_alone(self) -> None:
        """The repository's own file is never deleted by the belt."""
        done, tree = self.run_task("coverage:check", leftovers={"lcov.info": True})
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("leftover\n", (tree / "lcov.info").read_text("utf-8"))

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
        self.assertNotIn("coverage:report", CONFIG["tasks"])


# The Rust leg's tools: rustup asserting llvm-tools, and cargo answering
# the workspace read (no pgrx crate) and the measurement.
RUSTUP = """\
#!/usr/bin/env bash
echo "llvm-tools-stub"
"""

CARGO = """\
#!/usr/bin/env bash
case "$1" in
  metadata) echo '{"packages": [], "workspace_members": []}' ;;
  llvm-cov)
    out=""
    while [[ $# -gt 0 ]]; do
      [[ $1 == --output-path ]] && out=$2
      shift
    done
    record='SF:%s\\nLF:%s\\nLH:%s\\nend_of_record\\n'
    printf "${record}" a.rs "${STUB_LF}" "${STUB_LH}" > "${out}"
    printf "${record}" b.rs 0 0 >> "${out}"
    ;;
  *) exit 1 ;;
esac
"""


class MeasureSelectors(unittest.TestCase):
    """Every selector the script accepts measures its leg."""

    def test_the_rust_selector_measures_the_rust_leg(self) -> None:
        """`rust` is accepted, so it must not measure nothing and pass."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            stubs = Path(tmp) / "stubs"
            tree.mkdir()
            stubs.mkdir()
            for name, text in (("rustup", RUSTUP), ("cargo", CARGO)):
                (stubs / name).write_text(text, encoding="utf-8")
                (stubs / name).chmod(0o755)
            (tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
            env = hermetic.env()
            env |= {
                "ORG_BELT_DIR": str(BELT),
                "PATH": f"{stubs}{os.pathsep}{env.get('PATH', '')}",
                "STUB_LF": "13",
                "STUB_LH": "8",
            }
            done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [BASH, str(BELT / "coverage-measure.sh"), "coverage:test", "rust"],
                cwd=tree,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            written = (tree / "lcov.info").is_file()
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
            (tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
            (tree / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
            env = hermetic.env()
            env |= {
                "ORG_BELT_DIR": str(BELT),
                "PATH": f"{stubs}{os.pathsep}{env.get('PATH', '')}",
                "STUB_LF": "200",
                "STUB_LH": "123",
                "STUB_GO": "93.0",
            }
            done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [BASH, str(BELT / "coverage-measure.sh"), "coverage:test", "all"],
                cwd=tree,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("go 93.0\nrust 61.5\n", done.stdout)


def run_reports(legs: str, files: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the `reports` selector beside planted reports, legs on stdin.

    Returns:
        The finished process.

    """
    with tempfile.TemporaryDirectory() as tmp:
        for name in files:
            (Path(tmp) / name).write_text("report\n", encoding="utf-8")
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, str(BELT / "coverage-measure.sh"), "coverage:test", "reports"],
            cwd=tmp,
            env=hermetic.env(),
            input=legs,
            capture_output=True,
            text=True,
            check=False,
        )


class ReportsSelector(unittest.TestCase):
    """`reports` names what a run wrote, and refuses a run of nothing (#1528)."""

    def test_the_line_names_each_report_in_order(self) -> None:
        """The order the badge job lists them in, whatever exists."""
        done = run_reports("go 90.0\npython 80.0\n", ["coverage.xml", "coverage.txt"])
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:test: upload coverage.txt,coverage.xml\n", done.stdout
        )

    def test_a_run_that_measured_nothing_is_refused(self) -> None:
        """No leg lines: refused before any report is read."""
        done = run_reports("", ["coverage.xml"])
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertIn(
            "coverage:test: no leg was measured — a check that reads nothing"
            " must not pass",
            done.stderr,
        )


# The real coverage.py the belt pins, or None. The rows below run the
# python leg with it, not a stub: what they hold is coverage's own
# reading of a child started from another directory (#1528).
COVERAGE_TOOL = shutil.which("coverage")
COVERAGE_MISSING = (
    "coverage is not on PATH: these rows run the belt's real coverage.py"
    " over a planted suite — run: mise install"
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

    The tree tracks `sub/helper.py`; its test task is `test_run`, read
    through the stub `mise`.

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
        task.write_text(
            json.dumps({"dir": str(tree), "run": [test_run]}), encoding="utf-8"
        )
        env = hermetic.env()
        env |= {
            "ORG_BELT_DIR": str(BELT),
            "PATH": f"{stubs}{os.pathsep}{env.get('PATH', '')}",
            "STUB_TASK": str(task),
        }
        for args in (["init", "-q"], ["add", "sub/helper.py"]):
            subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [GIT, *args], cwd=tree, env=env, check=True
            )
        done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, str(BELT / "coverage-measure.sh"), "coverage:test", "python"],
            cwd=tree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return done, (tree / "coverage.xml").is_file()


class ChildFromAnotherDirectory(unittest.TestCase):
    """A child measured from a subdirectory is refused by name (#1528).

    Under `relative_files` a child records its files relative to its own
    directory, so a child started in `sub/` records `helper.py`, and the
    report cannot find it. The gate renders coverage.xml on every run
    since #1528, where it rendered only the JSON before.
    """

    def setUp(self) -> None:
        """Assert the tool first, as the belt's tasks assert theirs."""
        if COVERAGE_TOOL is None:
            self.fail(COVERAGE_MISSING)

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


if __name__ == "__main__":
    unittest.main()
