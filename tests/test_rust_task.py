"""Table tests for mise/rust_task.py, the Rust task helpers' shared steps.

One row per branch, in this process. The belt is a directory of this
file's own scripts, named by ORG_BELT_DIR: a `pgrx-env.sh` whose
functions answer from the row's variables, so a row says what each
call was given and what it returned without a Mac or a Postgres.
`mise` is a stub on PATH and ORG_PYTHON a stub interpreter; both log
what they were asked.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import gate_surface, pgrx_postgres, rust_task
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# Statuses the planted commands exit with, each its own: a command's,
# the contradiction count's, and a compile under at_major's.
FAILED = 3
COUNT_FAILED = 4
FAILED_AT_MAJOR = 7

ALL: list[str] = ["--all-features"]
PREPARED: list[str] = ["prepare 17"]
NOTHING: list[str] = []

PGRX_ENV = r"""
org_pgrx_highest() {
  cat > "${PLANT_DIR}/highest-in"
  printf '%b' "${PLANT_HIGHEST:-}"
  return "${PLANT_HIGHEST_STATUS:-0}"
}
org_pgrx_union() {
  cat > "${PLANT_DIR}/union-in"
  printf '%s\n' "${PLANT_UNION:-}"
  return "${PLANT_UNION_STATUS:-0}"
}
org_pgrx_darwin_prepare() {
  printf 'prepare %s\n' "$*" >> "${PLANT_DIR}/log"
  [[ ${PLANT_PREPARE_STATUS:-0} == 0 ]] || return "${PLANT_PREPARE_STATUS}"
  export PLANT_SDK=sdk
  export PLANT_KEPT="${PLANT_KEPT}"
}
org_pgrx_major() {
  local major=$1
  shift
  ( export PLANT_MAJOR="${major}"; "$@" )
}
"""
MISE_STUB = r"""#!/usr/bin/env bash
printf 'mise %s\n' "$*" >> "${PLANT_DIR}/log"
echo '[installed]'
exit "${PLANT_MISE_STATUS:-0}"
"""
LADDER = r"""
org_clippy_ladder() { echo "-D warnings  -W x"; return "${PLANT_LADDER_STATUS:-0}"; }
org_clippy_contradiction_count() {
  echo " 2 "
  return "${PLANT_COUNT_STATUS:-0}"
}
"""
RUSTUP_STUB = r"""#!/usr/bin/env bash
[[ $* == "component list --installed" ]] || exit 99
printf 'clippy-host\nrustfmt-host\n'
exit "${PLANT_RUSTUP_STATUS:-0}"
"""
CARGO_STUB = r"""#!/usr/bin/env bash
printf 'cargo %s\n' "$*" >> "${PLANT_DIR}/log"
echo '{"meta": 1}'
exit "${PLANT_CARGO_STATUS:-0}"
"""


def stand_in(command: str) -> Callable[[list[str]], int]:
    """Stand in for a belt command `rust_task` calls in process.

    It logs `belt <command> <argv> <stdin>` beside the planted stubs and
    answers PLANT_PYTHON_STATUS.

    Returns:
        The command's `main`.

    """

    def main(argv: list[str]) -> int:
        line = f"belt {command} {' '.join(argv)} <{sys.stdin.read().rstrip()}>\n"
        with Path(os.environ["PLANT_DIR"], "log").open("a", encoding="utf-8") as log:
            log.write(line)
        return int(os.environ.get("PLANT_PYTHON_STATUS", "0"))

    return main


# Prints what a compile under at_major would see.
SHOW = 'echo "major=${PLANT_MAJOR-unset} sdk=${PLANT_SDK-unset}"; exit "${1:-0}"'


class Planted:
    """A planted belt, the stubs, and the row's environment."""

    def __init__(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, env: support.Env
    ) -> None:
        """Plant the belt, the stubs and the row's environment."""
        self.make_env = env
        monkeypatch.setattr(gate_surface, "main", stand_in("gate-surface"))
        monkeypatch.setattr(pgrx_postgres, "main", stand_in("pgrx-postgres"))
        self.tmp = tmp_path
        self.belt = self.tmp / "belt"
        self.belt.mkdir()
        (self.belt / "pgrx-env.sh").write_text(PGRX_ENV, encoding="utf-8")
        (self.belt / "clippy-ladder.sh").write_text(LADDER, encoding="utf-8")
        bins = self.tmp / "bin"
        bins.mkdir()
        stubs = {
            "mise": MISE_STUB,
            "rustup": RUSTUP_STUB,
            "cargo": CARGO_STUB,
        }
        for name, text in stubs.items():
            stub = bins / name
            stub.write_text(text, encoding="utf-8")
            stub.chmod(0o755)
        (self.tmp / "log").touch()
        env = self.make_env()
        for name in list(os.environ):
            monkeypatch.delenv(name)
        for name, value in (
            env
            | {
                "PATH": f"{bins}:{env['PATH']}",
                "ORG_BELT_DIR": str(self.belt),
                "PLANT_DIR": str(self.tmp),
                "PLANT_KEPT": "same",
            }
        ).items():
            monkeypatch.setenv(name, value)

    def logged(self) -> list[str]:
        """Read the stubs' log, the belt masked.

        Returns:
            One line per call, in order.

        """
        text = (self.tmp / "log").read_text(encoding="utf-8")
        return text.replace(str(self.belt), "<belt>").splitlines()


