#!/usr/bin/env python3
"""Table tests for the Go condition-coverage floor (#1546).

`audit:coverage-go-branches` holds the `go-branches` line of
`.coverage-floor` on the Monday cron, measured by
`mise/coverage-go-branches.sh` with gobco;
`coverage:adopt` writes that line. The bodies under test are the tasks'
own `run` strings, read out of mise/config.toml with tomllib and run by
bash in a planted git tree, and they call the real measurement script
and the real `coverage_floor.py`. Only `go` and `gobco` are stubs, and
no row runs gobco or a Go suite.

The `go` stub answers what the leg asks of it (GOROOT, GOVERSION, the
build version of a binary, the package list, and the gate leg's
`go test` and `go tool cover`). The planted GOROOT's `bin/go` is the
same stub, so a row can tell which `go` gobco found first on PATH.
`gobco` logs each call (its directory, its arguments, and the `go` it
resolves) and prints the condition count the row planted for that
package.

Each row asserts the message, not just the exit status: a planted row
scored on exit alone can pass for the wrong reason.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import tomllib
import unittest
from decimal import Decimal
from pathlib import Path

import coverage_floor as cf
import hermetic

CANON = Path(__file__).resolve().parent.parent
BELT = CANON / "mise"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")

CONFIG = tomllib.loads((BELT / "config.toml").read_text(encoding="utf-8"))


TASK = "audit:coverage-go-branches"
SKIP = f"{TASK}: no go-branches line in .coverage-floor, skipped\n"
REMEDY = "    mise install go:github.com/rillig/gobco\n"

# The main floor, measured 92.0 minus the band; the go-branches line
# beside it, measured 82.0 minus the band. Both hold to the derived-state
# law.
MAIN = """\
# band: 2
# measured: 92.0
# derived: v1.0.0
90.0
"""
BRANCH = """\
# go-branches measured: 82.0
go-branches 80.0
"""

# Two packages, 42 of 50 conditions taken both ways: 84%.
COUNTS = "a=5/8 b=37/42"

GO = """\
#!/usr/bin/env bash
echo "go $*" >> "${STUB_GO_LOG}"
case "$1 $2" in
  "env GOROOT") echo "${STUB_GOROOT}" ;;
  "env GOVERSION") echo "${STUB_RUNNING}" ;;
  "version -m")
    [[ -z ${STUB_BUILT} ]] || printf '%s: %s\\n' "$3" "${STUB_BUILT}"
    printf '\\tpath\\tgithub.com/rillig/gobco\\n'
    ;;
  "list -f")
    for pkg in ${STUB_PKGS}; do echo "${PWD}/${pkg}"; done
    ;;
  "test ./...")
    # The profile at the path the leg names (#1628), outside the tree.
    for arg in "$@"; do
      [[ ${arg} != -coverprofile=* ]] || echo "mode: atomic" > "${arg#*=}"
    done
    ;;
  "tool cover") printf 'total:\\t(statements)\\t%s%%\\n' "${STUB_LINES}" ;;
  *) exit 1 ;;
esac
"""

GOBCO = """\
#!/usr/bin/env bash
pkg=${PWD##*/}
echo "${pkg} [$*] go=$(command -v go)" >> "${STUB_LOG}"
for entry in ${STUB_COUNTS}; do
  if [[ ${entry%%=*} == "${pkg}" ]]; then
    count=${entry#*=}
  fi
done
if [[ ${pkg} == "${STUB_FAIL:-}" ]]; then
  echo "--- FAIL: TestPlanted"
  echo "go test .: exit status 1"
  echo
  echo "Condition coverage: ${count}"
  exit 1
fi
echo "ok  	example/${pkg}	0.1s"
echo
[[ -n ${STUB_SILENT:-} ]] || echo "Condition coverage: ${count}"
"""

# mise's shim: `which gobco` names the real binary, anything else is
# refused, as a shim run would be.
MISE = """\
#!/usr/bin/env bash
case "$*" in
  "which gobco") printf '%s\\n' "${STUB_REAL_GOBCO}" ;;
  *) echo "mise ERROR shim refused: $*" >&2; exit 2 ;;
