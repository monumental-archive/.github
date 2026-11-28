"""Table tests for mise/msrv_lint.py, lint:msrv's body (#1911).

One row per branch of the body, each asserting the message it prints
and the commands it ran, in order. The helper runs in this process;
`cargo` and `mise` are stubs on PATH that log what they were asked,
`ORG_PYTHON` is a stub that answers each belt helper by name, and the
belt is planted with pgrx-env.sh's prepare and a major's environment
replaced by marks (the mise/test_pgrx_task_majors.py shape). Every
stream is captured at the descriptor, so a stub's or a belt function's
line lands beside the helper's own. One row runs the task's own body,
read from mise/config.toml, for the guard and the script contract.

`PLANT_FAIL` names the one step that fails: `cargo-<subcommand>`,
`mise-<what>`, a helper's file name, or `prepare`.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import (
    decoded,
    gate_surface,
    msrv_lint,
    msrv_plan,
    pgrx_postgres,
    rust_task,
    rust_workspaces,
    typed_std,
)
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# The calls and lines rows count: a run that registers nothing, and one
# that ends in the remedy; what a plan with a blank line says, what a red
# root leaves said, and what two roots compiled say.
UNREGISTERED_CALLS = 6
REMEDY_CALLS = 7
SAID_FOR_A_BLANK = 2
SAID_FOR_A_RED_ROOT = 2
SAID_FOR_TWO_ROOTS = 3

BELT = support.MISE
TASK = "lint:msrv"
GIT = support.tool("git")

CARGO_STUB = r"""#!/usr/bin/env bash
line="cargo $*"
[[ -z ${BINDGEN_EXTRA_CLANG_ARGS:-} ]] || line="${line} [${BINDGEN_EXTRA_CLANG_ARGS}]"
[[ -z ${RUSTUP_TOOLCHAIN:-} ]] || line="${line} {${RUSTUP_TOOLCHAIN}}"
[[ -z ${RUSTFLAGS+set} ]] || line="${line} (${RUSTFLAGS})"
printf '%s\n' "${line}" >> "${PLANT_LOG}"
[[ ${PLANT_FAIL:-} != "cargo-$1" ]] || exit 101
[[ $1 != metadata ]] || echo '{"planted": true}'
exit 0
"""
MISE_STUB = r"""#!/usr/bin/env bash
printf 'mise %s\n' "$*" >> "${PLANT_LOG}"
[[ ${PLANT_FAIL:-} != "mise-$2" ]] || exit 3
echo '[]'
"""
# What the in-process commands answer, by command: each stand-in logs
# `belt <command> <arguments> <its stdin>` and fails with 5 when
# PLANT_FAIL names it; `msrv-plan --failed` says the remedy instead.
ANSWERS = {
    "gate-surface": "PLANT_SURFACE",
    "pgrx-postgres": "",
    "msrv-plan": "PLANT_PLAN",
}
PG_CONFIG = "pg\t17\t/pg17/pg_config\n"


def stand_in(command: str) -> Callable[[list[str]], int]:
    """Stand in for one belt command's `main`, as the rows' stubs do.

    Returns:
        The stand-in.

    """

    def main(argv: list[str]) -> int:
        failed = argv[:1] == ["--failed"]
        given = "" if failed else typed_std.stdin_text().rstrip("\n")
        line = f"belt {command} {' '.join(argv)} <{given}>\n"
        with Path(os.environ["PLANT_LOG"]).open("a", encoding="utf-8") as log:
            log.write(line)
        if failed:
            sys.stderr.write(f"lint:msrv: remedy for {argv[1]}\n")
            return 1
        if os.environ.get("PLANT_FAIL") == command:
            return 5
        answer = ANSWERS[command]
        sys.stdout.write(
            Path(os.environ[answer]).read_text(encoding="utf-8")
            if answer
            else PG_CONFIG
        )
        return 0

    return main


PGRX_ENV_STUB = f"""
. "{BELT / "pgrx-env.sh"}"
org_pgrx_darwin_prepare() {{
  printf 'prepare %s\\n' "$*" >> "${{PLANT_LOG}}"
  [[ ${{PLANT_FAIL:-}} != prepare ]] || return 4
  export PLANT_PREPARED=sdk
}}
org_pgrx_major_env() {{
  export BINDGEN_EXTRA_CLANG_ARGS="-Iicu-pg$1-${{PLANT_PREPARED}}"
}}
"""
PLAIN = "crate\tplain@0.1.0\tplain\t-\tplain\n"
EXTENSION = "crate\text@0.2.0\tpgrx\t15,16,17\text\n"
CHECK = "check\t1.85.0\tdefault\tplain@0.1.0 other@0.1.0\n"
PG_CHECK = "check\t1.85.0\t--no-default-features --features pg17\text@0.2.0\n"
NOTE = "note\tunverified: gone@0.1.0 (CLIPPY_EXCLUDE)\n"
META = '<{"planted": true}>'
ROOT = "--manifest-path Cargo.toml"
FETCH = f"cargo fetch --locked {ROOT}"
METADATA = f"cargo metadata --format-version 1 --locked {ROOT} --all-features"
PINS = "mise ls rust --json"
# A root crate, and a crate under fuzz/ carrying the marker
# `cargo fuzz init` writes: compiled with `--cfg fuzzing`, as cargo-fuzz
# builds it (#2093).
WORKSPACE = "[workspace]\n"
MARKED = '[package]\nname = "parse"\n\n[package.metadata]\ncargo-fuzz = true\n'
TWO_ROOTS = ("Cargo.toml", "fuzz/Cargo.toml")
# Nothing printed, or nothing called, typed for the comparison.
NONE: list[str] = []


def lines(*text: str) -> list[str]:
    """Name the lines a row expects, typed for the comparison.

    Returns:
        The lines, as a list.

    """
    return list(text)


def body() -> str:
    """Read lint:msrv's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    entry = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(entry["run"], f"{TASK}.run")