@fixture
def planted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: support.Env
) -> Planted:
    """Give each row its own Planted.

    Returns:
        The row's Planted.

    """
    return Planted(tmp_path, monkeypatch, env=env)


def test_the_belt_is_org_belt_dir_else_the_trees_own(planted: Planted) -> None:
    """ORG_BELT_DIR when set; the package's tree's mise/ unset or empty."""
    assert planted.belt == rust_task.belt()
    os.environ["ORG_BELT_DIR"] = ""
    assert rust_task.belt() == support.MISE
    del os.environ["ORG_BELT_DIR"]
    assert rust_task.belt() == support.MISE


@pytest.mark.usefixtures("planted")
def test_a_specs_name_is_before_its_last_at() -> None:
    """`${spec%@*}`: the last `@` only, and a spec with none is whole."""
    rows = {"plain@0.1.0": "plain", "odd@1@2": "odd@1", "bare": "bare", "@1": ""}
    for spec, name in rows.items():
        assert name == rust_task.name_of(spec)


@pytest.mark.usefixtures("planted")
def test_features_are_clippy_features_else_all() -> None:
    """CLIPPY_FEATURES split into words; --all-features unset or empty."""
    assert rust_task.features() == ALL
    os.environ["CLIPPY_FEATURES"] = ""
    assert rust_task.features() == ALL
    os.environ["CLIPPY_FEATURES"] = "--features  a,b\t--locked"
    words: list[str] = ["--features", "a,b", "--locked"]
    assert words == rust_task.features()


@pytest.mark.usefixtures("planted")
def test_run_returns_the_status_and_passes_the_environment() -> None:
    """The command's own status, under the environment it was given."""
    bash = support.tool("bash")
    assert rust_task.run([bash, "-c", f"exit {FAILED}"]) == FAILED
    script = "[[ $PLANT_X == y ]] && [[ -z ${PLANT_DIR-} ]]"
    assert rust_task.run([bash, "-c", script], {"PLANT_X": "y"}) == 0


@pytest.mark.usefixtures("planted")
def test_output_reads_stdout_and_feeds_stdin() -> None:
    """The status and what it printed, the input on its stdin."""
    bash = support.tool("bash")
    assert rust_task.output([bash, "-c", 'echo "got:$(cat)"; exit 4'], "in") == (
        4,
        "got:in\n",
    )


def test_installed_components_are_rustups_listing(planted: Planted) -> None:
    """One per line; none when rustup fails or is absent."""
    want: list[str] = ["clippy-host", "rustfmt-host"]
    assert want == rust_task.installed_components()
    os.environ["PLANT_RUSTUP_STATUS"] = "1"
    assert rust_task.installed_components() == NOTHING
    (planted.tmp / "bin" / "rustup").unlink()
    os.environ["PATH"] = str(planted.tmp / "bin")
    assert rust_task.installed_components() == NOTHING


@pytest.mark.usefixtures("planted")
def test_a_component_is_had_when_a_line_starts_with_its_name() -> None:
    """`grep -q "^<name>"`: a prefix of a line, never a middle."""
    listing = ["clippy-host", "rustfmt-host"]
    assert rust_task.has(listing, "rustfmt")
    assert not rust_task.has(listing, "host")
    assert not rust_task.has([], "clippy")


def test_a_belt_call_sources_the_script_and_calls_the_function(
    planted: Planted,
) -> None:
    """The function's status and output; stdin reaches it."""
    os.environ |= {"PLANT_UNION": "16,17", "PLANT_UNION_STATUS": "2"}
    got = rust_task.belt_call(rust_task.pgrx_env(), "org_pgrx_union", stdin="lines")
    assert got == (2, "16,17\n")
    assert (planted.tmp / "union-in").read_text(encoding="utf-8") == "lines"


def test_exports_are_what_the_function_set_or_changed(planted: Planted) -> None:
    """New and changed variables; not bash's own, not those unchanged."""
    got = rust_task.belt_exports(rust_task.pgrx_env(), "org_pgrx_darwin_prepare", "17")
    assert got == (0, {"PLANT_SDK": "sdk"})
    assert planted.logged() == PREPARED


@pytest.mark.usefixtures("planted")
def test_a_failed_export_returns_its_status_and_nothing() -> None:
    """The function's status, and no variable taken from it."""
    os.environ["PLANT_PREPARE_STATUS"] = "6"
    got = rust_task.belt_exports(rust_task.pgrx_env(), "org_pgrx_darwin_prepare", "17")
    assert got == (6, {})


@pytest.mark.usefixtures("planted")
def test_the_ladder_is_its_flags_and_its_count() -> None:
    """The flags split into words, the count stripped."""
    flags: list[str] = ["-D", "warnings", "-W", "x"]
    assert (0, flags, "2") == rust_task.ladder()