esac
"""


def plant_tools(tmp: Path, gobco: str) -> None:
    """Write the go and gobco stubs, the planted GOROOT and the logs.

    `gobco` is "binary" (the stub on PATH), "shim" (on PATH as a mise
    shim whose `which` names the stub) or "missing".
    """
    stubs, real, goroot = tmp / "stubs", tmp / "real", tmp / "goroot"
    for d in (stubs, real, goroot / "bin"):
        d.mkdir(parents=True)
    for path, text in (
        (stubs / "go", GO),
        (goroot / "bin" / "go", GO),
        (real / "gobco", GOBCO),
        (stubs / "mise", MISE),
    ):
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
    shims = {"binary": real / "gobco", "shim": stubs / "mise"}
    if gobco in shims:
        (stubs / "gobco").symlink_to(shims[gobco])
    for name in ("gobco.log", "go.log"):
        (tmp / name).touch()


def plant_tree(tree: Path, pkgs: str, *, go_mod: bool, floor: str | None) -> list[str]:
    """Write one directory per package, go.mod and the floor.

    Returns:
        The paths to track.

    """
    tree.mkdir()
    tracked = []
    for pkg in pkgs.split():
        (tree / pkg).mkdir()
        (tree / pkg / f"{pkg}.go").write_text(f"package {pkg}\n", encoding="utf-8")
        tracked.append(f"{pkg}/{pkg}.go")
    if go_mod:
        (tree / "go.mod").write_text("module example\n\ngo 1.27.0\n", encoding="utf-8")
        tracked.append("go.mod")
    if floor is not None:
        (tree / ".coverage-floor").write_text(floor, encoding="utf-8")
        tracked.append(".coverage-floor")
    return tracked


def path_without_gobco(path: str, tmp: Path) -> list[str]:
    """Hide every gobco on the suite's PATH.

    It may carry one (the belt's lazy pin puts a mise shim for it beside
    the others), and the stubs stand in for it: a directory that holds
    one is mirrored without it, so "missing" is missing and every other
    tool in that directory still resolves.

    Returns:
        The PATH entries, in order.

    """
    entries = []
    for i, entry in enumerate(path.split(os.pathsep)):
        if not (Path(entry) / "gobco").exists():
            entries.append(entry)
            continue
        mirror = tmp / f"path{i}"
        mirror.mkdir()
        for tool in Path(entry).iterdir():
            if tool.name != "gobco":
                (mirror / tool.name).symlink_to(tool)
        entries.append(str(mirror))
    return entries


def run_task(  # ruff: ignore[too-many-arguments] -- one keyword per planted fact
    task: str,
    *,
    floor: str | None = MAIN + BRANCH,
    go_mod: bool = True,
    counts: str = COUNTS,
    pkgs: str = "a b",
    built: str = "go1.27.4",
    running: str = "go1.27.4",
    gobco: str = "binary",
    fail: str = "",
    silent: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run one task's body in a planted Go tree with stub tools.

    Returns:
        The finished process, and the temporary directory holding the
        tree (`tree/`), the gobco log (`gobco.log`), the go log
        (`go.log`) and the planted GOROOT (`goroot/`).

    """
    tmp = Path(tempfile.mkdtemp())
    tree = tmp / "tree"
    stubs = tmp / "stubs"
    plant_tools(tmp, gobco)
    tracked = plant_tree(tree, pkgs, go_mod=go_mod, floor=floor)
    env = hermetic.env()
    inherited = path_without_gobco(env.get("PATH", ""), tmp)
    env |= {
        "ORG_BELT_DIR": str(BELT),
        "PATH": os.pathsep.join([str(stubs), *inherited]),
        "STUB_LOG": str(tmp / "gobco.log"),
        "STUB_GO_LOG": str(tmp / "go.log"),
        "STUB_GOROOT": str(tmp / "goroot"),
        "STUB_RUNNING": running,
        "STUB_BUILT": built,
        "STUB_PKGS": pkgs,
        "STUB_COUNTS": counts,
        "STUB_FAIL": fail,
        "STUB_LINES": "95.0",
        "STUB_REAL_GOBCO": str(tmp / "real" / "gobco"),
        "ORG_COVERAGE_REPORTS": str(tmp / "reports"),
    }
    if silent:
        env["STUB_SILENT"] = "1"
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


