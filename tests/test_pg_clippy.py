"""lint:pg-clippy's body, one row per branch with its message (#1911).

The task's body after its guard is mise/pg_clippy.py. Each row calls
its `main` in this process, in a planted git tree, against a planted
belt whose clippy ladder and pgrx provisioning are the stubs
test_rust_lint.py plants for rust_lint.py, whose plumbing this helper
shares through mise/rust_task.py. cargo, rustup and mise are stubs on
PATH that log what they were asked; the workspace's cargo metadata is
read by the belt's real gate_surface.py, and its extension's major
derived by the real pgrx-env.sh. Every row asserts the whole log, so a
row cannot pass by stopping early.
"""

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import pg_clippy, pgrx_postgres, rust_workspaces
from belt.typed_pytest import fixture
from tests import support
from tests import test_rust_lint as planted

if TYPE_CHECKING:
    from pathlib import Path

BELT = support.MISE
GIT = support.tool("git")
TASK = "lint:pg-clippy"
# The stub cargo answers the workspace derivation as test_rust_lint.py's
# stubs do (#2004), then logs each call with the marks the prepare and
# the major's subshell exported, and the clippy config directory it saw.
# A manifest under fuzz/ reads STUB_FUZZ_METADATA.
CARGO = (
    planted.LOCATE
    + """\
marks="${STUB_PREPARED:-} ${STUB_MAJOR:-} ${CLIPPY_CONF_DIR:-}"
printf 'cargo %s [%s]\\n' "$*" "${marks}" >> "${STUB_LOG}"
case "$1" in
  fetch) exit "${STUB_FETCH_STATUS:-0}" ;;
  metadata)
    if [[ ${manifest} == fuzz/* ]]; then
      cat "${STUB_FUZZ_METADATA}"
    else
      cat "${STUB_METADATA}"
    fi
    exit "${STUB_METADATA_STATUS:-0}"
    ;;
  clippy) [[ $3 != "${STUB_RED:-none}@"* ]] || exit 101 ;;
  *) exit 99 ;;
esac
"""
)
# test_rust_lint.py's provisioning stub, and a derivation that names a
# major no build could take, which gate_surface.py never writes.
PGRX_ENV = (
    planted.STUB_PGRX_ENV
    + """
if [[ -n ${STUB_BAD_MAJOR:-} ]]; then
  org_pgrx_highest() { cat > /dev/null; printf 'ext@0.1.0\\tpg17\\n'; }
fi
"""
)
TWO = planted.workspace(
    planted.member("a"),
    planted.member("ext", pgrx=True),
    planted.member("other", pgrx=True),
)
AT = "--manifest-path Cargo.toml"
# The statuses this file's rows plant, each its own; the ladder's and the
# prepare's are test_rust_lint.py's stubs'.
FETCH_FAILED = 7
METADATA_FAILED = 4
PIN_MISSING = 6
FETCH = f"cargo fetch --locked {AT} [  ]"
METADATA = f"cargo metadata --no-deps --format-version 1 --locked {AT} [  ]"
REGISTER = ["prepare 17", "mise ls aqua:theseus-rs/postgresql-binaries --json"]
LADDER = "-- -D warnings"
OWN = (
    "--no-deps --all-targets --locked --keep-going --no-default-features"
    " --features pg17"
)


def clippy(spec: str, belt: Path, root: str = "Cargo.toml") -> str:
    """Write the log line of one extension's clippy at pg17.

    Returns:
        What the stub cargo logs.

    """
    at = f"--manifest-path {root}"
    return f"cargo clippy -p {spec} {at} {OWN} {LADDER} [yes pg17 {belt}]"


