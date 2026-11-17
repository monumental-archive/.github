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
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

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
  test) ;;
  tool) printf 'total:\\t(statements)\\t%s%%\\n' "${STUB_GO}" ;;
  *) exit 1 ;;
esac
"""

MISE = """\
#!/usr/bin/env bash
if [[ "$1 $2 $3" == "tasks info test" && -f ${STUB_TASK} ]]; then
  cat "${STUB_TASK}"
  exit 0
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
lines='{"total": {"lines": {"pct": %s}, "branches": {"pct": %s}}}\\n'
printf "${lines}" "${STUB_TS}" "${STUB_TS_BR}" > "${dir}/coverage-summary.json"
echo "TN:" > "${dir}/lcov.info"
"""

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
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run one coverage task's body in a planted tree with stub tools.

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
    if test_task:
        task_json.write_text(
            json.dumps({"dir": str(tree), "run": ["echo suite ran"]}),
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

    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env |= {
        "ORG_BELT_DIR": str(BELT),
        "PATH": f"{stubs}{os.pathsep}{env.get('PATH', '')}",
        "STUB_PYTHON": sys.executable,
        "STUB_PY": py,
        "STUB_BR": branches,
        "STUB_GO": go or "",
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
            "coverage:check: python-branches leg holds the floor 80.0 (85.0%)\n",
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
            "coverage:check: python-branches leg holds the floor 80.0 (85.0%)\n",
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
            "coverage:check: typescript-branches leg holds the floor 80.0 (81.0%)\n",
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
            "coverage:check: typescript-branches leg holds the floor 80.0 (81.0%)\n",
            done.stdout,
        )

    def test_report_writes_the_typescript_lcov(self) -> None:
        """The upload is the measured run's own lcov, off lcov.info."""
        done, tree = self.run_task("coverage:report", python=False, typescript="vitest")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("coverage:report: lcov-typescript.info written\n", done.stdout)
        self.assertTrue((tree / "lcov-typescript.info").is_file())
        self.assertFalse((tree / "lcov.info").exists())


def upload_step() -> str:
    """Read the upload job's "Generate the report" run block from ci.yml.

    Returns:
        The block, dedented, as the runner's bash receives it.

    """
    lines = (CANON / ".github/workflows/ci.yml").read_text("utf-8").splitlines()
    at = lines.index("      - name: Generate the report")
    at = lines.index("        run: |", at) + 1
    body = []
    for line in lines[at:]:
        if line.strip() and not line.startswith(" " * 10):
            break
        body.append(line[10:])
    return "\n".join(body) + "\n"


class UploadStep(unittest.TestCase):
    """ci.yml's report step asks the probe which legs the tree measures."""

    def test_a_python_only_tree_uploads(self) -> None:
        """The step once skipped any tree without Cargo.toml or go.mod."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            stubs = Path(tmp) / "stubs"
            tree.mkdir()
            stubs.mkdir()
            mise = MISE.replace(
                "exit 1\n",
                'if [[ "$1 $2" == "run coverage:report" ]]; then\n'
                "  touch coverage.xml\n  exit 0\nfi\nexit 1\n",
            )
            (stubs / "mise").write_text(mise, encoding="utf-8")
            (stubs / "mise").chmod(0o755)
            task = Path(tmp) / "task.json"
            task.write_text(json.dumps({"dir": str(tree), "run": ["true"]}), "utf-8")
            (tree / "helper.py").write_text("print('hi')\n", encoding="utf-8")
            (tree / ".coverage-floor").write_text(FLOOR, encoding="utf-8")
            (tree / ".org-canon").mkdir()
            (tree / ".org-canon" / "mise").symlink_to(BELT)
            out = Path(tmp) / "outputs"
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            env |= {
                "PATH": f"{stubs}{os.pathsep}{env.get('PATH', '')}",
                "STUB_TASK": str(task),
                "HAS_TOKEN": "true",
                "GITHUB_OUTPUT": str(out),
            }
            subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [GIT, "init", "-q"], cwd=tree, env=env, check=True
            )
            subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [GIT, "add", "helper.py", ".coverage-floor"],
                cwd=tree,
                env=env,
                check=True,
            )
            done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [BASH, "-e", "-c", upload_step()],
                cwd=tree,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            outputs = out.read_text("utf-8") if out.exists() else ""
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("files=coverage.xml\nskip=false\n", outputs)


class AdoptAndReportRows(CoverageTaskCase):
    """The write-mode and upload halves read the same measurement."""

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

    def test_report_writes_the_cobertura_report(self) -> None:
        """The upload is rendered from the leg's own run, and listed."""
        done, tree = self.run_task("coverage:report")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:report: coverage.xml written\n"
            "coverage:report: upload coverage.xml\n",
            done.stdout,
        )
        self.assertTrue((tree / "coverage.xml").is_file())

    def test_report_without_a_floor_lists_nothing(self) -> None:
        """No floor: skipped, and no upload line for the badge job to read."""
        done, _ = self.run_task("coverage:report", floor=False)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("coverage:report: no .coverage-floor, skipped\n", done.stdout)

    def test_report_without_a_measurable_language_lists_nothing(self) -> None:
        """Nothing measurable: skipped, and no upload line."""
        done, _ = self.run_task("coverage:report", test_task=False)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:report: no measurable language, skipped\n", done.stdout
        )

    def test_report_mixed_tree_lists_every_report_it_wrote(self) -> None:
        """#1386: the upload list is what exists, in one order."""
        done, _ = self.run_task("coverage:report", go="90.0")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(
            done.stdout.endswith("coverage:report: upload coverage.xml\n"),
            done.stdout,
        )

    def test_report_that_wrote_nothing_is_refused(self) -> None:
        """A leg that ran and left no report is refused by the task."""
        # The Go stub measures but writes no profile, so the Go leg ran
        # and nothing exists to upload.
        done, _ = self.run_task("coverage:report", go="90.0", python=False)
        self.assertEqual(1, done.returncode, done.stdout)
        self.assertIn(
            "coverage:report: wrote no report — nothing to upload", done.stderr
        )
        self.assertNotIn("upload ", done.stdout)


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
    printf '{"data": [{"totals": {"lines": {"percent": %s}}}]}\\n' "${STUB_RUST}"
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
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            env |= {
                "ORG_BELT_DIR": str(BELT),
                "PATH": f"{stubs}{os.pathsep}{env.get('PATH', '')}",
                "STUB_RUST": "61.5",
            }
            done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [BASH, str(BELT / "coverage-measure.sh"), "coverage:test", "rust"],
                cwd=tree,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("rust 61.5\n", done.stdout)


if __name__ == "__main__":
    unittest.main()
