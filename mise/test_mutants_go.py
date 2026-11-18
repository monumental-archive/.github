#!/usr/bin/env python3
"""Table tests for audit:mutants' Go leg, mise/mutants-go.sh + .py (#1356).

The leg runs for real in a planted git tree — a module with two
packages, `a` and `b`, whose files and `//mutants:equivalent` comments
are real tracked files, because the leg reads them with `git grep` —
with `go` and `gomu` as stubs. No row runs gomu: the Mac it was chosen
on ran out of memory under a real run, and the first real run is the
audit job's.

`go` answers `env GOROOT` with the planted GOROOT, `list` with the
planted package table, and `test` (the baseline coverage run) by
writing the planted profile and printing the planted log. The planted
GOROOT holds the real `go`; a second `go`, standing in for mise's shim,
is first on PATH, so the row that asserts which `go` gomu saw is red if
the leg stops putting the real toolchain first.

`gomu` logs its arguments, its working directory and the `go` it
resolves, copies the planted JSON report for the file it is given into
its working directory as `mutation-report.json` — the shape gomu v0.3.0
writes, read from its source — and writes `.gomu_history.json` there,
as the real one does whatever `--incremental` says. Every row asserts
the exact output, so a row cannot pass by stopping somewhere else.

GIT ENVIRONMENT IS SCRUBBED (#857): git exports GIT_DIR to hooks run in
a linked worktree, and a fixture that inherited it would write into the
caller's index when this suite runs from the pre-push hook.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import json
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from pathlib import Path

BELT = Path(__file__).resolve().parent
SCRIPT = BELT / "mutants-go.sh"
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"
LABEL = "audit:mutants: go"
MODULE = "example.com/p"
RED_TAIL = (
    f"{LABEL}: red — kill each survivor with an assertion, add a test for"
    " each line no test runs, or record an equivalent mutant at its site:"
    " //mutants:equivalent <type> <reason>\n"
)

REAL_GO_STUB = """#!/usr/bin/env bash
printf 'go %s\\n' "$*" >> "${PLANT_LOG}"
case "$1" in
  env) printf '%s\\n' "${PLANT_GOROOT}" ;;
  list) cat "${PLANT_LIST}" ;;
  test)
    for a in "$@"; do
      [[ ${a} != -coverprofile=* ]] || cp "${PLANT_COVER}" "${a#-coverprofile=}"
    done
    cat "${PLANT_BASELINE}"
    exit "${PLANT_TEST_STATUS:-0}"
    ;;
  *) exit 99 ;;
esac
"""
SHIM_GO_STUB = """#!/usr/bin/env bash
case "$1" in
  env) printf '%s\\n' "${PLANT_GOROOT}" ;;
  *) echo "shim go called: $*" >&2; exit 1 ;;