class Planted:
    """A planted tree, a planted belt and the stubs."""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        belt: support.Belt,
        env: support.Env,
    ) -> None:
        """Plant the tree, the belt and the stubs."""
        self.plant_belt = belt
        self.make_env = env
        # `belt pgrx-postgres`, which the registration calls in process.
        monkeypatch.setattr(pgrx_postgres, "main", planted.stub_pin)
        tmp = tmp_path
        self.tree = tmp / "repo"
        self.bins = tmp / "bin"
        self.bins.mkdir()
        for name, text in (
            ("cargo", CARGO),
            ("rustup", planted.STUB_RUSTUP),
            ("mise", planted.STUB_MISE),
        ):
            (self.bins / name).write_text(text, encoding="utf-8")
            (self.bins / name).chmod(0o755)
        (self.bins / "bash").symlink_to(planted.BASH)
        self.belt = self.plant_belt(
            tmp / "belt",
            stubs={
                "clippy-ladder.sh": planted.STUB_LADDER,
                "pgrx-env.sh": PGRX_ENV,
            },
        )
        self.log = tmp / "log"
        self.metadata = tmp / "metadata.json"
        self.metadata.write_text(TWO, encoding="utf-8")
        self.fuzz_metadata = tmp / "fuzz-metadata.json"
        self.fuzz_metadata.write_text(planted.FUZZ, encoding="utf-8")
        self.env = self.make_env(
            tmp / "home",
            PATH=os.pathsep.join((str(self.bins), "/usr/bin", "/bin")),
            MISE_TASK_NAME=TASK,
            ORG_FINDINGS="",
            ORG_PYTHON=sys.executable,
            CLIPPY_EXCLUDE="other",
            STUB_LOG=str(self.log),
            STUB_METADATA=str(self.metadata),
            STUB_FUZZ_METADATA=str(self.fuzz_metadata),
            STUB_REAL_BELT=str(BELT),
        )
        (self.tree / "src").mkdir(parents=True)
        (self.tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        subprocess.run([GIT, "init", "-q"], cwd=self.tree, env=self.env, check=True)
        self.track()

    def track(self) -> None:
        """Track every file the tree holds, as the derivation reads them."""
        subprocess.run([GIT, "add", "."], cwd=self.tree, env=self.env, check=True)

    def enter(self, monkeypatch: pytest.MonkeyPatch, **stub: str) -> None:
        """Stand in the tree from the planted belt, the row's stubs set.

        The derivation resolves cargo when it is imported; here, the
        stub on the row's PATH is the cargo it asks.
        """
        monkeypatch.setattr(rust_workspaces, "CARGO", str(self.bins / "cargo"))
        for name, value in (self.env | {"ORG_BELT_DIR": str(self.belt)} | stub).items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(self.tree)

    def logged(self) -> list[str]:
        """Read what the stubs were asked.

        Returns:
            One line per call.

        """
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    def assert_logged(self, *lines: str) -> None:
        """Assert the stubs were asked exactly these, in order."""
        want = list(lines)
        assert want == self.logged()


@fixture
def planted_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    belt: support.Belt,
    env: support.Env,
) -> Planted:
    """Give each row its own Planted.

    Returns:
        The row's Planted.

    """
    return Planted(tmp_path, monkeypatch, belt=belt, env=env)


