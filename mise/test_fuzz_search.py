#!/usr/bin/env python3
"""Table tests for what the scheduled fuzz search keeps (#1897).

The bodies under test are the real `audit:fuzz` and `audit:go-fuzz`
run strings, read out of mise/config.toml, with the real
fuzz-search-lib.sh and fuzz-corpus-lib.sh they source, run by bash in a
planted git tree. Only the tools are stubs (`cargo`, `cargo-fuzz`,
`rustup`, `rustc`, the toolchain's `llvm-cov`, `go`), each logging what
it was handed:

- the search (`cargo fuzz run … <finds> <base>`, `go test -fuzz`)
  writes the planted finds where the real engine does and prints the
  planted progress lines; a planted crash is written where libFuzzer
  and Go write one, and fails the run;
- libFuzzer's merge (`-merge=1`) adds each find whose name the corpus
  lacks;
- the measurement (`cargo fuzz coverage` then `llvm-cov`, `go test
  -coverprofile` then `go tool cover`) answers 10% plus one point per
  corpus file whose bytes start `raise`, so a row states which finds
  raise the figure.

The run's length is read from planted logs through the library's own
reader, and the workflow is read through mise/workflow_yaml.py for the
steps and jobs the rows hold to.

Each row asserts the message, not just the exit status: a planted row
scored on exit alone can pass for the wrong reason.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic
import workflow_yaml

BELT = Path(__file__).resolve().parent
CANON = BELT.parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
SEARCH_LIB = BELT / "fuzz-search-lib.sh"
WORKFLOW = CANON / ".github" / "workflows" / "repo-deep-tests.yml"
STUBS = (
    CANON / "workflow-templates" / "deep-tests.yml",
    CANON / ".github" / "workflows" / "self-deep-tests.yml",
)
HOST = "x86_64-unknown-linux-gnu"
NIGHTLY = "nightly-2026-11-01"

RUSTC = hermetic.RUSTC_STUB.replace(
    "#!/bin/sh\n",
    '#!/bin/sh\nif [ "$1" = --print ]; then echo "${STUB_SYSROOT}"; exit 0; fi\n',
    1,
)

RUSTUP = f"""\
#!/usr/bin/env bash
case "$1 $2" in
  "toolchain list") echo "{NIGHTLY}-{HOST}" ;;
  *) printf 'rust-src\\nllvm-tools-{HOST}\\n' ;;
