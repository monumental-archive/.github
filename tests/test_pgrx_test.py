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
"""

import json
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

import pytest

from belt import coverage_floor, gate_cache_key
from belt.typed_pytest import fixture
from tests import support

BELT = support.MISE
BASH = support.tool("bash")
GIT = support.tool("git")


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


# A floor the derived-state law accepts: the gate's rust leg and both
# majors, each measured 82.0 minus the band, each its own (#2143).
FLOOR = coverage_floor.render(
    {
        leg: coverage_floor.fresh(Decimal("82.0"))
        for leg in ("rust", "pgrx-pg17", "pgrx-pg18")
    },
    "v1.0.0",
)

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
    printf '%s|%s|%s\n' "$3" "${BINDGEN_EXTRA_CLANG_ARGS:-}" "${CFLAGS:-}" \
      >> "${PLANT_LOG}.icu"
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
# The real org_pgrx_major, read out of pgrx-env.sh rather than
# written again here: its subshell is the property the rows rely on.
WITH_MAJOR = re.search(
    r"^org_pgrx_major\(\) \{.*?^\}\n",
    (BELT / "pgrx-env.sh").read_text(encoding="utf-8"),
    re.MULTILINE | re.DOTALL,
)
if WITH_MAJOR is None:
    msg = "org_pgrx_major is gone from pgrx-env.sh"
    raise LookupError(msg)
PGRX_ENV_STUB = """
# The SDK and the check (#1774): logs the majors it was asked for. Each
# run adds its own major's mark to both flags, which the stub cargo
# logs, so a run that saw another major's would show it.
org_pgrx_darwin_prepare() { printf 'icu-check [%s]\\n' "$1" >> "${PLANT_LOG}.icu"; }
org_pgrx_major_env() {
  export BINDGEN_EXTRA_CLANG_ARGS="-Iicu-pg$1 ${BINDGEN_EXTRA_CLANG_ARGS:-}"
  export CFLAGS="-Iicu-pg$1 ${CFLAGS:-}"
}
""" + WITH_MAJOR.group(0)
# The command's stand-in, a belt module the launcher finds: the shell
# body under test calls it, and its own rows are test_pgrx_postgres.py's.
PGRX_POSTGRES_STUB = "def main(argv):\n    print('plan')\n    return 0\n"

MAJORS = [17, 18]

# The directory digest of the stand-in `rustc` the rows run beside
# (#1877), under the `stable` toolchain they set.
STABLE = gate_cache_key.directory_digest(support.rustc_verbose("stable"))

# What a row expects when a stub was never called, typed so the
# comparison carries no Any.
NOTHING: list[str] = []


def package(
    name: str, root: Path, *, pgrx: bool, majors: tuple[int, ...] = tuple(MAJORS)
) -> dict[str, object]:
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
            **{f"pg{m}": [f"pgrx/pg{m}"] for m in majors},
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


class Planted:
    """A planted extension-shaped tree, a stub PATH and a belt."""

    # The majors the planted extension declares.
    declares: ClassVar[tuple[int, ...]] = tuple(MAJORS)

    def __init__(
        self,
        tmp_path: Path,
        *,
        belt: support.Belt,
        env: support.Env,
        rustc: support.Rustc,
    ) -> None:
        """Plant a tracked workspace with one extension and one plain crate."""
        self.plant_belt = belt
        self.make_env = env
        self.plant_rustc = rustc
        self.tmp = Path(str(tmp_path)).resolve()
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
        self.plant_rustc(bins)
        self.belt = self.plant_belt(
            self.tmp / "belt",
            stubs={
                "pgrx-env.sh": PGRX_ENV_STUB,
                "belt/pgrx_postgres.py": PGRX_POSTGRES_STUB,
            },
        )
        self.log = self.tmp / "log"
        self.log.touch()
        # The run the belt's `[env]` names, present, so live (#2062).
        self.run_dir = self.tmp / "coverage-run"
        self.run_dir.mkdir()
        metadata = self.tmp / "metadata.json"
        packages = [
            package("lab-pg", self.tree, pgrx=True, majors=self.declares),
            package("core", self.tree, pgrx=False),
        ]
        document: dict[str, object] = {
            "packages": packages,
            "workspace_members": [p["id"] for p in packages],
            "workspace_root": str(self.tree),
            "target_directory": str(self.tree / "target"),
        }
        metadata.write_text(json.dumps(document), encoding="utf-8")
        self.env = self.make_env() | {
            "PATH": f"{bins}{os.pathsep}{os.environ['PATH']}",
            "ORG_BELT_DIR": str(self.belt),
            "ORG_PYTHON": sys.executable,
            "RUSTUP_TOOLCHAIN": "stable",
            "PLANT_LOG": str(self.log),
            "PLANT_METADATA": str(metadata),
            "ORG_COVERAGE_RUN": str(self.run_dir),
        }
        (self.tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", "Cargo.toml")

    def as_mise(self, task: str) -> dict[str, str]:
        """Give a body what mise gives it: its name and the planted belt's writer.

        Without BASH_ENV a body has no `org_tracked_has`, and a negated
        call that is not found reads as "absent" (#2013).

        Returns:
            MISE_TASK_NAME and BASH_ENV.

        """
        return {"MISE_TASK_NAME": task, "BASH_ENV": str(self.belt / "findings.sh")}

    def git(self, *args: str) -> None:
        """Run git in the planted tree, scrubbed of the caller's git state."""
        subprocess.run([GIT, *args], cwd=self.tree, env=self.make_env(), check=True)

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
        done = subprocess.run(
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
            [str(self.belt / "pgrx-test.sh"), *args],
            **({"ORG_PGRX_MAJOR": "18"} | env),
        )


@fixture
def planted(
    tmp_path: Path, belt: support.Belt, env: support.Env, rustc: support.Rustc
) -> Planted:
    """Give each row its own Planted."""
    return Planted(tmp_path, belt=belt, env=env, rustc=rustc)


def suites(log: list[str]) -> list[str]:
    """Keep only the suite runs from a call log.

    Returns:
        The `cargo pgrx test` lines, in order.

    """
    return [ln for ln in log if ln.startswith("cargo pgrx test")]


def test_one_major_runs_instrumented_and_prints_its_figure(planted: Planted) -> None:
    """The Done-when row: own target dir, absolute profile, the leg line.

    On main the run took no instrumentation and printed no leg line.
    """
    planted.floor()
    reports = planted.tmp / "reports"
    done, log = planted.run_pgrx(PLANT_LH="17", ORG_COVERAGE_REPORTS=str(reports))
    assert done.returncode == 0, done.stderr
    assert "pgrx-pg18 94.44444444444444" in done.stdout.splitlines()
    (suite,) = suites(log)
    # The major's directory names its compiler too (#1877).
    target = planted.tree / "target" / f"llvm-cov-pgrx-pg18-{STABLE}"
    assert f"|cwd={planted.tree / 'crates' / 'lab-pg'}|" in suite
    assert f"|target={target}|" in suite
    assert f"|profile={target}/lab-%p-%m.profraw" in suite
    (report,) = [ln for ln in log if ln.startswith("cargo llvm-cov report")]
    assert report.startswith(
        "cargo llvm-cov report -p lab-pg --lcov --output-path "
        f"{reports}/lcov-pgrx-pg18.info|"
    ), report
    assert (reports / "lcov-pgrx-pg18.info").is_file()


def test_a_held_major_keeps_its_figure_beside_its_report(planted: Planted) -> None:
    """coverage-figures.txt: the line held, for the release job (#1692)."""
    planted.floor()
    reports = planted.tmp / "reports"
    done, _ = planted.run_pgrx(PLANT_LH="17", ORG_COVERAGE_REPORTS=str(reports))
    assert done.returncode == 0, done.stderr
    assert (reports / "coverage-figures.txt").read_text(
        encoding="utf-8"
    ) == "pgrx-pg18 94.44444444444444\n"


def test_a_major_below_the_floor_keeps_no_figure(planted: Planted) -> None:
    """Red before it is kept: nothing for the release to read."""
    planted.floor()
    reports = planted.tmp / "reports"
    done, _ = planted.run_pgrx(ORG_COVERAGE_REPORTS=str(reports))
    assert done.returncode == 1
    assert not (reports / "coverage-figures.txt").exists()


def test_a_measured_run_leaves_the_tree_clean(
    planted: Planted, env: support.Env
) -> None:
    """No report in the tree, named directory or not (#1628).

    A report in the working tree is one `git add -A` from a commit.
    Without ORG_COVERAGE_REPORTS it goes to a scratch directory the
    script removes on exit, after the figure is read.
    """
    planted.floor()
    (planted.tree / ".gitignore").write_text("target/\n", encoding="utf-8")
    planted.git("add", "-A")
    planted.git("commit", "-q", "-m", "planted")
    for named in ({"ORG_COVERAGE_REPORTS": str(planted.tmp / "reports")}, {}):
        done, _ = planted.run_pgrx(PLANT_LH="17", **named)
        assert done.returncode == 0, done.stderr
        assert "pgrx-pg18 94.44444444444444" in done.stdout.splitlines()
        status = subprocess.run(
            [GIT, "status", "--porcelain"],
            cwd=planted.tree,
            env=env(),
            capture_output=True,
            text=True,
            check=True,
        )
        assert status.stdout == ""


def test_a_major_below_the_floor_is_red_by_name(planted: Planted) -> None:
    """14 of 18 is 77.8%, under the planted floor of 80.0."""
    planted.floor()
    done, _ = planted.run_pgrx()
    assert done.returncode == 1
    assert (
        "test:pgrx: pgrx-pg18: 77.77777777777777% is below its derived floor 80.0%"
        in done.stderr
    )


def test_a_major_at_the_floor_holds_it(planted: Planted) -> None:
    """18 of 18 holds its floor, and the hold names the major."""
    planted.floor()
    done, _ = planted.run_pgrx(PLANT_LH="18")
    assert done.returncode == 0, done.stderr
    assert "pgrx-pg18 100" in done.stdout.splitlines()
    assert "test:pgrx: pgrx-pg18 leg holds its floor 80.0 (100%)" in done.stderr


def test_the_hold_is_coverage_floor_py_scoped_to_its_major(planted: Planted) -> None:
    """End to end (#1875): one holder, judging only the job's own legs.

    The floor records the gate's rust leg and both majors. The pg18
    job reports pg18 alone and holds: pg17 is the pg17 job's and rust
    the gate's. The hold is coverage_floor.py's (its line, its
    drift refusal), not a second comparison in the script.
    """
    record = FLOOR
    (planted.tree / ".coverage-floor").write_text(record, encoding="utf-8")
    done, _ = planted.run_pgrx(PLANT_LH="18")
    assert done.returncode == 0, done.stderr
    want: list[str] = ["pgrx-pg18 100"]
    got = [ln for ln in done.stdout.splitlines() if ln.startswith("pgrx-pg")]
    assert want == got
    assert "test:pgrx: pgrx-pg18 leg holds its floor 80.0 (100%)" in done.stderr
    assert "measured none" not in done.stderr
    # The floor read is coverage_floor.py's, so a floor off its own
    # record is refused here as everywhere (the measured 82.0 derives
    # 80.0; 81.0 is drift).
    (planted.tree / ".coverage-floor").write_text(
        record.replace("80.0\n", "81.0\n"), encoding="utf-8"
    )
    done, _ = planted.run_pgrx(PLANT_LH="18")
    assert done.returncode == 1
    assert "test:pgrx: .coverage-floor" in done.stderr


def test_every_declared_major_side_by_side_holds_its_own(planted: Planted) -> None:
    """The local form: each major its own target dir and its own line."""
    planted.floor()
    done, log = planted.run_pgrx(ORG_PGRX_MAJOR="", PLANT_LH="18")
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert "pgrx-pg17 100" in lines
    assert "pgrx-pg18 100" in lines
    for major in MAJORS:
        assert (
            f"test:pgrx: pgrx-pg{major} leg holds its floor 80.0 (100%)" in done.stderr
        )
    targets = sorted(ln.split("|target=")[1].split("|")[0] for ln in suites(log))
    want = [
        str(planted.tree / "target" / f"llvm-cov-pgrx-pg17-{STABLE}"),
        str(planted.tree / "target" / f"llvm-cov-pgrx-pg18-{STABLE}"),
    ]
    assert want == targets


@pytest.mark.parametrize("major", MAJORS)
def test_each_major_cleans_its_workspace_before_its_suite(
    planted: Planted, major: object
) -> None:
    """`llvm-cov clean --workspace` in the major's own directory, first (#2061).

    On main the run cleaned `--profraw-only`, so a workspace
    executable an earlier build left (a version bump renames them
    all) stayed and `report` counted it with zero hits.
    """
    planted.floor()
    done, log = planted.run_pgrx(ORG_PGRX_MAJOR="", PLANT_LH="18")
    assert done.returncode == 0, done.stderr
    target = planted.tree / "target" / f"llvm-cov-pgrx-pg{major}-{STABLE}"
    mine = [ln for ln in log if f"|target={target}|" in ln]
    cleans = [i for i, ln in enumerate(mine) if ln.startswith("cargo llvm-cov clean ")]
    once: list[str] = ["cargo llvm-cov clean --workspace|"]
    seen: list[str] = [mine[i].split("cwd=")[0] for i in cleans]
    assert once == seen
    suite = next(i for i, ln in enumerate(mine) if ln.startswith("cargo pgrx test"))
    assert cleans[0] < suite


class IcuPerMajor(Planted):
    """Every suite run compiles with its own major's ICU headers (#1774)."""

    def icu(self) -> list[str]:
        """Read what the ICU stubs and the stub suite recorded.

        Returns:
            The check's line first, then each suite's flags, sorted: the
            side-by-side runs finish in any order.

        """
        lines = Path(f"{self.log}.icu").read_text(encoding="utf-8").splitlines()
        return lines[:1] + sorted(lines[1:])


@fixture
def icu_per_major(
    tmp_path: Path, belt: support.Belt, env: support.Env, rustc: support.Rustc
) -> IcuPerMajor:
    """Give each row its own IcuPerMajor."""
    return IcuPerMajor(tmp_path, belt=belt, env=env, rustc=rustc)


def flags(*majors: int) -> list[str]:
    """Spell what each major's suite saw, with the parent's flags behind.

    Returns:
        One line per major.

    """
    return [f"pg{m}|-Iicu-pg{m} parent-b|-Iicu-pg{m} parent-c" for m in majors]


def test_every_run_shape_sees_its_own_major_only(icu_per_major: IcuPerMajor) -> None:
    """Side by side plain, side by side measured, and one major alone."""
    parent = {
        "BINDGEN_EXTRA_CLANG_ARGS": "parent-b",
        "CFLAGS": "parent-c",
        "PLANT_LH": "18",
    }
    shapes: tuple[tuple[str, dict[str, str], str, tuple[int, ...]], ...] = (
        ("plain, side by side", {"ORG_PGRX_MAJOR": ""}, "17,18", (17, 18)),
        ("one major", {}, "18", (18,)),
    )
    for _name, env, checked, majors in shapes:
        for floor in (False, True):
            if floor:
                icu_per_major.floor()
            Path(f"{icu_per_major.log}.icu").unlink(missing_ok=True)
            done, _ = icu_per_major.run_pgrx(**(parent | env))
            assert done.returncode == 0, done.stderr
            want: list[str] = [f"icu-check [{checked}]", *flags(*majors)]
            assert want == icu_per_major.icu()
            (icu_per_major.tree / ".coverage-floor").unlink(missing_ok=True)


def test_no_floor_runs_the_plain_suite_and_prints_no_figure(planted: Planted) -> None:
    """No `.coverage-floor`: today's run, untouched."""
    done, log = planted.run_pgrx()
    assert done.returncode == 0, done.stderr
    assert "pgrx-pg18" not in done.stdout
    (suite,) = suites(log)
    assert "|target=|profile=" in suite
    coverage = [ln for ln in log if ln.startswith("cargo llvm-cov")]
    assert coverage == NOTHING
    assert "test:pgrx: 1 suite run: crates/lab-pg(pg18)" in done.stdout


def test_a_failed_suite_prints_no_figure(planted: Planted) -> None:
    """The suite's exit status gates the figure."""
    planted.floor()
    done, log = planted.run_pgrx(PLANT_FAIL="1")
    assert done.returncode == 1
    assert "pgrx-pg18" not in done.stdout
    assert (
        "test:pgrx: crates/lab-pg at pg18 failed — a suite that does not pass"
        " measures nothing"
    ) in done.stderr
    reports = [ln for ln in log if ln.startswith("cargo llvm-cov report")]
    assert reports == NOTHING


def test_a_pass_that_wrote_no_profile_is_refused_by_name(planted: Planted) -> None:
    """An extension that measured nothing must not read as held."""
    planted.floor()
    done, _ = planted.run_pgrx(PLANT_NO_PROFILE="1")
    assert done.returncode == 1
    assert "pgrx-pg18" not in done.stdout
    assert (
        "test:pgrx: pgrx-pg18: the suite passed and wrote no coverage profile"
        in done.stderr
    )


def test_a_relative_profile_path_is_refused(planted: Planted) -> None:
    """The backends' working directory is deleted when pgrx stops them."""
    planted.floor()
    done, log = planted.run_pgrx(PLANT_PROFILE_DIR="")
    assert done.returncode == 1
    assert (
        "test:pgrx: pg18: the profile path 'lab-%p-%m.profraw' is not absolute"
        in done.stderr
    )
    assert suites(log) == NOTHING


def test_a_directory_another_live_run_holds_is_refused(planted: Planted) -> None:
    """The planted row: nothing is cleaned or built, and the run is named.

    On main the run cleaned the other run's profiles and built into
    its directory.
    """
    planted.floor()
    target = planted.tree / "target" / f"llvm-cov-pgrx-pg18-{STABLE}"
    target.mkdir(parents=True)
    other = planted.tmp / f"org-coverage-{os.getpid()}"
    other.mkdir()
    (target / ".org-coverage-owner").write_text(f"{other}\n", encoding="utf-8")
    done, log = planted.run_pgrx()
    assert done.returncode == 1
    assert (
        f"{target} is being measured by another run ({other}); wait for it to finish"
    ) in done.stderr
    assert "pgrx-pg18" not in done.stdout
    cleaned = [ln for ln in log if ln.startswith("cargo llvm-cov clean")]
    assert cleaned == NOTHING
    assert suites(log) == NOTHING


def test_the_run_is_claimed_before_the_clean(planted: Planted) -> None:
    """A measured major records this run as its directory's owner."""
    planted.floor()
    done, _ = planted.run_pgrx(PLANT_LH="17")
    assert done.returncode == 0, done.stderr
    target = planted.tree / "target" / f"llvm-cov-pgrx-pg18-{STABLE}"
    assert f"{planted.run_dir}\n" == (target / ".org-coverage-owner").read_text(
        encoding="utf-8"
    )


def test_an_empty_run_is_red_by_name(planted: Planted) -> None:
    """Outside the belt there is no run to claim for: red, naming it."""
    planted.floor()
    done, log = planted.run_pgrx(ORG_COVERAGE_RUN="")
    assert done.returncode == 1
    assert (
        "test:pgrx: ORG_COVERAGE_RUN is empty — the run a measured build directory is"
    ) in done.stderr
    assert suites(log) == NOTHING


class CoverageTasks(Planted):
    """coverage:check leaves the extension to test:pgrx; `pgrx` measures it."""

    def measured_run(self) -> dict[str, str]:
        """Leave what the test task's measured `cargo test` leaves (#1653).

        Returns:
            The run's directory, as the belt's `[env]` names it.

        """
        run = self.tmp / "run"
        (run / "rust").mkdir(parents=True)
        profiles = self.tree / "target" / f"llvm-cov-target-{STABLE}"
        profiles.mkdir(parents=True)
        (profiles / "lab-1-1.profraw").touch()
        return {"ORG_COVERAGE_RUN": str(run)}


@fixture
def coverage_tasks(
    tmp_path: Path, belt: support.Belt, env: support.Env, rustc: support.Rustc
) -> CoverageTasks:
    """Give each row its own CoverageTasks."""
    return CoverageTasks(tmp_path, belt=belt, env=env, rustc=rustc)


def test_check_runs_no_suite_names_the_legs_and_excludes_the_crate(
    coverage_tasks: CoverageTasks,
) -> None:
    """Zero suite runs, both majors named, lab-pg's files out of the report."""
    coverage_tasks.floor()
    body = task_body("coverage:check")
    done, log = coverage_tasks.run_bash(
        ["-c", body],
        PLANT_LF="10",
        PLANT_LH="9",
        **coverage_tasks.as_mise("coverage:check"),
        **coverage_tasks.measured_run(),
    )
    assert done.returncode == 0, done.stderr
    assert suites(log) == NOTHING
    for major in MAJORS:
        assert (
            f"coverage:check: pgrx-pg{major}: measured and held by test:pgrx"
            in done.stderr
        )
    (report,) = [ln for ln in log if ln.startswith("cargo llvm-cov report")]
    ext = "".join(
        f"\\{c}" if c in "[]\\.*^$+?(){}|" else c
        for c in str(coverage_tasks.tree / "crates" / "lab-pg")
    )
    assert report.startswith(
        f"cargo llvm-cov report --ignore-filename-regex ^{ext}/ --lcov"
    ), report
    built = [ln for ln in log if ln.startswith("cargo llvm-cov --workspace")]
    assert built == NOTHING
    assert "coverage:check: rust leg holds its floor 80.0 (90%)" in done.stdout


def test_coverage_exclude_naming_the_extension_is_refused(
    coverage_tasks: CoverageTasks,
) -> None:
    """The removed variable is refused whatever it names (#1653)."""
    coverage_tasks.floor()
    body = task_body("coverage:check")
    done, _ = coverage_tasks.run_bash(
        ["-c", body],
        COVERAGE_EXCLUDE="lab-pg",
        **coverage_tasks.as_mise("coverage:check"),
    )
    assert done.returncode == 1
    assert (
        "coverage:check: COVERAGE_EXCLUDE is set (lab-pg) — it is removed (#1653):\n"
    ) in done.stderr


def test_the_pgrx_selector_prints_one_line_per_declared_major(
    coverage_tasks: CoverageTasks,
) -> None:
    """Every major, measured, stdout holding the leg lines and no more."""
    done, log = coverage_tasks.run_bash(
        [str(coverage_tasks.belt / "coverage-measure.sh"), "coverage:adopt", "pgrx"],
        ORG_PGRX_MAJOR="18",
    )
    assert done.returncode == 0, done.stderr
    want = ["pgrx-pg17 77.77777777777777", "pgrx-pg18 77.77777777777777"]
    got = sorted(done.stdout.splitlines())
    assert want == got
    assert len(suites(log)) == 2


def test_the_majors_selector_lists_what_the_pgrx_selector_measures(
    coverage_tasks: CoverageTasks,
) -> None:
    """pgrx-majors: the same majors, one per line, and no suite run (#1692)."""
    done, log = coverage_tasks.run_bash([
        str(coverage_tasks.belt / "coverage-measure.sh"),
        "release:coverage-floor",
        "pgrx-majors",
    ])
    assert done.returncode == 0, done.stderr
    assert done.stdout == "17\n18\n"
    assert not suites(log), log


def test_the_pgrx_selector_holds_no_floor(coverage_tasks: CoverageTasks) -> None:
    """Adoption and the release derive the floor; measuring is not holding."""
    coverage_tasks.floor()
    done, _ = coverage_tasks.run_bash([
        str(coverage_tasks.belt / "coverage-measure.sh"),
        "coverage:adopt",
        "pgrx",
    ])
    assert done.returncode == 0, done.stderr
    assert "below its derived floor" not in done.stderr


class FannedPlan(Planted):
    """`ORG_PGRX_FANNED` is the plan's JSON, decoded, never parsed in shell (#1880).

    The value is pgrx-plan.sh's stdout as the workflow hands it on: a
    JSON array of major strings. The rows feed the producer's own
    output in, and refuse what is not that shape by name.
    """

    declares: ClassVar[tuple[int, ...]] = (15, 16, 17, 18)


@fixture
def fanned_plan(
    tmp_path: Path, belt: support.Belt, env: support.Env, rustc: support.Rustc
) -> FannedPlan:
    """Give each row its own FannedPlan."""
    return FannedPlan(tmp_path, belt=belt, env=env, rustc=rustc)


def test_the_producers_stdout_holds_against_the_declaration(
    fanned_plan: FannedPlan,
) -> None:
    """pgrx-plan.sh's literal stdout for pg15 to pg18 is the planned list."""
    plan, _ = fanned_plan.run_bash([
        str(BELT / "pgrx-plan.sh"),
        str(BELT.parent),
    ])
    assert plan.returncode == 0, plan.stderr
    assert plan.stdout == '["15","16","17","18"]\n'
    done, log = fanned_plan.run_pgrx(ORG_PGRX_FANNED=plan.stdout)
    assert done.returncode == 0, done.stderr
    assert (
        "test:pgrx: run by the gate's fan-out, one leg per major: 15,16,17,18"
        in done.stdout
    )
    assert suites(log) == NOTHING


@pytest.mark.parametrize("value", ["[15]", '"15"', "15,16"])
def test_a_value_that_is_not_the_plans_shape_is_refused(
    fanned_plan: FannedPlan, value: object
) -> None:
    """A number, a bare string and a comma list are each refused by name."""
    done, log = fanned_plan.run_pgrx(ORG_PGRX_FANNED=value)
    assert done.returncode == 1
    assert (
        f"test:pgrx: ORG_PGRX_FANNED '{value}' is not a JSON array of"
        " canonical major strings\n"
    ) == done.stderr
    assert suites(log) == NOTHING
