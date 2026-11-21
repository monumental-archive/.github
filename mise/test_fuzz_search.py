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

# cargo: `fuzz list` the one target; `fuzz run` the search or the merge;
# `fuzz coverage` writes a profile naming the corpus it replayed.
CARGO = """\
#!/usr/bin/env bash
echo "$*" >> "${STUB_DIR}/cargo.log"
case "$1 ${2:-}" in
  "metadata --no-deps")
    printf '{"target_directory": "%s/fuzz/target"}\\n' "${PWD}"
    ;;
  "fuzz list") echo parse_marker ;;
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

# go: one package with FuzzLoad. `test -fuzz` writes the planted finds
# into the fuzz cache and prints the planted log; with STUB_GO_CRASH the
# plain pass writes a failing input among the seeds and fails. The
# measurement reads the seeds, and refuses a failing input among them,
# as the real replay would.
GO = """\
#!/usr/bin/env bash
echo "$*" >> "${STUB_DIR}/go.log"
seeds="${PWD}/internal/policy/testdata/fuzz/FuzzLoad"
cache="${STUB_GOCACHE}/fuzz/example.com/x/internal/policy/FuzzLoad"
out=""
for arg in "$@"; do
  case "${arg}" in -coverprofile=* | -func=*) out=${arg#*=} ;; *) ;; esac
done
case "$1 ${2:-}" in
  "list ./...") echo example.com/x/internal/policy ;;
  "list -f") echo "${PWD}/internal/policy" ;;
  "list -deps") echo example.com/x/internal/policy ;;
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
        return subprocess.run(
            [BASH, "-euo", "pipefail", "-c", task_body(name)],
            cwd=self.tree,
            env=self.env,
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
    """What the fuzz job hands the propose job, and when (items 3, 4)."""

    KEPT = "Hand what the search kept to the propose job"
    UPLOAD = "Upload what the search kept"

    def test_the_upload_runs_when_the_search_failed(self) -> None:
        """Both the hand-over and the upload run whether or not the search passed."""
        self.assertEqual("${{ !cancelled() }}", step(self.KEPT)["if"])
        self.assertEqual(
            "${{ !cancelled() && steps.kept.outputs.staged == 'true' }}",
            step(self.UPLOAD)["if"],
        )
        uploads = [
            s for s in fuzz_steps() if "upload-artifact" in str(s.get("uses", ""))
        ]
        self.assertEqual(1, len(uploads))
        self.assertEqual(self.UPLOAD, uploads[0].get("name"))

    def handover(self, files: dict[str, str]) -> dict[str, str]:
        """Run the hand-over step's own script over a planted stage.

        Returns:
            The step outputs it wrote.

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
        command = decoded.as_str(step(self.KEPT)["run"], "run")
        self.assertEqual(
            ".org-canon/release/propose-fuzz-corpus.sh handover", command.strip()
        )
        subprocess.run(
            [BASH, str(CANON / "release" / "propose-fuzz-corpus.sh"), "handover"],
            env=hermetic.env()
            | {
                "CANDIDATES": str(stage),
                "SUMMARY": str(tmp / "summary.md"),
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(tmp / "step-summary"),
            },
            capture_output=True,
            check=True,
        )
        text = output.read_text(encoding="utf-8")
        found = re.search(r"summary<<(\S+)\n(.*?)\n\1\n", text, re.DOTALL)
        staged = re.search(r"^staged=(\S+)$", text, re.MULTILINE)
        return {
            "staged": staged.group(1) if staged else "",
            "summary": found.group(2) if found else "",
        }

    def test_a_run_that_finds_nothing_uploads_nothing(self) -> None:
        """An empty stage hands over staged=false, which skips the upload."""
        handed = self.handover({})
        self.assertEqual("false", handed["staged"])
        self.assertEqual("", handed["summary"])

    def test_a_staged_file_hands_over_the_summary(self) -> None:
        """A staged file hands over staged=true and the per-target summary."""
        handed = self.handover({"fuzz/corpus/t/abc": "x"})
        self.assertEqual("true", handed["staged"])
        self.assertEqual("- `t`: kept 1", handed["summary"])


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
