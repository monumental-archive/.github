#!/usr/bin/env python3
"""Table tests for test:pgrx's measured run, mise/pgrx-test.sh (#1656).

A pgrx extension's functions run inside the Postgres backend, which the
rust leg's `cargo llvm-cov --workspace` never collected from (the leg
now reads the test task's own run, #1653), so every
line of one read as zero (#1652: 0 of 18 on release-lab, 0 of 143 on
edtf). Its own suite is the only thing that executes it, so where the
repository holds a `.coverage-floor`, test:pgrx builds each major's run
instrumented, prints `pgrx-pgNN <percent>` and holds that major to the
floor by itself.

The script under test is the belt's own, run by bash in a planted git
tree. gate_surface.py and coverage_floor.py are the real ones, so the
population and the floor are their derivations; `cargo`, `rustup` and
`mise` are stubs on PATH that log their calls, and pgrx-env.sh and
pgrx_postgres.py are replaced, because registering a Postgres is theirs
to prove. The stub `cargo pgrx test` writes a profile where
LLVM_PROFILE_FILE points, unless the row plants none, and the stub
`cargo llvm-cov report` writes the lcov counts the row plants.

Each row asserts the message, not just the exit status: a planted row
scored on exit alone can pass for the wrong reason.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import json
import os
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import hermetic

BELT = Path(__file__).resolve().parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")


def task_body(name: str) -> str:
    """Read one task's `run` body out of the belt's own config.

    Returns:
        The body, exactly as mise runs it.

    Raises:
        LookupError: no such task, or one with no literal body.

    """
    text = (BELT / "config.toml").read_text(encoding="utf-8")
    found = re.search(
        rf'^\[tasks\."{re.escape(name)}"\]\n.*?^run = \'\'\'\n(.*?)^\'\'\'$',
        text,
        re.MULTILINE | re.DOTALL,
    )
    if found is None:
        msg = f"no `run = '''` body for {name} in mise/config.toml"
        raise LookupError(msg)
    return found.group(1)


# A floor the derived-state law accepts: measured 82.0 minus the band.
FLOOR = """\
# band: 2
# measured: 82.0
# derived: v1.0.0
80.0
"""

CARGO_STUB = r"""#!/usr/bin/env bash
printf 'cargo %s|cwd=%s|target=%s|profile=%s\n' "$*" "${PWD}" "${CARGO_TARGET_DIR:-}" \
  "${LLVM_PROFILE_FILE:-}" >> "${PLANT_LOG}"
out=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  [[ ${args[i]} != --output-path ]] || out=${args[i + 1]}
done
lcov() {
  printf 'SF:%s/src/lib.rs\nLF:%s\nLH:%s\nend_of_record\n' "${PWD}" "${PLANT_LF:-18}" \
    "${PLANT_LH:-14}" > "${out}"
}
case "$1 $2" in
  "metadata --no-deps") cat "${PLANT_METADATA}" ;;
  "fetch --locked") ;;
  "llvm-cov show-env")
    dir=${PLANT_PROFILE_DIR-${CARGO_TARGET_DIR}}
    printf "export LLVM_PROFILE_FILE='%s'\n" "${dir:+${dir}/}lab-%p-%m.profraw"
    printf "export CARGO_LLVM_COV_TARGET_DIR='%s'\n" "${CARGO_TARGET_DIR}"
    ;;
  "llvm-cov clean") ;;
  "llvm-cov report") lcov ;;
  "pgrx test")
    [[ -z ${PLANT_FAIL:-} ]] || exit 101
    if [[ -z ${PLANT_NO_PROFILE:-} && -n ${LLVM_PROFILE_FILE:-} ]]; then
      mkdir -p "$(dirname "${LLVM_PROFILE_FILE}")"
      : > "$(dirname "${LLVM_PROFILE_FILE}")/lab-1-1.profraw"
    fi
    echo "test tests::pg_answers ... ok"
    ;;
  *) exit 99 ;;
