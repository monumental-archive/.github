#!/usr/bin/env python3
"""Table tests for lint:go-fuzz-seeds' package selection (#1413).

The body under test is the task's own `run` string from
mise/config.toml, run by bash in a planted git checkout. `go` is a
stub (the canon pins no Go): it answers `test -list` with the listing
planted in the module directory, logs every call with the directory it
ran in, and answers the run with the planted outcome. Each row asserts
the calls, so a row cannot pass by stopping somewhere else.

The listings are measured, never written by hand: go 1.27.4's
`go test -list '^Fuzz' ./...` in a scratch module of five packages, a
fuzz target in an internal test package (a), one in an external
`package b_test` (b), tests with no target (c), no test files (d), and
a target behind a build tag the run does not set (e). The toolchain
prints a package's matching names, then that package's `ok` line; a
package with no test files prints `?` and nothing else; the tagged
target is not listed, exactly as `go test` without the tag would not
build it. Timings are kept as measured: they are not parsed.

Fails before #1413 (the run was `./...`, whatever the listing said)
and passes after: `test_runs_only_the_packages_that_declare_a_target`.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic

BELT = Path(__file__).resolve().parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
# A scratch repository must not inherit the caller's git: under a hook,
# GIT_DIR and GIT_INDEX_FILE point every `git` at the repository being
# pushed, and the planted go.mod would be staged into the canon's index.
CLEAN_ENV = {
    **hermetic.env(),
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}

# What the body reaches for besides go, and `cat` for the stub, linked
# into a PATH of their own so the go-missing row can leave go off it on
# any machine. `mktemp` and `rm` are org_tracked_read's: it files the
# tracked list before reading it (#1986).
TOOLS = ("git", "dirname", "grep", "awk", "cat", "mktemp", "rm")

# Stands in for go. `test -list` prints the planted listing and exits
# with the planted status; the run prints a seed failure naming each
# package when one is planted, as `go test` does. Anything else is red.
GO_STUB = """#!{bash}
printf '%s|%s\\n' "${PWD}" "$*" >> "${PLANT_LOG}"
case "$1 ${2:-}" in
  "test -list")
    cat .listing
    exit "$(cat .list-status 2> /dev/null || echo 0)"
    ;;
  "test -race")
    if [[ -f .seed-fails ]]; then
      for p in "${@:5}"; do
        printf -- '--- FAIL: FuzzA (0.00s)\\nFAIL\\t%s\\t0.312s\\n' "${p}"
      done
      echo FAIL
      exit 1
    fi
    for p in "${@:5}"; do printf 'ok  \\t%s\\t0.312s\\n' "${p}"; done
    ;;
  *) exit 99 ;;
