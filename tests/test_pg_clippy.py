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
from pathlib import Path

import pytest

from belt import pg_clippy, pgrx_postgres, rust_workspaces
from tests import support
from tests import test_rust_lint as planted

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


@pytest.mark.usefixtures("_set_up_test_planted")
class TestPlanted:
    """A planted tree, a planted belt and the stubs."""

    @pytest.fixture
    def _set_up_test_planted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plant the tree, the belt and the stubs."""
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
        self.belt = support.belt(
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
        self.env = support.env(
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

    def call(
        self, *argv: str, capfd: pytest.CaptureFixture[str], **stub: str
    ) -> tuple[int, str, str]:
        """Call pg_clippy.py's main in the tree, from the planted belt.

        Returns:
            Its status and what it wrote.

        """
        # The derivation resolves cargo when it is imported; here, the
        # stub on the row's PATH is the cargo it asks.
        with pytest.MonkeyPatch.context() as patched:
            patched.setattr(rust_workspaces, "CARGO", str(self.bins / "cargo"))
            with pytest.MonkeyPatch.context() as patched:
                for name, value in (
                    self.env | {"ORG_BELT_DIR": str(self.belt)} | stub
                ).items():
                    patched.setenv(name, value)
                patched.chdir(self.tree)
                status = pg_clippy.main(list(argv))
            out, err = capfd.readouterr()
            return status, out, err

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

    def assert_finding(self, done: support.Called, status: int, *lines: str) -> None:
        """Assert the status, and the finding's lines in order on stderr."""
        assert status == done.returncode, done.stdout + done.stderr
        head, *detail = lines
        want = f"{TASK}: {head}\n" + "".join(f"  {line}\n" for line in detail)
        assert want in done.stderr


