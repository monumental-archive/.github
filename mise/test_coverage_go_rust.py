#!/usr/bin/env python3
"""The coverage tasks' Go and Rust legs (#1651, #1653).

Split out of test_coverage_tasks.py so the gate's test shards can run
it beside the rest (#2049); the planted trees, stubs and the task
runner are that file's, imported from it.

MeasureSelectors, RustLegGuards, GoOneRun and RustOneRun: the legs
measured from the one test run, and the guards on the Rust leg.

stdlib `unittest`, run by `mise run test`.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import gate_cache_key
import hermetic
from test_coverage_tasks import (
    BASH,
    BELT,
    FLOOR,
    GIT,
    GO,
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