def finding(*lines: str) -> str:
    """Say a finding as the task writes it to stderr.

    Returns:
        Its head line, then each detail line indented.

    """
    head, *detail = lines
    return f"{TASK}: {head}\n" + "".join(f"  {line}\n" for line in detail)


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param(
            ["lint"],
            "belt pg-clippy: error: unrecognized arguments: lint",
            id='["lint"]',
        ),
    ],
)
def test_an_argument_is_a_usage_error(
    argv: list[str], error: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """A usage error: argparse's exit 2 and its error.

    The task passes none; anything else is a caller's mistake.
    """
    with pytest.raises(SystemExit) as ended:
        pg_clippy.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt pg-clippy "), err
    assert err.splitlines()[-1] == error


def test_cargo_missing_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No cargo on PATH: the toolchain is broken, never a skip."""
    (planted_state.bins / "cargo").unlink()
    planted_state.enter(monkeypatch)
    status = pg_clippy.main([])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        finding("Cargo.toml present but cargo missing — pin rust in mise.toml") in err
    )
    planted_state.assert_logged()


def test_a_red_fetch_keeps_its_status(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`cargo fetch --locked` fails: nothing is derived after it."""
    planted_state.enter(monkeypatch, STUB_FETCH_STATUS=str(FETCH_FAILED))
    status = pg_clippy.main([])
    out = capfd.readouterr().out
    assert status == FETCH_FAILED
    planted_state.assert_logged(FETCH)
    assert not out


def test_a_red_metadata_is_never_a_workspace_with_no_extension(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing cargo metadata gives its status, not the skip."""
    planted_state.enter(monkeypatch, STUB_METADATA_STATUS=str(METADATA_FAILED))
    status = pg_clippy.main([])
    out = capfd.readouterr().out
    assert status == METADATA_FAILED
    planted_state.assert_logged(FETCH, METADATA)
    assert "skipped" not in out


def test_a_red_surface_is_never_a_workspace_with_no_extension(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gate_surface.py refuses the metadata: its status, not the skip."""
    planted_state.metadata.write_text("not json", encoding="utf-8")
    planted_state.enter(monkeypatch)
    status = pg_clippy.main([])
    out, err = capfd.readouterr()
    assert status == 1
    assert err.startswith("lint:pg-clippy: cargo metadata did not give JSON\n"), err
    planted_state.assert_logged(FETCH, METADATA)
    assert not out


def test_a_major_that_is_not_a_number_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pgrx-env.sh's union refuses it by name (#1795)."""
    planted_state.enter(monkeypatch, STUB_BAD_MAJOR="1")
    status = pg_clippy.main([])
    out, err = capfd.readouterr()
    assert status == 1
    assert err == "lint:pg-clippy: ext@0.1.0 names no Postgres major: 'pg17'\n"
    planted_state.assert_logged(FETCH, METADATA)
    assert not out


def test_no_extension_skips_clean(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workspace of plain crates has nothing for this task."""
    planted_state.metadata.write_text(
        planted.workspace(planted.member("a")), encoding="utf-8"
    )
    planted_state.enter(monkeypatch)
    status = pg_clippy.main([])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == "lint:pg-clippy: no pgrx extension tracked, skipped\n"
    planted_state.assert_logged(FETCH, METADATA)


def test_clippy_missing_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rustup that lists no clippy: the remedy names the pin."""
    planted_state.enter(monkeypatch, STUB_COMPONENTS="rustfmt-host\\n")
    status = pg_clippy.main([])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        finding(
            "clippy component missing — declare it with the rust pin in mise.toml:",
            'rust = { version = "1.xx.x", components = "clippy,rustfmt,llvm-tools" }',
        )
        in err
    )
    planted_state.assert_logged(FETCH, METADATA)


def test_a_red_ladder_keeps_its_status(
    planted_state: Planted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """clippy-ladder.sh fails: nothing is registered or linted."""
    planted_state.enter(monkeypatch, STUB_LADDER_FAILS="1")
    status = pg_clippy.main([])
    assert status == planted.LADDER_FAILED
    planted_state.assert_logged(FETCH, METADATA)


def test_a_red_prepare_keeps_its_status(
    planted_state: Planted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SDK or an ICU check fails: nothing is linted (#1774)."""
    planted_state.enter(monkeypatch, STUB_PREPARE_FAILS="1")
    status = pg_clippy.main([])
    assert status == planted.PREPARE_FAILED
    planted_state.assert_logged(FETCH, METADATA, "prepare 17")


def test_a_missing_postgres_pin_keeps_its_status(
    planted_state: Planted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pgrx_postgres.py reds a declared major with no pin (#1940)."""
    planted_state.enter(monkeypatch, STUB_PIN_STATUS=str(PIN_MISSING))
    status = pg_clippy.main([])
    assert status == PIN_MISSING
    planted_state.assert_logged(FETCH, METADATA, *REGISTER)


def test_a_red_extension_keeps_clippys_status(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused extension gives cargo's 101, and no summary."""
    planted_state.enter(monkeypatch, STUB_RED="ext")
    status = pg_clippy.main([])
    out = capfd.readouterr().out
    assert status == support.CARGO_FAILED
    planted_state.assert_logged(
        FETCH, METADATA, *REGISTER, clippy("ext@0.1.0", planted_state.belt)
    )
    assert not out


def test_a_clean_extension_says_what_was_linted(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At its highest major, with the canon's clippy.toml, under the ladder.

    `other` is excluded by CLIPPY_EXCLUDE and not linted; `a` is no
    extension.
    """
    planted_state.enter(monkeypatch)
    status = pg_clippy.main([])
    out, err = capfd.readouterr()
    assert status == 0, err
    planted_state.assert_logged(
        FETCH, METADATA, *REGISTER, clippy("ext@0.1.0", planted_state.belt)
    )
    said = [
        "lint:pg-clippy: Cargo.toml: 1 extension(s) linted: ext(pg17)",
        (
            "lint:pg-clippy: clean at every group"
            " (restriction minus 0 named contradictions)"
        ),
    ]
    assert said == out.splitlines()


def test_the_manifest_path_reaches_every_cargo_command(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fetch, metadata and clippy each name the workspace they read."""
    for name, value in (
        planted_state.env | {"ORG_BELT_DIR": str(planted_state.belt)}
    ).items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(planted_state.tree)
    status = pg_clippy.workspace(
        str(planted_state.bins / "cargo"), "crates/Cargo.toml"
    ).status
    _out, err = capfd.readouterr()
    assert status == 0, err
    tail = "--manifest-path crates/Cargo.toml"
    planted_state.assert_logged(
        f"cargo fetch --locked {tail} [  ]",
        f"cargo metadata --no-deps --format-version 1 --locked {tail} [  ]",
        *REGISTER,
        (
            f"cargo clippy -p ext@0.1.0 {tail} {OWN} {LADDER} [yes pg17 "
            f"{planted_state.belt}]"
        ),
    )


def test_an_extension_in_a_second_root_is_linted_there(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pgrx extension in fuzz/Cargo.toml, its own root: linted at it.

    The root workspace holds a plain crate only; before #2004 the
    task read the root workspace alone and skipped clean.
    """
    planted_state.metadata.write_text(
        planted.workspace(planted.member("a")), encoding="utf-8"
    )
    planted_state.fuzz_metadata.write_text(
        planted.workspace(planted.member("ext", pgrx=True)), encoding="utf-8"
    )
    (planted_state.tree / "fuzz").mkdir()
    (planted_state.tree / "fuzz" / "Cargo.toml").write_text(
        "[workspace]\n", encoding="utf-8"
    )
    planted_state.track()
    planted_state.enter(monkeypatch)
    status = pg_clippy.main([])
    out, err = capfd.readouterr()
    assert status == 0, err
    fuzz = "--manifest-path fuzz/Cargo.toml"
    planted_state.assert_logged(
        FETCH,
        METADATA,
        f"cargo fetch --locked {fuzz} [  ]",
        f"cargo metadata --no-deps --format-version 1 --locked {fuzz} [  ]",
        *REGISTER,
        clippy("ext@0.1.0", planted_state.belt, "fuzz/Cargo.toml"),
    )
    said = [
        "lint:pg-clippy: fuzz/Cargo.toml: 1 extension(s) linted: ext(pg17)",
        (
            "lint:pg-clippy: clean at every group"
            " (restriction minus 0 named contradictions)"
        ),
    ]
    assert said == out.splitlines()
