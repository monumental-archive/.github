#!/usr/bin/env python3
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

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import hermetic
import pg_clippy
import test_rust_lint as planted

BELT = Path(__file__).resolve().parent
GIT = hermetic.tool("git")
TASK = "lint:pg-clippy"
# The stub cargo logs each call with the marks the prepare and the
# major's subshell exported, and the clippy config directory it saw.
CARGO = """\
#!/usr/bin/env bash
marks="${STUB_PREPARED:-} ${STUB_MAJOR:-} ${CLIPPY_CONF_DIR:-}"
printf 'cargo %s [%s]\\n' "$*" "${marks}" >> "${STUB_LOG}"
case "$1" in
  fetch) exit "${STUB_FETCH_STATUS:-0}" ;;
  metadata) cat "${STUB_METADATA}"; exit "${STUB_METADATA_STATUS:-0}" ;;
  clippy) [[ $3 != "${STUB_RED:-none}@"* ]] || exit 101 ;;
  *) exit 99 ;;
esac
"""
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
FETCH = "cargo fetch --locked [  ]"
METADATA = "cargo metadata --no-deps --format-version 1 --locked [  ]"
REGISTER = ["prepare 17", "mise ls aqua:theseus-rs/postgresql-binaries --json"]
LADDER = "-- -D warnings"
OWN = (
    "--no-deps --all-targets --locked --keep-going --no-default-features"
    " --features pg17"
)


def clippy(spec: str, belt: Path) -> str:
    """Write the log line of one extension's clippy at pg17.

    Returns:
        What the stub cargo logs.

    """
    return f"cargo clippy -p {spec} {OWN} {LADDER} [yes pg17 {belt}]"


class Planted(unittest.TestCase):
    """A planted tree, a planted belt and the stubs."""

    @override
    def setUp(self) -> None:
        """Plant the tree, the belt and the stubs."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
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
        self.belt = hermetic.belt(
            tmp / "belt",
            stubs={
                "clippy-ladder.sh": planted.STUB_LADDER,
                "pgrx-env.sh": PGRX_ENV,
                "pgrx_postgres.py": planted.STUB_PIN,
            },
        )
        self.log = tmp / "log"
        self.metadata = tmp / "metadata.json"
        self.metadata.write_text(TWO, encoding="utf-8")
        self.env = hermetic.env(
            tmp / "home",
            PATH=os.pathsep.join((str(self.bins), "/usr/bin", "/bin")),
            MISE_TASK_NAME=TASK,
            ORG_FINDINGS="",
            ORG_PYTHON=sys.executable,
            CLIPPY_EXCLUDE="other",
            STUB_LOG=str(self.log),
            STUB_METADATA=str(self.metadata),
            STUB_REAL_BELT=str(BELT),
        )
        (self.tree / "src").mkdir(parents=True)
        (self.tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        subprocess.run([GIT, "init", "-q"], cwd=self.tree, env=self.env, check=True)

    def call(self, *argv: str, **stub: str) -> hermetic.Called:
        """Call pg_clippy.py's main in the tree, from the planted belt.

        Returns:
            Its status and what it wrote.

        """
        return hermetic.call_main(
            lambda: pg_clippy.main(list(argv)),
            env=self.env | {"ORG_BELT_DIR": str(self.belt)} | stub,
            cwd=self.tree,
        )

    def call_child(self, **stub: str) -> tuple[hermetic.Called, str]:
        """Call main, reading what a child wrote to descriptor 2.

        A finding a belt shell function or helper writes is a child's,
        on this process's descriptor 2, which call_main does not hold.

        Returns:
            The call's status and streams, and the child's stderr.

        """
        with tempfile.TemporaryFile() as caught:
            saved = os.dup(2)
            os.dup2(caught.fileno(), 2)
            try:
                done = self.call(**stub)
            finally:
                os.dup2(saved, 2)
                os.close(saved)
            caught.seek(0)
            return done, caught.read().decode()

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
        self.assertEqual(want, self.logged())

    def assert_finding(self, done: hermetic.Called, status: int, *lines: str) -> None:
        """Assert the status, and the finding's lines in order on stderr."""
        self.assertEqual(status, done.returncode, done.stdout + done.stderr)
        head, *detail = lines
        want = f"{TASK}: {head}\n" + "".join(f"  {line}\n" for line in detail)
        self.assertIn(want, done.stderr)