class TestBranches(TestPlanted):
    """Each branch of lint:pg-clippy's body, its message asserted."""

    def test_an_argument_is_a_usage_error(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The task passes none; anything else is a caller's mistake."""
        status, out, err = self.call("lint", capfd=capfd)
        self.assert_finding(done, 2, "usage: belt pg-clippy")
        self.assert_logged()

    def test_cargo_missing_is_red(self, capfd: pytest.CaptureFixture[str]) -> None:
        """No cargo on PATH: the toolchain is broken, never a skip."""
        (self.bins / "cargo").unlink()
        status, out, err = self.call(capfd=capfd)
        self.assert_finding(
            done, 1, "Cargo.toml present but cargo missing — pin rust in mise.toml"
        )
        self.assert_logged()

    def test_a_red_fetch_keeps_its_status(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """`cargo fetch --locked` fails: nothing is derived after it."""
        status, out, err = self.call(STUB_FETCH_STATUS="7", capfd=capfd)
        assert status == 7
        self.assert_logged(FETCH)
        assert out == ""

    def test_a_red_metadata_is_never_a_workspace_with_no_extension(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A failing cargo metadata gives its status, not the skip."""
        status, out, err = self.call(STUB_METADATA_STATUS="4", capfd=capfd)
        assert status == 4
        self.assert_logged(FETCH, METADATA)
        assert "skipped" not in out

    def test_a_red_surface_is_never_a_workspace_with_no_extension(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """gate_surface.py refuses the metadata: its status, not the skip."""
        self.metadata.write_text("not json", encoding="utf-8")
        status, out, err = self.call(capfd=capfd)
        assert status == 1
        assert err.startswith("lint:pg-clippy: cargo metadata did not give JSON\n"), err
        self.assert_logged(FETCH, METADATA)
        assert out == ""

    def test_a_major_that_is_not_a_number_is_red(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """pgrx-env.sh's union refuses it by name (#1795)."""
        status, out, err = self.call(STUB_BAD_MAJOR="1", capfd=capfd)
        assert status == 1
        assert err == "lint:pg-clippy: ext@0.1.0 names no Postgres major: 'pg17'\n"
        self.assert_logged(FETCH, METADATA)
        assert out == ""

    def test_no_extension_skips_clean(self, capfd: pytest.CaptureFixture[str]) -> None:
        """A workspace of plain crates has nothing for this task."""
        self.metadata.write_text(
            planted.workspace(planted.member("a")), encoding="utf-8"
        )
        status, out, err = self.call(capfd=capfd)
        assert status == 0, err
        assert out == "lint:pg-clippy: no pgrx extension tracked, skipped\n"
        self.assert_logged(FETCH, METADATA)

    def test_clippy_missing_is_red(self, capfd: pytest.CaptureFixture[str]) -> None:
        """A rustup that lists no clippy: the remedy names the pin."""
        status, out, err = self.call(STUB_COMPONENTS="rustfmt-host\\n", capfd=capfd)
        self.assert_finding(
            done,
            1,
            "clippy component missing — declare it with the rust pin in mise.toml:",
            'rust = { version = "1.xx.x", components = "clippy,rustfmt,llvm-tools" }',
        )
        self.assert_logged(FETCH, METADATA)

    def test_a_red_ladder_keeps_its_status(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """clippy-ladder.sh fails: nothing is registered or linted."""
        status, out, err = self.call(STUB_LADDER_FAILS="1", capfd=capfd)
        assert status == 3
        self.assert_logged(FETCH, METADATA)

    def test_a_red_prepare_keeps_its_status(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The SDK or an ICU check fails: nothing is linted (#1774)."""
        status, out, err = self.call(STUB_PREPARE_FAILS="1", capfd=capfd)
        assert status == 5
        self.assert_logged(FETCH, METADATA, "prepare 17")

    def test_a_missing_postgres_pin_keeps_its_status(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """pgrx_postgres.py reds a declared major with no pin (#1940)."""
        status, out, err = self.call(STUB_PIN_STATUS="6", capfd=capfd)
        assert status == 6
        self.assert_logged(FETCH, METADATA, *REGISTER)

    def test_a_red_extension_keeps_clippys_status(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A refused extension gives cargo's 101, and no summary."""
        status, out, err = self.call(STUB_RED="ext", capfd=capfd)
        assert status == 101
        self.assert_logged(FETCH, METADATA, *REGISTER, clippy("ext@0.1.0", self.belt))
        assert out == ""

    def test_a_clean_extension_says_what_was_linted(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """At its highest major, with the canon's clippy.toml, under the ladder.

        `other` is excluded by CLIPPY_EXCLUDE and not linted; `a` is no
        extension.
        """
        status, out, err = self.call(capfd=capfd)
        assert status == 0, err
        self.assert_logged(FETCH, METADATA, *REGISTER, clippy("ext@0.1.0", self.belt))
        said = [
            "lint:pg-clippy: Cargo.toml: 1 extension(s) linted: ext(pg17)",
            (
                "lint:pg-clippy: clean at every group"
                " (restriction minus 0 named contradictions)"
            ),
        ]
        assert said == out.splitlines()


class TestWorkspace(TestPlanted):
    """Every workspace the tree tracks is linted (#2004)."""

    def test_the_manifest_path_reaches_every_cargo_command(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Fetch, metadata and clippy each name the workspace they read."""
        with pytest.MonkeyPatch.context() as patched:
            for name, value in (self.env | {"ORG_BELT_DIR": str(self.belt)}).items():
                patched.setenv(name, value)
            patched.chdir(self.tree)
            status = pg_clippy.workspace(
                str(self.bins / "cargo"), "crates/Cargo.toml"
            ).status
        out, err = capfd.readouterr()
        assert status == 0, err
        tail = "--manifest-path crates/Cargo.toml"
        self.assert_logged(
            f"cargo fetch --locked {tail} [  ]",
            f"cargo metadata --no-deps --format-version 1 --locked {tail} [  ]",
            *REGISTER,
            f"cargo clippy -p ext@0.1.0 {tail} {OWN} {LADDER} [yes pg17 {self.belt}]",
        )

    def test_an_extension_in_a_second_root_is_linted_there(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A pgrx extension in fuzz/Cargo.toml, its own root: linted at it.

        The root workspace holds a plain crate only; before #2004 the
        task read the root workspace alone and skipped clean.
        """
        self.metadata.write_text(
            planted.workspace(planted.member("a")), encoding="utf-8"
        )
        self.fuzz_metadata.write_text(
            planted.workspace(planted.member("ext", pgrx=True)), encoding="utf-8"
        )
        (self.tree / "fuzz").mkdir()
        (self.tree / "fuzz" / "Cargo.toml").write_text(
            "[workspace]\n", encoding="utf-8"
        )
        self.track()
        status, out, err = self.call(capfd=capfd)
        assert status == 0, err
        fuzz = "--manifest-path fuzz/Cargo.toml"
        self.assert_logged(
            FETCH,
            METADATA,
            f"cargo fetch --locked {fuzz} [  ]",
            f"cargo metadata --no-deps --format-version 1 --locked {fuzz} [  ]",
            *REGISTER,
            clippy("ext@0.1.0", self.belt, "fuzz/Cargo.toml"),
        )
        said = [
            "lint:pg-clippy: fuzz/Cargo.toml: 1 extension(s) linted: ext(pg17)",
            (
                "lint:pg-clippy: clean at every group"
                " (restriction minus 0 named contradictions)"
            ),
        ]
        assert said == out.splitlines()