esac
"""

# go 1.27.4, byte for byte: the five-package module described above.
FIVE = (
    "FuzzA\n"
    "ok  \texample.com/probe/a\t0.601s\n"
    "FuzzB\n"
    "ok  \texample.com/probe/b\t0.843s\n"
    "ok  \texample.com/probe/c\t1.443s\n"
    "?   \texample.com/probe/d\t[no test files]\n"
    "?   \texample.com/probe/e\t[no test files]\n"
)
# The same toolchain on a module with tests and no target.
NONE = "ok  \texample.com/probe/c\t1.443s\n?   \texample.com/probe/d\t[no test files]\n"
# go 1.27.4 with a test file that does not compile in c, stdout only:
# the listing exits 1 and the package that failed is absent from it.
BROKEN = (
    "FAIL\texample.com/probe/c [setup failed]\n"
    "FuzzA\n"
    "ok  \texample.com/probe/a\t0.222s\n"
    "?   \texample.com/probe/d\t[no test files]\n"
    "FAIL\n"
)

LIST = "test -list ^Fuzz ./..."
RUN = "test -race -run ^Fuzz"

SKIPPED = "lint:go-fuzz-seeds: no Go fuzz targets, skipped\n"
NO_MOD = "lint:go-fuzz-seeds: no go.mod tracked, skipped\n"
NO_GO = "lint:go-fuzz-seeds: go.mod present but go missing — pin go in mise.toml\n"


def task_body(name: str) -> str:
    """Read a task's run string out of the belt as mise would render it.

    Returns:
        The body with any `{% raw %}` / `{% endraw %}` lines removed.

    """
    config = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the [tasks] table")
    run = decoded.as_str(decoded.as_object(tasks[name], name)["run"], "the run")
    return "".join(
        line
        for line in run.splitlines(keepends=True)
        if line.strip() not in {"{% raw %}", "{% endraw %}"}
    )


class GoFuzzSeeds(unittest.TestCase):
    """lint:go-fuzz-seeds runs exactly the packages the listing names."""

    @override
    def setUp(self) -> None:
        """Plant an empty git checkout, the go stub, and the tool links."""
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.git("init", "-q")
        self.tools = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tools, ignore_errors=True)
        for tool in TOOLS:
            (self.tools / tool).symlink_to(hermetic.tool(tool))
        self.stubs = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.stubs, ignore_errors=True)
        stub = self.stubs / "go"
        stub.write_text(GO_STUB.replace("{bash}", BASH), encoding="utf-8")
        stub.chmod(0o755)
        self.log = self.stubs / "calls.log"

    def git(self, *args: str) -> None:
        """Run git in the planted checkout."""
        subprocess.run([GIT, *args], cwd=self.root, env=CLEAN_ENV, check=True)

    def module(self, rel: str, listing: str, *, status: int = 0) -> Path:
        """Track a go.mod at `rel` and plant what go lists there.

        Returns:
            The module directory.

        """
        d = self.root / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "go.mod").write_text("module example.com/probe\n", encoding="utf-8")
        (d / ".listing").write_text(listing, encoding="utf-8")
        if status:
            (d / ".list-status").write_text(f"{status}\n", encoding="utf-8")
        self.git("add", str(d / "go.mod"))
        return d

    def run_lint(self, *, with_go: bool = True) -> tuple[int, str, str, list[str]]:
        """Run the task body in the planted checkout.

        Returns:
            Exit status, stdout, stderr, and the go calls as `dir|args`
            with the dir relative to the checkout.

        """
        path = [str(self.tools)]
        if with_go:
            path.insert(0, str(self.stubs))
        env = CLEAN_ENV | {
            "PATH": os.pathsep.join(path),
            "PLANT_LOG": str(self.log),
        }
        as_mise = hermetic.task_run(
            "lint:go-fuzz-seeds", task_body("lint:go-fuzz-seeds")
        )
        done = subprocess.run(
            as_mise.argv,
            cwd=self.root,
            env=env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )
        calls = [
            line.replace(f"{self.root}/", "").replace(str(self.root), ".")
            for line in (
                self.log.read_text(encoding="utf-8").splitlines()
                if self.log.exists()
                else []
            )
        ]
        return done.returncode, done.stdout, done.stderr, calls

    def test_runs_only_the_packages_that_declare_a_target(self) -> None:
        """Two of five packages declare targets: exactly those two run."""
        self.module(".", FIVE)
        code, out, err, calls = self.run_lint()
        self.assertEqual(code, 0, err)
        want: list[str] = [
            f".|{LIST}",
            f".|{RUN} example.com/probe/a example.com/probe/b",
        ]
        self.assertEqual(calls, want)
        self.assertTrue(
            out.endswith("lint:go-fuzz-seeds: 2 target(s) built, seed corpora clean\n"),
            out,
        )

    def test_each_module_runs_its_own_packages_in_its_own_directory(self) -> None:
        """A module with no target is listed and skipped; the other runs."""
        self.module(".", NONE)
        self.module("tools/gen", FIVE)
        code, out, err, calls = self.run_lint()
        self.assertEqual(code, 0, err)
        want: list[str] = [
            f".|{LIST}",
            f"tools/gen|{LIST}",
            f"tools/gen|{RUN} example.com/probe/a example.com/probe/b",
        ]
        self.assertEqual(calls, want)
        self.assertTrue(out.endswith("2 target(s) built, seed corpora clean\n"), out)

    def test_no_fuzz_target_skips(self) -> None:
        """Tests but no target: listed, never run, skipped as before."""
        self.module(".", NONE)
        code, out, err, calls = self.run_lint()
        self.assertEqual((code, out, err), (0, SKIPPED, ""))
        want: list[str] = [f".|{LIST}"]
        self.assertEqual(calls, want)

    def test_a_failing_seed_is_red_and_names_the_package(self) -> None:
        """A seed that fails reds the task, go's FAIL line and ours naming it."""
        d = self.module(".", FIVE)
        (d / ".seed-fails").write_text("", encoding="utf-8")
        code, out, err, calls = self.run_lint()
        self.assertEqual(code, 1)
        self.assertIn("FAIL\texample.com/probe/a\t", out)
        self.assertEqual(
            err,
            "lint:go-fuzz-seeds: a seed corpus failed in . — packages run: "
            "example.com/probe/a example.com/probe/b\n",
        )
        self.assertEqual(calls[-1], f".|{RUN} example.com/probe/a example.com/probe/b")

    def test_a_listing_that_fails_is_red_not_a_shorter_list(self) -> None:
        """A package whose tests do not compile cannot drop out of the run."""
        self.module(".", BROKEN, status=1)
        code, _out, err, calls = self.run_lint()
        self.assertEqual(code, 1)
        self.assertEqual(
            err,
            BROKEN + "lint:go-fuzz-seeds: go test -list failed in . — "
            "the fuzz targets cannot be enumerated\n",
        )
        want: list[str] = [f".|{LIST}"]
        self.assertEqual(calls, want)

    def test_go_missing_with_go_mod_tracked_is_refused(self) -> None:
        """The existing refusal, unchanged."""
        self.module(".", FIVE)
        code, out, err, calls = self.run_lint(with_go=False)
        self.assertEqual((code, out, err, calls), (1, "", NO_GO, []))

    def test_no_go_mod_skips(self) -> None:
        """No module tracked: skipped before go is consulted."""
        code, out, err, calls = self.run_lint(with_go=False)
        self.assertEqual((code, out, err, calls), (0, NO_MOD, "", []))


if __name__ == "__main__":
    unittest.main()