class GoBranchCase(unittest.TestCase):
    """Runs a task and removes the planted tree afterwards."""

    def run_task(
        self, task: str = TASK, **plant: str | bool | None
    ) -> tuple[subprocess.CompletedProcess[str], Path, list[str]]:
        """Run a task and schedule its tree for removal.

        Returns:
            The finished process, the temporary directory, and one line
            per gobco call: the package, its arguments, and its `go`.

        """
        done, tmp = run_task(task, **plant)  # type: ignore[arg-type]
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        calls = (tmp / "gobco.log").read_text(encoding="utf-8").splitlines()
        return done, tmp, calls


class AuditRows(GoBranchCase):
    """`audit:coverage-go-branches`: held, red by name, and the skips."""

    def test_a_floor_held(self) -> None:
        """A measurement at or above the line holds it, by name."""
        done, _, _ = self.run_task()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            f"{TASK}: go-branches holds the floor 80.0 (84%)\n", done.stdout
        )

    def test_below_the_floor_reds_by_name(self) -> None:
        """39 of 50 is 78%, under the 80.0 line: red, naming both numbers."""
        done, _, _ = self.run_task(counts="a=5/8 b=34/42")
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertIn(
            f"{TASK}: go-branches: 78% is below the derived floor 80.0%\n",
            done.stderr,
        )

    def test_one_package_per_call_with_the_real_go_first(self) -> None:
        """Each package is its own gobco call, default mode, real go first."""
        done, tmp, calls = self.run_task()
        self.assertEqual(0, done.returncode, done.stderr)
        go = tmp / "goroot" / "bin" / "go"
        self.assertEqual([f"a [] go={go}", f"b [] go={go}"], calls)

    def test_a_shim_is_resolved_to_the_binary(self) -> None:
        """A gobco found as a mise shim runs as the binary `mise which` names."""
        done, tmp, calls = self.run_task(gobco="shim")
        self.assertEqual(0, done.returncode, done.stderr)
        go = tmp / "goroot" / "bin" / "go"
        self.assertEqual([f"a [] go={go}", f"b [] go={go}"], calls)

    def test_no_go_module_skips_clean(self) -> None:
        """A tree with no go.mod has no Go condition to measure."""
        done, _, calls = self.run_task(go_mod=False)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(f"{TASK}: no go.mod, skipped\n", done.stdout)
        self.assertEqual([], calls)

    def test_no_such_line_skips_clean(self) -> None:
        """A floor with no go-branches line measures nothing at all."""
        done, _, calls = self.run_task(floor=MAIN)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(SKIP, done.stdout)
        self.assertEqual([], calls)

    def test_no_floor_file_skips_clean(self) -> None:
        """A repository that adopted no floor owes no condition figure either."""
        done, _, calls = self.run_task(floor=None)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(SKIP, done.stdout)
        self.assertEqual([], calls)

    def test_a_build_go_older_than_the_running_go_is_refused(self) -> None:
        """A gobco built with Go 1.24.13 under Go 1.27.4 is red, unrun."""
        done, tmp, calls = self.run_task(built="go1.24.13")
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertEqual(
            f"{TASK}: {tmp}/stubs/gobco was built with go1.24.13, older than the"
            " go1.27.4 it runs under —\n"
            "  rebuild it with the repository's go:\n" + REMEDY,
            done.stderr,
        )
        self.assertEqual([], calls)

    def test_an_older_patch_release_is_refused(self) -> None:
        """go1.27.3 under go1.27.4 is older too."""
        done, _, calls = self.run_task(built="go1.27.3")
        self.assertEqual(1, done.returncode)
        self.assertIn("was built with go1.27.3, older than the go1.27.4", done.stderr)
        self.assertEqual([], calls)

    def test_an_unreadable_build_go_is_refused(self) -> None:
        """No build version reads as older, never as fine."""
        done, _, calls = self.run_task(built="")
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "was built with an unreadable Go, older than the go1.27.4", done.stderr
        )
        self.assertEqual([], calls)

    def test_a_newer_build_go_runs(self) -> None:
        """A build Go later than the running one reads its syntax."""
        done, _, calls = self.run_task(built="go1.28.0")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(2, len(calls))

    def test_gobco_missing_reds_with_the_install_line(self) -> None:
        """The belt pins it lazy; a missing gobco is named, never skipped."""
        done, _, _ = self.run_task(gobco="missing")
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            f"{TASK}: gobco missing — the belt pins it lazy; install it by name:\n"
            + REMEDY,
            done.stderr,
        )

    def test_a_failing_package_reds_by_name(self) -> None:
        """A suite that fails under gobco is refused, naming the package."""
        done, _, calls = self.run_task(fail="a")
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertIn(
            f"{TASK}: gobco failed in a — a suite that does not pass\n", done.stderr
        )
        self.assertEqual(1, len(calls))

    def test_no_count_printed_is_refused(self) -> None:
        """A gobco run that prints no count is refused, never read as 0."""
        done, _, _ = self.run_task(silent=True)
        self.assertEqual(1, done.returncode)
        self.assertIn(
            f"{TASK}: gobco printed no condition count in a — a\n", done.stderr
        )

    def test_no_conditions_counted_is_refused(self) -> None:
        """0 of 0 is a run that measured nothing, never a 0% to compare."""
        done, _, _ = self.run_task(counts="a=0/0 b=0/0")
        self.assertEqual(1, done.returncode)
        self.assertIn(
            f"{TASK}: gobco counted no conditions in any package — a\n", done.stderr
        )

    def test_a_typed_line_is_refused_as_drift(self) -> None:
        """A go-branches number typed over its record reds before measuring."""
        typed = MAIN + "# go-branches measured: 82.0\ngo-branches 60.0\n"
        done, _, calls = self.run_task(floor=typed)
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "go-branches floor 60.0 disagrees with its own record: measured"
            " 82.0 minus band 2 is 80.0.",
            done.stderr,
        )
        self.assertEqual([], calls)

    def test_the_tree_is_left_untouched(self) -> None:
        """The leg writes nothing into the tree it measures."""
        done, tmp, _ = self.run_task()
        self.assertEqual(0, done.returncode, done.stderr)
        status = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [GIT, "ls-files", "--others", "--modified", "--exclude-standard"],
            cwd=tmp / "tree",
            env=hermetic.env(),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual("", status.stdout)