esac
"""
RUSTUP_STUB = """#!/usr/bin/env bash
echo llvm-tools-aarch64-apple-darwin
"""
MISE_STUB = """#!/usr/bin/env bash
[[ $1 == ls ]] && echo '{}' && exit 0
exit 1
"""
PGRX_ENV_STUB = """
org_pgrx_darwin_flags() { :; }
org_pgrx_init() { cat > /dev/null; }
"""
PGRX_POSTGRES_STUB = "print('plan')\n"

MAJORS = [17, 18]

# What a row expects when a stub was never called, typed so the
# comparison carries no Any.
NOTHING: list[str] = []


def package(name: str, root: Path, *, pgrx: bool) -> dict[str, object]:
    """Plant one workspace member as `cargo metadata` reports it.

    Returns:
        The package record.

    """
    features: dict[str, list[str]] = {"default": []}
    deps: list[dict[str, str]] = []
    if pgrx:
        features = {
            "default": ["pg18"],
            "pg_test": [],
            **{f"pg{m}": [f"pgrx/pg{m}"] for m in MAJORS},
        }
        deps = [{"name": "pgrx"}]
    return {
        "id": f"path+file://{root}/crates/{name}#{name}@1.0.0",
        "name": name,
        "version": "1.0.0",
        "publish": None,
        "dependencies": deps,
        "features": features,
        "targets": [{"kind": ["lib"], "crate_types": ["lib"]}],
        "manifest_path": f"{root}/crates/{name}/Cargo.toml",
    }


class Planted(unittest.TestCase):
    """A planted extension-shaped tree, a stub PATH and a belt."""

    @override
    def setUp(self) -> None:
        """Plant a tracked workspace with one extension and one plain crate."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.tree = self.tmp / "tree"
        (self.tree / "crates" / "lab-pg").mkdir(parents=True)
        (self.tree / "crates" / "core").mkdir(parents=True)
        bins = self.tmp / "bin"
        bins.mkdir()
        for name, body in {
            "cargo": CARGO_STUB,
            "rustup": RUSTUP_STUB,
            "mise": MISE_STUB,
        }.items():
            (bins / name).write_text(body, encoding="utf-8")
            (bins / name).chmod(0o755)
        self.belt = hermetic.belt(
            self.tmp / "belt",
            stubs={
                "pgrx-env.sh": PGRX_ENV_STUB,
                "pgrx_postgres.py": PGRX_POSTGRES_STUB,
            },
        )
        self.log = self.tmp / "log"
        self.log.touch()
        metadata = self.tmp / "metadata.json"
        packages = [
            package("lab-pg", self.tree, pgrx=True),
            package("core", self.tree, pgrx=False),
        ]
        document: dict[str, object] = {
            "packages": packages,
            "workspace_members": [p["id"] for p in packages],
            "workspace_root": str(self.tree),
            "target_directory": str(self.tree / "target"),
        }
        metadata.write_text(json.dumps(document), encoding="utf-8")
        self.env = hermetic.env() | {
            "PATH": f"{bins}{os.pathsep}{os.environ['PATH']}",
            "ORG_BELT_DIR": str(self.belt),
            "ORG_PYTHON": sys.executable,
            "PLANT_LOG": str(self.log),
            "PLANT_METADATA": str(metadata),
        }
        (self.tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", "Cargo.toml")

    def git(self, *args: str) -> None:
        """Run git in the planted tree, scrubbed of the caller's git state."""
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [GIT, *args], cwd=self.tree, env=hermetic.env(), check=True
        )

    def floor(self) -> None:
        """Commit a derived floor of 80.0 to the planted tree."""
        (self.tree / ".coverage-floor").write_text(FLOOR, encoding="utf-8")

    def run_bash(
        self, argv: list[str], **env: str
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run bash in the planted tree.

        Returns:
            The finished process and the stubs' call log.

        """
        done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, *argv],
            cwd=self.tree,
            env=self.env | env,
            capture_output=True,
            text=True,
            check=False,
        )
        return done, self.log.read_text(encoding="utf-8").splitlines()

    def run_pgrx(
        self, *args: str, **env: str
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run test:pgrx's script for pg18 unless a row names otherwise.

        Returns:
            The finished process and the stubs' call log.

        """
        return self.run_bash(
            [str(self.belt / "pgrx-test.sh"), *args], **({"ORG_PGRX_MAJOR": "18"} | env)
        )

    @staticmethod
    def suites(log: list[str]) -> list[str]:
        """Keep only the suite runs from a call log.

        Returns:
            The `cargo pgrx test` lines, in order.

        """
        return [ln for ln in log if ln.startswith("cargo pgrx test")]


class MeasuredRun(Planted):
    """With a floor, one major's run is the measurement (#1656)."""

    def test_one_major_runs_instrumented_and_prints_its_figure(self) -> None:
        """The Done-when row: own target dir, absolute profile, the leg line.

        On main the run took no instrumentation and printed no leg line.
        """
        self.floor()
        reports = self.tmp / "reports"
        done, log = self.run_pgrx(PLANT_LH="17", ORG_COVERAGE_REPORTS=str(reports))
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("pgrx-pg18 94.44444444444444", done.stdout.splitlines())
        (suite,) = self.suites(log)
        target = self.tree / "target" / "llvm-cov-pgrx-pg18"
        self.assertIn(f"|cwd={self.tree / 'crates' / 'lab-pg'}|", suite)
        self.assertIn(f"|target={target}|", suite)
        self.assertIn(f"|profile={target}/lab-%p-%m.profraw", suite)
        (report,) = [ln for ln in log if ln.startswith("cargo llvm-cov report")]
        self.assertTrue(
            report.startswith(
                "cargo llvm-cov report -p lab-pg --lcov --output-path "
                f"{reports}/lcov-pgrx-pg18.info|"
            ),
            report,
        )
        self.assertTrue((reports / "lcov-pgrx-pg18.info").is_file())

    def test_a_held_major_keeps_its_figure_beside_its_report(self) -> None:
        """coverage-figures.txt: the line held, for the release job (#1692)."""
        self.floor()
        reports = self.tmp / "reports"
        done, _ = self.run_pgrx(PLANT_LH="17", ORG_COVERAGE_REPORTS=str(reports))
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "pgrx-pg18 94.44444444444444\n",
            (reports / "coverage-figures.txt").read_text(encoding="utf-8"),
        )

    def test_a_major_below_the_floor_keeps_no_figure(self) -> None:
        """Red before it is kept: nothing for the release to read."""
        self.floor()
        reports = self.tmp / "reports"
        done, _ = self.run_pgrx(ORG_COVERAGE_REPORTS=str(reports))
        self.assertEqual(1, done.returncode)
        self.assertFalse((reports / "coverage-figures.txt").exists())

    def test_a_measured_run_leaves_the_tree_clean(self) -> None:
        """No report in the tree, named directory or not (#1628).

        A report in the working tree is one `git add -A` from a commit.
        Without ORG_COVERAGE_REPORTS it goes to a scratch directory the
        script removes on exit, after the figure is read.
        """
        self.floor()
        (self.tree / ".gitignore").write_text("target/\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "planted")
        for named in ({"ORG_COVERAGE_REPORTS": str(self.tmp / "reports")}, {}):
            with self.subTest(named=bool(named)):
                done, _ = self.run_pgrx(PLANT_LH="17", **named)
                self.assertEqual(0, done.returncode, done.stderr)
                self.assertIn("pgrx-pg18 94.44444444444444", done.stdout.splitlines())
                status = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                    [GIT, "status", "--porcelain"],
                    cwd=self.tree,
                    env=hermetic.env(),
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertEqual("", status.stdout)

    def test_a_major_below_the_floor_is_red_by_name(self) -> None:
        """14 of 18 is 77.8%, under the planted floor of 80.0."""
        self.floor()
        done, _ = self.run_pgrx()
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "test:pgrx: pgrx-pg18: 77.77777777777777% is below the derived floor 80.0%",
            done.stderr,
        )

    def test_a_major_at_the_floor_holds_it(self) -> None:
        """18 of 18 holds the floor, and the hold names the major."""
        self.floor()
        done, _ = self.run_pgrx(PLANT_LH="18")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("pgrx-pg18 100", done.stdout.splitlines())
        self.assertIn("test:pgrx: pgrx-pg18 holds the floor 80.0 (100%)", done.stderr)

    def test_every_declared_major_side_by_side_holds_its_own(self) -> None:
        """The local form: each major its own target dir and its own line."""
        self.floor()
        done, log = self.run_pgrx(ORG_PGRX_MAJOR="", PLANT_LH="18")
        self.assertEqual(0, done.returncode, done.stderr)
        lines = done.stdout.splitlines()
        self.assertIn("pgrx-pg17 100", lines)
        self.assertIn("pgrx-pg18 100", lines)
        targets = sorted(
            ln.split("|target=")[1].split("|")[0] for ln in self.suites(log)
        )
        want = [
            str(self.tree / "target" / "llvm-cov-pgrx-pg17"),
            str(self.tree / "target" / "llvm-cov-pgrx-pg18"),
        ]
        self.assertEqual(want, targets)


