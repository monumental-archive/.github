#!/usr/bin/env python3
"""Table tests for the majors pgrx is built at by three tasks (#1795).

Each task's own body, read from mise/config.toml, runs against a stub
surface of two extensions, `first` (majors 15,16,17) and `second`
(14,15,16), and one plain crate. Both tasks once derived the majors by
hand and kept the last extension's, so `fix:rust` registered `14,15,16`
and fixed both extensions at pg16, and `lint:msrv` registered pg16
alone. Now both take the derivation from pgrx-env.sh: each extension
is built at its own highest major, and the union, `16,17`, is what is
registered.

`cargo`, `git`, `mise` and `rustup` are stubs on PATH that log what
they were asked, and `ORG_PYTHON` is a stub that answers each belt
helper by name: `gate_surface.py` with the planted surface,
`pgrx_postgres.py` with a plan, `msrv_plan.py` with the planted plan.
`rust_lint.py`, `msrv_lint.py` and `pg_clippy.py`, the bodies of
fix:rust, lint:msrv and lint:pg-clippy after their guards (#1911), run
on this interpreter, from the planted belt, so they meet the same stubs.
pgrx-env.sh is the real one, sourced, with only what needs a Mac or a
Postgres replaced: the prepare and the init log, and a major's ICU
headers are a mark the stub cargo prints. Every row asserts the whole
log and the whole output, so a row cannot pass by stopping early.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom. stdlib
`unittest`, run through the gate as `mise run test`.
"""

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

BELT = Path(__file__).resolve().parent
BASH = hermetic.tool("bash")

# The workspace derivation's question (#2004) is answered and not
# logged: every manifest's root is the tree's Cargo.toml.
CARGO_STUB = r"""#!/usr/bin/env bash
[[ $1 != locate-project ]] || { echo "${PWD}/Cargo.toml"; exit 0; }
line="cargo $*"
[[ -z ${BINDGEN_EXTRA_CLANG_ARGS:-} ]] || line="${line} [${BINDGEN_EXTRA_CLANG_ARGS}]"
[[ -z ${RUSTUP_TOOLCHAIN:-} ]] || line="${line} {${RUSTUP_TOOLCHAIN}}"
printf '%s\n' "${line}" >> "${PLANT_LOG}"
[[ $1 != metadata ]] || echo '{}'
[[ $1 != check || -z ${PLANT_CHECK_FAILS:-} ]] || exit 101
exit 0
"""
# Answers the belt's one producer of the tracked list (#1986), and the
# workspace derivation's `rev-parse --show-toplevel` (#2004), and
# nothing else: `git -c core.quotePath=false ls-files -z -- …`.
GIT_STUB = """#!/usr/bin/env bash
[[ " $* " != *" rev-parse --show-toplevel "* ]] || { pwd; exit 0; }
[[ "$1 $2 $3 $4" == "-c core.quotePath=false ls-files -z" ]] || exit 99
printf 'Cargo.toml\\0'
"""
RUSTUP_STUB = """#!/usr/bin/env bash
[[ $* == "component list --installed" ]] || exit 99
printf 'clippy-host\\nrustfmt-host\\n'
"""
MISE_STUB = """#!/usr/bin/env bash
printf 'mise %s\\n' "$*" >> "${PLANT_LOG}"
echo '[]'
"""
# Reads its stdin to the end before it logs, so a pipeline's stages log
# in the order the pipeline runs them.
PYTHON_STUB = r"""#!/usr/bin/env bash
script=$(basename "$1")
case ${script} in
  rust_lint.py | msrv_lint.py | pg_clippy.py) exec "${PLANT_PYTHON}" "$@" ;;
esac
shift
[[ $1 == --failed ]] || cat > /dev/null
printf '%s %s\n' "${script}" "$*" >> "${PLANT_LOG}"
case ${script} in
  gate_surface.py) cat "${PLANT_SURFACE}" ;;
  pgrx_postgres.py) printf 'pg\t16\t/pg16/pg_config\npg\t17\t/pg17/pg_config\n' ;;
  msrv_plan.py) [[ $1 != --failed ]] || exit 1; cat "${PLANT_PLAN}" ;;
  *) exit 99 ;;
esac
"""
LADDER_STUB = """
org_clippy_ladder() { echo "-D warnings"; }
org_clippy_contradiction_count() { echo 0; }
"""
PGRX_ENV_STUB = f"""
. "{BELT / "pgrx-env.sh"}"
org_pgrx_darwin_prepare() {{ printf 'prepare %s\\n' "$*" >> "${{PLANT_LOG}}"; }}
# A major's ICU headers and Postgres (#1774, #1940): a mark the stub
# cargo could show, and no clone made.
org_pgrx_major_env() {{ export BINDGEN_EXTRA_CLANG_ARGS="-Iicu-pg$1"; }}
"""
SURFACE = (
    "crate\tfirst@0.1.0\tpgrx\t15,16,17\tfirst\n"
    "crate\tplain@0.1.0\tplain\t-\tplain\n"
    "crate\tsecond@0.1.0\tpgrx\t14,15,16\tsecond\n"
)
PLAN = (
    "check\t1.82.0\t--no-default-features --features pg17\tfirst@0.1.0\n"
    "check\t1.82.0\t--no-default-features --features pg16\tsecond@0.1.0\n"
)
METADATA = "cargo metadata --no-deps --format-version 1 --locked"
REGISTER = [
    "prepare 16,17",
    "mise ls aqua:theseus-rs/postgresql-binaries --json",
    "pgrx_postgres.py --majors 16,17",
]