class GateRows(GoBranchCase):
    """The gate's coverage:check never reaches gobco."""

    def test_check_measures_statements_beside_a_go_branches_line(self) -> None:
        """A committed go-branches line changes nothing the gate runs."""
        done, tmp, calls = self.run_task("coverage:check")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "coverage:check: go leg holds the floor 90.0 (95.0%)\n"
            f"coverage:check: upload {tmp / 'reports' / 'coverage.txt'}\n",
            done.stdout,
        )
        self.assertEqual([], calls)


class AdoptRows(GoBranchCase):
    """`coverage:adopt` derives the line from a measurement, never a guess."""

    def test_adopt_writes_a_derived_go_branches_line(self) -> None:
        """Both legs measured; the file parses with the line derived."""
        done, tmp, calls = self.run_task("coverage:adopt", floor=None)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn(
            "coverage-floor: adopted .coverage-floor at 93.0 (measured 95.0),"
            " go-branches 82.0 (measured 84.0)\n",
            done.stdout,
        )
        parsed, problems = cf.parse(
            (tmp / "tree" / ".coverage-floor").read_text(encoding="utf-8")
        )
        self.assertEqual([], problems)
        self.assertEqual(
            {"go-branches": (Decimal("82.0"), Decimal("84.0"))}, parsed.separate
        )
        self.assertEqual(2, len(calls))

    def test_adopt_cannot_lower_the_go_branches_line(self) -> None:
        """Below its floor plus the band, nothing is written."""
        done, tmp, _ = self.run_task("coverage:adopt", counts="a=5/8 b=35/42")
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "coverage-floor: go-branches measured 80.0% is below its floor"
            " 80.0% plus the 2-point band.",
            done.stderr,
        )
        self.assertEqual(
            MAIN + BRANCH,
            (tmp / "tree" / ".coverage-floor").read_text(encoding="utf-8"),
        )

    def test_adopt_with_gobco_missing_writes_nothing(self) -> None:
        """A Go tree's adoption owes the line; a missing gobco is red."""
        done, tmp, _ = self.run_task("coverage:adopt", floor=None, gobco="missing")
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "coverage:adopt: gobco missing — the belt pins it lazy", done.stderr
        )
        self.assertFalse((tmp / "tree" / ".coverage-floor").exists())


if __name__ == "__main__":
    unittest.main()