esac
"""

# Present on PATH, never run: `cargo fuzz` goes through cargo.
CARGO_FUZZ = "#!/usr/bin/env bash\nexit 99\n"

# cargo: `fuzz list` the targets (STUB_TARGETS, else the one); `fuzz
# run` the search or the merge;
# `fuzz coverage` writes a profile naming the corpus it replayed.
CARGO = """\
#!/usr/bin/env bash
echo "$*" >> "${STUB_DIR}/cargo.log"
case "$1 ${2:-}" in
  "metadata --no-deps")
    printf '{"target_directory": "%s/fuzz/target"}\\n' "${PWD}"
    ;;
  "fuzz list") printf '%s\\n' ${STUB_TARGETS:-parse_marker} ;;
  "fuzz run")
    target=$6 first=$7 second=$8
    if [[ " $* " == *" -merge=1 "* ]]; then
      for f in "${second}"/*; do
        [[ -f ${f} && ! -e ${first}/${f##*/} ]] || continue
        cp "${f}" "${first}/"
      done
      exit 0
    fi
    echo "base: $(cd "${second}" && ls -A | tr '\\n' ' ')" >> "${STUB_DIR}/cargo.log"
    [[ -z ${STUB_FINDS:-} ]] || cp "${STUB_FINDS}"/* "${first}/"
    echo "#2\tINITED cov: 10 ft: 10 corp: 1/1b"
    echo "#9\tNEW    cov: 12 ft: 12 corp: 2/2b"
    if [[ -n ${STUB_CRASH:-} ]]; then
      mkdir -p "fuzz/artifacts/${target}"
      echo boom > "fuzz/artifacts/${target}/crash-0badc0de"
      echo "==1==ERROR: AddressSanitizer: heap-buffer-overflow"
      exit 77
    fi
    ;;
  "fuzz coverage")
    target=$7 corpus=$8
    mkdir -p "fuzz/coverage/${target}"
    echo "${corpus}" > "fuzz/coverage/${target}/coverage.profdata"
    ;;
  *) exit 1 ;;
esac
"""

# llvm-cov: 100 lines, 10 covered plus one per corpus file starting
# `raise`.
LLVM_COV = """\
#!/usr/bin/env bash
for arg in "$@"; do
  case "${arg}" in -instr-profile=*) profile=${arg#*=} ;; *) ;; esac
done
corpus=$(< "${profile}")
n=$(grep -l '^raise' "${corpus}"/* 2> /dev/null | wc -l | tr -d ' ')
printf '{"data": [{"totals": {"lines": {"count": 100, "covered": %s}}}]}\\n' \\
  "$((10 + n))"
"""

# go: one package with FuzzLoad, at STUB_GO_PKG in the module it is run
# in (default internal/policy), imported as STUB_GO_IMPORT. Each call is
# also logged with the directory it ran in, to go-dir.log. `test -fuzz`
# writes the planted finds into the fuzz cache and prints the planted
# log; with STUB_GO_CRASH the plain pass writes a failing input among
# the seeds and fails. The measurement reads the seeds, and refuses a
# failing input among them, as the real replay would.
GO = """\
#!/usr/bin/env bash
echo "$*" >> "${STUB_DIR}/go.log"
echo "${PWD} $*" >> "${STUB_DIR}/go-dir.log"
dir=${STUB_GO_PKG:-internal/policy}
import=${STUB_GO_IMPORT:-example.com/x/internal/policy}
seeds="${PWD}/${dir}/testdata/fuzz/FuzzLoad"
cache="${STUB_GOCACHE}/fuzz/${import}/FuzzLoad"
out=""
for arg in "$@"; do
  case "${arg}" in -coverprofile=* | -func=*) out=${arg#*=} ;; *) ;; esac
done
case "$1 ${2:-}" in
  "list ./...") echo "${import}" ;;
  "list -f") echo "${PWD}/${dir}" ;;
  "list -deps") echo "${import}" ;;
  "test -list") echo FuzzLoad ;;
  "clean -fuzzcache") rm -rf "${STUB_GOCACHE}/fuzz" ;;
  "env GOCACHE") echo "${STUB_GOCACHE}" ;;
  "test -fuzz" | "test -race")
    mkdir -p "${cache}"
    [[ -z ${STUB_FINDS:-} ]] || cp "${STUB_FINDS}"/* "${cache}/"
    echo "fuzz: elapsed: 0s, gathering baseline coverage: 0/1 completed"
    echo "fuzz: elapsed: 3s, execs: 100 (33/sec), new interesting: 1 (total: 2)"
    echo "fuzz: elapsed: 6s, execs: 200 (33/sec), new interesting: 1 (total: 2)"
    if [[ -n ${STUB_GO_CRASH:-} && $2 == -fuzz ]]; then
      mkdir -p "${seeds}"
      echo 'go test fuzz v1' > "${seeds}/feedface"
      echo "Failing input written to testdata/fuzz/FuzzLoad/feedface"
      exit 1
    fi
    ;;
  "test -count=1")
    [[ ! -e ${seeds}/feedface ]] || exit 1
    n=$(grep -l '^raise' "${seeds}"/* 2> /dev/null | wc -l | tr -d ' ')
    echo "$((10 + n))" > "${out}"
    ;;
  "tool cover") printf 'total:\\t(statements)\\t%s.0%%\\n' "$(< "${out}")" ;;
  *) exit 1 ;;
esac
"""

FUZZ_MANIFEST = (
    '[package]\nname = "probe-fuzz"\nversion = "0.0.0"\n\n[workspace]\n\n'
    '[[bin]]\nname = "parse_marker"\npath = "fuzz_targets/parse_marker.rs"\n'
)
SECOND_TARGET = '\n[[bin]]\nname = "parse_date"\npath = "fuzz_targets/parse_date.rs"\n'


GO_TEST = """\
package policy

import "testing"

func FuzzLoad(f *testing.F) {
\tf.Fuzz(func(t *testing.T, b []byte) {})
}
"""

SEEDS = "internal/policy/testdata/fuzz/FuzzLoad"


def same(case: unittest.TestCase, want: list[str], got: list[str]) -> None:
    """Assert two lists of strings are equal, typed so a literal is too."""
    case.assertEqual(want, got)


def task_body(name: str) -> str:
    """Read a task's run string out of the belt, as mise renders it.

    The only Tera a fuzz body carries is a `{% raw %}` pair, which
    renders to nothing.

    Returns:
        The body.

    """
    config = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    body = decoded.as_str(decoded.as_object(tasks[name], name)["run"], name)
    return body.replace("{% raw %}", "").replace("{% endraw %}", "")


def lib(script: str, *args: str) -> str:
    """Run `script` with fuzz-search-lib.sh sourced.

    Returns:
        Its standard output.

    """
    return subprocess.run(
        [BASH, "-c", f'. "{SEARCH_LIB}"; {script}', "lib", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


class SearchCase(unittest.TestCase):
    """A planted repository, the stand-in tools and a stage, per row."""

    @override
    def setUp(self) -> None:
        """Plant an empty repository and the stubs beside it."""
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tree = self.tmp / "tree"
        self.tree.mkdir()
        self.stage = self.tmp / "stage"
        self.finds = self.tmp / "finds"
        self.finds.mkdir()
        stubs = self.tmp / "stubs"
        sysroot = self.tmp / "sysroot"
        tools = sysroot / "lib" / "rustlib" / HOST / "bin"
        tools.mkdir(parents=True)
        for where, name, text in (
            (stubs, "cargo", CARGO),
            (stubs, "cargo-fuzz", CARGO_FUZZ),
            (stubs, "rustup", RUSTUP),
            (stubs, "rustc", RUSTC),
            (stubs, "go", GO),
            (tools, "llvm-cov", LLVM_COV),
        ):
            where.mkdir(parents=True, exist_ok=True)
            (where / name).write_text(text, encoding="utf-8")
            (where / name).chmod(0o755)
        env = hermetic.env()
        self.env = env | {
            "ORG_BELT_DIR": str(BELT),
            "ORG_PYTHON": sys.executable,
            "ORG_FUZZ_CANDIDATES": str(self.stage),
            "ORG_FUZZ_SUMMARY": str(self.tmp / "summary.md"),
            "FUZZ_TOOLCHAIN": NIGHTLY,
            "FUZZ_SECONDS": "300",
            "RUSTUP_TOOLCHAIN": "stable",
            "PATH": f"{stubs}{os.pathsep}{env.get('PATH', '')}",
            "STUB_DIR": str(self.tmp),
            "STUB_SYSROOT": str(sysroot),
            "STUB_GOCACHE": str(self.tmp / "gocache"),
        }
        self.git("init", "-q")

    def git(self, *args: str) -> str:
        """Run git in the planted tree.

        Returns:
            Its standard output.

        """
        return subprocess.run(
            [GIT, *args],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def write(self, name: str, text: str) -> None:
        """Write and track a file in the tree."""
        path = self.tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.git("add", "--", name)

    def find(self, name: str, text: str) -> None:
        """Plant a file the search will find."""
        (self.finds / name).write_text(text, encoding="utf-8")
        self.env["STUB_FINDS"] = str(self.finds)

    def rust(self) -> None:
        """Plant a fuzz crate whose one target has one tracked seed."""
        self.write("Cargo.toml", "[workspace]\n")
        self.write("src/lib.rs", "pub fn f() {}\n")
        self.write("fuzz/Cargo.toml", FUZZ_MANIFEST)
        self.write("fuzz/fuzz_targets/parse_marker.rs", "#![no_main]\n")
        self.write("fuzz/corpus/parse_marker/seed", "seed")

    def rust_two(self) -> None:
        """Plant a fuzz crate with two targets, each with a tracked seed."""
        self.rust()
        self.write("fuzz/Cargo.toml", FUZZ_MANIFEST + SECOND_TARGET)
        self.write("fuzz/fuzz_targets/parse_date.rs", "#![no_main]\n")
        self.write("fuzz/corpus/parse_date/seed", "seed")
        self.env["STUB_TARGETS"] = "parse_marker parse_date"

    def searched(self) -> list[str]:
        """Read which Rust targets the search ran, in order.

        Returns:
            The target of each `cargo fuzz run … -max_total_time` call.

        """
        return [
            line.split(" ")[5]
            for line in self.log("cargo")
            if "-max_total_time" in line
        ]

    def go(self) -> None:
        """Plant a Go module with one fuzz target and one tracked seed."""
        self.write("go.mod", "module example.com/x\n")
        self.write("internal/policy/load_test.go", GO_TEST)
        self.write(f"{SEEDS}/seed", "go test fuzz v1\n")

    def run_task(self, name: str) -> subprocess.CompletedProcess[str]:
        """Run a task's own body in the planted tree.

        Returns:
            The finished process.

        """
        as_mise = hermetic.task_run(name, task_body(name))
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def staged(self) -> list[str]:
        """List the stage's files by repository path.

        Returns:
            Each staged path, sorted; none when nothing was staged.

        """
        if not self.stage.exists():
            return []
        return sorted(
            str(p.relative_to(self.stage)) for p in self.stage.rglob("*") if p.is_file()
        )

    def log(self, tool: str) -> list[str]:
        """Read what a stand-in tool was handed.

        Returns:
            Its log lines, none when it was never called.

        """
        path = self.tmp / f"{tool}.log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


class RustSearch(SearchCase):
    """audit:fuzz keeps the finds that raise the figure, and every crash."""

    def test_only_the_find_that_raises_the_figure_is_staged(self) -> None:
        """Two finds, one raising the figure: only it is staged (#1897 item 2).

        Against the body before #1897, the search was handed no finds
        directory and staged nothing, so this row fails there.
        """
        self.rust()
        self.find("aaa-flat", "flat")
        self.find("bbb-raise", "raise me")
        before = self.git("ls-files", "-s", "fuzz/corpus/parse_marker")
        status = self.git("status", "--porcelain", "--untracked-files=all", "fuzz")
        result = self.run_task("audit:fuzz")
        self.assertEqual(0, result.returncode, result.stderr)
        same(self, ["fuzz/corpus/parse_marker/bbb-raise"], self.staged())
        self.assertIn(
            "- `parse_marker` (fuzz-parse-marker): corpus 10% to 11%, 1 input(s)"
            " kept, 0 crash(es);",
            result.stdout,
        )
        # The tracked corpus is byte-identical, and nothing was added to it.
        self.assertEqual(before, self.git("ls-files", "-s", "fuzz/corpus/parse_marker"))
        self.assertEqual(
            status, self.git("status", "--porcelain", "--untracked-files=all", "fuzz")
        )
        corpus = self.tree / "fuzz/corpus/parse_marker"
        names: list[str] = sorted(path.name for path in corpus.iterdir())
        same(self, ["seed"], names)
        # The search wrote into a scratch finds/ handed first, never the tree.
        search = next(line for line in self.log("cargo") if "-max_total_time" in line)
        self.assertNotIn("fuzz/corpus", search)
        self.assertTrue(search.split(" ")[6].endswith("/finds"), search)
        self.assertFalse(any("cmin" in line for line in self.log("cargo")))

    def test_a_run_that_finds_nothing_stages_nothing(self) -> None:
        """No find, no crash: the stage holds no file to upload."""
        self.rust()
        result = self.run_task("audit:fuzz")
        self.assertEqual(0, result.returncode, result.stderr)
        same(self, [], self.staged())
        self.assertIn("corpus 10% to 10%, 0 input(s) kept, 0 crash(es)", result.stdout)

    def test_a_crash_is_staged_into_the_corpus_and_the_run_is_red(self) -> None:
        """A crash- file libFuzzer wrote is staged in fuzz/corpus/<target>/."""
        self.rust()
        self.env["STUB_CRASH"] = "1"
        result = self.run_task("audit:fuzz")
        self.assertEqual(1, result.returncode, result.stdout)
        same(self, ["fuzz/corpus/parse_marker/crash-0badc0de"], self.staged())
        self.assertIn("audit:fuzz: failing target(s): parse_marker", result.stderr)
        self.assertIn("1 crash(es)", result.stdout)

    def test_a_proposal_feeds_the_base_but_its_crash_does_not(self) -> None:
        """origin/fuzz-corpus's corpus file is in base/, its crash- file is not."""
        self.rust()
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
        self.git("checkout", "-qb", "proposal")
        self.write("fuzz/corpus/parse_marker/proposed", "raise proposed")
        self.write("fuzz/corpus/parse_marker/crash-old", "boom")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "p")
        self.git("update-ref", "refs/remotes/origin/fuzz-corpus", "HEAD")
        self.git("checkout", "-q", "-")
        result = self.run_task("audit:fuzz")
        self.assertEqual(0, result.returncode, result.stderr)
        base = next(line for line in self.log("cargo") if line.startswith("base: "))
        self.assertEqual("base: proposed seed ", base)
        # Measured against what is already proposed: 10% plus `proposed`.
        self.assertIn("corpus 11% to 11%", result.stdout)
        same(self, [], self.staged())


class GoSearch(SearchCase):
    """audit:go-fuzz keeps the cache's finds that raise the figure, and a crash."""

    def test_cache_finds_are_staged_under_the_package_testdata(self) -> None:
        """The raising cache file is staged; the cache was cleaned first."""
        self.go()
        self.find("aaa-flat", "go test fuzz v1\nflat\n")
        self.find("bbb-raise", "raise\n")
        status = self.git("status", "--porcelain", "--untracked-files=all")
        result = self.run_task("audit:go-fuzz")
        self.assertEqual(0, result.returncode, result.stderr)
        same(self, [f"{SEEDS}/bbb-raise"], self.staged())
        calls = self.log("go")
        cleaned = calls.index("clean -fuzzcache")
        first = next(i for i, c in enumerate(calls) if c.startswith("test -fuzz"))
        self.assertLess(cleaned, first)
        self.assertIn(
            "- `FuzzLoad` in example.com/x/internal/policy (fuzz-internal-policy-load):"
            " corpus 10.0% to 11.0%, 1 input(s) kept, 0 crash(es)",
            result.stdout,
        )
        # The tree is left as found: only the tracked seed remains.
        self.assertEqual(
            status, self.git("status", "--porcelain", "--untracked-files=all")
        )

    def test_a_failing_input_is_staged_renamed_crash(self) -> None:
        """Go's failing input is staged as crash-<name> and the run is red."""
        self.go()
        self.env["STUB_GO_CRASH"] = "1"
        status = self.git("status", "--porcelain", "--untracked-files=all")
        result = self.run_task("audit:go-fuzz")
        self.assertEqual(1, result.returncode, result.stdout)
        same(self, [f"{SEEDS}/crash-feedface"], self.staged())
        self.assertIn(
            "audit:go-fuzz: failing target(s):"
            " example.com/x/internal/policy.FuzzLoad(plain)",
            result.stderr,
        )
        # Moved out of the tree, where it would fail the measurement.
        self.assertEqual(
            status, self.git("status", "--porcelain", "--untracked-files=all")
        )


class NestedModule(SearchCase):
    """A target in a nested module is searched and measured in it (#1899)."""

    def test_a_nested_target_runs_and_is_measured_in_its_module(self) -> None:
        """tools/ is its own module: every go call for its target runs there."""
        self.write("tools/go.mod", "module example.com/x/tools\n")
        self.write("tools/gen/gen_test.go", GO_TEST.replace("policy", "gen"))
        self.write("tools/gen/testdata/fuzz/FuzzLoad/seed", "go test fuzz v1\n")
        self.env |= {"STUB_GO_PKG": "gen", "STUB_GO_IMPORT": "example.com/x/tools/gen"}
        self.find("bbb-raise", "raise\n")
        result = self.run_task("audit:go-fuzz")
        self.assertEqual(0, result.returncode, result.stderr)
        same(self, ["tools/gen/testdata/fuzz/FuzzLoad/bbb-raise"], self.staged())
        self.assertIn(
            "- `FuzzLoad` in example.com/x/tools/gen (fuzz-tools-gen-load):"
            " corpus 10.0% to 11.0%, 1 input(s) kept",
            result.stdout,
        )
        calls = self.log("go-dir")
        module = f"{self.tree}/tools"
        for verb in ("test -fuzz", "test -race", "test -count=1", "list -deps"):
            ran = [c for c in calls if c.split(" ", 1)[1].startswith(verb)]
            with self.subTest(verb=verb):
                self.assertTrue(ran, calls)
                for call in ran:
                    self.assertEqual(module, call.split(" ", 1)[0], call)
        measured = next(c for c in calls if " test -count=1 " in c)
        self.assertTrue(measured.endswith(" ./gen"), measured)


class Selection(SearchCase):
    """FUZZ_TARGET picks one runner's target; FUZZ_SECONDS is its budget (#1899)."""

    def test_fuzz_target_set_runs_that_target_alone(self) -> None:
        """Of two targets, FUZZ_TARGET's is the only one searched."""
        self.rust_two()
        self.env["FUZZ_TARGET"] = "parse_date"
        result = self.run_task("audit:fuzz")
        self.assertEqual(0, result.returncode, result.stderr)
        same(self, ["parse_date"], self.searched())
        self.assertIn("- `parse_date` (fuzz-parse-date):", result.stdout)
        self.assertNotIn("- `parse_marker`", result.stdout)

    def test_fuzz_target_unset_runs_every_target(self) -> None:
        """With no FUZZ_TARGET, a local run searches every target in turn."""
        self.rust_two()
        result = self.run_task("audit:fuzz")
        self.assertEqual(0, result.returncode, result.stderr)
        same(self, ["parse_marker", "parse_date"], self.searched())

    def test_an_unknown_rust_target_is_red_naming_it(self) -> None:
        """A name cargo fuzz list does not print is red, and nothing is searched."""
        self.rust_two()
        self.env["FUZZ_TARGET"] = "parse_time"
        result = self.run_task("audit:fuzz")
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn(
            "audit:fuzz: FUZZ_TARGET 'parse_time' is no fuzz target of"
            " fuzz/Cargo.toml (cargo fuzz list: parse_marker parse_date)",
            result.stderr,
        )
        same(self, [], self.searched())

    def test_a_rust_target_with_no_fuzz_crate_is_red_naming_it(self) -> None:
        """A named target on a tree with no fuzz/ is red, not skipped."""
        self.write("Cargo.toml", "[workspace]\n")
        self.env["FUZZ_TARGET"] = "parse_marker"
        result = self.run_task("audit:fuzz")
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn(
            "audit:fuzz: FUZZ_TARGET 'parse_marker' is no fuzz target of this"
            " tree (no fuzz/)",
            result.stderr,
        )

    def test_a_go_target_is_picked_by_name_and_package(self) -> None:
        """FUZZ_TARGET and FUZZ_PACKAGE name the package directory's target."""
        self.go()
        self.env |= {"FUZZ_TARGET": "FuzzLoad", "FUZZ_PACKAGE": "internal/policy"}
        result = self.run_task("audit:go-fuzz")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("- `FuzzLoad` in example.com/x/internal/policy", result.stdout)

    def test_an_unknown_go_target_is_red_naming_it(self) -> None:
        """A Go target in a package that does not hold it is red, naming both."""
        self.go()
        self.env |= {"FUZZ_TARGET": "FuzzLoad", "FUZZ_PACKAGE": "cmd/x"}
        result = self.run_task("audit:go-fuzz")
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn(
            "audit:go-fuzz: FUZZ_TARGET 'FuzzLoad' in 'cmd/x' is no fuzz target"
            " of this tree's Go packages",
            result.stderr,
        )
        self.assertFalse(any(c.startswith("test -fuzz") for c in self.log("go")))

    def test_the_go_task_passes_half_the_budget_to_each_pass(self) -> None:
        """FUZZ_SECONDS 300: the plain and the race pass are 150s each."""
        self.go()
        result = self.run_task("audit:go-fuzz")
        self.assertEqual(0, result.returncode, result.stderr)
        passes = [c for c in self.log("go") if " -fuzz " in f" {c} "]
        self.assertEqual(2, len(passes), passes)
        for call in passes:
            with self.subTest(call=call):
                self.assertIn("-fuzztime 150s", call)
        self.assertTrue(passes[1].startswith("test -race"), passes)
        self.assertIn("last new coverage at 3s of 150s", result.stdout)

    def test_the_budget_is_thirty_minutes_by_default(self) -> None:
        """FUZZ_SECONDS unset: 1800s for a Rust target, 900s a pass for Go."""
        del self.env["FUZZ_SECONDS"]
        self.rust()
        self.go()
        rust = self.run_task("audit:fuzz")
        self.assertEqual(0, rust.returncode, rust.stderr)
        search = next(line for line in self.log("cargo") if "-max_total_time" in line)
        self.assertTrue(search.endswith("-- -max_total_time=1800"), search)
        go = self.run_task("audit:go-fuzz")
        self.assertEqual(0, go.returncode, go.stderr)
        passes = [c for c in self.log("go") if " -fuzz " in f" {c} "]
        self.assertTrue(passes)
        for call in passes:
            with self.subTest(call=call):
                self.assertIn("-fuzztime 900s", call)


class RunLength(unittest.TestCase):
    """The summary says when a run was still finding new code (item 5)."""

    def timing(self, kind: str, log: str, budget: int) -> str:
        """Read a planted log through the library's reader and timing line.

        Returns:
            The timing line.

        """
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".log", delete=False
        ) as handle:
            handle.write(log)
        self.addCleanup(Path(handle.name).unlink)
        return lib(
            'org_fuzz_timing "$(org_fuzz_last_new "$1" "$2")" "$3"',
            kind,
            handle.name,
            str(budget),
        ).strip()

    def test_a_rust_run_still_finding_at_the_end_says_so(self) -> None:
        """A last NEW at 290s of 300 is in the last quarter; at 100s it is not."""
        late = "0 #2\tINITED cov: 1\n100 #50\tNEW    cov: 2\n290 #90\tNEW    cov: 3\n"
        early = "0 #2\tINITED cov: 1\n100 #50\tNEW    cov: 2\n290 #90\tpulse  cov: 2\n"
        self.assertEqual(
            "last new coverage at 290s of 300s: still finding new code when it stopped",
            self.timing("rust", late, 300),
        )
        self.assertEqual(
            "last new coverage at 100s of 300s", self.timing("rust", early, 300)
        )

    def test_a_go_run_still_finding_at_the_end_says_so(self) -> None:
        """Go's elapsed carries the time: 4m50s of 300 says so; 1m40s does not."""
        head = "fuzz: elapsed: 0s, gathering baseline coverage: 0/3 completed\n"
        late = (
            head
            + "fuzz: elapsed: 1m40s, execs: 9 (0/sec), new interesting: 2 (total: 5)\n"
            + "fuzz: elapsed: 4m50s, execs: 99 (0/sec), new interesting: 3 (total: 6)\n"
        )
        early = (
            head
            + "fuzz: elapsed: 1m40s, execs: 9 (0/sec), new interesting: 2 (total: 5)\n"
            + "fuzz: elapsed: 4m50s, execs: 99 (0/sec), new interesting: 2 (total: 5)\n"
        )
        self.assertEqual(
            "last new coverage at 290s of 300s: still finding new code when it stopped",
            self.timing("go", late, 300),
        )
        self.assertEqual(
            "last new coverage at 100s of 300s", self.timing("go", early, 300)
        )

    def test_the_rust_search_is_read_through_the_stamping_reader(self) -> None:
        """Each line is prefixed with whole seconds since the run began."""
        self.assertEqual("0 a\n0 b\n", lib("printf 'a\\nb' | org_fuzz_stamp"))


def fuzz_steps() -> list[dict[str, object]]:
    """Read the fuzz job's steps out of the workflow.

    Returns:
        The steps.

    """
    return workflow_yaml.steps(
        workflow_yaml.jobs(workflow_yaml.document(WORKFLOW))["fuzz"]
    )


def step(name: str) -> dict[str, object]:
    """Find one of the fuzz job's steps by its name.

    Returns:
        The step.

    """
    return next(s for s in fuzz_steps() if s.get("name") == name)


class Handover(unittest.TestCase):
    """What a fuzz runner hands the propose job, and when (items 3, 4; #1899)."""

    KEPT = "Hand what the search kept to the propose job"
    UPLOAD = "Upload what the search kept"
    UPLOAD_SUMMARY = "Upload what the search says it kept"

    def test_the_uploads_run_when_the_search_failed(self) -> None:
        """The hand-over and both uploads run whether or not the search passed."""
        self.assertEqual("${{ !cancelled() }}", step(self.KEPT)["if"])
        # The findings hand-over (#1933) is held by test_findings_handoff.
        uploads = [
            s
            for s in fuzz_steps()
            if "upload-artifact" in str(s.get("uses", ""))
            and s.get("name") != "Hand the findings to the report job"
        ]
        same(
            self,
            [self.UPLOAD, self.UPLOAD_SUMMARY],
            [decoded.as_str(s.get("name"), "name") for s in uploads],
        )
        for upload in uploads:
            with self.subTest(upload=upload.get("name")):
                self.assertEqual(
                    "${{ !cancelled() && steps.kept.outputs.staged == 'true' }}",
                    upload["if"],
                )

    def test_the_fuzz_job_hands_nothing_on_through_job_outputs(self) -> None:
        """A matrix's job outputs are last-writer-wins, so the job has none."""
        jobs = workflow_yaml.jobs(workflow_yaml.document(WORKFLOW))
        self.assertNotIn("outputs", jobs["fuzz"])
        self.assertNotIn("needs.fuzz.outputs", WORKFLOW.read_text(encoding="utf-8"))

    def handover(self, files: dict[str, str]) -> tuple[str, str, str]:
        """Run the hand-over step's own script over a planted stage.

        Returns:
            What it wrote to GITHUB_OUTPUT, to the step summary, and to
            its standard output.

        """
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        stage = tmp / "stage"
        stage.mkdir()
        for name, text in files.items():
            (stage / name).parent.mkdir(parents=True, exist_ok=True)
            (stage / name).write_text(text, encoding="utf-8")
        (tmp / "summary.md").write_text("- `t`: kept 1\n", encoding="utf-8")
        output = tmp / "output"
        page = tmp / "step-summary"
        command = decoded.as_str(step(self.KEPT)["run"], "run")
        self.assertEqual(
            ".org-canon/release/propose-fuzz-corpus.sh handover", command.strip()
        )
        run = subprocess.run(
            [BASH, str(CANON / "release" / "propose-fuzz-corpus.sh"), "handover"],
            env=hermetic.env()
            | {
                "CANDIDATES": str(stage),
                "SUMMARY": str(tmp / "summary.md"),
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(page),
            },
            capture_output=True,
            text=True,
            check=True,
        )
        return (
            output.read_text(encoding="utf-8"),
            page.read_text(encoding="utf-8"),
            run.stdout,
        )

    def test_a_run_that_finds_nothing_uploads_nothing(self) -> None:
        """An empty stage writes staged=false, which skips both uploads."""
        output, page, said = self.handover({})
        self.assertEqual("staged=false\n", output)
        self.assertEqual("- `t`: kept 1\n", page)
        self.assertEqual("the search staged nothing; nothing to propose\n", said)

    def test_a_staged_file_says_so_and_keeps_its_summary_on_the_page(self) -> None:
        """staged=true is the one output; the summary goes to the page, not it."""
        output, page, said = self.handover({"fuzz/corpus/t/abc": "x"})
        self.assertEqual("staged=true\n", output)
        self.assertEqual("- `t`: kept 1\n", page)
        self.assertIn("the propose job opens them as a pull request", said)


class OneRunnerPerTarget(unittest.TestCase):
    """The fuzz job is a matrix over the applies job's list, run nightly (#1899)."""

    def test_the_fuzz_matrix_is_the_applies_output(self) -> None:
        """One leg per listed target, none cancelled by another, 60 minutes each."""
        jobs = workflow_yaml.jobs(workflow_yaml.document(WORKFLOW))
        fuzz = jobs["fuzz"]
        strategy = decoded.as_object(fuzz["strategy"], "strategy")
        self.assertIsInstance(strategy["fail-fast"], bool)
        self.assertFalse(strategy["fail-fast"])
        matrix = decoded.as_object(strategy["matrix"], "matrix")
        keys: list[str] = list(matrix)
        same(self, ["include"], keys)
        self.assertEqual(
            "${{ fromJSON(needs.applies.outputs.fuzz-targets) }}", matrix["include"]
        )
        self.assertEqual(60, fuzz["timeout-minutes"])
        outputs = decoded.as_object(jobs["applies"]["outputs"], "outputs")
        self.assertEqual(
            "${{ steps.applies.outputs.fuzz-targets }}", outputs["fuzz-targets"]
        )
        env = decoded.as_object(fuzz["env"], "env")
        self.assertEqual("${{ matrix.target }}", env["FUZZ_TARGET"])
        self.assertEqual("${{ matrix.package }}", env["FUZZ_PACKAGE"])
        # Each leg runs its own language's task alone.
        tasks: list[tuple[str, str]] = [
            (decoded.as_str(s["run"], "run"), decoded.as_str(s.get("if"), "if"))
            for s in workflow_yaml.steps(fuzz)
            if "mise run audit:" in str(s.get("run", ""))
        ]
        want: list[tuple[str, str]] = [
            ("mise run audit:fuzz", "${{ matrix.kind == 'rust' }}"),
            ("mise run audit:go-fuzz", "${{ matrix.kind == 'go' }}"),
        ]
        self.assertEqual(want, tasks)

    def test_the_stubs_run_every_night(self) -> None:
        """The template and the canon's own stub carry a daily cron, no other."""
        for path in STUBS:
            on = decoded.as_object(workflow_yaml.document(path)["on"], "on")
            crons = [
                decoded.as_str(decoded.as_object(e, "schedule")["cron"], "cron")
                for e in decoded.as_array(on["schedule"], "schedule")
            ]
            with self.subTest(stub=path.name):
                self.assertEqual(1, len(crons), crons)
                self.assertRegex(crons[0], r"^[0-9]+ [0-9]+ \* \* \*$")

    def test_each_target_uploads_its_own_artifact_and_propose_takes_all(self) -> None:
        """Upload by kind and target; propose downloads every one into one tree."""
        names: list[str] = [
            decoded.as_str(decoded.as_object(step(n)["with"], "with")["name"], "name")
            for n in (Handover.UPLOAD, Handover.UPLOAD_SUMMARY)
        ]
        same(
            self,
            [
                "fuzz-corpus-candidates-${{ matrix.leg }}",
                "fuzz-corpus-summary-${{ matrix.leg }}",
            ],
            names,
        )
        jobs = workflow_yaml.jobs(workflow_yaml.document(WORKFLOW))
        downloads = [
            decoded.as_object(s["with"], "with")
            for s in workflow_yaml.steps(jobs["propose"])
            if "download-artifact" in str(s.get("uses", ""))
        ]
        got: list[str] = [
            f"{d.get('pattern')} {d.get('path')} {d.get('merge-multiple')}"
            for d in downloads
        ]
        temp = "${{ runner.temp }}"
        same(
            self,
            [
                f"fuzz-corpus-candidates-* {temp}/fuzz-corpus-candidates True",
                f"fuzz-corpus-summary-* {temp}/fuzz-corpus-summaries True",
            ],
            got,
        )
        for download in downloads:
            self.assertNotIn("name", download)
        # propose is one job, never a matrix.
        self.assertNotIn("strategy", jobs["propose"])


class ProposeRunsNoRepositoryCode(unittest.TestCase):
    """The propose job is the token's only holder and runs nothing of the repo."""

    ALLOWED_USES = (
        "$/.github/actions/canon",
        "actions/download-artifact@",
        "actions/create-github-app-token@",
    )

    def test_propose_runs_only_the_canon_and_never_repository_code(self) -> None:
        """No checkout, no mise, cargo or go, no file of the repository."""
        jobs = workflow_yaml.jobs(workflow_yaml.document(WORKFLOW))
        steps = workflow_yaml.steps(jobs["propose"])
        self.assertTrue(steps)
        for s in steps:
            uses = str(s.get("uses", ""))
            if uses:
                self.assertTrue(uses.startswith(self.ALLOWED_USES), uses)
                continue
            run = decoded.as_str(s["run"], "run")
            self.assertIsNone(re.search(r"\b(mise|cargo|go)\b", run), run)
            self.assertEqual(
                ".org-canon/release/propose-fuzz-corpus.sh",
                run.split()[0],
                "a propose step runs a canon script, nothing else",
            )
        # The token is minted only after the paths are checked.
        names = [s.get("name") for s in steps]
        self.assertLess(
            names.index("Refuse any path outside the two corpus shapes"),
            names.index("Mint the proposal token"),
        )

    def test_with_nothing_staged_there_is_no_mint_and_no_proposal(self) -> None:
        """Propose runs whenever fuzz ran; check, mint and propose wait on staged."""
        jobs = workflow_yaml.jobs(workflow_yaml.document(WORKFLOW))
        self.assertEqual(
            "${{ !cancelled() && needs.fuzz.result != 'skipped' }}",
            jobs["propose"]["if"],
        )
        steps = workflow_yaml.steps(jobs["propose"])
        names = [decoded.as_str(s.get("name"), "name") for s in steps]
        reader = names.index("Read whether any runner staged anything")
        self.assertEqual(
            ".org-canon/release/propose-fuzz-corpus.sh staged",
            decoded.as_str(steps[reader]["run"], "run").strip(),
        )
        self.assertEqual("staged", steps[reader]["id"])
        gated: list[str] = [
            n
            for n, s in zip(names, steps, strict=True)
            if s.get("if") == "${{ steps.staged.outputs.staged == 'true' }}"
        ]
        same(
            self,
            [
                "Refuse any path outside the two corpus shapes",
                "Mint the proposal token",
                "Commit the inputs to fuzz-corpus and open its pull request",
            ],
            gated,
        )
        self.assertTrue(all(names.index(g) > reader for g in gated))

    def test_only_propose_mints_a_token(self) -> None:
        """No other job of the workflow mints a token or writes with one."""
        jobs = workflow_yaml.jobs(workflow_yaml.document(WORKFLOW))
        minting = sorted(
            name
            for name, job in jobs.items()
            for s in workflow_yaml.steps(job)
            if "create-github-app-token" in str(s.get("uses", ""))
        )
        same(self, ["propose"], minting)
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(1, text.count("secrets.app-private-key"))
        self.assertNotIn("id-token", text)


if __name__ == "__main__":
    unittest.main()