class Planted:
    """The planted belt, stubs and log every in-process row runs in."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        belt: support.Belt,
        env: support.Env,
    ) -> None:
        """Plant the stubs, the surface, the plan and the belt."""
        self.plant_belt = belt
        self.make_env = env
        self.tmp = tmp_path
        self.bins = self.tmp / "bin"
        self.bins.mkdir()
        for name, text in {
            "cargo": CARGO_STUB,
            "mise": MISE_STUB,
        }.items():
            stub = self.bins / name
            stub.write_text(text, encoding="utf-8")
            stub.chmod(0o755)
        self.belt = self.plant_belt(
            self.tmp / "belt", stubs={"pgrx-env.sh": PGRX_ENV_STUB}
        )
        self.log = self.tmp / "log"
        self.log.touch()
        # The tree each root's manifest is read from: the root's, and an
        # unmarked one under fuzz/ unless a row plants the marker.
        self.tree = self.tmp / "repo"
        self.manifests(WORKSPACE)
        self.surface = self.tmp / "surface"
        self.plan = self.tmp / "plan"
        self.plant(PLAIN, CHECK)
        environ = self.make_env()
        self.env = environ | {
            "PATH": f"{self.bins}:{environ['PATH']}",
            "ORG_BELT_DIR": str(self.belt),
            "ORG_FINDINGS": "",
            "MISE_TASK_NAME": TASK,
            "BASH_ENV": str(BELT / "findings.sh"),
            "PLANT_LOG": str(self.log),
            "PLANT_SURFACE": str(self.surface),
            "PLANT_PLAN": str(self.plan),
        }

    def manifests(self, fuzz: str) -> None:
        """Write the tree's root manifest and fuzz/Cargo.toml."""
        (self.tree / "fuzz").mkdir(parents=True, exist_ok=True)
        (self.tree / "Cargo.toml").write_text(WORKSPACE, encoding="utf-8")
        (self.tree / "fuzz" / "Cargo.toml").write_text(fuzz, encoding="utf-8")

    def plant(self, surface: str, plan: str) -> None:
        """Write the surface gate_surface.py answers and the plan msrv_plan.py does."""
        self.surface.write_text(surface, encoding="utf-8")
        self.plan.write_text(plan, encoding="utf-8")

    def enter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        roots: tuple[str, ...] | None = ("Cargo.toml",),
    ) -> None:
        """Stand in the tree as the task runs the helper in this process.

        `roots` is what rust_workspaces.resolved answers, None for a tree
        it refused: the root manifest alone unless a row says otherwise
        (mise/test_msrv_roots.py runs the real one).
        """
        monkeypatch.chdir(self.tree)
        for name in list(os.environ):
            monkeypatch.delenv(name)
        for name, value in self.env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(gate_surface, "main", stand_in("gate-surface"))
        monkeypatch.setattr(msrv_plan, "main", stand_in("msrv-plan"))
        monkeypatch.setattr(pgrx_postgres, "main", stand_in("pgrx-postgres"))

        def resolved() -> list[str] | None:
            return None if roots is None else list(roots)

        monkeypatch.setattr(rust_workspaces, "resolved", resolved)

    def logged(self) -> list[str]:
        """Read the stubs' log, the scratch directory masked.

        Returns:
            One line per call, in order.

        """
        text = self.log.read_text(encoding="utf-8")
        masked = re.sub(r"\S*/toolchains\.json", "<tmp>/toolchains.json", text)
        return masked.replace(str(self.belt), "<belt>").splitlines()