esac
"""
GOMU_STUB = """#!/usr/bin/env bash
printf 'gomu %s\\n' "$*" >> "${PLANT_LOG}"
printf 'cwd %s\\n' "$(pwd -P)" >> "${PLANT_LOG}"
printf 'sees %s\\n' "$(command -v go)" >> "${PLANT_LOG}"
# gomu v0.3.0's FindIgnoreFile (internal/ignore/parser.go): from the
# target, or its directory when the target is a file, walk up to the
# first directory holding a .gomuignore; a relative target resolves
# against the CWD first (filepath.Abs).
dir=$2
[[ -d ${dir} ]] || dir=$(dirname "${dir}")
[[ ${dir} == /* ]] || dir="${PWD}/${dir}"
while [[ ! -f ${dir}/.gomuignore && ${dir} != / ]]; do dir=$(dirname "${dir}"); done
found="${dir}/.gomuignore"
[[ ! -f ${found} ]] || printf 'ignore %s\\n' "${found}" >> "${PLANT_LOG}"
base=$(basename "$2" .go)
[[ ! -f ${PLANT_REPORTS}/${base}.log ]] || cat "${PLANT_REPORTS}/${base}.log"
json="${PLANT_REPORTS}/${base}.json"
[[ ! -f ${json} ]] || cp "${json}" mutation-report.json
echo '{}' > .gomu_history.json
exit "${PLANT_GOMU_STATUS:-0}"
"""

COMPILE_ERROR = (
    "Compilation failed: compilation error: # example.com/p/a\n"
    "./a.go:9:9: missing return\n"
)
SHIM_ERROR = (
    "Compilation failed: compilation error: mise ERROR No version is set for shim: go\n"
)
MISE_STUB = """#!/usr/bin/env bash
case "$*" in
  "which gomu") printf '%s\\n' "${PLANT_REAL_GOMU}" ;;
  *) echo "mise ERROR shim refused: $*" >&2; exit 2 ;;
esac
"""

A_GO = """package a

func Add(x, y int) int {
\treturn x + y
}

func Sign(x int) int {
\tif x < 0 {
\t\treturn -1
\t}
\treturn 1
}
"""
ABOVE = "\t//mutants:equivalent conditional zero takes either branch\n\tif x < 0 {"
IF_LINE = "\tif x < 0 {"
B_GO = """package b

func Twice(x int) int {
\treturn x * 2
}
"""


def clean_env() -> dict[str, str]:
    """Copy the environment without git's own variables.

    Returns:
        os.environ minus every GIT_* key.

    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def summary_line(counts: tuple[int, int, int, int, int, int]) -> str:
    """Spell the leg's summary line for a whole (unsharded) run.

    `counts` is mutants, killed, survived, not covered, not viable,
    excluded.

    Returns:
        The line, newline included.

    """
    total, killed, survived, uncovered, viable, excluded = counts
    return (
        f"{LABEL}: {total} mutants, {killed} killed, {survived} survived,"
        f" {uncovered} not covered, {viable} not viable, {excluded} excluded\n"
    )


def measuring(count: int = 2) -> str:
    """Spell the coverage line for a whole run.

    Returns:
        The line, newline included.

    """
    return f"{LABEL}: measuring coverage of {count} package(s)\n"


class Planted(unittest.TestCase):
    """A planted module, a real GOROOT, a shim first on PATH, stub gomu."""

    def setUp(self) -> None:
        """Plant the module, the stubs and the default answers."""
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.tree = self.tmp / "tree"
        (self.tree / "a").mkdir(parents=True)
        (self.tree / "b").mkdir()
        self.goroot = self.tmp / "goroot"
        (self.goroot / "bin").mkdir(parents=True)
        shims = self.tmp / "shims"
        shims.mkdir()
        self.shims = shims
        for path, body in {
            self.goroot / "bin" / "go": REAL_GO_STUB,
            shims / "go": SHIM_GO_STUB,
            shims / "gomu": GOMU_STUB,
        }.items():
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
        self.reports = self.tmp / "reports"
        self.reports.mkdir()
        # (package, line) to the `error` gomu writes for that mutant.
        self.errors: dict[tuple[str, int], str] = {}
        self.summary = self.tmp / "summary"
        self.summary.mkdir()
        self.log = self.tmp / "log"
        self.log.touch()
        self.listing = self.tmp / "list"
        self.cover = self.tmp / "cover.out"
        self.baseline = self.tmp / "baseline"
        self.env = clean_env() | {
            "PATH": f"{shims}{os.pathsep}{os.environ['PATH']}",
            "ORG_BELT_DIR": str(BELT),
            "PLANT_GOROOT": str(self.goroot),
            "PLANT_LOG": str(self.log),
            "PLANT_LIST": str(self.listing),
            "PLANT_COVER": str(self.cover),
            "PLANT_BASELINE": str(self.baseline),
            "PLANT_REPORTS": str(self.reports),
        }
        self.git("init", "-q")
        root = self.tree.resolve()
        self.root = root
        self.listing.write_text(
            f"{MODULE}/a\t{root}/a\ta.go\n{MODULE}/b\t{root}/b\tb.go\n",
            encoding="utf-8",
        )
        self.cover_lines(a=[(3, 5, 1), (7, 12, 1)], b=[(3, 5, 1)])
        self.baseline.write_text(
            f"ok  \t{MODULE}/a\t0.5s\tcoverage: 100.0% of statements\n"
            f"ok  \t{MODULE}/b\t12.3s\tcoverage: 100.0% of statements\n",
            encoding="utf-8",
        )
        self.write("go.mod", f"module {MODULE}\n")
        self.write("a/a.go", A_GO)
        self.write("b/b.go", B_GO)

    def git(self, *args: str) -> None:
        """Run git in the planted tree, scrubbed of the caller's git state."""
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [GIT, *args], cwd=self.tree, env=clean_env(), check=True
        )

    def write(self, name: str, text: str) -> None:
        """Write and track one file in the planted tree."""
        (self.tree / name).write_text(text, encoding="utf-8")
        self.git("add", name)

    def adopt(
        self, ignore: str = "# no patterns: records live at their sites\n"
    ) -> None:
        """Track the adoption file at the root."""
        self.write(".gomuignore", ignore)

    def cover_lines(self, **files: list[tuple[int, int, int]]) -> None:
        """Plant the profile: per package, (first line, last line, count)."""
        lines = ["mode: set"]
        for pkg, blocks in files.items():
            lines.extend(
                f"{MODULE}/{pkg}/{pkg}.go:{first}.1,{last}.2 1 {count}"
                for first, last, count in blocks
            )
        self.cover.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def report(
        self, pkg: str, *results: tuple[str, int, str], total: int | None = None
    ) -> None:
        """Plant gomu's report for one file: (status, line, type) per mutant."""
        body = {
            "totalFiles": 1,
            "processedFiles": 1,
            "totalMutants": len(results) if total is None else total,
            "results": [
                {
                    "mutant": {
                        "id": f"{pkg}-{i}",
                        "filePath": f"{self.root}/{pkg}/{pkg}.go",
                        "line": line,
                        "column": 9,
                        "type": kind,
                    },
                    "status": status,
                    "error": self.errors.get((pkg, line), ""),
                }
                for i, (status, line, kind) in enumerate(results)
            ],
        }
        (self.reports / f"{pkg}.json").write_text(json.dumps(body), encoding="utf-8")

    def clean_reports(self) -> None:
        """Plant an all-killed report for both packages."""
        self.report("a", ("KILLED", 4, "arithmetic"), ("KILLED", 8, "conditional"))
        self.report("b", ("KILLED", 4, "arithmetic"))

    def run_leg(self) -> subprocess.CompletedProcess:
        """Run the leg in the planted tree.

        Returns:
            The completed process.

        """
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, str(SCRIPT)],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def calls(self, prefix: str) -> list[str]:
        """Read the stub log's lines that start with `prefix`.

        Returns:
            The matching lines.

        """
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return [line for line in lines if line.startswith(prefix)]


class Adoption(Planted):
    """A tracked root .gomuignore with no pattern is adoption."""

    def test_no_gomuignore_skips_clean_by_name(self) -> None:
        """Without the adoption file nothing runs, and the totals hear so."""
        self.env["MUTANTS_SUMMARY_DIR"] = str(self.summary)
        got = self.run_leg()
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(got.stdout, f"{LABEL}: no .gomuignore tracked, skipped\n")
        self.assertEqual(got.stderr, "")
        self.assertEqual(self.log.read_text(encoding="utf-8"), "")
        self.assertEqual(
            (self.summary / "go.tsv").read_text(encoding="utf-8"), "whole\tskipped\n"
        )

    def test_a_pattern_in_the_root_gomuignore_is_red_by_name(self) -> None:
        """A pattern drops whole files from the run and the count."""
        self.adopt("# reason\nb/\n")
        got = self.run_leg()
        self.assertEqual(got.returncode, 1)
        self.assertEqual(got.stdout, "")
        self.assertEqual(
            got.stderr,
            f"{LABEL}: pattern in .gomuignore:2: b/\n"
            "  a .gomuignore pattern drops whole files from the run and the count;\n"
            "  record an equivalent mutant at its site instead:"
            " //mutants:equivalent <type> <reason>\n",
        )
        self.assertEqual(self.calls("gomu"), [])

    def test_a_pattern_in_a_nested_gomuignore_is_red_by_name(self) -> None:
        """Gomu walks up from each file, so a nested file applies too."""
        self.adopt()
        self.write("a/.gomuignore", "a.go\n")
        got = self.run_leg()
        self.assertEqual(got.returncode, 1)
        self.assertIn(f"{LABEL}: pattern in a/.gomuignore:1: a.go\n", got.stderr)
        self.assertEqual(self.calls("gomu"), [])


class Verdicts(Planted):
    """Each status is classed, each survivor named, the summary always printed."""

    def test_clean_run_is_green_with_the_summary(self) -> None:
        """Killed and timed out are killed; not viable is its own count."""
        self.adopt()
        self.errors["a", 9] = COMPILE_ERROR
        self.report(
            "a",
            ("KILLED", 4, "arithmetic"),
            ("TIMED_OUT", 8, "conditional"),
            ("NOT_VIABLE", 9, "return"),
        )
        self.report("b", ("KILLED", 4, "arithmetic"))
        got = self.run_leg()
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(got.stdout, measuring() + summary_line((4, 3, 0, 0, 1, 0)))
        self.assertEqual(got.stderr, "")

    def test_one_survivor_is_red_with_file_and_line(self) -> None:
        """A covered survivor is named with its site and mutator."""
        self.adopt()
        self.report("a", ("KILLED", 4, "arithmetic"), ("SURVIVED", 8, "boundary_value"))
        self.report("b", ("KILLED", 4, "arithmetic"))
        got = self.run_leg()
        self.assertEqual(got.returncode, 1)
        self.assertEqual(
            got.stdout,
            measuring()
            + f"{LABEL}: survived: a/a.go:8:9 boundary_value\n"
            + summary_line((3, 2, 1, 0, 0, 0)),
        )
        self.assertEqual(got.stderr, RED_TAIL)

    def test_a_survivor_no_test_runs_is_not_covered(self) -> None:
        """Gomu says SURVIVED; the profile says no test executed the line."""
        self.adopt()
        self.cover_lines(a=[(3, 5, 1), (7, 12, 0)], b=[(3, 5, 1)])
        self.report("a", ("KILLED", 4, "arithmetic"), ("SURVIVED", 9, "return"))
        self.report("b", ("KILLED", 4, "arithmetic"))
        got = self.run_leg()
        self.assertEqual(got.returncode, 1)
        self.assertEqual(
            got.stdout,
            measuring()
            + f"{LABEL}: not covered: a/a.go:9:9 return\n"
            + summary_line((3, 2, 0, 1, 0, 0)),
        )
        self.assertEqual(got.stderr, RED_TAIL)

    def test_an_error_result_is_not_judged(self) -> None:
        """Gomu's ERROR is a mutant nobody asked a test about."""
        self.adopt()
        self.report("a", ("KILLED", 4, "arithmetic"), ("ERROR", 8, "branch"))
        self.report("b", ("KILLED", 4, "arithmetic"))
        got = self.run_leg()
        self.assertEqual(got.returncode, 1)
        self.assertEqual(
            got.stderr,
            f"{LABEL}: not judged: ERROR: a/a.go:8:9 branch — gomu did not judge it\n"
            + RED_TAIL,
        )

    def test_a_count_gomu_did_not_report_is_not_judged(self) -> None:
        """Mutants counted but missing from results were never run."""
        self.adopt()
        self.report("a", ("KILLED", 4, "arithmetic"), total=5)
        self.report("b", ("KILLED", 4, "arithmetic"))
        got = self.run_leg()
        self.assertEqual(got.returncode, 1)
        self.assertEqual(
            got.stderr,
            f"{LABEL}: not judged: 1.json: gomu counted 5 mutants and reported 1\n"
            + RED_TAIL,
        )

    def test_a_file_gomu_skipped_with_an_error_is_red(self) -> None:
        """Gomu prints "(error: ...)" for a file it skipped, and exits 0."""
        self.adopt()
        self.clean_reports()
        (self.reports / "b.log").write_text(
            "[1/1] b.go (error: parse failed)\n", encoding="utf-8"
        )
        (self.reports / "b.json").unlink()
        got = self.run_leg()
        self.assertEqual(got.returncode, 1)
        self.assertEqual(
            got.stderr,
            f"{LABEL}: not judged: [1/1] b.go (error: parse failed)\n"
            f"{LABEL}: not judged: b/b.go: gomu wrote no report\n" + RED_TAIL,
        )

    def test_a_failing_gomu_shows_its_own_output_once(self) -> None:
        """Exit 2 on every file, as on stele's first run: the cause is shown."""
        self.adopt()
        self.env["PLANT_GOMU_STATUS"] = "2"
        (self.reports / "a.log").write_text(
            "panic: boom\ngoroutine 1 [running]:\n", encoding="utf-8"
        )
        (self.reports / "b.log").write_text("panic: boom\n", encoding="utf-8")
        got = self.run_leg()
        self.assertEqual(got.returncode, 1)
        self.assertEqual(
            got.stderr,
            f"{LABEL}: gomu ({self.shims}/gomu) exited 2 on a/a.go; its output:\n"
            "    panic: boom\n"
            "    goroutine 1 [running]:\n"
            f"{LABEL}: not judged: a/a.go: gomu exited 2\n"
            f"{LABEL}: not judged: b/b.go: gomu exited 2\n" + RED_TAIL,
        )

    def test_not_viable_counts_only_a_compiler_error(self) -> None:
        """A compile check that failed for a reason of its own judged nothing."""
        self.adopt()
        self.errors["a", 9] = SHIM_ERROR
        self.report("a", ("KILLED", 4, "arithmetic"), ("NOT_VIABLE", 9, "return"))
        self.report("b", ("KILLED", 4, "arithmetic"))
        got = self.run_leg()
        self.assertEqual(got.returncode, 1)
        self.assertIn(summary_line((3, 2, 0, 0, 0, 0)), got.stdout)
        self.assertEqual(
            got.stderr,
            f"{LABEL}: not judged: NOT_VIABLE: a/a.go:9:9 return — not the"
            " compiler: Compilation failed: compilation error: mise ERROR No"
            " version is set for shim: go\n" + RED_TAIL,
        )

    def test_a_red_baseline_is_red_before_any_mutant(self) -> None:
        """A suite that already fails cannot judge a mutant."""
        self.adopt()
        self.clean_reports()
        self.env["PLANT_TEST_STATUS"] = "1"
        self.baseline.write_text("FAIL\texample.com/p/a\n", encoding="utf-8")
        got = self.run_leg()
        self.assertEqual(got.returncode, 1)
        self.assertEqual(
            got.stderr,
            "FAIL\texample.com/p/a\n"
            f"{LABEL}: the suite fails before any mutant (go test exited 1)\n",
        )
        self.assertEqual(self.calls("gomu"), [])


class Records(Planted):
    """`//mutants:equivalent <type> <reason>` at the site, and only there."""

    def survive_at_sign(self, line: int, kind: str = "conditional") -> None:
        """Plant one covered survivor in a/a.go."""
        self.report("a", ("KILLED", 4, "arithmetic"), ("SURVIVED", line, kind))
        self.report("b", ("KILLED", 4, "arithmetic"))

    def test_a_record_above_the_line_explains_it_and_is_counted(self) -> None:
        """The comment on the line above names the type and says why."""
        self.adopt()
        self.write(
            "a/a.go",
            A_GO.replace(IF_LINE, ABOVE),
        )
        self.survive_at_sign(9)
        got = self.run_leg()
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(got.stdout, measuring() + summary_line((3, 2, 0, 0, 0, 1)))

    def test_a_trailing_record_explains_its_own_line(self) -> None:
        """Code before the comment: the record's site is its own line."""
        self.adopt()
        self.write(
            "a/a.go",
            A_GO.replace(
                "\tif x < 0 {",
                "\tif x < 0 { //mutants:equivalent conditional zero is either",
            ),
        )
        self.survive_at_sign(8)
        got = self.run_leg()
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn(summary_line((3, 2, 0, 0, 0, 1)), got.stdout)

    def test_a_record_with_no_reason_is_red(self) -> None:
        """An exclusion that does not say why explains nothing."""
        self.adopt()
        self.write(
            "a/a.go",
            A_GO.replace(
                "\tif x < 0 {", "\t//mutants:equivalent conditional\n\tif x < 0 {"
            ),
        )
        self.survive_at_sign(9)
        got = self.run_leg()
        self.assertEqual(got.returncode, 1)
        self.assertIn(f"{LABEL}: survived: a/a.go:9:9 conditional\n", got.stdout)
        self.assertEqual(
            got.stderr,
            f"{LABEL}: a/a.go:8: //mutants:equivalent conditional gives no reason\n"
            + RED_TAIL,
        )

    def test_a_record_of_the_wrong_type_is_red(self) -> None:
        """The record must name the surviving mutant's type."""
        self.adopt()
        self.write(
            "a/a.go",
            A_GO.replace(
                "\tif x < 0 {",
                "\t//mutants:equivalent branch the sign is total\n\tif x < 0 {",
            ),
        )
        self.survive_at_sign(9)
        got = self.run_leg()
        self.assertEqual(got.returncode, 1)
        self.assertEqual(
            got.stderr,
            f"{LABEL}: a/a.go:8: //mutants:equivalent branch names branch,"
            " but what survives here is conditional\n" + RED_TAIL,
        )

    def test_a_stale_record_is_red(self) -> None:
        """A record whose mutant is now killed, or gone, must be deleted."""
        self.adopt()
        self.write(
            "a/a.go",
            A_GO.replace(IF_LINE, ABOVE),
        )
        self.clean_reports()
        got = self.run_leg()
        self.assertEqual(got.returncode, 1)
        self.assertEqual(
            got.stderr,
            f"{LABEL}: a/a.go:8: //mutants:equivalent conditional is stale:"
            " nothing survives at line 9 — delete it\n" + RED_TAIL,
        )

    def test_a_record_on_a_not_covered_mutant_is_still_red(self) -> None:
        """Untested is not equivalent: only a test fixes it."""
        self.adopt()
        self.cover_lines(a=[(3, 5, 1), (7, 13, 0)], b=[(3, 5, 1)])
        self.write(
            "a/a.go",
            A_GO.replace(IF_LINE, ABOVE),
        )
        self.survive_at_sign(9)
        got = self.run_leg()
        self.assertEqual(got.returncode, 1)
        self.assertIn(f"{LABEL}: not covered: a/a.go:9:9 conditional\n", got.stdout)
        self.assertEqual(
            got.stderr,
            f"{LABEL}: a/a.go:8: //mutants:equivalent conditional records a mutant"
            " no test runs — untested is not equivalent; only a test fixes it\n"
            + RED_TAIL,
        )


class Toolchain(Planted):
    """The real go, one worker, no history, nothing left in the tree."""

    def test_gomu_runs_with_the_real_go_first_on_path(self) -> None:
        """A shim first on PATH is put behind the toolchain's own go."""
        self.adopt()
        self.clean_reports()
        got = self.run_leg()
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(set(self.calls("sees")), {f"sees {self.goroot}/bin/go"})

    def test_gomu_is_called_per_file_non_incremental_with_one_worker(self) -> None:
        """Each file, absolute; the timeout from the package's own test time."""
        self.adopt()
        self.clean_reports()
        got = self.run_leg()
        self.assertEqual(got.returncode, 0, got.stderr)
        tail = "--incremental=false --output json --fail-on-gate=false"
        self.assertEqual(
            self.calls("gomu"),
            [
                f"gomu run {self.root}/a/a.go --workers 1 --timeout 30 {tail}",
                f"gomu run {self.root}/b/b.go --workers 1 --timeout 124 {tail}",
            ],
        )

    def test_gomu_finds_the_root_gomuignore_from_the_scratch_directory(
        self,
    ) -> None:
        """The absolute file path, not the CWD, leads gomu to the adoption file."""
        self.adopt()
        self.clean_reports()
        got = self.run_leg()
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(self.calls("ignore"), [f"ignore {self.root}/.gomuignore"] * 2)
        self.assertNotIn(f"cwd {self.root}", self.calls("cwd"))

    def test_a_mise_shim_is_resolved_to_the_binary(self) -> None:
        """The binary runs, not the shim, so its go children see real go."""
        self.adopt()
        self.clean_reports()
        misebin = self.tmp / "misebin"
        misebin.mkdir()
        (misebin / "mise").write_text(MISE_STUB, encoding="utf-8")
        (misebin / "mise").chmod(0o755)
        (misebin / "gomu").symlink_to(misebin / "mise")
        self.env["PATH"] = f"{misebin}{os.pathsep}{self.env['PATH']}"
        self.env["PLANT_REAL_GOMU"] = f"{self.shims}/gomu"
        got = self.run_leg()
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(len(self.calls("gomu run")), 2)

    def test_nothing_gomu_writes_lands_in_the_tree(self) -> None:
        """History and report go to a scratch directory, never the tree."""
        self.adopt()
        self.clean_reports()
        got = self.run_leg()
        self.assertEqual(got.returncode, 0, got.stderr)
        for cwd in self.calls("cwd"):
            self.assertNotEqual(cwd, f"cwd {self.root}")
        self.assertFalse((self.tree / ".gomu_history.json").exists())
        self.assertFalse((self.tree / "mutation-report.json").exists())

    def test_a_generated_file_is_not_mutated(self) -> None:
        """Go's generated-code marker keeps a file out of the population."""
        self.adopt()
        self.write("b/b.go", "// Code generated by hand. DO NOT EDIT.\n\n" + B_GO)
        self.clean_reports()
        got = self.run_leg()
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(len(self.calls("gomu")), 1)
        self.assertIn(summary_line((2, 2, 0, 0, 0, 0)), got.stdout)


class Shards(Planted):
    """MUTANTS_SHARD=k/n takes every n-th package; go.tsv feeds the totals."""

    def test_a_shard_mutates_its_packages_and_writes_its_counts(self) -> None:
        """Shard 1/2 is package b alone; excluded is the whole tree's."""
        self.adopt()
        self.write(
            "a/a.go",
            A_GO.replace(IF_LINE, ABOVE),
        )
        self.report("b", ("KILLED", 4, "arithmetic"), ("SURVIVED", 4, "return"))
        self.env |= {"MUTANTS_SHARD": "1/2", "MUTANTS_SUMMARY_DIR": str(self.summary)}
        got = self.run_leg()
        self.assertEqual(got.returncode, 1)
        self.assertEqual(
            got.stdout,
            f"{LABEL}: measuring coverage of 1 package(s), shard 1/2\n"
            f"{LABEL}: survived: b/b.go:4:9 return\n"
            f"{LABEL}: shard 1/2: 2 mutants, 1 killed, 1 survived,"
            " 0 not covered, 0 not viable, 1 excluded\n",
        )
        self.assertEqual(len(self.calls("gomu")), 1)
        self.assertEqual(
            (self.summary / "go.tsv").read_text(encoding="utf-8"), "1/2\t2\t1\t1\t1\n"
        )

    def test_a_malformed_shard_is_red(self) -> None:
        """A typo must not read as "run everything"."""
        self.adopt()
        self.env["MUTANTS_SHARD"] = "2/2"
        got = self.run_leg()
        self.assertEqual(got.returncode, 1)
        self.assertEqual(
            got.stderr, f"{LABEL}: MUTANTS_SHARD='2/2' is not k/n with k < n\n"
        )


if __name__ == "__main__":
    unittest.main()