@pytest.mark.usefixtures("planted")
def test_a_failed_ladder_stops_before_the_count() -> None:
    """The ladder's status, no flags; a failed count is its status."""
    os.environ["PLANT_LADDER_STATUS"] = "3"
    assert rust_task.ladder() == (3, NOTHING, "")
    os.environ |= {"PLANT_LADDER_STATUS": "0", "PLANT_COUNT_STATUS": str(COUNT_FAILED)}
    status, _, _ = rust_task.ladder()
    assert status == COUNT_FAILED


def test_the_surface_is_gate_surfaces_reading_of_cargos_metadata(
    planted: Planted,
) -> None:
    """No-deps metadata on the helper's stdin, CLIPPY_EXCLUDE passed."""
    os.environ["CLIPPY_EXCLUDE"] = "held"
    assert rust_task.surface(str(planted.tmp / "bin" / "cargo")) == (0, "")
    want = [
        "cargo metadata --no-deps --format-version 1 --locked",
        'belt gate-surface --exclude held <{"meta": 1}>',
    ]
    assert want == planted.logged()


def test_the_surfaces_status_is_the_helpers_else_cargos(planted: Planted) -> None:
    """gate-surface's status first, as pipefail read the pipe."""
    cargo = str(planted.tmp / "bin" / "cargo")
    rows = {("0", "5"): 5, ("3", "0"): 3, ("3", "5"): 5}
    for (meta, helper), want in rows.items():
        os.environ |= {
            "PLANT_CARGO_STATUS": meta,
            "PLANT_PYTHON_STATUS": helper,
        }
        status, _ = rust_task.surface(cargo)
        assert want == status


def test_pairs_are_each_extension_and_its_major(planted: Planted) -> None:
    """Each `spec<TAB>major` line, a blank or one-field line dropped."""
    os.environ |= {
        "PLANT_HIGHEST": "a@1\\t17\\n\\nb@2\\t16\\nlone\\n\\tx\\n",
        "PLANT_UNION": "16,17",
    }
    got = rust_task.pgrx_pairs("surface")
    assert got == (0, [["a@1", "17"], ["b@2", "16"]], "16,17")
    assert (planted.tmp / "highest-in").read_text(encoding="utf-8") == "surface"
    union_in = (planted.tmp / "union-in").read_text(encoding="utf-8")
    assert union_in == "a@1\t17\n\nb@2\t16\nlone\n\tx\n"


def test_a_failed_highest_stops_before_the_union(planted: Planted) -> None:
    """The derivation's status, no pairs, and the union never asked."""
    os.environ |= {"PLANT_HIGHEST": "a@1\\t17\\n", "PLANT_HIGHEST_STATUS": "1"}
    assert rust_task.pgrx_pairs("surface") == (1, [], "")
    assert not (planted.tmp / "union-in").exists()


@pytest.mark.usefixtures("planted")
def test_a_failed_union_is_its_status() -> None:
    """The union's status rides back, so its caller stops."""
    os.environ |= {"PLANT_HIGHEST": "a@1\\tnext\\n", "PLANT_UNION_STATUS": "1"}
    status, _, _ = rust_task.pgrx_pairs("surface")
    assert status == 1


def test_registering_prepares_then_checks_every_majors_postgres(
    planted: Planted,
) -> None:
    """The prepare's exports, the listing on pgrx-postgres's stdin."""
    assert rust_task.register("16,17") == (0, {"PLANT_SDK": "sdk"})
    want = [
        "prepare 16,17",
        "mise ls aqua:theseus-rs/postgresql-binaries --json",
        "belt pgrx-postgres --majors 16,17 <[installed]>",
    ]
    assert want == planted.logged()


def test_a_failed_prepare_registers_nothing(planted: Planted) -> None:
    """The prepare's status, no exports, and no Postgres looked at."""
    os.environ["PLANT_PREPARE_STATUS"] = "4"
    assert rust_task.register("17") == (4, {})
    assert planted.logged() == PREPARED


@pytest.mark.usefixtures("planted")
def test_registrations_status_is_the_check_else_the_listing() -> None:
    """pgrx-postgres's status first, as pipefail read the pipe."""
    rows = {("0", "5"): 5, ("3", "0"): 3, ("3", "5"): 5}
    for (listing, check), want in rows.items():
        os.environ |= {
            "PLANT_MISE_STATUS": listing,
            "PLANT_PYTHON_STATUS": check,
        }
        status, _ = rust_task.register("17")
        assert want == status


def test_a_compile_runs_at_its_major_with_what_was_prepared(planted: Planted) -> None:
    """org_pgrx_major given the major; the prepared variables reach it."""
    bash = support.tool("bash")
    out = planted.tmp / "out"
    argv = [bash, "-c", f'{{ {SHOW}; }} > "{out}"', "show", str(FAILED_AT_MAJOR)]
    assert rust_task.at_major("17", argv, {"PLANT_SDK": "sdk"}) == FAILED_AT_MAJOR
    assert out.read_text(encoding="utf-8") == "major=17 sdk=sdk\n"
    assert rust_task.at_major("", argv[:-1], {}) == 0
    assert out.read_text(encoding="utf-8") == "major= sdk=unset\n"