@fixture
def planted(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    belt: support.Belt,
    env: support.Env,
) -> Planted:
    """Give each row its own Planted, dropping what planting printed.

    Returns:
        The planted world.

    """
    world = Planted(tmp_path, belt=belt, env=env)
    capfd.readouterr()
    return world


def planned(pgrx: str = "") -> list[str]:
    """Name the calls up to and including the plan.

    Returns:
        The log lines every row that plans begins with.

    """
    plan = (
        "belt msrv-plan --toolchains <tmp>/toolchains.json --exclude "
        f" --pgrx {pgrx} --floor <belt>/rust-floor.toml --root Cargo.toml {META}"
    )
    return [PINS, FETCH, METADATA, f"belt gate-surface --exclude  {META}", plan]


def test_one_group_compiles_at_its_toolchain_and_is_counted(
    planted: Planted, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A plain group: one `-p` per spec, the gate's features, the count."""
    planted.enter(monkeypatch)
    status = msrv_lint.main([])
    captured = capfd.readouterr()
    said, err = captured.out.splitlines(), captured.err.splitlines()
    assert (status, err) == (0, NONE)
    check = f"cargo check --locked {ROOT} --all-features -p plain@0.1.0 -p other@0.1.0"
    assert lines(*planned(), f"{check} {{1.85.0}}") == planted.logged()
    want = [
        "lint:msrv: compiling at 1.85.0: plain@0.1.0 other@0.1.0",
        "lint:msrv: 1 declared minimum(s) compiled at the toolchain they name",
    ]
    assert want == said


def test_the_gates_features_are_clippy_features(
    planted: Planted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLIPPY_FEATURES and CLIPPY_EXCLUDE reach every step that reads them."""
    planted.env |= {"CLIPPY_FEATURES": "--features a", "CLIPPY_EXCLUDE": "gone"}
    planted.enter(monkeypatch)
    status = msrv_lint.main([])
    assert status == 0
    logged = planted.logged()
    assert (
        f"cargo metadata --format-version 1 --locked {ROOT} --features a" == logged[2]
    )
    assert f"belt gate-surface --exclude gone {META}" == logged[3]
    assert "--exclude gone --pgrx " in logged[4]
    check = f"cargo check --locked {ROOT} --features a -p plain@0.1.0 -p other@0.1.0"
    assert f"{check} {{1.85.0}}" == logged[5]


def test_a_note_is_said_and_nothing_verified_says_so(
    planted: Planted, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Every minimum excluded: the note, then both lines, and green."""
    planted.plant(PLAIN, NOTE)
    planted.enter(monkeypatch)
    status = msrv_lint.main([])
    captured = capfd.readouterr()
    said, err = captured.out.splitlines(), captured.err.splitlines()
    assert (status, err) == (0, NONE)
    assert planned() == planted.logged()
    want = [
        "lint:msrv: unverified: gone@0.1.0 (CLIPPY_EXCLUDE)",
        "lint:msrv: every declared minimum sits outside the gate's compile surface",
        "lint:msrv: nothing verified — the exclusions above are what to remove",
    ]
    assert want == said


def test_an_extension_is_registered_and_compiled_at_its_major(
    planted: Planted, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Its own flags in place of the gate's, prepared, at its major."""
    planted.plant(PLAIN + EXTENSION, CHECK + PG_CHECK)
    planted.enter(monkeypatch)
    status = msrv_lint.main([])
    captured = capfd.readouterr()
    said, err = captured.out.splitlines(), captured.err.splitlines()
    assert (status, err) == (0, NONE)
    own = (
        f"cargo check --locked {ROOT} --no-default-features --features pg17"
        " -p ext@0.2.0"
    )
    want = [
        *planned("ext:17"),
        "prepare 17",
        "mise ls aqua:theseus-rs/postgresql-binaries --json",
        "belt pgrx-postgres --majors 17 <[]>",
        (
            f"cargo check --locked {ROOT} --all-features"
            " -p plain@0.1.0 -p other@0.1.0 {1.85.0}"
        ),
        f"{own} [-Iicu-pg17-sdk] {{1.85.0}}",
    ]
    assert want == planted.logged()
    said_want = lines(
        "lint:msrv: compiling at 1.85.0: plain@0.1.0 other@0.1.0",
        "lint:msrv: compiling at 1.85.0: ext@0.2.0",
        "lint:msrv: 2 declared minimum(s) compiled at the toolchain they name",
    )
    assert said_want == said


def test_an_extension_the_plan_never_compiles_registers_nothing(
    planted: Planted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An extension on the surface but no pg check in the plan: no prepare."""
    planted.plant(PLAIN + EXTENSION, CHECK)
    planted.enter(monkeypatch)
    status = msrv_lint.main([])
    assert status == 0
    assert "prepare 17" not in planted.logged()
    assert len(planted.logged()) == UNREGISTERED_CALLS


def test_a_red_compile_says_the_rule_and_keeps_cargos_status(
    planted: Planted, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """msrv_plan.py --failed for the group, cargo's 101, and nothing after."""
    planted.plant(PLAIN, CHECK + CHECK)
    planted.env["PLANT_FAIL"] = "cargo-check"
    planted.enter(monkeypatch)
    status = msrv_lint.main([])
    captured = capfd.readouterr()
    said, err = captured.out.splitlines(), captured.err.splitlines()
    assert status == support.CARGO_FAILED
    failed = (
        "belt msrv-plan --failed 1.85.0 plain@0.1.0 other@0.1.0 --root Cargo.toml <>"
    )
    assert failed == planted.logged()[-1]
    assert len(planted.logged()) == REMEDY_CALLS
    compiling = lines("lint:msrv: compiling at 1.85.0: plain@0.1.0 other@0.1.0")
    assert compiling == said
    assert lines("lint:msrv: remedy for 1.85.0") == err


def test_an_unreadable_record_is_red_by_its_kind(
    planted: Planted, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A kind the plan never writes is a finding, and nothing compiles."""
    planted.plant(PLAIN, "verdict\tx\n" + CHECK)
    planted.enter(monkeypatch)
    status = msrv_lint.main([])
    captured = capfd.readouterr()
    said, err = captured.out.splitlines(), captured.err.splitlines()
    assert (status, said) == (1, NONE)
    unreadable = (
        "lint:msrv: Cargo.toml: unreadable plan record from belt msrv-plan: verdict"
    )
    assert lines(unreadable) == err
    assert planned() == planted.logged()


def test_a_blank_plan_line_is_skipped(
    planted: Planted, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """An empty record is no record, as the body's `read` loop had it."""
    planted.plant(PLAIN, "\n" + CHECK)
    planted.enter(monkeypatch)
    status = msrv_lint.main([])
    said = capfd.readouterr().out.splitlines()
    assert status == 0
    assert len(said) == SAID_FOR_A_BLANK


def test_no_cargo_is_red(
    planted: Planted, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Cargo absent with a Cargo.toml tracked is the vacuous-success failure."""
    (planted.bins / "cargo").unlink()
    planted.env["PATH"] = str(planted.bins)
    planted.enter(monkeypatch)
    status = msrv_lint.main([])
    captured = capfd.readouterr()
    said, err = captured.out.splitlines(), captured.err.splitlines()
    assert (status, said) == (1, NONE)
    missing = "lint:msrv: Cargo.toml present but cargo missing — pin rust in mise.toml"
    assert lines(missing) == err
    assert planted.logged() == NONE


def test_no_mise_is_red(
    planted: Planted, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Mise absent: a check that sees no pins would call a repo unpinned."""
    (planted.bins / "mise").unlink()
    planted.env["PATH"] = str(planted.bins)
    planted.enter(monkeypatch)
    status = msrv_lint.main([])
    captured = capfd.readouterr()
    said, err = captured.out.splitlines(), captured.err.splitlines()
    assert (status, said) == (1, NONE)
    missing = "lint:msrv: mise missing — the toolchain pins this reads are mise's"
    assert lines(missing) == err
    assert planted.logged() == NONE


def test_each_step_that_fails_stops_the_run_with_its_status(
    planted: Planted, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The first failure's own status, and no step after it."""
    rows = {
        "mise-rust": (3, 1),
        "cargo-fetch": (101, 2),
        "cargo-metadata": (101, 3),
        "gate-surface": (5, 4),
        "msrv-plan": (5, 5),
    }
    for step, (code, calls) in rows.items():
        planted.log.write_text("", encoding="utf-8")
        planted.env["PLANT_FAIL"] = step
        planted.enter(monkeypatch)
        status = msrv_lint.main([])
        said = capfd.readouterr().out.splitlines()
        assert (code, NONE) == (status, said)
        assert planned()[:calls] == planted.logged()


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(("prepare", 4, "prepare 17"), id="prepare"),
        pytest.param(
            ("pgrx-postgres", 5, "belt pgrx-postgres --majors 17 <[]>"),
            id="pgrx-postgres",
        ),
        pytest.param(
            (
                "mise-aqua:theseus-rs/postgresql-binaries",
                3,
                "belt pgrx-postgres --majors 17 <>",
            ),
            id="mise-aqua:theseus-rs/postgresql-binaries",
        ),
    ],
)
def test_a_failed_registration_stops_before_any_compile(
    row: tuple[str, int, str],
    planted: Planted,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The prepare's status, or the Postgres check's, and nothing compiled."""
    step, code, last = row
    planted.plant(PLAIN + EXTENSION, PG_CHECK)
    planted.log.write_text("", encoding="utf-8")
    planted.env["PLANT_FAIL"] = step
    planted.enter(monkeypatch)
    status = msrv_lint.main([])
    said = capfd.readouterr().out.splitlines()
    assert (code, NONE) == (status, said)
    assert last == planted.logged()[-1]


def test_an_extension_naming_no_major_is_red_before_the_plan(
    planted: Planted, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """pgrx-env.sh's union refuses it by name; msrv_plan.py never runs."""
    planted.plant("crate\text@0.2.0\tpgrx\tnext\text\n", PG_CHECK)
    planted.enter(monkeypatch)
    status = msrv_lint.main([])
    captured = capfd.readouterr()
    said, err = captured.out.splitlines(), captured.err.splitlines()
    assert (status, said) == (1, NONE)
    refused = "lint:msrv: ext@0.2.0 names no Postgres major: 'next'"
    assert lines(refused) == err
    assert planned()[:4] == planted.logged()


def test_a_workspace_manifest_reaches_every_cargo_call(
    planted: Planted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`workspace` at a manifest: the fetch, the graph and each compile name it."""
    planted.plant(PLAIN, CHECK)
    planted.enter(monkeypatch)
    status, checked = msrv_lint.workspace("fuzz/Cargo.toml", planted.tmp / "pins")
    assert (status, checked) == (0, 1)
    at = "--manifest-path fuzz/Cargo.toml"
    logged = planted.logged()
    assert f"cargo fetch --locked {at}" == logged[0]
    assert (
        f"cargo metadata --format-version 1 --locked {at} --all-features" == logged[1]
    )
    check = f"cargo check --locked {at} --all-features -p plain@0.1.0 -p other@0.1.0"
    assert f"{check} {{1.85.0}}" == logged[-1]


def test_every_root_runs_past_a_red_one(
    planted: Planted, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Two roots, every compile refused: both roots run, the first red kept."""
    planted.env["PLANT_FAIL"] = "cargo-check"
    planted.enter(monkeypatch, roots=("Cargo.toml", "fuzz/Cargo.toml"))
    status = msrv_lint.main([])
    said = capfd.readouterr().out.splitlines()
    assert status == support.CARGO_FAILED
    fetched = [line for line in planted.logged() if line.startswith("cargo fetch")]
    want = [FETCH, "cargo fetch --locked --manifest-path fuzz/Cargo.toml"]
    assert want == fetched
    assert len(said) == SAID_FOR_A_RED_ROOT


def test_the_count_covers_every_root(
    planted: Planted, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Two roots of one group each: one count line, of both."""
    planted.enter(monkeypatch, roots=("Cargo.toml", "fuzz/Cargo.toml"))
    status = msrv_lint.main([])
    said = capfd.readouterr().out.splitlines()
    assert status == 0
    count = "lint:msrv: 2 declared minimum(s) compiled at the toolchain they name"
    assert count == said[-1]
    assert len(said) == SAID_FOR_TWO_ROOTS


def test_a_tree_whose_roots_cannot_be_named_is_red(
    planted: Planted, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """rust_workspaces.resolved said why: status 1, and nothing run."""
    planted.enter(monkeypatch, roots=None)
    status = msrv_lint.main([])
    said = capfd.readouterr().out.splitlines()
    assert (status, said) == (1, NONE)
    assert planted.logged() == NONE


class Fuzzing(Planted):
    """A cargo-fuzz root is compiled as cargo-fuzz builds it (#2093)."""

    def compiles(self) -> list[str]:
        """Read the log's `cargo check` lines.

        Returns:
            One per compile, the root's first.

        """
        return [line for line in self.logged() if line.startswith("cargo check")]


@fixture
def fuzzing(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    belt: support.Belt,
    env: support.Env,
) -> Fuzzing:
    """Give each row its own Fuzzing, dropping what planting printed.

    Returns:
        The planted world.

    """
    world = Fuzzing(tmp_path, belt=belt, env=env)
    capfd.readouterr()
    return world


def compile_at(root: str, flags: str | None) -> str:
    """Write the log line of one compile at the plan's check.

    Returns:
        The line, with RUSTFLAGS in parentheses when it was set.

    """
    at = f"--manifest-path {root} --all-features -p plain@0.1.0 -p other@0.1.0"
    line = f"cargo check --locked {at} {{1.85.0}}"
    return line if flags is None else f"{line} ({flags})"


def test_a_marked_root_is_compiled_with_cfg_fuzzing(
    fuzzing: Fuzzing, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The marker: its compile has the cfg, the root's has no RUSTFLAGS."""
    fuzzing.manifests(MARKED)
    fuzzing.enter(monkeypatch, roots=TWO_ROOTS)
    status = msrv_lint.main([])
    assert status == 0
    want = [
        compile_at("Cargo.toml", None),
        compile_at("fuzz/Cargo.toml", "--cfg fuzzing"),
    ]
    assert want == fuzzing.compiles()
    assert "RUSTFLAGS" not in os.environ


def test_a_fuzz_directory_without_the_marker_gets_no_cfg(
    fuzzing: Fuzzing, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fuzz/Cargo.toml with no marker: neither compile has RUSTFLAGS."""
    fuzzing.enter(monkeypatch, roots=TWO_ROOTS)
    status = msrv_lint.main([])
    assert status == 0
    want = [
        compile_at("Cargo.toml", None),
        compile_at("fuzz/Cargo.toml", None),
    ]
    assert want == fuzzing.compiles()


def test_a_marked_root_keeps_the_rustflags_it_had(
    fuzzing: Fuzzing, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RUSTFLAGS=-Dwarnings set: the marked root's has the cfg appended."""
    fuzzing.manifests(MARKED)
    fuzzing.env["RUSTFLAGS"] = "-Dwarnings"
    fuzzing.enter(monkeypatch, roots=TWO_ROOTS)
    status = msrv_lint.main([])
    assert status == 0
    want = [
        compile_at("Cargo.toml", "-Dwarnings"),
        compile_at("fuzz/Cargo.toml", "-Dwarnings --cfg fuzzing"),
    ]
    assert want == fuzzing.compiles()


# The plan's records, as the compile loop reads them.
def test_a_short_record_is_padded_to_four_fields() -> None:
    """A note has two fields; the rest of a record rides in its last."""
    got = msrv_lint.records("note\tsaid\n\ncheck\t1.85.0\tdefault\ta b\tc\n")
    want = [["note", "said", "", ""], ["check", "1.85.0", "default", "a b\tc"]]
    assert want == got


class Task:
    """The task's own body: its guard, and the call that starts the helper."""

    def __init__(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path


@fixture
def task(tmp_path: Path) -> Task:
    """Give each row its own Task.

    Returns:
        The row's Task.

    """
    return Task(tmp_path)


def run_body(
    tree: Path, overrides: dict[str, str], *, env: support.Env
) -> subprocess.CompletedProcess[str]:
    """Run lint:msrv's body in a tree, as mise runs it.

    Returns:
        The finished process.

    """
    as_mise = support.task_run(TASK, body())
    return subprocess.run(
        as_mise.argv,
        cwd=tree,
        env=env() | overrides | as_mise.env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.usefixtures("task")
def test_no_cargo_toml_tracked_is_skipped(env: support.Env) -> None:
    """No tracked Cargo.toml: the skip line, green, and no helper."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        subprocess.run([GIT, "init", "-q", tmp], check=True, env=env())
        overrides = {"ORG_BELT_DIR": str(BELT), "ORG_PYTHON": "/nonexistent"}
        done = run_body(tree, overrides, env=env)
    assert (done.returncode, done.stdout) == (
        0,
        "lint:msrv: no Cargo.toml tracked, skipped\n",
    )


@pytest.mark.usefixtures("task")
def test_the_body_starts_the_helper_which_exits_with_its_status(
    env: support.Env,
) -> None:
    """A tracked Cargo.toml and no cargo: the helper's finding and its 1."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        subprocess.run([GIT, "init", "-q", tmp], check=True, env=env())
        (tree / "Cargo.toml").write_text("", encoding="utf-8")
        subprocess.run([GIT, "-C", tmp, "add", "Cargo.toml"], check=True, env=env())
        bins = tree.parent / f"{tree.name}-bin"
        bins.mkdir()
        for tool in ("git", "bash", "dirname", "cat", "mktemp", "rm"):
            (bins / tool).symlink_to(support.tool(tool))
        overrides = {
            "PATH": str(bins),
            "ORG_BELT_DIR": str(BELT),
            "ORG_PYTHON": sys.executable,
            "ORG_FINDINGS": "",
        }
        done = run_body(tree, overrides, env=env)
    want = f"lint:msrv: {rust_task.NO_CARGO}\n"
    assert (1, "", want) == (done.returncode, done.stdout, done.stderr)