class Branches(Planted):
    """Each branch of lint:pg-clippy's body, its message asserted."""

    def test_an_argument_is_a_usage_error(self) -> None:
        """The task passes none; anything else is a caller's mistake."""
        done = self.call("lint")
        self.assert_finding(done, 2, "usage: pg_clippy.py")
        self.assert_logged()

    def test_cargo_missing_is_red(self) -> None:
        """No cargo on PATH: the toolchain is broken, never a skip."""
        (self.bins / "cargo").unlink()
        done = self.call()
        self.assert_finding(
            done, 1, "Cargo.toml present but cargo missing — pin rust in mise.toml"
        )
        self.assert_logged()

    def test_a_red_fetch_keeps_its_status(self) -> None:
        """`cargo fetch --locked` fails: nothing is derived after it."""
        done = self.call(STUB_FETCH_STATUS="7")
        self.assertEqual(7, done.returncode)
        self.assert_logged(FETCH)
        self.assertEqual("", done.stdout)

    def test_a_red_metadata_is_never_a_workspace_with_no_extension(self) -> None:
        """A failing cargo metadata gives its status, not the skip."""
        done = self.call(STUB_METADATA_STATUS="4")
        self.assertEqual(4, done.returncode)
        self.assert_logged(FETCH, METADATA)
        self.assertNotIn("skipped", done.stdout)

    def test_a_red_surface_is_never_a_workspace_with_no_extension(self) -> None:
        """gate_surface.py refuses the metadata: its status, not the skip."""
        self.metadata.write_text("not json", encoding="utf-8")
        done, child = self.call_child()
        self.assertEqual(1, done.returncode)
        self.assertTrue(
            child.startswith("lint:pg-clippy: cargo metadata did not give JSON\n"),
            child,
        )
        self.assert_logged(FETCH, METADATA)
        self.assertEqual("", done.stdout)

    def test_a_major_that_is_not_a_number_is_red(self) -> None:
        """pgrx-env.sh's union refuses it by name (#1795)."""
        done, child = self.call_child(STUB_BAD_MAJOR="1")
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            "lint:pg-clippy: ext@0.1.0 names no Postgres major: 'pg17'\n", child
        )
        self.assert_logged(FETCH, METADATA)
        self.assertEqual("", done.stdout)

    def test_no_extension_skips_clean(self) -> None:
        """A workspace of plain crates has nothing for this task."""
        self.metadata.write_text(
            planted.workspace(planted.member("a")), encoding="utf-8"
        )
        done = self.call()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "lint:pg-clippy: no pgrx extension tracked, skipped\n", done.stdout
        )
        self.assert_logged(FETCH, METADATA)

    def test_clippy_missing_is_red(self) -> None:
        """A rustup that lists no clippy: the remedy names the pin."""
        done = self.call(STUB_COMPONENTS="rustfmt-host\\n")
        self.assert_finding(
            done,
            1,
            "clippy component missing — declare it with the rust pin in mise.toml:",
            'rust = { version = "1.xx.x", components = "clippy,rustfmt,llvm-tools" }',
        )
        self.assert_logged(FETCH, METADATA)

    def test_a_red_ladder_keeps_its_status(self) -> None:
        """clippy-ladder.sh fails: nothing is registered or linted."""
        done = self.call(STUB_LADDER_FAILS="1")
        self.assertEqual(3, done.returncode)
        self.assert_logged(FETCH, METADATA)

    def test_a_red_prepare_keeps_its_status(self) -> None:
        """The SDK or an ICU check fails: nothing is linted (#1774)."""
        done = self.call(STUB_PREPARE_FAILS="1")
        self.assertEqual(5, done.returncode)
        self.assert_logged(FETCH, METADATA, "prepare 17")

    def test_a_missing_postgres_pin_keeps_its_status(self) -> None:
        """pgrx_postgres.py reds a declared major with no pin (#1940)."""
        done = self.call(STUB_PIN_STATUS="6")
        self.assertEqual(6, done.returncode)
        self.assert_logged(FETCH, METADATA, *REGISTER)

    def test_a_red_extension_keeps_clippys_status(self) -> None:
        """A refused extension gives cargo's 101, and no summary."""
        done = self.call(STUB_RED="ext")
        self.assertEqual(101, done.returncode)
        self.assert_logged(FETCH, METADATA, *REGISTER, clippy("ext@0.1.0", self.belt))
        self.assertEqual("", done.stdout)

    def test_a_clean_extension_says_what_was_linted(self) -> None:
        """At its highest major, with the canon's clippy.toml, under the ladder.

        `other` is excluded by CLIPPY_EXCLUDE and not linted; `a` is no
        extension.
        """
        done = self.call()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assert_logged(FETCH, METADATA, *REGISTER, clippy("ext@0.1.0", self.belt))
        said = [
            "lint:pg-clippy: 1 extension(s) linted: ext(pg17)",
            (
                "lint:pg-clippy: clean at every group"
                " (restriction minus 0 named contradictions)"
            ),
        ]
        self.assertEqual(said, done.stdout.splitlines())


class Workspace(Planted):
    """The workspace is a parameter, for #2004's per-root lint."""

    def test_the_manifest_path_reaches_every_cargo_command(self) -> None:
        """Fetch, metadata and clippy each name the workspace they read."""
        where = ["--manifest-path", "crates/Cargo.toml"]
        done = hermetic.call_main(
            lambda: pg_clippy.workspace(str(self.bins / "cargo"), where),
            env=self.env | {"ORG_BELT_DIR": str(self.belt)},
            cwd=self.tree,
        )
        self.assertEqual(0, done.returncode, done.stderr)
        tail = "--manifest-path crates/Cargo.toml"
        self.assert_logged(
            f"cargo fetch --locked {tail} [  ]",
            f"cargo metadata --no-deps --format-version 1 --locked {tail} [  ]",
            *REGISTER,
            f"cargo clippy -p ext@0.1.0 {tail} {OWN} {LADDER} [yes pg17 {self.belt}]",
        )


if __name__ == "__main__":
    unittest.main()