def body(task: str) -> str:
    """Read one task's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    entry = decoded.as_object(tasks[task], task)
    return decoded.as_str(entry["run"], f"{task}.run")


class TwoExtensions(unittest.TestCase):
    """A workspace of two extensions at different highest majors."""

    @override
    def setUp(self) -> None:
        """Plant the stubs, the surface, the plan and the belt."""
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.tree = self.tmp / "tree"
        self.tree.mkdir()
        bins = self.tmp / "bin"
        bins.mkdir()
        for name, text in {
            "cargo": CARGO_STUB,
            "git": GIT_STUB,
            "mise": MISE_STUB,
            "rustup": RUSTUP_STUB,
            "python": PYTHON_STUB,
        }.items():
            stub = bins / name
            stub.write_text(text, encoding="utf-8")
            stub.chmod(0o755)
        self.belt = hermetic.belt(
            self.tmp / "belt",
            stubs={
                "pgrx-env.sh": PGRX_ENV_STUB,
                "clippy-ladder.sh": LADDER_STUB,
                # A copy, not the link the belt plants: Python puts a
                # script's RESOLVED directory on sys.path, so a linked
                # pg_clippy.py would import the canon's own rust_lint,
                # whose BELT is the canon's, past these stubs.
                "pg_clippy.py": (BELT / "pg_clippy.py").read_text(encoding="utf-8"),
            },
        )
        self.log = self.tmp / "log"
        self.log.touch()
        surface = self.tmp / "surface"
        surface.write_text(SURFACE, encoding="utf-8")
        plan = self.tmp / "plan"
        plan.write_text(PLAN, encoding="utf-8")
        env = hermetic.env()
        self.env = env | {
            "PATH": f"{bins}:{env['PATH']}",
            "ORG_BELT_DIR": str(self.belt),
            "ORG_PYTHON": str(bins / "python"),
            "PLANT_LOG": str(self.log),
            "PLANT_SURFACE": str(surface),
            "PLANT_PLAN": str(plan),
            "PLANT_PYTHON": sys.executable,
        }

    def run_task(self, task: str) -> subprocess.CompletedProcess[str]:
        """Run one task's body in the planted tree.

        Returns:
            The finished process.

        """
        # BASH_ENV, as the belt's [env] sets it, so the body has the
        # writer and org_tracked_read (#1986); `--norc`, as the belt's
        # shell has it, is what lets BASH_ENV through whatever stdin is
        # (hermetic.task_run).
        as_mise = hermetic.task_run(task, body(task))
        return subprocess.run(
            [BASH, "--norc", "-c", body(task)],
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def logged(self) -> list[str]:
        """Read the stubs' log, the msrv scratch directory masked.

        Returns:
            One line per call, in order.

        """
        text = self.log.read_text(encoding="utf-8")
        masked = re.sub(r"\S*/toolchains\.json", "<tmp>/toolchains.json", text)
        return masked.splitlines()

    def test_fix_rust_fixes_each_extension_at_its_own_major(self) -> None:
        """fix:rust registers 16,17 and fixes first at pg17, second at pg16.

        Before #1795 it registered `14,15,16`, the last extension's
        majors, and fixed both extensions with `--features pg16`.
        """
        done = self.run_task("fix:rust")
        self.assertEqual(0, done.returncode, done.stderr)
        fix = "cargo clippy --fix --allow-dirty"
        own = "--no-deps --all-targets --locked --no-default-features"
        at = "--manifest-path Cargo.toml"
        want = [
            f"cargo fmt --all {at} -- --config-path {self.belt}/rustfmt.toml",
            f"cargo fetch --locked {at}",
            f"{METADATA} {at}",
            "gate_surface.py --exclude ",
            (
                f"{fix} --workspace --exclude first --exclude second"
                f" --all-features --locked {at} -- -D warnings"
            ),
            *REGISTER,
            f"{fix} -p first {own} --features pg17 {at} -- -D warnings [-Iicu-pg17]",
            f"{fix} -p second {own} --features pg16 {at} -- -D warnings [-Iicu-pg16]",
        ]
        self.assertEqual(want, self.logged())
        said = [
            "fix:rust: Cargo.toml: formatted",
            "fix:rust: Cargo.toml: plain rewritten where clippy could",
            "fix:rust: Cargo.toml: pgrx extension(s) fixed: first(pg17) second(pg16)",
            "fix:rust: applied what is machine-applicable — the rest is judgement",
        ]
        self.assertEqual(said, done.stdout.splitlines())

    def test_lint_pg_clippy_lints_each_extension_at_its_own_major(self) -> None:
        """lint:pg-clippy registers 16,17 and lints first at pg17, second at pg16."""
        done = self.run_task("lint:pg-clippy")
        self.assertEqual(0, done.returncode, done.stderr)
        own = "--no-deps --all-targets --locked --keep-going --no-default-features"
        at = "--manifest-path Cargo.toml"
        want = [
            f"cargo fetch --locked {at}",
            f"{METADATA} {at}",
            "gate_surface.py --exclude ",
            *REGISTER,
            (
                f"cargo clippy -p first@0.1.0 {at} {own} --features pg17"
                " -- -D warnings [-Iicu-pg17]"
            ),
            (
                f"cargo clippy -p second@0.1.0 {at} {own} --features pg16"
                " -- -D warnings [-Iicu-pg16]"
            ),
        ]
        self.assertEqual(want, self.logged())
        said = [
            (
                "lint:pg-clippy: Cargo.toml: 2 extension(s) linted:"
                " first(pg17) second(pg16)"
            ),
            (
                "lint:pg-clippy: clean at every group"
                " (restriction minus 0 named contradictions)"
            ),
        ]
        self.assertEqual(said, done.stdout.splitlines())

    def test_lint_msrv_registers_every_extensions_major(self) -> None:
        """lint:msrv registers 16,17 and compiles each group at its own.

        Before #1795 it registered `16` alone, the text after the last
        colon of its `name:major` list, and handed the ICU check that
        list rather than majors.
        """
        done = self.run_task("lint:msrv")
        self.assertEqual(0, done.returncode, done.stderr)
        check = "cargo check --locked --no-default-features"
        want = [
            # The pins once, then the workspace (#1911).
            "mise ls rust --json",
            "cargo fetch --locked",
            # The whole graph, resolved with the gate's features (#1850).
            "cargo metadata --format-version 1 --locked --all-features",
            "gate_surface.py --exclude ",
            (
                "msrv_plan.py --toolchains <tmp>/toolchains.json --exclude "
                f" --pgrx first:17,second:16 --floor {self.belt}/rust-floor.toml"
            ),
            *REGISTER,
            f"{check} --features pg17 -p first@0.1.0 [-Iicu-pg17] {{1.82.0}}",
            f"{check} --features pg16 -p second@0.1.0 [-Iicu-pg16] {{1.82.0}}",
        ]
        self.assertEqual(want, self.logged())
        said = [
            "lint:msrv: compiling at 1.82.0: first@0.1.0",
            "lint:msrv: compiling at 1.82.0: second@0.1.0",
            "lint:msrv: 2 declared minimum(s) compiled at the toolchain they name",
        ]
        self.assertEqual(said, done.stdout.splitlines())

    def test_lint_msrv_red_compile_says_the_rule_and_keeps_its_status(self) -> None:
        """A red compile at the derived number runs item 4's remedy (#1850).

        From msrv_lint.py's `check` (#1911; the body's EXIT trap before
        it), for the group that failed and no other, and
        the task exits with cargo's own status rather than the remedy's.
        """
        self.env["PLANT_CHECK_FAILS"] = "1"
        done = self.run_task("lint:msrv")
        self.assertEqual(101, done.returncode, done.stderr)
        failed = [line for line in self.logged() if "--failed" in line]
        want: list[str] = ["msrv_plan.py --failed 1.82.0 first@0.1.0"]
        self.assertEqual(want, failed)
        said = ["lint:msrv: compiling at 1.82.0: first@0.1.0"]
        self.assertEqual(said, done.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