class PlainOrRefused(Planted):
    """No floor, a failed suite, and a pass that measured nothing."""

    def test_no_floor_runs_the_plain_suite_and_prints_no_figure(self) -> None:
        """No `.coverage-floor`: today's run, untouched."""
        done, log = self.run_pgrx()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertNotIn("pgrx-pg18", done.stdout)
        (suite,) = self.suites(log)
        self.assertIn("|target=|profile=", suite)
        coverage = [ln for ln in log if ln.startswith("cargo llvm-cov")]
        self.assertEqual(NOTHING, coverage)
        self.assertIn("test:pgrx: 1 suite run: crates/lab-pg(pg18)", done.stdout)

    def test_a_failed_suite_prints_no_figure(self) -> None:
        """The suite's exit status gates the figure."""
        self.floor()
        done, log = self.run_pgrx(PLANT_FAIL="1")
        self.assertEqual(1, done.returncode)
        self.assertNotIn("pgrx-pg18", done.stdout)
        self.assertIn(
            "test:pgrx: crates/lab-pg at pg18 failed — a suite that does not pass"
            " measures nothing",
            done.stderr,
        )
        reports = [ln for ln in log if ln.startswith("cargo llvm-cov report")]
        self.assertEqual(NOTHING, reports)

    def test_a_pass_that_wrote_no_profile_is_refused_by_name(self) -> None:
        """An extension that measured nothing must not read as held."""
        self.floor()
        done, _ = self.run_pgrx(PLANT_NO_PROFILE="1")
        self.assertEqual(1, done.returncode)
        self.assertNotIn("pgrx-pg18", done.stdout)
        self.assertIn(
            "test:pgrx: pgrx-pg18: the suite passed and wrote no coverage profile",
            done.stderr,
        )

    def test_a_relative_profile_path_is_refused(self) -> None:
        """The backends' working directory is deleted when pgrx stops them."""
        self.floor()
        done, log = self.run_pgrx(PLANT_PROFILE_DIR="")
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "test:pgrx: pg18: the profile path 'lab-%p-%m.profraw' is not absolute",
            done.stderr,
        )
        self.assertEqual(NOTHING, self.suites(log))


