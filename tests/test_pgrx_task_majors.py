"""Table tests for the majors pgrx is built at by three tasks (#1795).

Each task's helper, `main` of belt.rust_lint, belt.pg_clippy or
belt.msrv_lint, runs in this process against a stub surface of two
extensions, `first` (majors 15,16,17) and `second` (14,15,16), and one
plain crate. Both tasks once derived the majors by hand and kept the
last extension's, so `fix:rust` registered `14,15,16` and fixed both
extensions at pg16, and `lint:msrv` registered pg16 alone. Now both take
the derivation from pgrx-env.sh: each extension is built at its own
highest major, and the union, `16,17`, is what is registered.

`cargo`, `git`, `mise` and `rustup` are stubs on PATH that log what
they were asked, and the belt commands the helpers call in process
stand in by monkeypatch, logging what they were given: `gate-surface`
answers the planted surface, `pgrx-postgres` a plan, `msrv-plan` the
planted plan. pgrx-env.sh is the real one, sourced, with only what needs a Mac or a
Postgres replaced: the prepare and the init log, and a major's ICU
headers are a mark the stub cargo prints. Every row asserts the whole
log and the whole output, so a row cannot pass by stopping early.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from belt import (
    gate_surface,
    msrv_lint,
    msrv_plan,
    pg_clippy,
    pgrx_postgres,
    rust_lint,
    rust_workspaces,
    tracked,
)
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

BELT = support.MISE

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
PG_CONFIG = "pg\t16\t/pg16/pg_config\npg\t17\t/pg17/pg_config\n"
ANSWERS = {"gate-surface": "PLANT_SURFACE", "msrv-plan": "PLANT_PLAN"}


def stand_in(command: str) -> Callable[[list[str]], int]:
    """Stand in for a belt command the helpers call in process.

    It reads its stdin to the end before it logs, so a pipeline's
    stages log in the order the pipeline runs them, then answers what
    the row planted; `msrv-plan --failed` exits 1.

    Returns:
        The command's `main`.

    """

    def main(argv: list[str]) -> int:
        failed = argv[:1] == ["--failed"]
        if not failed:
            sys.stdin.read()
        with Path(os.environ["PLANT_LOG"]).open("a", encoding="utf-8") as log:
            log.write(f"belt {command} {' '.join(argv)}\n")
        if failed:
            return 1
        answer = ANSWERS.get(command)
        sys.stdout.write(
            Path(os.environ[answer]).read_text(encoding="utf-8")
            if answer
            else PG_CONFIG
        )
        return 0

    return main


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
    "belt pgrx-postgres --majors 16,17",
]


class TwoExtensions:
    """A workspace of two extensions at different highest majors."""

    def __init__(self, tmp_path: Path, *, belt: support.Belt, env: support.Env) -> None:
        """Plant the stubs, the surface, the plan and the belt."""
        self.plant_belt = belt
        self.make_env = env
        self.tmp = tmp_path
        self.tree = self.tmp / "tree"
        self.tree.mkdir()
        # The manifest the stub git tracks, read for the cargo-fuzz
        # marker (#2093): a plain workspace, so no root gets the cfg.
        (self.tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        bins = self.bins = self.tmp / "bin"
        bins.mkdir()
        for name, text in {
            "cargo": CARGO_STUB,
            "git": GIT_STUB,
            "mise": MISE_STUB,
            "rustup": RUSTUP_STUB,
        }.items():
            stub = bins / name
            stub.write_text(text, encoding="utf-8")
            stub.chmod(0o755)
        self.belt = self.plant_belt(
            self.tmp / "belt",
            stubs={"pgrx-env.sh": PGRX_ENV_STUB, "clippy-ladder.sh": LADDER_STUB},
        )
        self.log = self.tmp / "log"
        self.log.touch()
        surface = self.tmp / "surface"
        surface.write_text(SURFACE, encoding="utf-8")
        plan = self.tmp / "plan"
        plan.write_text(PLAN, encoding="utf-8")
        env = self.make_env()
        self.env = env | {
            "PATH": f"{bins}:{env['PATH']}",
            "ORG_BELT_DIR": str(self.belt),
            "ORG_FINDINGS": "",
            "BASH_ENV": str(BELT / "findings.sh"),
            "PLANT_LOG": str(self.log),
            "PLANT_SURFACE": str(surface),
            "PLANT_PLAN": str(plan),
        }

    def run_task(
        self,
        task: str,
        monkeypatch: pytest.MonkeyPatch,
        capfd: pytest.CaptureFixture[str],
    ) -> tuple[int, str, str]:
        """Run one task's helper in this process, standing in the planted tree.

        Returns:
            The helper's status, its stdout and its stderr.

        """
        monkeypatch.chdir(self.tree)
        for name in list(os.environ):
            monkeypatch.delenv(name)
        for name, value in (self.env | {"MISE_TASK_NAME": task}).items():
            monkeypatch.setenv(name, value)
        for module, command in (
            (gate_surface, "gate-surface"),
            (pgrx_postgres, "pgrx-postgres"),
            (msrv_plan, "msrv-plan"),
        ):
            monkeypatch.setattr(module, "main", stand_in(command))
        # The tools the helpers resolved at import, before PATH had the stubs.
        monkeypatch.setattr(rust_workspaces, "CARGO", str(self.bins / "cargo"))
        monkeypatch.setattr(rust_workspaces, "GIT", str(self.bins / "git"))
        monkeypatch.setattr(tracked, "GIT", str(self.bins / "git"))
        capfd.readouterr()
        status = HELPERS[task]()
        out, err = capfd.readouterr()
        return status, out, err

    def logged(self) -> list[str]:
        """Read the stubs' log, the msrv scratch directory masked.

        Returns:
            One line per call, in order.

        """
        text = self.log.read_text(encoding="utf-8")
        masked = re.sub(r"\S*/toolchains\.json", "<tmp>/toolchains.json", text)
        return masked.splitlines()


HELPERS: dict[str, Callable[[], int]] = {
    "fix:rust": lambda: rust_lint.main(["fix"]),
    "lint:pg-clippy": lambda: pg_clippy.main([]),
    "lint:msrv": lambda: msrv_lint.main([]),
}


@fixture
def two_extensions(
    tmp_path: Path, belt: support.Belt, env: support.Env
) -> TwoExtensions:
    """Give each row its own TwoExtensions.

    Returns:
        The row's TwoExtensions.

    """
    return TwoExtensions(tmp_path, belt=belt, env=env)


def test_fix_rust_fixes_each_extension_at_its_own_major(
    two_extensions: TwoExtensions,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """fix:rust registers 16,17 and fixes first at pg17, second at pg16.

    Before #1795 it registered `14,15,16`, the last extension's
    majors, and fixed both extensions with `--features pg16`.
    """
    status, out, err = two_extensions.run_task("fix:rust", monkeypatch, capfd)
    assert status == 0, err
    fix = "cargo clippy --fix --allow-dirty"
    own = "--no-deps --all-targets --locked --no-default-features"
    at = "--manifest-path Cargo.toml"
    want = [
        f"cargo fmt --all {at} -- --config-path {two_extensions.belt}/rustfmt.toml",
        f"cargo fetch --locked {at}",
        f"{METADATA} {at}",
        "belt gate-surface --exclude ",
        (
            f"{fix} --workspace --exclude first --exclude second"
            f" --all-features --locked {at} -- -D warnings"
        ),
        *REGISTER,
        f"{fix} -p first {own} --features pg17 {at} -- -D warnings [-Iicu-pg17]",
        f"{fix} -p second {own} --features pg16 {at} -- -D warnings [-Iicu-pg16]",
    ]
    assert want == two_extensions.logged()
    said = [
        "fix:rust: Cargo.toml: formatted",
        "fix:rust: Cargo.toml: plain rewritten where clippy could",
        "fix:rust: Cargo.toml: pgrx extension(s) fixed: first(pg17) second(pg16)",
        "fix:rust: applied what is machine-applicable — the rest is judgement",
    ]
    assert said == out.splitlines()


def test_lint_pg_clippy_lints_each_extension_at_its_own_major(
    two_extensions: TwoExtensions,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """lint:pg-clippy registers 16,17 and lints first at pg17, second at pg16."""
    status, out, err = two_extensions.run_task("lint:pg-clippy", monkeypatch, capfd)
    assert status == 0, err
    own = "--no-deps --all-targets --locked --keep-going --no-default-features"
    at = "--manifest-path Cargo.toml"
    want = [
        f"cargo fetch --locked {at}",
        f"{METADATA} {at}",
        "belt gate-surface --exclude ",
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
    assert want == two_extensions.logged()
    said = [
        ("lint:pg-clippy: Cargo.toml: 2 extension(s) linted: first(pg17) second(pg16)"),
        (
            "lint:pg-clippy: clean at every group"
            " (restriction minus 0 named contradictions)"
        ),
    ]
    assert said == out.splitlines()


def test_lint_msrv_registers_every_extensions_major(
    two_extensions: TwoExtensions,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """lint:msrv registers 16,17 and compiles each group at its own.

    Before #1795 it registered `16` alone, the text after the last
    colon of its `name:major` list, and handed the ICU check that
    list rather than majors.
    """
    status, out, err = two_extensions.run_task("lint:msrv", monkeypatch, capfd)
    assert status == 0, err
    at = "--manifest-path Cargo.toml"
    check = f"cargo check --locked {at} --no-default-features"
    want = [
        # The pins once, then each of the tree's workspaces (#2011).
        "mise ls rust --json",
        f"cargo fetch --locked {at}",
        # The whole graph, resolved with the gate's features (#1850).
        f"cargo metadata --format-version 1 --locked {at} --all-features",
        "belt gate-surface --exclude ",
        (
            "belt msrv-plan --toolchains <tmp>/toolchains.json --exclude "
            f" --pgrx first:17,second:16 --floor {two_extensions.belt}/rust-floor.toml"
            " --root Cargo.toml"
        ),
        *REGISTER,
        f"{check} --features pg17 -p first@0.1.0 [-Iicu-pg17] {{1.82.0}}",
        f"{check} --features pg16 -p second@0.1.0 [-Iicu-pg16] {{1.82.0}}",
    ]
    assert want == two_extensions.logged()
    said = [
        "lint:msrv: compiling at 1.82.0: first@0.1.0",
        "lint:msrv: compiling at 1.82.0: second@0.1.0",
        "lint:msrv: 2 declared minimum(s) compiled at the toolchain they name",
    ]
    assert said == out.splitlines()


def test_lint_msrv_red_compile_says_the_rule_and_keeps_its_status(
    two_extensions: TwoExtensions,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A red compile at the derived number runs item 4's remedy (#1850).

    From msrv_lint.py's `check` (#1911; the body's EXIT trap before
    it), for the group that failed and no other, and
    the task exits with cargo's own status rather than the remedy's.
    """
    two_extensions.env["PLANT_CHECK_FAILS"] = "1"
    status, out, err = two_extensions.run_task("lint:msrv", monkeypatch, capfd)
    assert status == support.CARGO_FAILED, err
    failed = [line for line in two_extensions.logged() if "--failed" in line]
    want: list[str] = ["belt msrv-plan --failed 1.82.0 first@0.1.0 --root Cargo.toml"]
    assert want == failed
    said = ["lint:msrv: compiling at 1.82.0: first@0.1.0"]
    assert said == out.splitlines()