class CoverageTasks(Planted):
    """coverage:check leaves the extension to test:pgrx; `pgrx` measures it."""

    def measured_run(self) -> dict[str, str]:
        """Leave what the test task's measured `cargo test` leaves (#1653).

        Returns:
            The run's directory, as the belt's `[env]` names it.

        """
        run = self.tmp / "run"
        (run / "rust").mkdir(parents=True)
        profiles = self.tree / "target" / "llvm-cov-target"
        profiles.mkdir(parents=True)
        (profiles / "lab-1-1.profraw").touch()
        return {"ORG_COVERAGE_RUN": str(run)}

    def test_check_runs_no_suite_names_the_legs_and_excludes_the_crate(self) -> None:
        """Zero suite runs, both majors named, lab-pg's files out of the report."""
        self.floor()
        body = task_body("coverage:check")
        done, log = self.run_bash(
            ["-c", body], PLANT_LF="10", PLANT_LH="9", **self.measured_run()
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(NOTHING, self.suites(log))
        for major in MAJORS:
            self.assertIn(
                f"coverage:check: pgrx-pg{major}: measured and held by test:pgrx",
                done.stderr,
            )
        (report,) = [ln for ln in log if ln.startswith("cargo llvm-cov report")]
        ext = "".join(
            f"\\{c}" if c in "[]\\.*^$+?(){}|" else c
            for c in str(self.tree / "crates" / "lab-pg")
        )
        self.assertTrue(
            report.startswith(
                f"cargo llvm-cov report --ignore-filename-regex ^{ext}/ --lcov"
            ),
            report,
        )
        built = [ln for ln in log if ln.startswith("cargo llvm-cov --workspace")]
        self.assertEqual(NOTHING, built)
        self.assertIn(
            "coverage:check: rust leg holds the floor 80.0 (90%)", done.stdout
        )

    def test_coverage_exclude_naming_the_extension_is_refused(self) -> None:
        """The removed variable is refused whatever it names (#1653)."""
        self.floor()
        body = task_body("coverage:check")
        done, _ = self.run_bash(["-c", body], COVERAGE_EXCLUDE="lab-pg")
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "coverage:check: COVERAGE_EXCLUDE is set (lab-pg) — it is removed"
            " (#1653):\n",
            done.stderr,
        )

    def test_the_pgrx_selector_prints_one_line_per_declared_major(self) -> None:
        """Every major, measured, stdout holding the leg lines and no more."""
        done, log = self.run_bash(
            [str(self.belt / "coverage-measure.sh"), "coverage:adopt", "pgrx"],
            ORG_PGRX_MAJOR="18",
        )
        self.assertEqual(0, done.returncode, done.stderr)
        want = ["pgrx-pg17 77.77777777777777", "pgrx-pg18 77.77777777777777"]
        got = sorted(done.stdout.splitlines())
        self.assertEqual(want, got)
        self.assertEqual(2, len(self.suites(log)))

    def test_the_majors_selector_lists_what_the_pgrx_selector_measures(
        self,
    ) -> None:
        """pgrx-majors: the same majors, one per line, and no suite run (#1692)."""
        done, log = self.run_bash([
            str(self.belt / "coverage-measure.sh"),
            "release:coverage-floor",
            "pgrx-majors",
        ])
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("17\n18\n", done.stdout)
        self.assertFalse(self.suites(log), log)

    def test_the_pgrx_selector_holds_no_floor(self) -> None:
        """Adoption and the release derive the floor; measuring is not holding."""
        self.floor()
        done, _ = self.run_bash([
            str(self.belt / "coverage-measure.sh"),
            "coverage:adopt",
            "pgrx",
        ])
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertNotIn("below the derived floor", done.stderr)


if __name__ == "__main__":
    unittest.main()
